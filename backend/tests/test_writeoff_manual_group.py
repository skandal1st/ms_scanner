import base64
import json
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import httpx
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from app.api import integrations
from app.db.models import DocumentKind, DocumentStatus
from app.services import document_guard
from app.services.chestnyznak import ChestnyZnakService
from tests.test_release_processing import Result


def setup(monkeypatch, count=587):
    doc=NS(id=uuid4(),kind=DocumentKind.loss,status=DocumentStatus.draft,cz_doc_ids=None,writeoff_reason=None)
    user=NS(id=uuid4())
    codes=[f'04640275972501S{i:06d}' for i in range(count)]
    scans=[NS(code=c,child_codes=None) for c in codes]
    db=NS(execute=AsyncMock(return_value=Result(scans)),commit=AsyncMock())
    monkeypatch.setattr(document_guard,'editable_document',AsyncMock(return_value=doc))
    monkeypatch.setattr(integrations,'_cz_source_for_document',AsyncMock(return_value=NS(
        cz_token='encrypted',cz_token_expires_at=None,cz_inn='1234567890',cz_product_groups=['tobacco','otp'])))
    monkeypatch.setattr(integrations,'decrypt_token',lambda token:'fixture')
    cache=NS(set=AsyncMock(),aclose=AsyncMock())
    monkeypatch.setattr(integrations.aioredis,'from_url',lambda *args,**kwargs:cache)
    resolver=AsyncMock(side_effect=AssertionError('manual group must not query CZ'))
    submit=AsyncMock(side_effect=AssertionError('prepare must not submit writeoff'))
    cz=NS(writeoff_places=AsyncMock(return_value=[{"fiasId":"address-1","kpp":None,"productGroups":[]}]),
        resolve_product_group=resolver,build_writeoff_document=ChestnyZnakService.build_writeoff_document,
        encode_product_document=ChestnyZnakService.encode_product_document,submit_document=submit)
    monkeypatch.setattr(integrations,'ChestnyZnakService',lambda **kwargs:cz)
    return doc,user,codes,db,cache,cz


async def test_587_marks_with_explicit_group_have_zero_discovery_requests(monkeypatch):
    doc,user,codes,db,cache,cz=setup(monkeypatch)
    manual=AsyncMock(side_effect=AssertionError('document selection must not overwrite global rules'))
    monkeypatch.setattr(integrations,'set_manual_group',manual)
    result=await integrations.cz_writeoff_prepare(integrations.WriteoffPrepareRequest(
        document_id=doc.id,reason='own_use',product_group=' OTP ',basis_number='QA-1'),user,db)
    assert len(result.parts)==1 and result.parts[0].pg=='otp' and not result.unresolved
    payload=json.loads(base64.b64decode(result.parts[0].product_document_b64))
    assert payload['fias_id'] == 'address-1' and 'fiasId' not in payload and 'kpp' not in payload
    assert [r['cis'] for r in payload['products']]==codes
    assert payload['action']=='OWN_USE' and payload['document_number']=='QA-1'
    stored=json.loads(cache.set.await_args.args[1])
    assert set(stored['parts'])=={'otp'} and len(stored['scan_codes'])==587
    assert cache.set.await_args.kwargs['ex']==integrations.WRITEOFF_TTL_SECONDS
    cz.resolve_product_group.assert_not_awaited(); cz.submit_document.assert_not_awaited(); manual.assert_not_awaited()
    assert doc.status==DocumentStatus.draft and doc.cz_doc_ids is None


async def test_auto_mode_keeps_per_code_grouping_and_unresolved_results(monkeypatch):
    doc,user,codes,db,cache,cz=setup(monkeypatch,3)
    cz.resolve_product_group.side_effect=[('otp',None),('tobacco',None),(None,'Не определена группа')]
    result=await integrations.cz_writeoff_prepare(integrations.WriteoffPrepareRequest(document_id=doc.id,reason='own_use'),user,db)
    assert {p.pg for p in result.parts}=={'otp','tobacco'}
    assert len(result.unresolved)==1 and result.unresolved[0].cis==codes[2]
    assert cz.resolve_product_group.await_count==3


