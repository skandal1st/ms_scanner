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
    values = dict(id=uuid4(), document_id=uuid4(), status=ScanStatus.valid, is_box=False,
        is_barcode=False, box_quantity=10, child_codes=None, keep_aggregate=True,
        box_expanded=False,
        package_type='GROUP', code='010466051533035921TEST\x1d93TEST', gtin='04660515330359',
        moysklad_product_id='flavor', product_name='Аромат', error_message=None)
    values.update(changes)
    return NS(**values)


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


@pytest.mark.parametrize('is_box', [False, True])
async def test_pack_mode_with_known_children_never_calls_cz(monkeypatch, is_box):
    p = pack(is_box=is_box, package_type='BOX' if is_box else 'GROUP')
    p.child_codes = ['child']
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    db = NS(commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(side_effect=lambda s: s))
    await scans.set_scan_pack_mode(db, p, uuid4(), True)
    assert not p.keep_aggregate and p.child_codes == ['child']
    assert p.box_expanded == is_box
    await scans.set_scan_pack_mode(db, p, uuid4(), False)
    assert p.keep_aggregate and p.box_quantity == (1 if is_box else 10)
    assert not p.box_expanded and p.is_box == is_box


@pytest.mark.parametrize('is_box', [False, True])
async def test_pack_mode_queues_work_without_external_api(monkeypatch, is_box):
    p = pack(is_box=is_box, package_type='BOX' if is_box else 'GROUP')
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    queue = Mock()
    monkeypatch.setattr(tasks.unpack_scan_task, 'delay', queue)
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(side_effect=lambda s: s))
    db = NS(commit=AsyncMock(), refresh=AsyncMock())
    await scans.set_scan_pack_mode(db, p, uuid4(), True)
    assert p.status == ScanStatus.pending and p.keep_aggregate
    assert not p.box_expanded
    queue.assert_called_once()


async def test_sscc_cached_composition_switches_ms_payload_and_keeps_identity(monkeypatch):
    from app.services.moysklad import MoySkladService
    p = pack(is_box=True, keep_aggregate=False, package_type='BOX', code='00046402759700334510',
             child_codes=['010464027597309621UNIT001', '010464027597309621UNIT002', '010464027597309621UNIT001'])
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(side_effect=lambda s: s))
    db = NS(commit=AsyncMock(), refresh=AsyncMock())
    # Legacy boxes have keep_aggregate=False but must stay intact until explicitly opened.
    assert tasks._build_moysklad_scans_data([p], 'demand', {})[0]['is_box']
    await scans.set_scan_pack_mode(db, p, uuid4(), True)
    rows = tasks._build_moysklad_scans_data([p], 'demand', {})
    assert p.is_box and p.code == '00046402759700334510' and p.box_quantity == 2
    assert [r['code'] for r in rows] == p.child_codes and len(rows) == 2
    assert all(not r['is_box'] and r['quantity'] == 1 for r in rows)
    ms = MoySkladService('fake')
    assert all(ms._tracking_code_entry(row, 'TOBACCO')['type'] == 'trackingcode' for row in rows)
    await scans.set_scan_pack_mode(db, p, uuid4(), False)
    rows = tasks._build_moysklad_scans_data([p], 'demand', {})
    assert len(rows) == 1 and rows[0]['quantity'] == 2
    assert ms._tracking_code_entry(rows[0], 'TOBACCO')['type'] == 'transportpack'


async def test_sscc_queue_failure_keeps_whole_box(monkeypatch):
    p = pack(is_box=True, package_type='BOX')
    monkeypatch.setattr(scans, 'editable_document', AsyncMock())
    monkeypatch.setattr(tasks.unpack_scan_task, 'delay', Mock(side_effect=RuntimeError('broker unavailable')))
    with pytest.raises(HTTPException) as error:
        await scans.set_scan_pack_mode(NS(commit=AsyncMock(), refresh=AsyncMock()), p, uuid4(), True)
    assert error.value.status_code == 503 and p.status == ScanStatus.valid
    assert not p.box_expanded and p.is_box and p.box_quantity == 10


