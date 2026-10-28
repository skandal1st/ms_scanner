from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import IntegrityError
from app.worker import tasks
from app.api import scans
from app.services import gtin_product_store,moysklad
from tests.test_release_processing import Result
import httpx

@pytest.mark.parametrize('remembered,saved', [('chosen',True),('other',True),('chosen',False)])
async def test_barcode_write_retries_failures_and_skips_outdated_assignments(monkeypatch,remembered,saved):
    import redis.asyncio as redis
    from app.db import session
    from app.core import security
    user_id=str(uuid4()); document_id=str(uuid4())
    db=NS(execute=AsyncMock(return_value=Result(NS(moysklad_token='encrypted'))),
          get=AsyncMock(return_value=NS(user_id=user_id)))
    context=AsyncMock();context.__aenter__.return_value=db
    monkeypatch.setattr(session,'AsyncSessionLocal',Mock(return_value=context))
    connection=NS(lock=Mock(return_value=AsyncMock()),aclose=AsyncMock())
    monkeypatch.setattr(redis,'from_url',Mock(return_value=connection))
    monkeypatch.setattr(gtin_product_store,'get_gtin_product',AsyncMock(return_value=(remembered,'Name')))
    monkeypatch.setattr(security,'decrypt_token',lambda value:'token')
    ms=NS(add_gtin_barcode_to_product=AsyncMock(return_value=saved),get_product_by_id=AsyncMock(return_value={'barcodes': []}))
    monkeypatch.setattr(moysklad,'MoySkladService',Mock(return_value=ms))
    if not saved:
        with pytest.raises(ValueError,match='не сохранил'):
            await tasks._persist_assigned_gtin_async(user_id,'04620164405358','chosen',document_id)
    else:
        await tasks._persist_assigned_gtin_async(user_id,'04620164405358','chosen',document_id)
    if remembered=='chosen':ms.add_gtin_barcode_to_product.assert_awaited_once_with('chosen','04620164405358',expected_gtins=[])
    else:ms.add_gtin_barcode_to_product.assert_not_awaited()
    connection.aclose.assert_awaited_once()


@pytest.mark.parametrize('confirmed,write', [(None, False), (['04620543080503'], True), (['04620543080504'], False)])
async def test_existing_gtin_requires_confirmation_of_current_card(monkeypatch, confirmed, write):
    import redis.asyncio as redis
    from app.db import session
    from app.core import security
    from app.services import scan_events
    user_id=str(uuid4()); document_id=str(uuid4())
    db=NS(execute=AsyncMock(return_value=Result(NS(moysklad_token='encrypted'))),get=AsyncMock(return_value=NS(user_id=user_id)))
    context=AsyncMock();context.__aenter__.return_value=db
    monkeypatch.setattr(session,'AsyncSessionLocal',Mock(return_value=context))
    connection=NS(lock=Mock(return_value=AsyncMock()),aclose=AsyncMock())
    monkeypatch.setattr(redis,'from_url',Mock(return_value=connection))
    monkeypatch.setattr(gtin_product_store,'get_gtin_product',AsyncMock(return_value=('chosen','Name')))
    monkeypatch.setattr(security,'decrypt_token',lambda value:'token')
    ms=NS(add_gtin_barcode_to_product=AsyncMock(return_value=True),get_product_by_id=AsyncMock(return_value={'name':'Name','barcodes':[{'ean13':'4620543080503'}]}))
    monkeypatch.setattr(moysklad,'MoySkladService',Mock(return_value=ms))
    event=AsyncMock();monkeypatch.setattr(scan_events,'publish_event',event)
    await tasks._persist_assigned_gtin_async(user_id,'04620164405358','chosen',document_id,confirmed)
    assert ms.add_gtin_barcode_to_product.await_count == int(write)
    assert event.await_count == int(not write)
    if not write:
        assert event.await_args.args[1]['existing_gtins']==['04620543080503']


async def test_legacy_assignment_tasks_do_not_write_without_origin(monkeypatch):
    factory=Mock();monkeypatch.setattr(moysklad,'MoySkladService',factory)
    await tasks._persist_assigned_gtin_async(str(uuid4()),'04620164405358','chosen')
    factory.assert_not_called()


@pytest.mark.parametrize('expected,writes', [([], 0), (['04620543080503'], 1)])
async def test_card_change_after_confirmation_prevents_overwriting_barcodes(monkeypatch, expected, writes):
    import json
    calls=[]
    original=[{'ean13':'4620543080503'}, {'code128':'article-123'}]
    def request(req):
        calls.append(req)
        return httpx.Response(200,json={'barcodes':original} if req.method=='GET' else {})
    factory=httpx.AsyncClient
    monkeypatch.setattr(moysklad.httpx,'AsyncClient',lambda **kwargs:factory(transport=httpx.MockTransport(request),**kwargs))
    assert await moysklad.MoySkladService('fake').add_gtin_barcode_to_product('p','04620164405358',expected_gtins=expected) is bool(writes)
    puts=[r for r in calls if r.method=='PUT']
    assert len(puts)==writes
    if writes:
        assert json.loads(puts[0].content)['barcodes']==original+[{'gtin':'04620164405358'}]


async def test_terminal_credentials_cannot_confirm_product_card_writes(monkeypatch):
    from app.api import deps
    from fastapi import HTTPException
    monkeypatch.setattr(deps,'decode_token',lambda value:{'type':'tsd_access','sub':str(uuid4())})
    db=NS(execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await deps.get_current_user(NS(credentials='terminal'),db)
    assert error.value.status_code==401
    db.execute.assert_not_awaited()

@pytest.mark.parametrize('duplicate', [False,True])
async def test_invalid_and_duplicate_scans_never_change_mapping(monkeypatch,duplicate):
    doc=NS(kind='demand',plan=[{'product_id':'chosen','product_name':'Name'}])
    monkeypatch.setattr(scans,'editable_document',AsyncMock(return_value=doc))
    remember=AsyncMock();queue=Mock()
    monkeypatch.setattr(gtin_product_store,'remember_gtin_product',remember)
    monkeypatch.setattr(tasks.persist_assigned_gtin_task,'delay',queue)
    raw='010462016440535821TEST000000001\x1d93TEST' if duplicate else '010462016440535921TEST000000001\x1d93TEST'
    existing=NS(id=uuid4(),moysklad_product_id='original')
    db=NS(execute=AsyncMock(side_effect=[Result(None),Result(existing)]),add=Mock(),commit=AsyncMock(),refresh=AsyncMock(),rollback=AsyncMock(),flush=AsyncMock())
    if duplicate:db.flush.side_effect=IntegrityError('duplicate',{},Exception())
    _,is_duplicate=await scans._create_scan_record(db,uuid4(),raw,uuid4(),moysklad_product_id='chosen')
    assert is_duplicate==duplicate
    remember.assert_not_awaited();queue.assert_not_called()
