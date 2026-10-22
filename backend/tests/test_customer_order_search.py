"""Buyer names must be searchable even when MS document search cannot find them."""
import httpx
import pytest

from app.services.moysklad import MoySkladService


def source(ms, rows):
    calls = []

    async def request(client, method, url, **kwargs):
        assert method == "GET" and url.endswith("/entity/customerorder")
        params = kwargs["params"]
        calls.append(params)
        start = params["offset"]
        return httpx.Response(200, json={"rows": rows[start:start + params["limit"]]},
                              request=httpx.Request(method, url))

    ms._request_with_retry = request
    return calls


@pytest.mark.parametrize("query", ["тула", " ТУЛА ", "(Тула)", "Габибов"])
async def test_find_old_order_by_buyer_city_name_or_number(query):
    ms = MoySkladService("fake")
    order = dict(id="target", name="28072", agent={"name": "(Тула)ИП Габибов Эминь Сейфаддинович"})
    calls = source(ms, [dict(id=str(i), name="Другой заказ") for i in range(205)] + [order])

    assert await ms.get_customer_orders("org", query) == [order]
    assert [p["offset"] for p in calls] == [0, 100, 200]
    for params in calls:
        assert "search" not in params
        assert params["expand"] == "agent,store,state" and params["limit"] <= 100
        assert params["filter"] == f"organization={ms.base_url}/entity/organization/org"


async def test_number_search_keeps_fast_native_lookup():
    ms = MoySkladService("fake")
    order = dict(id="target", name="28072")
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append(kwargs["params"])
        return httpx.Response(200, json={"rows": [order]}, request=httpx.Request(method, url))

    ms._request_with_retry = request
    assert await ms.get_customer_orders("org", " 28072 ", offset=50) == [order]
    assert len(calls) == 1 and calls[0]["search"] == "28072" and calls[0]["offset"] == 50


async def test_paginate_matches_instead_of_source_rows_and_keep_order():
    ms = MoySkladService("fake")
    rows = [dict(id=str(i), name=str(i), agent={"name": "Тула" if i % 3 == 0 else "Москва"})
            for i in range(310)]
    calls = source(ms, rows)
    first = await ms.get_customer_orders("org", "тула")
    second = await ms.get_customer_orders("org", "тула", offset=50)
    last = await ms.get_customer_orders("org", "тула", offset=100)
    assert first + second + last == rows[::3]
    assert len(first) == len(second) == 50 and len(last) == 4
    assert all(p["order"] == "moment,desc" for p in calls)


@pytest.mark.parametrize("query", [None, "   "])
async def test_empty_search_keeps_direct_pagination(query):
    ms = MoySkladService("fake")
    rows = [dict(id=str(i)) for i in range(120)]
    calls = source(ms, rows)
    assert await ms.get_customer_orders("org", query, offset=50) == rows[50:100]
    assert len(calls) == 1 and calls[0]["offset"] == 50 and calls[0]["limit"] == 50


async def test_missing_fields_description_and_literal_filter_characters():
    ms = MoySkladService("fake")
    order = dict(id="match", description="Доставка Тула;state=новый")
    calls = source(ms, [dict(id="empty", name=None, agent=None), order])
    assert await ms.get_customer_orders("org", "тула;state=новый") == [order]
    assert calls[0]["filter"] == f"organization={ms.base_url}/entity/organization/org"


async def test_failed_later_page_is_not_returned_as_empty_search():
    ms = MoySkladService("fake")

    async def request(client, method, url, **kwargs):
        status = 503 if kwargs["params"]["offset"] else 200
        return httpx.Response(status, json={"rows": [dict(id=str(i)) for i in range(100)]},
                              request=httpx.Request(method, url))

    ms._request_with_retry = request
    with pytest.raises(httpx.HTTPStatusError):
        await ms.get_customer_orders("org", "тула")


async def test_cursor_returns_recent_match_without_scanning_whole_history():
    ms = MoySkladService("fake")
    order = dict(id="target", name="28072", agent={"name": "(Тула)ИП Габибов"})
    calls = source(ms, [order] + [dict(id=str(i)) for i in range(5000)])
    rows, cursor = await ms.get_customer_order_page("org", "тула")
    assert rows == [order] and cursor == 100
    assert len(calls) == 1


async def test_cursor_paginates_all_matches_without_loss_or_duplicates():
    ms = MoySkladService("fake")
    rows = [dict(id=str(i), agent={"name": "Тула"}) for i in range(205)]
    source(ms, rows)
    found, cursor = [], 0
    while cursor is not None:
        page, cursor = await ms.get_customer_order_page("org", "тула", cursor)
        assert len(page) <= 50
        found.extend(page)
    assert found == rows


async def test_empty_cursor_page_is_bounded_and_can_continue_to_old_match():
    ms = MoySkladService("fake")
    order = dict(id="old", agent={"name": "Тула"})
    calls = source(ms, [dict(id=str(i)) for i in range(350)] + [order])
    assert await ms.get_customer_order_page("org", "тула") == ([], 300)
    assert len(calls) == 3
    assert await ms.get_customer_order_page("org", "тула", 300) == ([order], None)


async def test_cursor_preserves_scope_and_saved_filters():
    from tests.test_customer_order_filters import multi_preset
    from app.services.customer_order_filters import moysklad_order_filter_conditions
    ms = MoySkladService("fake")
    calls = source(ms, [])
    preset = multi_preset()
    assert await ms.get_customer_order_page("org", "тула", order_filter=preset) == ([], None)
    for condition in moysklad_order_filter_conditions(ms.base_url, preset):
        assert condition in calls[0]["filter"]


async def test_tsd_cursor_response_exposes_continuation(monkeypatch):
    from fastapi import Response
    from unittest.mock import AsyncMock
    from app.api import tsd
    from tests.test_tsd_orders import context
    device, _, _, ms, db = context(monkeypatch, ([],))
    ms.get_customer_order_page = AsyncMock(return_value=([dict(id="target", name="28072")], 100))
    response = Response()
    result = await tsd.list_tsd_orders(search="тула", cursor=0, response=response, device=device, db=db)
    assert result[0].name == "28072" and response.headers["X-Next-Offset"] == "100"
    ms.get_customer_order_page.assert_awaited_once_with("org", "тула", cursor=0)


async def test_pc_cursor_response_marks_end_of_search(monkeypatch):
    from fastapi import Response
    from unittest.mock import AsyncMock
    from types import SimpleNamespace as NS
    from app.api import documents
    ms = NS(get_customer_order_page=AsyncMock(return_value=([], None)))
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=ms))
    response = Response()
    rows = await documents.list_customer_orders(search="тула", cursor=300, response=response,
        current_user=NS(), profile=NS(moysklad_organization_id="org"), db=NS())
    assert rows == [] and response.headers["X-Next-Offset"] == ""
    ms.get_customer_order_page.assert_awaited_once_with("org", "тула", cursor=300)
