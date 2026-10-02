"""TSD deletion must stay scoped to a draft document and the selected scan."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import tsd
from app.db.models import DocumentStatus, ScanStatus
from tests.test_release_processing import Result


def setup(monkeypatch, status=DocumentStatus.draft, found=True):
    doc = NS(id=uuid4(), status=status)
    device = NS(id=uuid4(), user_id=uuid4())
    scan = NS(id=uuid4(), document_id=doc.id, code="010462054308052721TEST000000001",
              status=ScanStatus.scanned, scanned_at=datetime.now(timezone.utc))
    db = NS(execute=AsyncMock(side_effect=[Result(doc), Result(scan if found else None)]),
            delete=AsyncMock(), commit=AsyncMock())
    owned = AsyncMock(return_value=doc)
    monkeypatch.setattr(tsd, "_owned_tsd_document", owned)
    return doc, device, scan, db, owned


async def test_delete_exact_scan_in_draft(monkeypatch):
    doc, device, scan, db, owned = setup(monkeypatch)
    result = await tsd.delete_tsd_scan(doc.id, scan.id, device, db)
    assert result.id == scan.id
    owned.assert_awaited_once_with(db, device, doc.id)
    lock_query, scan_query = [call.args[0] for call in db.execute.await_args_list]
    assert "FOR UPDATE" in str(lock_query)
    params = scan_query.compile().params
    assert scan.id in params.values() and doc.id in params.values()
    assert "scans.document_id" in str(scan_query) and "scans.id" in str(scan_query)
    db.delete.assert_awaited_once_with(scan)
    db.commit.assert_awaited_once()


async def test_scan_from_another_document_is_not_deleted(monkeypatch):
    doc, device, scan, db, _ = setup(monkeypatch, found=False)
    with pytest.raises(HTTPException) as exc:
        await tsd.delete_tsd_scan(doc.id, scan.id, device, db)
    assert exc.value.status_code == 404
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("route", ["selected", "last"])
@pytest.mark.parametrize("status", [DocumentStatus.processing, DocumentStatus.accepted])
async def test_processing_and_accepted_cannot_delete(monkeypatch, route, status):
    doc, device, scan, db, _ = setup(monkeypatch, status=status)
    with pytest.raises(HTTPException) as exc:
        if route == "selected":
            await tsd.delete_tsd_scan(doc.id, scan.id, device, db)
        else:
            await tsd.delete_last_tsd_scan(doc.id, device, db)
    assert exc.value.status_code == 409
    db.delete.assert_not_awaited()


async def test_inaccessible_document_stops_before_deletion(monkeypatch):
    doc, device, scan, db, owned = setup(monkeypatch)
    owned.side_effect = HTTPException(404, "Отгрузка недоступна на этом рабочем месте")
    with pytest.raises(HTTPException):
        await tsd.delete_tsd_scan(doc.id, scan.id, device, db)
    db.execute.assert_not_awaited()
    db.delete.assert_not_awaited()
