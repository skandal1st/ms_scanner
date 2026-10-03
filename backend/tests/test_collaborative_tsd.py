from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.api import tsd
from app.db.models import DocumentStatus, ScanStatus
from app.main import WebSocketManager
from tests.test_release_processing import Result


def undo_context(monkeypatch, barcode=False, quantity=None, found=True):
    device = NS(id=uuid4(), user_id=uuid4())
    doc = NS(id=uuid4(), status=DocumentStatus.draft)
    action = NS(id=uuid4(), device_id=device.id, undone_at=None)
    scan = NS(id=uuid4(), document_id=doc.id, code="test", status=ScanStatus.scanned,
              is_barcode=barcode, box_quantity=quantity, scanned_at=datetime.now(timezone.utc))
    db = NS(execute=AsyncMock(side_effect=[Result(doc), Result((action, scan) if found else None)]),
            delete=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(tsd, "_owned_tsd_document", AsyncMock(return_value=doc))
    return device, doc, action, scan, db


async def test_undo_is_scoped_to_terminal_and_keeps_other_terminal_mark(monkeypatch):
    device, doc, action, scan, db = undo_context(monkeypatch)
    response = await tsd.delete_last_tsd_scan(doc.id, device, db)
    query = db.execute.await_args_list[-1].args[0]
    assert device.id in query.compile().params.values()
    assert "tsd_scan_actions.device_id" in str(query) and "tsd_scan_actions.undone_at IS NULL" in str(query)
    assert response.id == scan.id and action.undone_at is not None
    db.delete.assert_awaited_once_with(scan)
    tsd.publish_removed.assert_awaited_once_with(device.user_id, doc.id, scan.id)


async def test_undo_barcode_decrements_one_unit_instead_of_deleting_shared_quantity(monkeypatch):
    device, doc, action, scan, db = undo_context(monkeypatch, barcode=True, quantity=5)
    response = await tsd.delete_last_tsd_scan(doc.id, device, db)
    assert response.box_quantity == 4 and scan.box_quantity == 4
    db.delete.assert_not_awaited()
    tsd.publish_scan.assert_awaited_once_with(device.user_id, scan)


async def test_undo_never_falls_back_to_someone_elses_or_legacy_scan(monkeypatch):
    device, doc, action, scan, db = undo_context(monkeypatch, found=False)
    with pytest.raises(HTTPException) as error:
        await tsd.delete_last_tsd_scan(doc.id, device, db)
    assert error.value.status_code == 404
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()


class Socket:
    def __init__(self):
        self.accept = AsyncMock()
        self.close = AsyncMock()
        self.send_json = AsyncMock()


async def test_live_updates_are_document_scoped_and_revoked_device_is_disconnected():
    manager = WebSocketManager()
    pc, first, second = Socket(), Socket(), Socket()
    await manager.connect("u", pc)
    await manager.connect("u", first, document_id="one", device_id="first")
    await manager.connect("u", second, document_id="two", device_id="second")
    event = {"type": "scan_upsert", "document_id": "one", "scan": {"id": "s"}}
    await manager.send("u", event)
    first.send_json.assert_awaited_once_with(event)
    second.send_json.assert_not_awaited()
    pc.send_json.assert_awaited_once_with(event)
    await manager.send("other", event)
    assert first.send_json.await_count == 1
    await manager.send("u", {"type": "tsd_device_revoked", "device_id": "first"})
    first.close.assert_awaited_once_with(code=1008)
    assert first not in manager.connections["u"] and first not in manager.terminal_scopes
    assert pc in manager.connections["u"] and second in manager.connections["u"]
