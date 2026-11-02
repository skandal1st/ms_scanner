import base64
import json
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
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
    cz=NS(resolve_product_group=resolver,build_writeoff_document=ChestnyZnakService.build_writeoff_document,
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
