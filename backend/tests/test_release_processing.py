"""Release regressions: no lost marks, false success or blind external replay."""
import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api import documents, integrations
from app.core.config import settings
from app.db.models import DocumentKind, DocumentStatus, ScanStatus
from app.services import document_guard
from app.services.chestnyznak import CZApiError
from app.services.moysklad import MoySkladService
from app.worker import tasks


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def first(self):
        return self.value


class DB:
    def __init__(self, *values):
        self.values = iter(values)
        self.commit = AsyncMock()

    async def execute(self, query):
        return Result(next(self.values))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def position(pid="p1", qty=1, row_id="r1"):
    return {"id": row_id, "quantity": qty, "price": 1234, "assortment": {
        "id": pid, "trackingType": "TOBACCO", "meta": {"type": "product", "href": f"https://example/product/{pid}"},
    }}


def codes(n, pid="p1"):
    return [{"product_id": pid, "code": f"010466032120585821{i:07d}", "quantity": 1} for i in range(n)]


def setup_ms(monkeypatch, rows):
    monkeypatch.setattr(settings, "CZ_MOCK_MODE", False)
    ms = MoySkladService("fake-token")
    ms._load_positions_rows = AsyncMock(return_value=rows)
    ms._load_tracking_codes = AsyncMock(return_value=[])
    calls = []

    async def request(client, method, url, **kwargs):
        calls.append((method, url, copy.deepcopy(kwargs.get("json"))))
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    ms._request_with_retry = request
    return ms, calls


async def test_overflow_and_unplanned_products_are_all_sent(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position(), position("keep", 9, "untouched")])
    ms._load_positions_rows.side_effect = [[position(), position("keep", 9, "untouched")], [position(), position("new", 1, "r2")]]
    await ms.update_document("demand", "doc", codes(2) + [{"product_id": "new", "code": "0104660321205858219999999", "quantity": 1}])
    put = next(body for method, _, body in calls if method == "PUT")
    assert [p["quantity"] for p in put["positions"]] == [2, 9, 1]
    assert put["positions"][1]["price"] == 1234
    assert sum(len(body) for method, _, body in calls if method == "POST") == 3
    assert all("trackingCodes" not in p for p in put["positions"])


async def test_25000_marks_use_bounded_batches_and_report_progress(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position(qty=10)])
    progress = AsyncMock()
    await ms.update_document("demand", "doc", codes(25000), on_progress=progress)
    batches = [body for method, _, body in calls if method == "POST"]
    assert len(batches) == 50
    assert all(len(batch) == 500 for batch in batches)
    assert len({tc["cis"] for batch in batches for tc in batch}) == 25000
    progress.assert_awaited_with(25000, 25000)


async def test_read_failure_does_not_write_or_replace_positions(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    ms._load_positions_rows.side_effect = httpx.ReadTimeout("read failed")
    with pytest.raises(httpx.ReadTimeout):
        await ms.update_document("demand", "doc", codes(2))
    assert calls == []


async def test_retry_sends_only_codes_missing_after_partial_failure(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position(qty=1200)])
    saved = []
    ms._load_tracking_codes = AsyncMock(side_effect=lambda *a: list(saved))
    original = ms._request_with_retry
    post_calls = 0

    async def request(client, method, url, **kwargs):
        nonlocal post_calls
        if method == "POST":
            post_calls += 1
            saved.extend(kwargs["json"])
            if post_calls == 2:
                # The server saved this batch, but its response was lost.
                raise httpx.ReadTimeout("uncertain outcome")
        return await original(client, method, url, **kwargs)

    ms._request_with_retry = request
    with pytest.raises(httpx.ReadTimeout):
        await ms.update_document("demand", "doc", codes(1200))
    assert len(saved) == 1000
    await ms.update_document("demand", "doc", codes(1200))
    assert len(saved) == 1200
    assert len({tc["cis"] for tc in saved}) == 1200


async def test_missing_product_rejects_entire_transfer(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    with pytest.raises(ValueError, match="Не все марки"):
        await ms.update_document("demand", "doc", codes(1) + [{"code": "missing"}])
    assert calls == []


@pytest.mark.parametrize("status", [DocumentStatus.accepted, DocumentStatus.processing])
async def test_edit_and_repeated_process_are_blocked(status):
    doc = NS(status=status)
    with pytest.raises(HTTPException) as exc:
        await document_guard.editable_document(DB(doc), uuid4(), uuid4())
    assert exc.value.status_code == 409


async def test_process_blocks_pending_scans(monkeypatch):
    doc = NS(id=uuid4(), status=DocumentStatus.draft, kind=DocumentKind.demand, moysklad_id="ms1")
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=NS(get_document=AsyncMock(return_value={}))))
    with pytest.raises(HTTPException) as exc:
        await documents.process_document(doc.id, NS(id=uuid4()), DB(doc, 1))
    assert exc.value.status_code == 409
    assert doc.status == DocumentStatus.draft