def test_unknown_manual_group_is_rejected_and_empty_selection_means_auto():
    with pytest.raises(ValidationError):
        integrations.WriteoffPrepareRequest(document_id=uuid4(),reason='own_use',product_group='unknown')
    assert integrations.WriteoffPrepareRequest(document_id=uuid4(),reason='own_use',product_group=' ').product_group is None


async def test_explicit_group_does_not_bypass_submitted_document_guard(monkeypatch):
    doc,user,codes,db,cache,cz=setup(monkeypatch)
    doc.cz_doc_ids=[{'doc_id':'already-submitted'}]
    with pytest.raises(HTTPException) as err:
        await integrations.cz_writeoff_prepare(integrations.WriteoffPrepareRequest(document_id=doc.id,reason='own_use',product_group='otp'),user,db)
    assert err.value.status_code==409
    cache.set.assert_not_awaited(); cz.resolve_product_group.assert_not_awaited()

@pytest.mark.parametrize("choice", [None, "unknown", "second"])
async def test_multiple_places_require_registered_choice_in_each_signed_part(monkeypatch, choice):
    doc, user, _, db, cache, cz = setup(monkeypatch, 2)
    cz.resolve_product_group.side_effect = [('otp', None), ('tobacco', None)]
    cz.writeoff_places.return_value = [
        {"fiasId": key, "kpp": "123456789", "productGroups": ["otp", "tobacco"]}
        for key in ("first", "second")
    ]
    request = integrations.WriteoffPrepareRequest(document_id=doc.id, reason="own_use",
                                                  fias_id=choice, kpp="123456789" if choice else None)
    if choice != "second":
        with pytest.raises(HTTPException):
            await integrations.cz_writeoff_prepare(request, user, db)
        cache.set.assert_not_awaited()
        return
    result = await integrations.cz_writeoff_prepare(request, user, db)
    assert len(result.parts) == 2
    for part in result.parts:
        payload = json.loads(base64.b64decode(part.product_document_b64))
        assert payload["fias_id"] == choice and payload["kpp"] == "123456789"


@pytest.mark.parametrize(("group", "reason"), [
    ("otp", "own_use"), ("tobacco", "production"), ("ncp", "spoilage"),
])
async def test_tobacco_writeoff_requires_registered_place_before_signing(monkeypatch, group, reason):
    doc, user, _, db, cache, cz = setup(monkeypatch, 1)
    cz.writeoff_places.return_value = []
    request = integrations.WriteoffPrepareRequest(document_id=doc.id, reason=reason, product_group=group)
    with pytest.raises(HTTPException) as error:
        await integrations.cz_writeoff_prepare(request, user, db)
    assert error.value.status_code == 400 and "МОД" in error.value.detail
    cache.set.assert_not_awaited()
    db.commit.assert_not_awaited()

async def test_places_read_every_page_for_document_inn_and_group(monkeypatch):
    from app.services import cz_logger
    inn = "123456789012"
    pages = []
    def handle(request):
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.url.path.endswith("/api/v3/true-api/mods/list")
        assert request.url.params["inns"] == inn and request.url.params["productGroups"] == "otp"
        assert request.headers["Authorization"] == "Bearer fixture"
        point = {"inn": inn, "address": "Адрес", "fiasId": "a", "productGroups": ["otp"]}
        rows = [point] if page == 0 else [point, {**point, "fiasId": "b"}, {**point, "inn": "other", "fiasId": "c"}]
        return httpx.Response(200, json={"result": rows, "nextPage": page == 0})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(cz_logger, "log_cz_request", AsyncMock())
    places = await ChestnyZnakService(token="fixture", mock=False).writeoff_places(inn, "otp")
    assert pages == [0, 1]
    assert [p["fiasId"] for p in places] == ["a", "b"]
