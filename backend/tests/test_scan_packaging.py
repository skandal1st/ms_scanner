from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from app.api import scans
from app.db.models import Scan, ScanStatus, DocumentStatus
from app.services.scan_packaging import package_type, scan_package_type
from app.services.legacy_pack_plan import refresh_legacy_pack_plan
from app.worker import tasks
from tests.test_release_processing import Result


def pack(**changes):
    return NS(id=uuid4(), document_id=uuid4(), status=ScanStatus.valid, is_box=False,
        is_barcode=False, box_quantity=10, child_codes=None, keep_aggregate=True,
        package_type='GROUP', code='010466051533035921TEST\x1d93TEST', gtin='04660515330359',
        moysklad_product_id='flavor', product_name='Аромат', error_message=None, **changes)


def test_group_and_transport_pack_are_distinct():
    assert package_type('LEVEL1') == 'GROUP'
    assert package_type('LEVEL2') == 'BOX'
    assert scan_package_type(pack()) == 'GROUP'
    p = pack()
    p.child_codes = ['010468061741991221FIRST', '010468061741991221SECOND']
    whole = tasks._build_moysklad_scans_data([p], 'demand', {})
    assert len(whole) == 1 and whole[0]['quantity'] == 10 and not whole[0]['is_box']
    p.keep_aggregate = False
    children = tasks._build_moysklad_scans_data([p], 'demand', {})
    assert len(children) == 2 and all(row['quantity'] == 1 for row in children)


async def test_pack_mode_with_known_children_never_calls_cz(monkeypatch):
    p = pack()
    p.child_codes = ['child']
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    db = NS(commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(side_effect=lambda s: s))
    await scans.set_scan_pack_mode(db, p, uuid4(), True)
    assert not p.keep_aggregate and p.child_codes == ['child']
    await scans.set_scan_pack_mode(db, p, uuid4(), False)
    assert p.keep_aggregate and p.box_quantity == 10


async def test_pack_mode_queues_work_without_external_api(monkeypatch):
    p = pack()
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    queue = Mock()
    monkeypatch.setattr(tasks.unpack_scan_task, 'delay', queue)
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(side_effect=lambda s: s))
    db = NS(commit=AsyncMock(), refresh=AsyncMock())
    await scans.set_scan_pack_mode(db, p, uuid4(), True)
    assert p.status == ScanStatus.pending and p.keep_aggregate
    queue.assert_called_once()


async def test_unit_and_frozen_document_cannot_change_pack_mode(monkeypatch):
    guard = AsyncMock(side_effect=HTTPException(409, 'Документ уже отправлен'))
    monkeypatch.setattr(scans, 'editable_document', guard)
    with pytest.raises(HTTPException):
        await scans.set_scan_pack_mode(NS(), pack(), uuid4(), True)
    guard.side_effect = None
    p = pack()
    p.box_quantity = 1
    p.package_type = 'UNIT'
    with pytest.raises(HTTPException) as error:
        await scans.set_scan_pack_mode(NS(refresh=AsyncMock()), p, uuid4(), True)
    assert error.value.status_code == 400


@pytest.mark.parametrize('children', [[], ['first', 'second']])
async def test_unpack_task_keeps_whole_pack_until_composition_received(monkeypatch, children):
    p = pack()
    p.status = ScanStatus.pending
    db = NS(execute=AsyncMock(side_effect=[Result(p), Result(NS(status=DocumentStatus.draft)), Result(p)]),
            rollback=AsyncMock(), commit=AsyncMock())
    @asynccontextmanager
    async def session():
        yield db
    import app.db.session
    monkeypatch.setattr(app.db.session, 'AsyncSessionLocal', session)
    monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value='fake'))
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['tobacco']))
    monkeypatch.setattr(tasks, 'ChestnyZnakService', Mock(return_value=NS(
        get_code_info=AsyncMock(return_value=NS(children=children, package_type='GROUP')))))
    monkeypatch.setattr(tasks, '_push_ws_update', AsyncMock())
    await tasks._unpack_scan_async(str(p.id), str(uuid4()), 'valid')
    assert p.status == ScanStatus.valid
    if children:
        assert p.child_codes == children and p.box_quantity == 2 and not p.keep_aggregate
    else:
        assert p.keep_aggregate and p.box_quantity == 10 and p.error_message


async def test_opening_legacy_plan_updates_pack_metadata_and_existing_counts():
    p = pack()
    p.box_quantity = None
    p.package_type = None
    p.keep_aggregate = False
    doc = NS(id=p.document_id, status=DocumentStatus.draft, moysklad_id='ms-doc',
             kind=NS(value='demand'), plan=[{'product_id': 'flavor', 'pack_gtins': [p.gtin]}])
    fresh = [{'product_id': 'flavor', 'pack_gtins': [p.gtin], 'pack_quantities': {p.gtin: 10}}]
    ms = NS(build_plan=AsyncMock(return_value=fresh))
    result = NS(scalars=lambda: NS(all=lambda: [p]))
    db = NS(execute=AsyncMock(side_effect=[Result(doc), result]), commit=AsyncMock())
    await refresh_legacy_pack_plan(db, doc, ms)
    assert p.box_quantity == 10 and p.package_type == 'GROUP' and p.keep_aggregate
    await refresh_legacy_pack_plan(db, doc, ms)
    ms.build_plan.assert_awaited_once()


@pytest.mark.parametrize('aggregate_found', [True, False])
async def test_cz_pack_type_fetches_composition_without_child_field(monkeypatch, aggregate_found):
    import httpx
    from app.services.chestnyznak import ChestnyZnakService
    import app.services.cz_logger as cz_logger
    import app.services.cz_pg_cache as cache
    raw = '010466051533035921TEST'
    post = AsyncMock(side_effect=[httpx.Response(200, json=[{'cisInfo': {
        'cis': raw, 'generalPackageType': 'GROUP', 'gtin': '04660515330359'}}]),
        httpx.Response(200, json={raw: ['child-a', 'child-b']} if aggregate_found else {})])
    @asynccontextmanager
    async def client(**kwargs):
        yield NS(post=post)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    monkeypatch.setattr(cz_logger, 'log_cz_request', AsyncMock())
    monkeypatch.setattr(cache, 'get_cached_pg', AsyncMock(return_value=None))
    monkeypatch.setattr(cache, 'set_cached_pg', AsyncMock())
    service = ChestnyZnakService(token='fake', mock=False, product_groups=['tobacco'])
    info = await service.get_code_info(raw)
    assert info.package_type == 'GROUP'
    assert post.await_count == 2
    assert info.children == (['child-a', 'child-b'] if aggregate_found else [])