async def test_no_integration_never_becomes_accepted(monkeypatch):
    from app.db import session
    doc = NS(status=DocumentStatus.processing, kind=DocumentKind.demand, moysklad_id="ms1")
    scan = NS(is_box=False, child_codes=None)
    db = DB(doc, [scan], None, None)
    monkeypatch.setattr(session, "AsyncSessionLocal", lambda: db)
    with pytest.raises(ValueError, match="МойСклад не подключён"):
        await tasks._process_document_unlocked_async(str(uuid4()), str(uuid4()))
    assert doc.status != DocumentStatus.accepted


async def test_partial_writeoff_keeps_first_id_and_blocks_replay(monkeypatch):
    doc = NS(id=uuid4(), kind=DocumentKind.loss, status=DocumentStatus.draft, cz_doc_ids=None)
    user = NS(id=uuid4())
    scan = NS(status=ScanStatus.valid, is_box=False, is_barcode=False, child_codes=None, code="cis1")
    monkeypatch.setattr(document_guard, "editable_document", AsyncMock(return_value=doc))
    monkeypatch.setattr(integrations, "_cz_source_for_document", AsyncMock(return_value=NS(cz_token="encrypted", cz_token_expires_at=None)))
    monkeypatch.setattr(integrations, "decrypt_token", lambda token: "token")
    monkeypatch.setattr(integrations, "_scan_cises", lambda scans: [s.code for s in scans])
    cz = NS(submit_document=AsyncMock(side_effect=["external-id-1", CZApiError("timeout")]))
    monkeypatch.setattr(integrations, "ChestnyZnakService", lambda **kwargs: cz)
    monkeypatch.setattr(tasks.poll_writeoff_status_task, "delay", lambda *args: None)
    payload = {"document_id": str(doc.id), "scan_codes": ["cis1"], "parts": {
        "milk": {"product_document_b64": "first"}, "water": {"product_document_b64": "second"},
    }}
    signatures = [NS(pg="milk", signature="s1"), NS(pg="water", signature="s2")]
    with pytest.raises(HTTPException):
        await integrations._submit_writeoff_parts(DB([scan]), user, payload, signatures)
    assert doc.cz_doc_ids[0]["doc_id"] == "external-id-1"
    assert doc.cz_doc_ids[1]["state"] == "submitting"
    assert doc.status == DocumentStatus.processing
    with pytest.raises(HTTPException):
        await integrations._submit_writeoff_parts(DB(), user, payload, signatures)
    assert cz.submit_document.await_count == 2


async def test_slow_writeoff_remains_processing_and_is_polled_again(monkeypatch):
    from app.db import session
    doc = NS(status=DocumentStatus.processing, cz_doc_ids=[{"pg": "milk", "doc_id": "id1"}])
    monkeypatch.setattr(session, "AsyncSessionLocal", lambda: DB(doc))
    monkeypatch.setattr(tasks, "_get_cz_source", AsyncMock(return_value=NS(cz_token="encrypted")))
    monkeypatch.setattr(__import__("app.core.security", fromlist=["decrypt_token"]), "decrypt_token", lambda token: "token")
    from app.services import chestnyznak
    monkeypatch.setattr(chestnyznak, "ChestnyZnakService", lambda **kwargs: NS(get_document_info=AsyncMock(return_value={"status": "IN_PROGRESS"})))
    queued = []
    monkeypatch.setattr(tasks.poll_writeoff_status_task, "apply_async", lambda **kwargs: queued.append(kwargs))
    await tasks._poll_writeoff_unlocked(str(uuid4()), str(uuid4()))
    assert doc.status == DocumentStatus.processing
    assert queued[0]["countdown"] == 60


def test_plan_preserves_unmarked_flag():
    plan = documents.PlanItem(gtin=None, product_id="p1", product_name="test", expected_qty=1, marked=False)
    assert plan.model_dump()["marked"] is False


async def test_position_and_code_reads_follow_all_pages(monkeypatch):
    ms = MoySkladService("fake")
    offsets = []

    async def request(client, method, url, **kwargs):
        offset = kwargs["params"]["offset"]
        offsets.append(offset)
        limit = kwargs["params"]["limit"]
        rows = [position(row_id=str(i)) for i in range(limit)] if offset == 0 else [position(row_id="last")]
        return httpx.Response(200, json={"rows": rows}, request=httpx.Request(method, url))

    ms._request_with_retry = request
    assert len(await ms._load_positions_rows("demand", "doc")) == 101
    assert offsets == [0, 100]
    offsets.clear()
    assert len(await ms._load_tracking_codes(None, "demand", "doc", "pos")) == 101
    assert offsets == [0, 100]


