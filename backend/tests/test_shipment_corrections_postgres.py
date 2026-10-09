"""Real JSONB persistence and correction access checks in an isolated audit database."""
import copy
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import Base, Document, DocumentKind, DocumentStatus, OrganizationProfile, Scan, ScanStatus, User
from app.services import shipment_corrections as service
from app.api import shipment_corrections as routes
from app.services.document_guard import editable_document

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


async def seed():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Test', moysklad_organization_id='org')
        db.add(profile)
        await db.flush()
        source = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id,
            name='Sent', kind=DocumentKind.demand, status=DocumentStatus.accepted,
            moysklad_id=str(uuid4()), moysklad_organization_id='org', moysklad_store_id='store')
        db.add(source)
        await db.commit()
    return engine, factory, user, profile, source


async def test_baseline_persists_old_source_stays_accepted_and_cancel_is_local(monkeypatch):
    engine, factory, user, profile, source = await seed()
    pos_id = str(uuid4())
    baseline = {pos_id: {'fields': {'id': pos_id, 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': str(uuid4()), 'cis': 'old-code', 'type': 'trackingcode'}]}}
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({'organization': {'id': 'org'}, 'store': {'id': 'store'}}, baseline)))
    ms = NS(build_plan=AsyncMock(return_value=[]))
    access = routes.Scope(user, profile.id, ['store'])
    try:
        async with factory() as db:
            revision = await service.start(db, await db.get(Document, source.id), ms, 'actor')
            revision_id = revision.id
        async with factory() as db:
            original, revision = await db.get(Document, source.id), await db.get(Document, revision_id)
            assert original.status == DocumentStatus.accepted
            assert service.correction(revision)['baseline'] == baseline
            scans = (await db.execute(select(Scan).where(Scan.document_id == revision_id))).scalars().all()
            assert len(scans) == 1 and scans[0].status == ScanStatus.valid
            assert str(scans[0].id) in service.correction(revision)['baseline_scans']
            detail = await routes.detail(revision_id, access, db)
            assert detail['scans'][0]['existing']
            assert detail['scans'][0]['position_id'] == pos_id
            await routes.remove_code(revision_id, scans[0].id, access, db)
            delta = await routes.preview(revision_id, routes.PreviewRequest(), access, db)
            assert delta['removed'][0]['code'] == 'old-code'
            await routes.cancel(revision_id, access, db)
        async with factory() as db:
            listed = await routes.list_sources(access, db)
            assert [row['id'] for row in listed] == [str(source.id)]
            assert (await db.get(Document, source.id)).status == DocumentStatus.accepted
    finally:
        await engine.dispose()


async def test_foreign_account_profile_and_warehouse_cannot_access_revision():
    engine, factory, user, profile, source = await seed()
    try:
        for scope in [routes.Scope(NS(id=uuid4()), profile.id, []),
                      routes.Scope(user, uuid4(), []), routes.Scope(user, profile.id, ['foreign'])]:
            async with factory() as db:
                with pytest.raises(HTTPException) as error:
                    await routes.owned(db, scope, source.id)
                assert error.value.status_code == 404
    finally:
        await engine.dispose()


async def test_concurrent_open_reuses_one_revision(monkeypatch):
    import asyncio
    from app.db import session
    engine, factory, user, profile, source = await seed()
    monkeypatch.setattr(session, 'engine', engine)
    pos_id = str(uuid4())
    baseline = {pos_id: {'fields': {'id': pos_id, 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': str(uuid4()), 'cis': 'old-code', 'type': 'trackingcode'}]}}
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({'organization': {'id': 'org'}, 'store': {'id': 'store'}}, baseline)))
    monkeypatch.setattr(routes, '_get_ms_service', AsyncMock(return_value=NS(build_plan=AsyncMock(return_value=[]))))
    async def open_one():
        async with factory() as db:
            return await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
    try:
        opened = await asyncio.gather(open_one(), open_one())
        assert opened[0]['id'] == opened[1]['id']
        async with factory() as db:
            revisions = (await db.execute(select(Document).where(Document.moysklad_id == source.moysklad_id, Document.status == DocumentStatus.draft))).scalars().all()
            assert len(revisions) == 1
    finally:
        await engine.dispose()


async def test_queue_freezes_reviewed_revision_and_duplicate_save_does_not_enqueue(monkeypatch):
    engine, factory, user, profile, source = await seed()
    scan_id, tc_id = uuid4(), str(uuid4())
    baseline = {'position': {'fields': {'id': 'position', 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': tc_id, 'cis': 'old', 'type': 'trackingcode'}]}}
    state = {'source_id': str(source.id), 'baseline': baseline,
        'baseline_scans': {str(scan_id): {'position_id': 'position', 'code': baseline['position']['codes'][0]}}, 'add_positions': {}}
    access = routes.Scope(user, profile.id, [])
    from app.worker.tasks import correct_shipment_task
    queued = []
    monkeypatch.setattr(correct_shipment_task, 'delay', lambda *args: queued.append(args))
    try:
        async with factory() as db:
            revision = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Revision',
                kind=DocumentKind.demand, moysklad_id=source.moysklad_id, status=DocumentStatus.draft,
                upd_meta={service.MARKER: copy.deepcopy(state)})
            db.add(revision)
            await db.commit()
            revision_id = revision.id
            delta = await routes.preview(revision_id, routes.PreviewRequest(), access, db)
            await routes.save(revision_id, routes.PreviewRequest(preview_hash=delta['preview_hash']), access, db)
        async with factory() as db:
            saved = await db.get(Document, revision_id)
            assert saved.status == DocumentStatus.processing
            assert service.correction(saved)['job']['completed'] == 0
            with pytest.raises(HTTPException):
                await routes.save(revision_id, routes.PreviewRequest(preview_hash=delta['preview_hash']), access, db)
            with pytest.raises(HTTPException):
                await editable_document(db, revision_id, user.id)
            assert len(queued) == 1
            saved.status = DocumentStatus.draft
            await db.commit()
            with pytest.raises(HTTPException, match=''):
                await editable_document(db, revision_id, user.id)
    finally:
        await engine.dispose()
