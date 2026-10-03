from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import documents, organization_profiles, tsd
from app.services.customer_order_filters import CustomerOrderFilter, CustomerOrderFilterList, resolve_order_filter, moysklad_order_filter_conditions
from app.services.moysklad import MoySkladService
from tests.test_release_processing import Result
from tests.test_tsd_orders import context


def preset():
    return dict(id=str(uuid4()), name="Хорека · Питер", project_id=str(uuid4()), project_name="Хорека",
                sale_attribute_id=str(uuid4()), sale_dictionary_id=str(uuid4()), sale_value_id=str(uuid4()), sale_value_name="Питер")


def multi_preset():
    item = preset()
    return {**item, "projects": [{"id": item["project_id"], "name": "Хорека"}, {"id": str(uuid4()), "name": "Розница"}],
            "sale_values": [{"id": item["sale_value_id"], "name": "Питер"}, {"id": str(uuid4()), "name": "Москва"}]}


def test_legacy_values_migrate_and_explicit_empty_arrays_clear_old_selections():
    item = preset()
    migrated = CustomerOrderFilter.model_validate(item)
    assert str(migrated.projects[0].id) == item["project_id"]
    assert str(migrated.sale_values[0].id) == item["sale_value_id"]
    cleared = CustomerOrderFilter.model_validate({**item, "projects": []})
    assert cleared.project_id is None and cleared.project_name is None
    conditions = moysklad_order_filter_conditions("https://ms", cleared.model_dump(mode="json"))
    assert len(conditions) == 1 and not conditions[0].startswith("project=")
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate({**item, "projects": [], "sale_values": [], "sale_attribute_id": None, "sale_dictionary_id": None})


def test_multiple_values_repeat_same_field_without_cross_product():
    item = multi_preset()
    conditions = moysklad_order_filter_conditions("https://ms", item)
    assert conditions == [f"project=https://ms/entity/project/{value['id']}" for value in item["projects"]] + [
        f"https://ms/entity/customerorder/metadata/attributes/{item['sale_attribute_id']}=https://ms/entity/customentity/{item['sale_dictionary_id']}/{value['id']}"
        for value in item["sale_values"]]
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate({**item, "projects": [item["projects"][0]] * 2})
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate({**item, "sale_dictionary_id": None})


def test_presets_validate_names_conditions_and_unique_ids():
    item = preset()
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate({**item, "name": "  "})
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate({**item, "sale_dictionary_id": None})
    with pytest.raises(ValidationError):
        CustomerOrderFilterList.model_validate({"filters": [item, item]})
    with pytest.raises(ValidationError):
        CustomerOrderFilter.model_validate(dict(id=str(uuid4()), name="Без условий"))
    assert CustomerOrderFilter.model_validate({**item, "name": "  Питер  "}).name == "Питер"


async def test_saved_presets_are_shared_with_tsd_but_isolated_by_profile(monkeypatch):
    item = multi_preset()
    profile = NS(customer_order_filters=[])
    db = NS(commit=AsyncMock())
    saved = await organization_profiles.save_order_filters(CustomerOrderFilterList(filters=[item]), profile, db)
    assert saved == await organization_profiles.get_order_filters(profile)
    db.commit.assert_awaited_once()
    monkeypatch.setattr(tsd, "_device_scope", AsyncMock(return_value=(NS(), NS(), profile)))
    assert await tsd.get_tsd_order_filters(NS(), db) == saved
    assert resolve_order_filter(profile, CustomerOrderFilter.model_validate(item).id) == saved[0]
    with pytest.raises(HTTPException) as exc:
        resolve_order_filter(NS(customer_order_filters=[]), CustomerOrderFilter.model_validate(item).id)
    assert exc.value.status_code == 404


async def test_clear_filters_and_reject_removed_selection():
    profile = NS(customer_order_filters=[preset()])
    removed = CustomerOrderFilter.model_validate(profile.customer_order_filters[0]).id
    assert await organization_profiles.save_order_filters(CustomerOrderFilterList(), profile, NS(commit=AsyncMock())) == []
    with pytest.raises(HTTPException):
        resolve_order_filter(profile, removed)


