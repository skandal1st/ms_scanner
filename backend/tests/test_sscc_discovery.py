from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api import scans
from app.api import tsd
from app.db.models import DocumentKind, DocumentStatus, ScanStatus
from app.services import sscc_discovery as discovery
from app.worker import tasks
from tests.test_release_processing import Result

GTIN = '04640275972501'
SSCC = '00046402759700345554'


@pytest.mark.parametrize('unpack', [False, True])
async def test_box_request_saves_pending_without_any_external_request(monkeypatch, unpack):
    cz = NS(get_sscc_info=AsyncMock(), unpack_box=AsyncMock())
    p = NS(id=uuid4(), status=ScanStatus.pending, duplicate=False)
    create = AsyncMock(return_value=(p, False))
    monkeypatch.setattr(scans, '_create_scan_record', create)
    monkeypatch.setattr(scans.ScanResponse, 'model_validate', Mock(return_value=p))
    uid, did, device = uuid4(), uuid4(), uuid4()
    result = await scans._create_box_scans_core(None, did, [GTIN], SSCC, unpack, uid, cz, device)
    assert result == [p]
    assert create.call_args.kwargs == {'is_box': True, 'device_id': device, 'sscc_unpack': unpack}
    cz.get_sscc_info.assert_not_awaited()
    cz.unpack_box.assert_not_awaited()


@pytest.mark.parametrize('alias', ['gtins', 'pack_gtins'])
async def test_box_plan_alias_prevents_global_wrong_product_lookup(alias):
    planned = {'product_id': 'peach-25g', 'gtin': '02000000071978', alias: [GTIN], 'product_name': 'Персик 25г'}
    p = NS(document_id=uuid4(), gtin=GTIN, moysklad_product_id=None, product_name=None)
    db = NS(execute=AsyncMock(return_value=Result(NS(plan=[planned]))))
    await tasks._enrich_scan_product_name_from_plan(db, p)
    assert p.moysklad_product_id == 'peach-25g'
    assert p.product_name == 'Персик 25г'


async def test_plan_does_not_replace_explicit_assignment():
    p = NS(document_id=uuid4(), gtin=GTIN, moysklad_product_id='manual', product_name='Ручной выбор')
    db = NS(execute=AsyncMock(return_value=Result(NS(plan=[{'product_id': 'other', 'gtins': [GTIN]}]))))
    await tasks._enrich_scan_product_name_from_plan(db, p)
    assert p.moysklad_product_id == 'manual'


async def test_sscc_is_committed_before_worker_is_queued(monkeypatch):
    doc = NS(kind=DocumentKind.demand, plan=[])
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    db = NS(execute=AsyncMock(return_value=Result(None)), add=Mock(),
            commit=AsyncMock(), refresh=AsyncMock())
    queue = Mock(side_effect=lambda *args: db.commit.assert_awaited_once())
    monkeypatch.setattr(tasks.discover_sscc_task, 'delay', queue)
    p, duplicate = await scans._create_scan_record(db, uuid4(), SSCC, uuid4(), is_box=True, sscc_unpack=False)
    assert not duplicate and p.status == ScanStatus.pending
    assert p.gtin is None and p.box_quantity is None
    assert p.verification['source'] == 'sscc_pending'
    assert tsd._scan_units(p) == 0
    queue.assert_called_once()


async def test_interrupted_whole_box_finalization_resumes_without_cz(monkeypatch):
    uid = uuid4()
    p = NS(is_box=True, box_quantity=20, status=ScanStatus.pending, box_expanded=False, document_id=uuid4())
    db = NS(get=AsyncMock(side_effect=[p, NS(user_id=uid, status=DocumentStatus.draft)]), rollback=AsyncMock())
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(discovery, 'AsyncSessionLocal', session)
    verify = AsyncMock()
    monkeypatch.setattr(tasks, '_verify_box_async', verify)
    cz = Mock()
    monkeypatch.setattr(discovery, 'ChestnyZnakService', cz)
    await discovery.discover_sscc('scan', str(uid))
    verify.assert_awaited_once_with('scan', str(uid))
    cz.assert_not_called()


@pytest.mark.parametrize('unpack', [False, True])
@pytest.mark.parametrize('outcome', ['ok', 'deleted', 'submitted', 'error', 'delivered_twice', 'duplicate_children'])
async def test_background_sscc_result_is_scoped_and_never_revives_deleted_scan(monkeypatch, unpack, outcome):
    uid, did = uuid4(), uuid4()
    doc = NS(id=did, user_id=uid, kind=DocumentKind.demand, status=DocumentStatus.draft,
             plan=[{'product_id': 'peach-25g', 'gtin': '02000000071978', 'gtins': [GTIN], 'product_name': 'Персик 25г'}])
    p = NS(id=uuid4(), document_id=did, code=SSCC, is_box=True, box_quantity=None,
           box_expanded=unpack, moysklad_product_id=None, status=ScanStatus.pending)
    final_doc = NS(**vars(doc))
    if outcome == 'submitted': final_doc.status = DocumentStatus.processing
    db = NS(get=AsyncMock(side_effect=[p, doc]), execute=AsyncMock(side_effect=[Result(final_doc), Result(None if outcome=='deleted' else p), Result(uuid4() if outcome=='duplicate_children' else None)]),
            rollback=AsyncMock(), commit=AsyncMock())
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(discovery, 'AsyncSessionLocal', session)
    monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value='fake'))
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['otp']))
    verify = AsyncMock()
    monkeypatch.setattr(tasks, '_verify_box_async', verify)
    children = ['01'+GTIN+'21FIRST', '01'+GTIN+'21SECOND']
    async def answer(*args):
        if outcome == 'error': raise TimeoutError()
        if outcome == 'delivered_twice': p.box_quantity = 2
        return children if unpack else NS(gtin=GTIN, quantity=20)
    cz = NS(unpack_box=AsyncMock(side_effect=answer), get_sscc_info=AsyncMock(side_effect=answer))
    monkeypatch.setattr(discovery, 'ChestnyZnakService', Mock(return_value=cz))
    published = AsyncMock()
    monkeypatch.setattr(discovery, 'publish_scan', published)
    await discovery.discover_sscc(str(p.id), str(uid))
    if outcome in {'deleted', 'submitted', 'delivered_twice'}:
        db.commit.assert_not_awaited()
        published.assert_not_awaited()
        verify.assert_not_awaited()
    elif outcome == 'error' or (outcome == 'duplicate_children' and unpack):
        assert p.status == ScanStatus.invalid and p.box_quantity is None
        assert ('уже отсканированы' if outcome == 'duplicate_children' else 'ЧЗ') in p.error_message
        verify.assert_not_awaited()
    else:
        assert p.moysklad_product_id == 'peach-25g'
        assert p.box_quantity == (2 if unpack else 20)
        assert p.child_codes == (children if unpack else None)
        assert p.status == (ScanStatus.scanned if unpack else ScanStatus.pending)
        assert verify.await_count == (0 if unpack else 1)
        published.assert_awaited_once()
        p.is_barcode = False
        rows = tasks._build_moysklad_scans_data([p], 'demand', {})
        assert [r['code'] for r in rows] == (children if unpack else [SSCC])
        assert sum(r['quantity'] for r in rows) == (2 if unpack else 20)
