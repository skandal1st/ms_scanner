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
