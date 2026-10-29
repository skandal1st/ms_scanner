"""Real PostgreSQL coverage for tenancy, atomic snapshots, pagination and retry."""
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import gtin_control
from app.core.config import settings
from app.core.security import encrypt_token
from app.db.models import User, Integration, GtinProductMap, GtinAuditRun, GtinAuditFinding
from app.services import gtin_audit_job
from app.services.gtin_audit import Catalog
from app.worker import tasks

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
GTIN = '04640275972501'


async def setup(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    import app.db.session
    monkeypatch.setattr(app.db.session, 'engine', engine)
    monkeypatch.setattr(gtin_audit_job, 'AsyncSessionLocal', factory)
    user = User(id=uuid4(), email=f'audit-{uuid4()}@example.test', password_hash='fixture')
    async with factory() as db:
        db.add(user); await db.flush()
        db.add(Integration(user_id=user.id, moysklad_token=encrypt_token('fixture-no-live-access')))
        db.add(GtinProductMap(user_id=user.id, gtin=GTIN, product_id='old-card', product_name='Персик 200 г'))
        await db.commit()
    return engine, factory, user


async def cleanup(engine, factory, user):
    async with factory() as db:
        for model in (GtinAuditRun, GtinProductMap, Integration):
            await db.execute(delete(model).where(model.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()
    await engine.dispose()


async def test_real_snapshot_refresh_paging_and_tenant_isolation(monkeypatch):
    engine, factory, user = await setup(monkeypatch)
    queue = Mock(); monkeypatch.setattr(tasks.gtin_audit_task, 'delay', queue)
    catalog = Catalog()
    catalog.add({'id': 'old-card', 'name': 'Персик 200 г', 'barcodes': []})
    catalog.add({'id': 'correct', 'name': 'Персик 25 г', 'barcodes': [{'gtin': GTIN}]})
    catalog.add({'id': 'duplicate-one', 'name': 'Другой вкус', 'barcodes': [{'gtin': '04620164405358'}]})
    catalog.add({'id': 'duplicate-two', 'name': 'Другой вкус hard', 'barcodes': [{'gtin': '04620164405358'}]})
    monkeypatch.setattr(gtin_audit_job, 'load_catalog', AsyncMock(return_value=catalog))
    try:
        async with factory() as db:
            first = await gtin_control.refresh(user, db)
            again = await gtin_control.refresh(user, db)
            assert first['id'] == again['id']
            queue.assert_called_once()
        await gtin_audit_job.run_audit(str(first['id']), str(user.id))
        async with factory() as db:
            status = await gtin_control.status(user, db)
            assert status['job'] is None
            assert status['report']['summary']['duplicate_active'] == 1
            assert status['report']['summary']['other_product'] == 1
            page = await gtin_control.findings(first['id'], 'mapping', '', 'ПЕРСИК', False, 1, 1, user, db)
            assert page['total'] == 1 and page['items'][0]['candidates'][0]['id'] == 'correct'
            assert not (await gtin_control.findings(first['id'], 'mapping', '', '%', False, 1, 50, user, db))['items']
            duplicate = await gtin_control.findings(first['id'], 'duplicate', '', '', False, 1, 1, user, db)
            assert duplicate['total'] == 1 and len(duplicate['items'][0]['candidates']) == 2
            with pytest.raises(HTTPException) as error:
                await gtin_control.findings(first['id'], 'mapping', '', '', False, 1, 50,
                                            type('Other', (), {'id': uuid4()})(), db)
            assert error.value.status_code == 404
            mapping = (await db.execute(select(GtinProductMap).where(GtinProductMap.user_id == user.id))).scalar_one()
            assert mapping.product_id == 'old-card'
        # A repeated completed delivery must not overwrite the snapshot.
        gtin_audit_job.load_catalog.reset_mock()
        await gtin_audit_job.run_audit(str(first['id']), str(user.id))
        gtin_audit_job.load_catalog.assert_not_awaited()
    finally:
        await cleanup(engine, factory, user)


async def test_failed_refresh_preserves_previous_success_and_does_not_publish_partial_data(monkeypatch):
    engine, factory, user = await setup(monkeypatch)
    monkeypatch.setattr(tasks.gtin_audit_task, 'delay', Mock())
    try:
        async with factory() as db:
            old = GtinAuditRun(user_id=user.id, status='done', phase='done', summary={'mapping_count': 7},
                               finished_at=datetime.now(timezone.utc))
            db.add(old); await db.commit()
            next_run = await gtin_control.refresh(user, db)
        monkeypatch.setattr(gtin_audit_job, 'load_catalog', AsyncMock(side_effect=ValueError('Не удалось получить полный каталог МС.')))
        await gtin_audit_job.run_audit(str(next_run['id']), str(user.id))
        async with factory() as db:
            state = await gtin_control.status(user, db)
            assert state['report']['id'] == old.id and state['report']['summary']['mapping_count'] == 7
            assert state['job']['status'] == 'error'
            assert 'полный каталог' in state['job']['error_message']
            assert not (await db.execute(select(GtinAuditFinding.id).where(GtinAuditFinding.run_id == next_run['id']))).all()
    finally:
        await cleanup(engine, factory, user)
