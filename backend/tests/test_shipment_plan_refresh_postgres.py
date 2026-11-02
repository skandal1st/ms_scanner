import os
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import User, Document, DocumentKind, Scan, ScanStatus
from app.services import shipment_plan_refresh as service
from tests.test_shipment_plan_refresh import row


pytestmark = pytest.mark.skipif(os.getenv('PLAN_POSTGRES') != '1', reason='isolated PostgreSQL required')


async def test_refresh_keeps_verified_marks_and_includes_concurrent_scan(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid, did = uuid4(), uuid4()
    verified = datetime.now(timezone.utc)
    monkeypatch.setattr(service, 'publish_event', AsyncMock())
    try:
        async with factory() as db:
            db.add(User(id=uid, email=f'plan-{uid}@example.test', password_hash='fixture'))
            await db.flush()
            db.add(Document(id=did, user_id=uid, kind=DocumentKind.demand, name='Plan QA',
                            moysklad_id='fixture', plan=[row('a', 2), row('removed', 1)]))
            await db.flush()
            db.add_all([Scan(document_id=did, code=code, gtin='04640275972501',
                             moysklad_product_id=pid, status=ScanStatus.valid,
                             verified_at=verified, verification={'checked': 'unchanged'})
                        for code, pid in [('first', 'a'), ('removed', 'removed')]])
            await db.commit()

        async def get_document(*args):
            # This writer would block if refresh held its document row lock over MS IO.
            async with factory() as other:
                from app.services.document_guard import editable_document
                await editable_document(other, did, uid)
                other.add(Scan(document_id=did, code='concurrent', gtin='04640275972501',
                               moysklad_product_id='a', status=ScanStatus.valid,
                               verified_at=verified, verification={'checked': 'unchanged'}))
                await other.commit()
            return {'name': 'Updated shipment'}

        ms = NS(get_document=AsyncMock(side_effect=get_document),
                build_plan=AsyncMock(return_value=[row('a', 1), row('new', 3)]))
        async with factory() as db:
            await service.refresh(db, did, uid, ms)
        async with factory() as db:
            doc = await db.get(Document, did)
            scans = list((await db.scalars(select(Scan).where(Scan.document_id == did))).all())
            assert {s.code for s in scans} == {'first', 'removed', 'concurrent'}
            assert all(s.verified_at == verified and s.verification == {'checked': 'unchanged'} for s in scans)
            assert next(s for s in scans if s.code == 'removed').moysklad_product_id == 'removed'
            assert sorted(s.status.value for s in scans if s.moysklad_product_id == 'a') == ['overflow', 'valid']
            assert doc.moysklad_name == 'Updated shipment'
            assert {r['kind'] for r in doc.upd_meta['plan_sync']['changes']} == {'added', 'removed', 'quantity'}
        # Increasing the plan restores quantity status without another CZ check.
        ms.get_document.side_effect = None
        ms.get_document.return_value = {'name': 'Updated shipment'}
        ms.build_plan.return_value = [row('a', 3), row('new', 3)]
        async with factory() as db:
            await service.refresh(db, did, uid, ms)
        async with factory() as db:
            scans = list((await db.scalars(select(Scan).where(Scan.document_id == did))).all())
            assert all(s.status == ScanStatus.valid and s.verified_at == verified for s in scans)
    finally:
        async with factory() as db:
            await db.execute(delete(Scan).where(Scan.document_id == did))
            await db.execute(delete(Document).where(Document.id == did))
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
        await engine.dispose()
