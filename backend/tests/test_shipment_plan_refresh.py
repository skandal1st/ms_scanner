from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.db.models import DocumentKind,DocumentStatus,ScanStatus
from app.services import shipment_plan_refresh as service
from tests.test_release_processing import Result


def row(pid,qty,**kw):
    return {'product_id':pid,'product_name':pid,'expected_qty':qty,'gtin':'04640275972501',**kw}


def test_diff_uses_product_identity_sums_duplicate_positions_and_detects_alias_changes():
    old=[row('a',2),row('a',3),row('removed',4)]
    new=[row('a',6),row('added',2)]
    result={r['product_id']:r for r in service.changes(old,new)}
    assert result['a']['before']==5 and result['a']['after']==6
    assert result['removed']['kind']=='removed' and result['added']['kind']=='added'
    assert service.changes([row('a',3)],[row('a',3,gtins=['04650300879569'])])[0]['kind']=='details'
    assert not service.changes(new,list(reversed(new)))


def test_reclassifies_quantity_only_preserving_cz_facts_and_explicit_product_identity():
    now=datetime.now(timezone.utc)
    def mark(pid,index,**changes):
        return NS(id=str(index),scanned_at=now+timedelta(seconds=index),moysklad_product_id=pid,
            gtin='04640275972501',status=changes.get('status',ScanStatus.valid),
            box_quantity=changes.get('box_quantity'),error_message=changes.get('error_message'),
            verification={'source':'cz_info','checked_at':'before'},child_codes=['unchanged'])
    a,b,c=mark('a',1),mark('a',2,box_quantity=10),mark('removed',3)
    pending=mark('a',4,status=ScanStatus.pending)
    service.reclassify([row('a',5)],[a,b,c,pending])
    assert a.status==ScanStatus.valid and b.status==ScanStatus.overflow
    assert c.status==ScanStatus.valid and pending.status==ScanStatus.pending
    assert b.box_quantity==10 and b.child_codes==['unchanged'] and b.verification['checked_at']=='before'
    service.reclassify([row('a',20)],[a,b,c,pending])
    assert b.status==ScanStatus.valid and b.error_message is None


@pytest.mark.parametrize('case',['ok','invoice','wrong_invoice','different_order','ms_failure','deleted','empty','scope_changed','finished','concurrent'])
async def test_refresh_preserves_scans_releases_lock_before_io_and_rechecks_editability(monkeypatch,case):
    did,uid=uuid4(),uuid4()
    doc=NS(id=did,user_id=uid,kind=DocumentKind.demand,status=DocumentStatus.draft,moysklad_id='ms',
        moysklad_organization_id='org',moysklad_store_id='store',moysklad_customer_order_id=None,
        plan=[row('a',2)],upd_meta={})
    db=NS(rollback=AsyncMock(),execute=AsyncMock(return_value=Result([])),commit=AsyncMock())
    if case in ('invoice','wrong_invoice','different_order'):
        doc.moysklad_customer_order_id='order'
    guard=AsyncMock(side_effect=[doc,HTTPException(409,'Документ завершён')] if case=='finished' else None,
                    return_value=doc)
    monkeypatch.setattr(service,'editable_document',guard)
    async def get_document(*args):
        db.rollback.assert_awaited_once()
        if case=='ms_failure': raise TimeoutError()
        return {'name':'New name','deleted':case=='deleted','organization':{'id':'wrong' if case=='scope_changed' else 'org'},'store':{'id':'store'},
                'customerOrder':{'id':'other'} if case=='different_order' else None,
                'invoicesOut':[{'id':'invoice'}] if case in ('invoice','wrong_invoice') else []}
    async def build_plan(*args):
        if case=='concurrent': doc.upd_meta={'plan_sync':{'checked_at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()}}
        return [] if case=='empty' else [row('a',4)]
    ms=NS(get_document=AsyncMock(side_effect=get_document),build_plan=AsyncMock(side_effect=build_plan))
    ms.get_customer_order=AsyncMock(return_value={'invoicesOut':[{'id':'other' if case=='wrong_invoice' else 'invoice'}]})
    monkeypatch.setattr(service,'publish_event',AsyncMock())
    if case in ('ok','invoice'):
        result=await service.refresh(db,did,uid,ms)
        assert result.plan[0]['expected_qty']==4 and result.moysklad_name=='New name'
        assert result.upd_meta['plan_sync']['changes'][0]['after']==4
        db.commit.assert_awaited_once(); service.publish_event.assert_awaited_once()
    else:
        with pytest.raises(HTTPException): await service.refresh(db,did,uid,ms)
        assert doc.plan==[row('a',2)]
        db.commit.assert_not_awaited(); service.publish_event.assert_not_awaited()
    assert ms.get_customer_order.await_count == int(case in ('invoice','wrong_invoice'))
