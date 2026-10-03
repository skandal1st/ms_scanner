from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app.api import tsd
from app.db.models import DocumentStatus
from app.services.moysklad import MoySkladService
from tests.test_release_processing import Result


def context(monkeypatch, values=()):
    device = NS(id=uuid4(), user_id=uuid4())
    workplace = NS(id=uuid4(), store_ids=["allowed"])
    profile = NS(id=uuid4(), moysklad_organization_id="org")
    ms = NS(get_customer_order=AsyncMock(), get_customer_order_demands=AsyncMock(),
            get_customer_orders=AsyncMock(), get_document=AsyncMock(), build_plan=AsyncMock())
    db = NS(execute=AsyncMock(side_effect=[Result(v) for v in values]), commit=AsyncMock(),
            flush=AsyncMock(), refresh=AsyncMock(), add=lambda obj: None)
    monkeypatch.setattr(tsd, "_device_scope", AsyncMock(return_value=(NS(), workplace, profile)))
    monkeypatch.setattr(tsd, "_ms_for_user", AsyncMock(return_value=ms))
    return device, workplace, profile, ms, db


def demand(id="ship", store="allowed", org="org"):
    return dict(id=id, name=id, organization={"id": org}, store={"id": store, "name": store})


async def test_order_shipments_exclude_other_warehouses_organizations_and_completed(monkeypatch):
    completed = NS(id=uuid4(), moysklad_id="done", status=DocumentStatus.accepted)
    device, _, _, ms, db = context(monkeypatch, ([completed], [], []))
    order_id = uuid4()
    ms.get_customer_order.return_value = dict(name="42", organization={"id": "org"})
    ms.get_customer_order_demands.return_value = [demand(), demand("foreign-store", "other"),
        demand("foreign-org", org="other"), demand("done")]
    result = await tsd.get_tsd_order_shipments(order_id, device, db)
    assert [s.moysklad_id for s in result.shipments] == ["ship"]
    assert result.order_name == "42"
    ms.get_customer_order_demands.assert_awaited_once_with(str(order_id), "org")


async def test_order_without_shipments_does_not_create_any_document(monkeypatch):
    device, _, _, ms, db = context(monkeypatch, ([],))
    ms.get_customer_order.return_value = dict(name="empty", organization={"id": "org"})
    ms.get_customer_order_demands.return_value = []
    result = await tsd.get_tsd_order_shipments(uuid4(), device, db)
    assert result.shipments == []
    ms.get_document.assert_not_awaited()
    ms.build_plan.assert_not_awaited()


async def test_foreign_order_is_rejected_before_reading_shipments(monkeypatch):
    device, _, _, ms, db = context(monkeypatch)
    ms.get_customer_order.return_value = dict(organization={"id": "other"})
    with pytest.raises(HTTPException) as error:
        await tsd.get_tsd_order_shipments(uuid4(), device, db)
    assert error.value.status_code == 403
    ms.get_customer_order_demands.assert_not_awaited()


async def test_permission_denial_is_not_reported_as_empty_orders(monkeypatch):
    device, _, _, ms, db = context(monkeypatch)
    request = httpx.Request("GET", "https://example/entity/customerorder")
    ms.get_customer_orders.side_effect = httpx.HTTPStatusError("denied", request=request,
                                                             response=httpx.Response(403, request=request))
    with pytest.raises(HTTPException) as error:
        await tsd.list_tsd_orders(device=device, db=db)
    assert error.value.status_code == 403 and "права" in error.value.detail


async def test_legacy_started_shipment_is_matched_to_order_by_id(monkeypatch):
    oid = str(uuid4())
    doc = NS(id=uuid4(), moysklad_id="old-demand", moysklad_customer_order_id=None, moysklad_store_id="allowed")
    device, _, _, ms, db = context(monkeypatch, ([doc], [], [doc.id]))
    ms.get_customer_orders.return_value = [dict(id=oid, name="42", demands=[{"id": "old-demand"}])]
    result = await tsd.list_tsd_orders(device=device, db=db)
    assert result[0].in_work and doc.moysklad_customer_order_id == oid


async def test_existing_shipment_link_is_rechecked_before_opening(monkeypatch):
    doc = NS(id=uuid4())
    device, _, _, ms, db = context(monkeypatch, (doc,))
    ms.get_document.return_value = dict(customerOrder={"id": str(uuid4())})
    with pytest.raises(HTTPException) as error:
        await tsd.select_tsd_document(tsd.SelectDocumentRequest(moysklad_id="ship", customer_order_id=uuid4()), device, db)
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()
    ms.build_plan.assert_not_awaited()


async def test_new_session_uses_selected_demand_plan_and_keeps_parent_order(monkeypatch):
    device, _, _, ms, db = context(monkeypatch, (None, None, [], 0))
    order_id = uuid4()
    ms.get_document.return_value = {**demand(), "customerOrder": {"id": str(order_id)}}
    ms.get_customer_order.return_value = dict(name="42", organization={"id": "org"}, positions="not-used")
    ms.build_plan.return_value = [{"product_id": "partial", "gtin": "04620543080527", "expected_qty": 2}]
    async def flush():
        for obj in added:
            obj.id = uuid4()
            obj.status = DocumentStatus.draft
    async def refresh(obj):
        obj.id = uuid4()
    added = []
    db.add = added.append
    db.flush = flush
    db.refresh = refresh
    result = await tsd.select_tsd_document(tsd.SelectDocumentRequest(moysklad_id="ship", customer_order_id=order_id), device, db)
    assert result.plan[0]["expected_qty"] == 2
    assert result.customer_order_id == str(order_id) and result.customer_order_name == "42"
    ms.build_plan.assert_awaited_once_with("demand", "ship")
    assert len(added) == 2  # local document and session, no remote creation


async def test_order_demand_search_is_scoped_paginated_and_read_only():
    ms = MoySkladService("fake")
    calls = []
    async def request(client, method, url, **kwargs):
        calls.append((method, url, kwargs["params"]))
        rows = [demand(str(i)) for i in range(100)] if len(calls) == 1 else [demand("last")]
        return httpx.Response(200, json={"rows": rows}, request=httpx.Request(method, url))
    ms._request_with_retry = request
    assert len(await ms.get_customer_order_demands("order", "org")) == 101
    assert [c[2]["offset"] for c in calls] == [0, 100]
    assert all(c[0] == "GET" and "customerOrder=" in c[2]["filter"] and "organization=" in c[2]["filter"] for c in calls)
