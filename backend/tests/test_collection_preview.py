"""Opening a shipment is a preview; only explicit start unlocks scanning."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import documents, scans, tsd
from app.db.models import DocumentKind, DocumentStatus
from app.services import collection_start


def shipment(marker=None):
    return NS(id=uuid4(), user_id=uuid4(), kind=DocumentKind.demand,
              moysklad_id='shipment', status=DocumentStatus.draft,
              upd_meta={'collection_start': marker} if marker else {})


@pytest.mark.parametrize('marker', [None, {'shipment_done': True}, {'done': False}])
def test_partial_or_missing_start_does_not_unlock_scans(marker):
    with pytest.raises(HTTPException) as error:
        collection_start.require_collection_started(shipment(marker))
    assert error.value.status_code == 409


def test_completed_start_survives_reload_and_other_flows_are_unchanged():
    doc = shipment({'done': True})
    collection_start.require_collection_started(doc)
    assert collection_start.collection_started(doc)
    doc = shipment()
    doc.kind = DocumentKind.supply
    collection_start.require_collection_started(doc)


@pytest.mark.parametrize('mode', ['single', 'bulk', 'box'])
async def test_pc_scan_endpoints_block_before_external_calls(mode, monkeypatch):
    doc = shipment()
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    db, user = NS(), NS(id=doc.user_id)
    if mode == 'single':
        request = scans.CreateScanRequest(document_id=doc.id, code='010461011399135321OtKd+c_93ZSBQ')
        call = scans.create_scan(request, user, db)
    elif mode == 'bulk':
        request = scans.BulkScanRequest(document_id=doc.id, codes=['010461011399135321OtKd+c_93ZSBQ'])
        call = scans.create_bulk_scans(request, user, db)
    else:
        request = scans.CreateBoxRequest(document_id=doc.id, sscc='00123456789012345678')
        call = scans.create_box_scans(request, user, db)
    with pytest.raises(HTTPException) as error:
        await call
    assert error.value.status_code == 409


async def test_pc_process_requires_start_before_moysklad(monkeypatch):
    from app.services import document_guard
    doc = shipment()
    monkeypatch.setattr(document_guard, 'editable_document', AsyncMock(return_value=doc))
    ms = AsyncMock()
    monkeypatch.setattr(documents, '_get_ms_service', ms)
    with pytest.raises(HTTPException) as error:
        await documents.process_document(doc.id, NS(id=doc.user_id), NS())
    assert error.value.status_code == 409
    ms.assert_not_awaited()


async def test_tsd_scan_requires_start_before_box_or_barcode_processing(monkeypatch):
    from app.services import document_guard
    doc = shipment()
    monkeypatch.setattr(tsd, '_owned_tsd_document', AsyncMock(return_value=doc))
    monkeypatch.setattr(document_guard, 'editable_document', AsyncMock(return_value=doc))
    scope = AsyncMock()
    monkeypatch.setattr(tsd, '_device_scope', scope)
    with pytest.raises(HTTPException) as error:
        await tsd.create_tsd_scan(doc.id, tsd.TsdScanRequest(code='00123456789012345678'),
                                  NS(id=uuid4(), user_id=doc.user_id), NS())
    assert error.value.status_code == 409
    scope.assert_not_awaited()


async def test_tsd_start_checks_device_session_and_calls_shared_start(monkeypatch):
    doc = shipment()
    device = NS(user_id=doc.user_id)
    monkeypatch.setattr(tsd, '_owned_tsd_document', AsyncMock(return_value=doc))
    session = AsyncMock()
    monkeypatch.setattr(tsd, 'get_tsd_document', session)
    ms = object()
    monkeypatch.setattr(tsd, '_ms_for_user', AsyncMock(return_value=ms))
    start = AsyncMock()
    monkeypatch.setattr(collection_start, 'start_collection', start)
    db = NS()
    result = await tsd.start_tsd_collection(doc.id, device, db)
    assert result['collection_started']
    session.assert_awaited_once_with(doc.id, device, db)
    start.assert_awaited_once_with(db, doc, ms)
