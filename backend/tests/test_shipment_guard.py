from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api import documents, tsd
from app.db.models import DocumentKind, DocumentStatus
from app.services import document_guard
from app.services.moysklad import MoySkladService
from app.services.shipment_guard import ensure_active_shipment, ensure_no_stranded_scans
from tests.test_release_processing import DB, setup_ms, codes, position
from tests.test_tsd_orders import context, demand


def test_same_number_does_not_make_deleted_shipment_active():
    ensure_active_shipment({'id': 'new', 'name': '166233'})
    with pytest.raises(ValueError, match='удалена'):
        ensure_active_shipment({'id': 'old', 'name': '166233', 'deleted': '2026-10-05'})


async def test_deleted_shipment_is_blocked_before_remote_writes(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    async def request(client, method, url, **kwargs):
        calls.append((method, url, None))
        return httpx.Response(200, json={'name': '166233', 'deleted': '2026-10-05'},
                              request=httpx.Request(method, url))
    ms._request_with_retry = request
    with pytest.raises(ValueError, match='удалена'):
        await ms.update_document('demand', 'old', codes(1))
    assert [method for method, _, _ in calls] == ['GET']
    ms._load_tracking_codes.assert_not_awaited()


async def test_migrated_document_cannot_be_scanned_or_sent():
    doc = NS(status=DocumentStatus.draft, upd_meta={'superseded_by_document_id': str(uuid4())})
    with pytest.raises(HTTPException) as error:
        await document_guard.editable_document(DB(doc), uuid4(), uuid4())
    assert error.value.status_code == 409 and 'перенесены' in error.value.detail


async def test_deleted_shipment_cannot_be_opened_on_tsd(monkeypatch):
    device, _, _, ms, db = context(monkeypatch, (None,))
    ms.get_document.return_value = {**demand(), 'deleted': '2026-10-05'}
    with pytest.raises(HTTPException) as error:
        await tsd.select_tsd_document(tsd.SelectDocumentRequest(moysklad_id='old'), device, db)
    assert error.value.status_code == 409
    ms.build_plan.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_deleted_shipment_cannot_be_resolved_on_pc(monkeypatch):
    ms = NS(get_document=AsyncMock(return_value={'name': '166233', 'deleted': '2026-10-05'}))
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=ms))
    with pytest.raises(HTTPException) as error:
        await documents.resolve_document(documents.ResolveDocRequest(moysklad_id='old'), NS(id=uuid4()), NS(id=uuid4()), DB())
    assert error.value.status_code == 409


async def test_stranded_marks_block_opening_replacement():
    old = NS(moysklad_id='old', name='166233')
    ms = NS(get_document=AsyncMock(return_value={'deleted': '2026-10-05'}))
    with pytest.raises(HTTPException) as error:
        await ensure_no_stranded_scans(DB([old]), ms, uuid4(), uuid4(), uuid4(), 'new')
    assert error.value.status_code == 409 and 'Повторно сканировать' in error.value.detail


async def test_multiple_live_shipments_remain_allowed():
    ms = NS(get_document=AsyncMock(return_value={'id': 'other'}))
    await ensure_no_stranded_scans(DB([NS(moysklad_id='other', name='other')]),
                                  ms, uuid4(), uuid4(), uuid4(), 'new')


async def test_ms_outage_never_silently_ignores_previous_assembly():
    ms = NS(get_document=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(HTTPException) as error:
        await ensure_no_stranded_scans(DB([NS(moysklad_id='old')]), ms, uuid4(), uuid4(), uuid4(), 'new')
    assert error.value.status_code == 502


async def test_deleted_shipment_not_queued_for_processing(monkeypatch):
    doc = NS(id=uuid4(), kind=DocumentKind.demand, moysklad_id='old', status=DocumentStatus.draft)
    db = DB(doc)
    ms = NS(get_document=AsyncMock(return_value={'deleted': '2026-10-05'}))
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=ms))
    with pytest.raises(HTTPException) as error:
        await documents.process_document(doc.id, NS(id=uuid4()), db)
    assert error.value.status_code == 409
    assert doc.status == DocumentStatus.draft
    db.commit.assert_not_awaited()


@pytest.mark.parametrize('deleted', [True, False])
async def test_stranded_scan_query_on_postgres(monkeypatch, deleted):
    import os
    from app.db.models import Document, Scan, ScanStatus
    from tests.test_collaborative_postgres import context as postgres_context
    if os.getenv('AUDIT_POSTGRES') != '1':
        pytest.skip('requires isolated PostgreSQL')
    engine, factory, user, profile, _, _ = await postgres_context(monkeypatch)
    order_id = str(uuid4())
    ms = NS(get_document=AsyncMock(return_value={'deleted': '2026-10-05' if deleted else None}))
    try:
        async with factory() as db:
            source = Document(user_id=user.id, organization_profile_id=profile.id,
                              moysklad_id=str(uuid4()), moysklad_customer_order_id=order_id,
                              name='166233', kind=DocumentKind.demand, status=DocumentStatus.draft)
            db.add(source)
            await db.flush()
            db.add(Scan(document_id=source.id, code=str(uuid4()), status=ScanStatus.valid))
            await db.commit()
        async with factory() as db:
            if deleted:
                with pytest.raises(HTTPException) as error:
                    await ensure_no_stranded_scans(db, ms, user.id, profile.id, order_id, 'replacement')
                assert error.value.status_code == 409
            else:
                await ensure_no_stranded_scans(db, ms, user.id, profile.id, order_id, 'replacement')
            ms.get_document.assert_awaited_once_with('demand', source.moysklad_id)
        ms.get_document.reset_mock()
        async with factory() as db:
            await ensure_no_stranded_scans(db, ms, user.id, profile.id, str(uuid4()), 'replacement')
            await ensure_no_stranded_scans(db, ms, uuid4(), profile.id, order_id, 'replacement')
            await ensure_no_stranded_scans(db, ms, user.id, uuid4(), order_id, 'replacement')
        ms.get_document.assert_not_awaited()
    finally:
        await engine.dispose()
