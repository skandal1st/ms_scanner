from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from app.services import gtin_mapping_sync as service
from app.core.security import encrypt_token
from tests.test_release_processing import Result

GTIN='04640275972501'


def fixture(monkeypatch, **product_changes):
    uid,pid,rid,fid=uuid4(),uuid4(),uuid4(),uuid4()
    body=service.Selection(run_id=rid,finding_id=fid,target_product_id=pid)
    finding=NS(gtin=GTIN,candidates=[{'id':str(pid)}],saved_card=None)
    now=datetime.now(timezone.utc)
    mapping=NS(product_id='old',product_name='Old 200 g',updated_at=now)
    product={'id':str(pid),'name':'Peach 25 g','barcodes':[{'gtin':GTIN}],**product_changes}
    client=NS(get_product_by_id=AsyncMock(return_value=product))
    monkeypatch.setattr(service,'MoySkladService',Mock(return_value=client))
    db=NS(execute=AsyncMock(side_effect=[Result(finding),Result(mapping),Result(NS(moysklad_token=encrypt_token('fake')))]),
        rollback=AsyncMock(),commit=AsyncMock())
    return db,uid,body,client,mapping


@pytest.mark.parametrize('changes', [{'archived':True},{'barcodes':[]},{'id':str(uuid4())}])
async def test_removed_gtin_archived_or_wrong_card_never_changes_mapping(monkeypatch,changes):
    db,uid,body,client,mapping=fixture(monkeypatch,**changes)
    with pytest.raises(HTTPException) as err: await service.preview(db,uid,body)
    assert err.value.status_code==409
    db.commit.assert_not_awaited()


async def test_foreign_or_missing_report_is_rejected_before_ms(monkeypatch):
    client=Mock(); monkeypatch.setattr(service,'MoySkladService',client)
    db=NS(execute=AsyncMock(return_value=Result(None)))
    with pytest.raises(HTTPException) as err:
        await service.preview(db,uuid4(),service.Selection(run_id=uuid4(),finding_id=uuid4(),target_product_id=uuid4()))
    assert err.value.status_code==404
    client.assert_not_called()


async def test_target_not_in_snapshot_is_rejected(monkeypatch):
    db,uid,body,client,mapping=fixture(monkeypatch)
    body.target_product_id=uuid4()
    with pytest.raises(HTTPException) as err: await service.preview(db,uid,body)
    assert err.value.status_code==409
    client.get_product_by_id.assert_not_awaited()


async def test_preview_uses_live_card_and_reads_packs_without_writing(monkeypatch):
    db,uid,body,client,mapping=fixture(monkeypatch,barcodes=[],packs=[{'barcodes':[{'ean13':GTIN[1:]}]}])
    value=await service.preview(db,uid,body)
    assert value['before']['product_id']=='old' and value['target']['name']=='Peach 25 g'
    assert GTIN in value['target']['pack_gtins']
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.parametrize('stale', ['mapping','card_name','concurrent'])
async def test_stale_preview_never_overwrites_changed_data(monkeypatch,stale):
    db,uid,selection,client,mapping=fixture(monkeypatch)
    body=service.Confirmation(**selection.model_dump(),expected_product_id='changed' if stale=='mapping' else 'old',
        expected_product_name=mapping.product_name,expected_updated_at=mapping.updated_at,
        target_name='Different' if stale=='card_name' else 'Peach 25 g')
    if stale=='concurrent':
        db.execute.side_effect=[Result(NS(gtin=GTIN,candidates=[{'id':str(body.target_product_id)}],saved_card=None)),
            Result(mapping),Result(NS(moysklad_token=encrypt_token('fake'))),Result(None)]
    with pytest.raises(HTTPException) as err: await service.apply(db,uid,body)
    assert err.value.status_code==409
    db.commit.assert_not_awaited()