def test_expanded_sscc_without_contents_cannot_silently_send_whole_box():
    with pytest.raises(ValueError, match='Состав раскрытого SSCC'):
        tasks._build_moysklad_scans_data([pack(is_box=True, box_expanded=True)], 'demand', {})


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
@pytest.mark.parametrize('is_box', [False, True])
async def test_unpack_task_keeps_whole_pack_until_composition_received(monkeypatch, children, is_box):
    p = pack(is_box=is_box, package_type='BOX' if is_box else 'GROUP',
             code='00046402759700334510' if is_box else '010466051533035921TEST\x1d93TEST')
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
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=children, package_type='GROUP')),
            unpack_box=AsyncMock(return_value=children))
    monkeypatch.setattr(tasks, 'ChestnyZnakService', Mock(return_value=cz))
    monkeypatch.setattr(tasks, '_push_ws_update', AsyncMock())
    await tasks._unpack_scan_async(str(p.id), str(uuid4()), 'valid')
    assert p.status == ScanStatus.valid
    if is_box:
        cz.unpack_box.assert_awaited_once_with(p.code)
        cz.get_code_info.assert_not_awaited()
    if children:
        assert p.child_codes == children and p.box_quantity == 2 and not p.keep_aggregate
        assert p.box_expanded == is_box
        update = tasks._push_ws_update.call_args.kwargs
        assert update['box_expanded'] == is_box and update['is_box'] == is_box
    else:
        assert p.keep_aggregate and p.box_quantity == 10 and p.error_message
        assert not p.box_expanded


async def test_opening_legacy_plan_never_reclassifies_existing_marks_from_ms_packs():
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
    assert doc.plan == fresh
    assert p.box_quantity is None and p.package_type is None and not p.keep_aggregate
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


@pytest.mark.parametrize('pg', ['tobacco', 'otp', 'ncp', 'antiseptic'])
@pytest.mark.parametrize('brackets', [False, True])
def test_ai02_logistics_identifier_is_never_shortened_as_a_unit_mark(pg, brackets):
    from app.services.chestnyznak import cis_for_cz_info, cis_string_for_moysklad_api
    from app.services.moysklad import MoySkladService
    raw = '020080208412846113260930102MSCS21110429237250'
    displayed = '(02)00802084128461(13)260930(10)2MSCS(21)1104292(37)250'
    assert cis_for_cz_info(displayed if brackets else raw, pg) == raw
    value = displayed if brackets else raw
    assert cis_string_for_moysklad_api(value, pg.upper()) == raw
    ms = MoySkladService('fake')
    assert ms._tracking_code_entry({'code': value, 'is_box': False}, pg.upper()) == {'cis': raw, 'type': 'trackingcode'}
    assert ms._cis_dedup_key(value) == raw


async def test_ai02_box_fetches_all_250_leaves_with_original_identifier(monkeypatch):
    import httpx
    from app.services.chestnyznak import ChestnyZnakService
    from app.services import cz_logger, cz_pg_cache
    raw = '020080208412846113260930102MSCS21110429237250'
    children = [f'010080208412846121{i:07d}' for i in range(250)]
    tree = {f'block-{i}': children[i * 10:(i + 1) * 10] for i in range(25)}
    post = AsyncMock(side_effect=[httpx.Response(200, json=[{'cisInfo': {
        'cis': raw, 'generalPackageType': 'BOX', 'child': list(tree)}}]),
        httpx.Response(200, json={raw: tree})])
    @asynccontextmanager
    async def client(**kwargs):
        yield NS(post=post)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    log = AsyncMock(); monkeypatch.setattr(cz_logger, 'log_cz_request', log)
    cached = AsyncMock(return_value='otp'); monkeypatch.setattr(cz_pg_cache, 'get_cached_pg', cached)
    saved = AsyncMock(); monkeypatch.setattr(cz_pg_cache, 'set_cached_pg', saved)
    info = await ChestnyZnakService(token='fake', mock=False, product_groups=['otp']).get_code_info(raw)
    assert info.package_type == 'BOX' and info.children == children
    assert post.await_count == 2
    assert all(call.kwargs['json'] == [raw] for call in post.await_args_list)
    cached.assert_awaited_once_with('00802084128461')
    saved.assert_awaited_once_with('00802084128461', 'otp')
    assert all(call.kwargs['original_code'] == raw for call in log.await_args_list)


def test_unit_mark_normalization_still_removes_only_the_known_crypto_tail():
    from app.services.chestnyznak import cis_for_cz_info
    compact = '04620164405358TEST0010000ABCD'
    assert len(compact) == 29
    assert cis_for_cz_info(compact, 'otp') == compact[:21]
