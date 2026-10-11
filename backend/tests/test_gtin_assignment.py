from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import IntegrityError
from app.worker import tasks
from app.api import scans
from app.services import gtin_product_store,moysklad
from tests.test_release_processing import Result

@pytest.mark.parametrize('remembered,saved', [('chosen',True),('other',True),('chosen',False)])
async def test_barcode_write_retries_failures_and_skips_outdated_assignments(monkeypatch,remembered,saved):
    import redis.asyncio as redis
    from app.db import session
    from app.core import security
    db=NS(execute=AsyncMock(return_value=Result(NS(moysklad_token='encrypted'))))
    context=AsyncMock();context.__aenter__.return_value=db
    monkeypatch.setattr(session,'AsyncSessionLocal',Mock(return_value=context))
    connection=NS(lock=Mock(return_value=AsyncMock()),aclose=AsyncMock())
    monkeypatch.setattr(redis,'from_url',Mock(return_value=connection))
    monkeypatch.setattr(gtin_product_store,'get_gtin_product',AsyncMock(return_value=(remembered,'Name')))
    monkeypatch.setattr(security,'decrypt_token',lambda value:'token')
    ms=NS(add_gtin_barcode_to_product=AsyncMock(return_value=saved))
    monkeypatch.setattr(moysklad,'MoySkladService',Mock(return_value=ms))
    user_id=str(uuid4())
    if not saved:
        with pytest.raises(ValueError,match='не сохранил'):
            await tasks._persist_assigned_gtin_async(user_id,'04620164405358','chosen')
    else:
        await tasks._persist_assigned_gtin_async(user_id,'04620164405358','chosen')
    if remembered=='chosen':ms.add_gtin_barcode_to_product.assert_awaited_once_with('chosen','04620164405358')
    else:ms.add_gtin_barcode_to_product.assert_not_awaited()
    connection.aclose.assert_awaited_once()

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