async def test_combined_filter_is_applied_before_pagination_and_preserves_search():
    ms = MoySkladService("fake")
    calls = []
    async def request(client, method, url, **kwargs):
        calls.append(kwargs["params"])
        return httpx.Response(200, json={"rows": [{"id": "matching"}]}, request=httpx.Request(method, url))
    ms._request_with_retry = request
    item = multi_preset()
    assert await ms.get_customer_orders("org", "27370", offset=50, order_filter=item) == [{"id": "matching"}]
    params = calls[0]
    assert params["offset"] == 50 and params["limit"] == 50 and params["search"] == "27370"
    assert f"organization={ms.base_url}/entity/organization/org" in params["filter"]
    assert f"project={ms.base_url}/entity/project/{item['project_id']}" in params["filter"]
    assert f"metadata/attributes/{item['sale_attribute_id']}={ms.base_url}/entity/customentity/{item['sale_dictionary_id']}/{item['sale_value_id']}" in params["filter"]
    assert f"project={ms.base_url}/entity/project/{item['projects'][1]['id']}" in params["filter"]
    assert f"/{item['sale_values'][1]['id']}" in params["filter"]


async def test_pc_and_tsd_pass_identical_saved_conditions(monkeypatch):
    item = multi_preset()
    device, _, profile, ms, db = context(monkeypatch, ([],))
    profile.customer_order_filters = [item]
    ms.get_customer_orders.return_value = []
    fid = CustomerOrderFilter.model_validate(item).id
    await tsd.list_tsd_orders(search="42", offset=50, device=device, db=db, filter_id=fid)
    terminal_call = ms.get_customer_orders.call_args
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=ms))
    await documents.list_customer_orders(search="42", offset=50, current_user=NS(), profile=profile, db=db, filter_id=fid)
    assert ms.get_customer_orders.call_args == terminal_call


async def test_options_report_missing_vendor_permissions_without_empty_success():
    ms = MoySkladService("fake")
    item = preset()
    async def request(client, method, url, **kwargs):
        if url.endswith("metadata/attributes"):
            body = {"rows": [{"id": item["sale_attribute_id"], "name": "Где продажа", "type": "customentity",
                "customEntityMeta": {"href": f"{ms.base_url}/context/companysettings/metadata/customEntities/{item['sale_dictionary_id']}"}}]}
            return httpx.Response(200, json=body, request=httpx.Request(method, url))
        return httpx.Response(403, json={"errors": []}, request=httpx.Request(method, url))
    ms._request_with_retry = request
    result = await ms.get_customer_order_filter_options()
    assert len(result["warnings"]) == 2 and result["sale_attribute_id"] == item["sale_attribute_id"]


async def test_options_load_full_dictionaries_and_skip_archived_values():
    ms = MoySkladService("fake")
    item = preset()
    async def request(client, method, url, **kwargs):
        if url.endswith("metadata/attributes"):
            rows = [{"id": item["sale_attribute_id"], "name": "Где продажа", "type": "customentity",
                "customEntityMeta": {"href": f"{ms.base_url}/context/companysettings/metadata/customEntities/{item['sale_dictionary_id']}"}}]
        elif url.endswith("project"):
            rows = [{"id": str(uuid4()), "name": "Проект"} for _ in range(1000)] if kwargs["params"]["offset"] == 0 else [{"id": "archived", "name": "Архив", "archived": True}]
        else:
            rows = [{"id": item["sale_value_id"], "name": "Питер"}]
        return httpx.Response(200, json={"rows": rows}, request=httpx.Request(method, url))
    ms._request_with_retry = request
    result = await ms.get_customer_order_filter_options()
    assert len(result["projects"]) == 1000 and result["sale_values"] == [{"id": item["sale_value_id"], "name": "Питер"}]
    assert result["warnings"] == []
