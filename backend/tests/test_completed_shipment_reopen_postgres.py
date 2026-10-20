"""Completed shipments must not acquire a second collection through PC or TSD."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import documents, tsd
from app.core.config import settings
from app.db.models import Base, Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services.shipment_sessions import shipment_session_query
from tests.test_collaborative_postgres import context

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


@pytest.fixture
async def completed(monkeypatch):
    bootstrap = create_async_engine(settings.DATABASE_URL)
    async with bootstrap.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap.dispose()
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    now = datetime.now(timezone.utc)
    marker = {'done': True, 'started_at': now.isoformat()}
    async with factory() as db:
        source = Document(user_id=user.id, organization_profile_id=profile.id, name='166948',
            kind=DocumentKind.demand, status=DocumentStatus.accepted, moysklad_id=ms_id,
            moysklad_organization_id='org', moysklad_store_id='store',
            created_at=now - timedelta(minutes=20), upd_meta={'collection_start': marker})
        db.add(source)
        await db.flush()
        db.add(Scan(document_id=source.id, code='saved-mark', status=ScanStatus.valid))
        preview = Document(user_id=user.id, organization_profile_id=profile.id, name='166948',
            kind=DocumentKind.demand, status=DocumentStatus.draft, moysklad_id=ms_id,
            moysklad_organization_id='org', moysklad_store_id='store',
            created_at=now, upd_meta={'collection_start': marker})
        db.add(preview)
        await db.commit()
    yield factory, user, profile, devices[0], source, preview, ms_id
    await engine.dispose()


@pytest.mark.parametrize('entry', ['create', 'resolve'])
async def test_pc_reopens_completed_with_marks_instead_of_newer_empty_preview(completed, monkeypatch, entry):
    factory, user, profile, _, source, preview, ms_id = completed
    async with factory() as db:
        if entry == 'create':
            result = await documents.create_document(documents.CreateDocumentRequest(name='166948', moysklad_id=ms_id), user, profile, db)
        else:
            result = await documents.resolve_document(documents.ResolveDocRequest(moysklad_id=ms_id), user, profile, db)
        assert result.id == source.id and result.status == DocumentStatus.accepted and result.scan_count == 1
        assert (await db.execute(select(func.count()).select_from(Document).where(Document.moysklad_id == ms_id))).scalar_one() == 2
    documents._get_ms_service.return_value.build_plan.assert_not_awaited()


async def test_tsd_rejects_completed_shipment_even_when_newer_empty_preview_exists(completed):
    factory, _, _, device, _, _, ms_id = completed
    async with factory() as db:
        with pytest.raises(HTTPException) as error:
            await tsd.select_tsd_document(tsd.SelectDocumentRequest(moysklad_id=ms_id), device, db)
        assert error.value.status_code == 409 and 'уже собрана' in error.value.detail
        assert (await db.execute(select(func.count()).select_from(Document).where(Document.moysklad_id == ms_id))).scalar_one() == 2
    tsd._ms_for_user.return_value.build_plan.assert_not_awaited()


async def test_list_hides_only_empty_legacy_preview_and_keeps_nonempty_work(completed):
    factory, user, profile, _, source, preview, _ = completed
    async with factory() as db:
        result = await documents.list_documents(DocumentKind.demand, user, profile, db)
        assert [d.id for d in result] == [source.id] and result[0].scan_count == 1
        db.add(Scan(document_id=preview.id, code='do-not-lose', status=ScanStatus.invalid))
        await db.commit()
        result = await documents.list_documents(DocumentKind.demand, user, profile, db)
        assert {d.id for d in result} == {source.id, preview.id}


async def test_completed_correction_is_latest_history_but_pending_correction_is_separate(completed):
    factory, user, profile, _, source, preview, ms_id = completed
    async with factory() as db:
        pending = await db.get(Document, preview.id)
        pending.upd_meta = {'shipment_correction': {'source_id': str(source.id)}}
        await db.commit()
        selected = (await db.execute(shipment_session_query(user.id, ms_id))).scalars().first()
        assert selected.id == source.id
        pending.status = DocumentStatus.accepted
        await db.commit()
        selected = (await db.execute(shipment_session_query(user.id, ms_id))).scalars().first()
        assert selected.id == preview.id


async def test_copied_collection_marker_on_empty_preview_does_not_block_correction(completed, monkeypatch):
    factory, user, profile, _, source, preview, _ = completed
    from app.api import shipment_corrections as routes
    from app.services import shipment_corrections as service
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(
        {'organization': {'id': 'org'}, 'store': {'id': 'store'}}, {})))
    monkeypatch.setattr(routes, '_get_ms_service', documents._get_ms_service)
    async with factory() as db:
        opened = await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
        saved = await db.get(Document, preview.id)
        assert saved.upd_meta['superseded_by_document_id'] == opened['id']
        assert await db.get(Scan, (await db.execute(select(Scan.id).where(Scan.document_id == source.id))).scalar_one())


async def test_concurrent_pc_opens_after_completion_do_not_create_documents(completed):
    factory, user, profile, _, source, _, ms_id = completed
    async def open_one():
        async with factory() as db:
            return await documents.create_document(documents.CreateDocumentRequest(name='166948', moysklad_id=ms_id), user, profile, db)
    results = await asyncio.wait_for(asyncio.gather(open_one(), open_one()), timeout=10)
    assert {d.id for d in results} == {source.id}


async def test_no_draft_created_when_only_completed_collection_exists(completed):
    factory, user, profile, _, source, preview, ms_id = completed
    async with factory() as db:
        await db.delete(await db.get(Document, preview.id))
        await db.commit()
        result = await documents.create_document(documents.CreateDocumentRequest(name='166948', moysklad_id=ms_id), user, profile, db)
        assert result.id == source.id and result.scan_count == 1
        assert (await db.execute(select(func.count()).select_from(Document).where(Document.moysklad_id == ms_id))).scalar_one() == 1


async def test_terminal_list_omits_completed_despite_newer_empty_draft(completed):
    factory, _, _, device, _, _, ms_id = completed
    tsd._ms_for_user.return_value.get_documents = AsyncMock(return_value=[{'id': ms_id, 'name': '166948'}])
    async with factory() as db:
        result = await tsd.list_tsd_documents(None, device, db)
        assert result == []