async def test_rate_limit_uses_server_retry_after(monkeypatch):
    ms = MoySkladService("fake")
    request = httpx.Request("POST", "https://example")
    client = NS(request=AsyncMock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "4"}, request=request),
        httpx.Response(200, json={}, request=request),
    ]))
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.moysklad.asyncio.sleep", sleep)
    assert (await ms._request_with_retry(client, "POST", "https://example", json=[])).status_code == 200
    sleep.assert_awaited_once_with(4.0)


async def test_uncertain_post_is_not_blindly_retried():
    ms = MoySkladService("fake")
    client = NS(request=AsyncMock(side_effect=httpx.ReadTimeout("response lost")))
    with pytest.raises(httpx.ReadTimeout):
        await ms._request_with_retry(client, "POST", "https://example", json=[])
    assert client.request.await_count == 1


async def test_412_never_continues_with_other_batches(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position(qty=1001)])
    original = ms._request_with_retry

    async def request(client, method, url, **kwargs):
        if method == "POST":
            calls.append((method, url, kwargs["json"]))
            return httpx.Response(412, json={"errors": [{"error": "Неверная марка"}]}, request=httpx.Request(method, url))
        return await original(client, method, url, **kwargs)

    ms._request_with_retry = request
    result = await ms.update_document("demand", "doc", codes(1001))
    assert result["__moysklad_412__"] is True
    assert len([method for method, _, _ in calls if method == "POST"]) == 1

async def test_shipment_state_is_last_write_after_all_tracking_batches(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    ms.get_shipment_states = AsyncMock(return_value=[{'id': 'ready', 'name': 'Собран'}])
    await ms.update_document('demand', 'doc', codes(501), shipment_state_id='ready')
    assert [method for method, _, _ in calls if method != 'GET'] == ['PUT', 'POST', 'POST', 'PUT']
    assert calls[-1][2] == {'state': {'meta': {'href': ms.base_url + '/entity/demand/metadata/states/ready', 'type': 'state', 'mediaType': 'application/json'}}}
    assert 'state' not in calls[1][2] if calls[0][0] == 'GET' else 'state' not in calls[0][2]

async def test_rejected_marks_never_change_shipment_state(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    ms.get_shipment_states = AsyncMock(return_value=[{'id': 'ready', 'name': 'Собран'}])
    original = ms._request_with_retry
    async def request(client, method, url, **kwargs):
        response = await original(client, method, url, **kwargs)
        return httpx.Response(412, text='rejected', request=httpx.Request(method, url)) if method == 'POST' else response
    ms._request_with_retry = request
    result = await ms.update_document('demand', 'doc', codes(1), shipment_state_id='ready')
    assert result['__moysklad_412__']
    assert not any('state' in (body or {}) for _, _, body in calls)

async def test_unavailable_state_blocks_transfer_before_writes(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    ms.get_shipment_states = AsyncMock(return_value=[])
    with pytest.raises(ValueError, match='больше недоступен'):
        await ms.update_document('demand', 'doc', codes(1), shipment_state_id='removed')
    assert not calls

async def test_state_failure_reports_marks_saved_and_disabled_setting_preserves_state(monkeypatch):
    ms, calls = setup_ms(monkeypatch, [position()])
    ms.get_shipment_states = AsyncMock(return_value=[{'id': 'ready', 'name': 'Собран'}])
    original = ms._request_with_retry
    async def request(client, method, url, **kwargs):
        response = await original(client, method, url, **kwargs)
        return httpx.Response(403, request=httpx.Request(method, url)) if 'state' in kwargs.get('json', {}) else response
    ms._request_with_retry = request
    with pytest.raises(ValueError, match='Марки переданы'):
        await ms.update_document('demand', 'doc', codes(1), shipment_state_id='ready')
    assert any(method == 'POST' for method, _, _ in calls)
    calls.clear()
    await ms.update_document('demand', 'doc', codes(1))
    assert not any('state' in (body or {}) for _, _, body in calls)

async def test_shipment_status_setting_validates_live_choices_and_can_be_disabled(monkeypatch):
    from app.api.organization_profiles import save_shipment_status, ShipmentStateRequest
    import app.api.organization_profiles as profiles
    state = uuid4()
    profile = NS(shipment_sent_state_id=None)
    db = NS(commit=AsyncMock())
    monkeypatch.setattr(profiles, 'shipment_status_settings', AsyncMock(return_value={'states': [{'id': str(state), 'name': 'Собран'}]}))
    await save_shipment_status(ShipmentStateRequest(state_id=state), NS(), profile, db)
    assert profile.shipment_sent_state_id == state
    with pytest.raises(HTTPException) as exc:
        await save_shipment_status(ShipmentStateRequest(state_id=uuid4()), NS(), profile, db)
    assert exc.value.status_code == 400 and profile.shipment_sent_state_id == state
    await save_shipment_status(ShipmentStateRequest(), NS(), profile, db)
    assert profile.shipment_sent_state_id is None
