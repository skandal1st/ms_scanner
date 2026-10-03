from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.api import tsd
from app.db.models import ScanStatus


def setup(monkeypatch, marked=True):
    doc = NS(id=uuid4(), plan=[dict(product_id="chosen", product_name="Выбранный товар", marked=marked)])
    device = NS(id=uuid4(), user_id=uuid4())
    db = NS()
    scan = NS(id=uuid4(), document_id=doc.id, code="010462054308050321TEST", status=ScanStatus.scanned,
              scanned_at=datetime.now(timezone.utc), moysklad_product_id="chosen")
    monkeypatch.setattr(tsd, "_owned_tsd_document", AsyncMock(return_value=doc))
    monkeypatch.setattr("app.services.document_guard.editable_document", AsyncMock(return_value=doc))
    monkeypatch.setattr(tsd, "_device_scope", AsyncMock(return_value=(NS(), None, None)))
    create = AsyncMock(return_value=(scan, False))
    monkeypatch.setattr(tsd, "_create_scan_record", create)
    return doc, device, db, create


@pytest.mark.parametrize("target", [None, "chosen"])
async def test_assignment_is_forwarded_to_shared_scan_flow(monkeypatch, target):
    doc, device, db, create = setup(monkeypatch)
    body = tsd.TsdScanRequest(code="010462054308050321TEST", moysklad_product_id=target)
    await tsd.create_tsd_scan(doc.id, body, device, db)
    assert create.await_args.kwargs == {"moysklad_product_id": target, "device_id": device.id}
    assert create.await_args.args[2] == body.code


@pytest.mark.parametrize("code,target,marked", [
    ("010462054308050321TEST", "foreign", True),
    ("00123456789012345678", "chosen", True),
    ("010462054308050321TEST", "chosen", False),
])
async def test_invalid_assignment_stops_before_scan_or_external_calls(monkeypatch, code, target, marked):
    doc, device, db, create = setup(monkeypatch, marked)
    external = AsyncMock()
    monkeypatch.setattr(tsd, "_resolve_cz_for_boxes", external)
    with pytest.raises(HTTPException) as exc:
        await tsd.create_tsd_scan(doc.id, tsd.TsdScanRequest(code=code, moysklad_product_id=target), device, db)
    assert exc.value.status_code == 400
    create.assert_not_awaited()
    external.assert_not_awaited()


async def test_duplicate_does_not_reassign_existing_mark(monkeypatch):
    doc, device, db, create = setup(monkeypatch)
    existing = create.return_value[0]
    existing.moysklad_product_id = "original"
    create.return_value = (existing, True)
    result = await tsd.create_tsd_scan(doc.id, tsd.TsdScanRequest(code=existing.code, moysklad_product_id="chosen"), device, db)
    assert result.duplicate and result.moysklad_product_id == "original"


async def test_barcode_of_another_unmarked_position_cannot_be_redirected(monkeypatch):
    doc, device, db, create = setup(monkeypatch)
    monkeypatch.setattr(tsd, "_classify_barcode", lambda plan, code: ({"product_id": "other"}, None))
    increment = AsyncMock()
    monkeypatch.setattr(tsd, "_create_or_increment_barcode_scan", increment)
    with pytest.raises(HTTPException) as exc:
        await tsd.create_tsd_scan(doc.id, tsd.TsdScanRequest(code="2000000000001", moysklad_product_id="chosen"), device, db)
    assert exc.value.status_code == 400
    increment.assert_not_awaited()
    create.assert_not_awaited()
