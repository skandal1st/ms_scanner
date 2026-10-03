from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.api import documents
from app.db.models import Document, DocumentKind, DocumentStatus
from tests.test_release_processing import Result


def setup(monkeypatch, existing=None):
    user = NS(id=uuid4())
    profile = NS(id=uuid4(), moysklad_organization_id="org")
    oid = uuid4()
    ms = NS(get_document=AsyncMock(return_value={"id": "ship", "name": "52", "organization": {"id": "org"},
        "customerOrder": {"id": str(oid)}}), get_customer_order=AsyncMock(return_value={"name": "42", "organization": {"id": "org"}}),
        build_plan=AsyncMock(return_value=[{"gtin": "04620543080527", "product_id": "partial", "product_name": "Товар", "expected_qty": 2}]),
        get_customer_order_demands=AsyncMock())
    added = []
    db = NS(execute=AsyncMock(return_value=Result(existing)), commit=AsyncMock(), add=added.append, refresh=AsyncMock())
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=ms))
    monkeypatch.setattr(documents, "_scan_count", AsyncMock(return_value=2))
    body = documents.ResolveDocRequest(moysklad_id="ship", customer_order_id=oid)
    return body, user, profile, ms, db, added


async def test_desktop_reuses_existing_tsd_document_and_scans(monkeypatch):
    doc = Document(id=uuid4(), user_id=uuid4(), name="52", kind=DocumentKind.demand, status=DocumentStatus.draft,
        moysklad_id="ship", plan=[], created_at=datetime.now(timezone.utc))
    body, user, profile, ms, db, added = setup(monkeypatch, doc)
    result = await documents.resolve_document(body, user, profile, db)
    assert result.id == doc.id and result.scan_count == 2 and result.customer_order_name == "42"
    assert doc.moysklad_customer_order_id == str(body.customer_order_id)
    ms.build_plan.assert_not_awaited()
    assert added == []


async def test_changed_order_link_is_rejected_before_local_document_query(monkeypatch):
    body, user, profile, ms, db, _ = setup(monkeypatch)
    ms.get_document.return_value["customerOrder"]["id"] = str(uuid4())
    with pytest.raises(HTTPException) as error:
        await documents.resolve_document(body, user, profile, db)
    assert error.value.status_code == 409
    db.execute.assert_not_awaited()


async def test_foreign_shipment_is_rejected_even_if_order_is_ours(monkeypatch):
    body, user, profile, ms, db, _ = setup(monkeypatch)
    ms.get_document.return_value["organization"]["id"] = "foreign"
    with pytest.raises(HTTPException) as error:
        await documents.resolve_document(body, user, profile, db)
    assert error.value.status_code == 403
    db.commit.assert_not_awaited()


async def test_plan_failure_does_not_open_free_collection(monkeypatch):
    body, user, profile, ms, db, added = setup(monkeypatch)
    ms.build_plan.side_effect = TimeoutError()
    with pytest.raises(HTTPException) as error:
        await documents.resolve_document(body, user, profile, db)
    assert error.value.status_code == 502 and "план" in error.value.detail
    assert added == []


async def test_new_collection_uses_partial_shipment_plan_and_order_header(monkeypatch):
    body, user, profile, ms, db, added = setup(monkeypatch)
    async def refresh(doc):
        doc.id = uuid4()
        doc.status = DocumentStatus.draft
        doc.created_at = datetime.now(timezone.utc)
    db.refresh = refresh
    result = await documents.resolve_document(body, user, profile, db)
    assert result.plan[0].expected_qty == 2 and result.customer_order_name == "42"
    assert len(added) == 1 and added[0].moysklad_customer_order_id == str(body.customer_order_id)
    ms.build_plan.assert_awaited_once_with("demand", "ship")


async def test_completed_shipment_cannot_restart_from_stale_order_choice(monkeypatch):
    body, user, profile, ms, db, added = setup(monkeypatch, NS(status=DocumentStatus.accepted))
    with pytest.raises(HTTPException) as error:
        await documents.resolve_document(body, user, profile, db)
    assert error.value.status_code == 409
    assert added == []


async def test_shipment_list_omits_completed_deleted_and_foreign_documents(monkeypatch):
    body, user, profile, ms, db, _ = setup(monkeypatch, ["done"])
    ms.get_customer_order_demands.return_value = [
        {"id": "partial", "name": "52", "organization": {"id": "org"}},
        {"id": "done", "organization": {"id": "org"}},
        {"id": "deleted", "organization": {"id": "org"}, "deleted": "2026-10-03"},
        {"id": "foreign", "organization": {"id": "other"}},
    ]
    result = await documents.customer_order_shipments(body.customer_order_id, user, profile, db)
    assert [row.id for row in result] == ["partial"]
    assert result[0].customer_order_name == "42"
