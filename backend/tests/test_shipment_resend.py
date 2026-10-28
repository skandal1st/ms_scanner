import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.db.models import Scan, ScanStatus, DocumentStatus
from app.services import shipment_resend as service
from app.services.shipment_resend import assert_idle as real_assert_idle
from app.services.moysklad import MoySkladService
from tests.test_release_processing import Result


def code(i):
    return f'010464019551637321S{i:06d}'


def row(codes, product='product', quantity=115):
    return {'fields': {'id': 'position', 'quantity': quantity, 'price': 12500, 'vat': 22},
            'codes': [{'id': f'cis-{i}', 'cis': c, 'type': 'trackingcode'} for i, c in enumerate(codes)],
            'product_id': product, 'product_name': 'Товар', 'tracking_type': 'OTP'}


def fixture(monkeypatch, expected=115, existing=59, *, drop=False, timeout=False):
    ms = MoySkladService('fake')
    ms.rows = {'position': row([code(i) for i in range(existing)], quantity=expected)}
    desired = [{'code': code(i), 'product_id': 'product', 'quantity': 1} for i in range(expected)]
    doc = NS(moysklad_id='shipment', moysklad_store_id=None, moysklad_organization_id=None)
    async def snapshot(*args):
        return {'name': '167366'}, copy.deepcopy(ms.rows)
    monkeypatch.setattr(service.corrections, 'snapshot', snapshot)
    async def post(client, method, url, **kwargs):
        assert method == 'POST' and url.endswith('/trackingCodes')
        if timeout:
            raise httpx.ReadTimeout('unknown outcome')
        if not drop:
            for entry in kwargs['json']:
                ms.rows['position']['codes'].append({**entry, 'id': str(uuid4())})
        return httpx.Response(200, json=[], request=httpx.Request(method, url))
    ms._request_with_retry = AsyncMock(side_effect=post)
    ms._load_tracking_codes = AsyncMock(side_effect=lambda *args: copy.deepcopy(ms.rows['position']['codes']))
    return ms, desired, doc


async def test_resends_only_missing_56_and_preserves_fields_then_noops_on_second_run(monkeypatch):
    ms, desired, doc = fixture(monkeypatch)
    before = copy.deepcopy(ms.rows['position']['fields'])
    job = {}
    result = await service.restore(ms, doc, desired, job, AsyncMock())
    assert job['already'] == 59 and job['added'] == 56 and job['confirmed'] == 115
    assert len(result['position']['codes']) == 115
    assert sum(len(call.kwargs['json']) for call in ms._request_with_retry.await_args_list) == 56
    assert ms.rows['position']['fields'] == before
    ms._request_with_retry.reset_mock()
    second = {}
    await service.restore(ms, doc, desired, second, AsyncMock())
    assert second['confirmed'] == second['already'] == 115 and second['added'] == 0
    ms._request_with_retry.assert_not_awaited()


@pytest.mark.parametrize('timeout', [False, True])
async def test_unconfirmed_success_or_timeout_never_blindly_replays_post(monkeypatch, timeout):
    ms, desired, doc = fixture(monkeypatch, expected=3, existing=1, drop=True, timeout=timeout)
    job = {}
    with pytest.raises((ValueError, httpx.ReadTimeout)):
        await service.restore(ms, doc, desired, job, AsyncMock())
    assert job['in_flight'] and not job.get('confirmed')
    assert ms._request_with_retry.await_count == 1
    with pytest.raises(ValueError, match='предыдущей записи'):
        await service.restore(ms, doc, desired, job, AsyncMock())
    assert ms._request_with_retry.await_count == 1


async def test_unknown_post_outcome_is_reconciled_readonly_once_codes_appear(monkeypatch):
    ms, desired, doc = fixture(monkeypatch, expected=3, existing=3)
    job = {'in_flight': {'position_id': 'position', 'keys': [ms._cis_dedup_key(code(2))]}}
    await service.restore(ms, doc, desired, job, AsyncMock())
    assert job['confirmed'] == 3 and 'in_flight' not in job
    ms._request_with_retry.assert_not_awaited()


def test_no_quantity_increase_or_new_product_positions():
    ms = MoySkladService('fake')
    with pytest.raises(ValueError, match='Количество'):
        service.missing_batches(ms, {'position': row([], quantity=1)},
            [{'code': code(i), 'product_id': 'product'} for i in range(2)])
    with pytest.raises(ValueError, match='Товар из сборки'):
        service.missing_batches(ms, {'position': row([])}, [{'code': code(0), 'product_id': 'other'}])


def test_duplicates_wrong_position_and_nested_packaging_are_not_sent_twice():
    ms = MoySkladService('fake')
    with pytest.raises(ValueError, match='повторяющиеся'):
        service.missing_batches(ms, {'position': row([])}, [{'code': code(0), 'product_id': 'product'}] * 2)
    wrong = {'position': row([]), 'other': row([code(0)], product='wrong')}
    with pytest.raises(ValueError, match='другую позицию'):
        service.missing_batches(ms, wrong, [{'code': code(0), 'product_id': 'product'}])
    nested = {'position': row([], quantity=2)}
    nested['position']['codes'] = [{'id': 'sscc', 'cis': '00046402759700334510', 'type': 'transportpack',
                                    'trackingCodes': row([code(0), code(1)])['codes']}]
    expected, actual, batches = service.missing_batches(ms, nested,
        [{'code': code(i), 'product_id': 'product'} for i in range(2)])
    assert set(expected) <= set(actual) and not batches


async def test_empty_correction_is_refreshed_without_losing_existing_scan_ids():
    old = Scan(id=uuid4(), code=code(0), status=ScanStatus.valid, is_barcode=False)
    doc = NS(id=uuid4(), upd_meta={service.corrections.MARKER: {
        'baseline_scans': {str(old.id): {'position_id': 'position', 'code': row([code(0)])['codes'][0]}},
        'baseline': {'position': row([code(0)])}, 'add_positions': {}}})
    added = []
    db = NS(add=added.append)
    rows = {'position': row([code(0), code(1)])}
    await service.refresh_correction(db, doc, [old], rows, [])
    assert str(old.id) in doc.upd_meta[service.corrections.MARKER]['baseline_scans']
    assert len(added) == 1 and added[0].code == code(1)
    assert len(doc.upd_meta[service.corrections.MARKER]['baseline_scans']) == 2
    assert not service.corrections.build_delta(doc, [old, *added])['operations']


async def test_edited_correction_and_active_resend_are_blocked():
    doc = NS(upd_meta={service.corrections.MARKER: {'baseline_scans': {'old': {}}, 'add_positions': {}}}, id=uuid4())
    db = NS(execute=AsyncMock(return_value=Result([])))
    with pytest.raises(ValueError, match='несохранённые'):
        await service.empty_correction(db, doc)
    doc = NS(kind='demand', moysklad_id='ms', user_id=uuid4())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await real_assert_idle(NS(execute=AsyncMock(return_value=Result(uuid4()))), doc)
    assert error.value.status_code == 409


def test_api_status_does_not_expose_inflight_mark_codes():
    result = service.public_state(NS(upd_meta={service.MARKER: {'id': 'job', 'status': 'error',
        'in_flight': {'keys': ['secret-cis']}, 'confirmed': 0}}))
    assert result == {'id': 'job', 'status': 'error', 'confirmed': 0}


def test_interrupted_worker_preserves_inflight_but_releases_edit_guard():
    doc = NS(upd_meta={service.MARKER: {'status': 'running', 'in_flight': {'keys': ['cis']}}})
    service.stop_interrupted(doc, 'Отправка прервана')
    assert doc.upd_meta[service.MARKER]['status'] == 'error'
    assert doc.upd_meta[service.MARKER]['in_flight']['keys'] == ['cis']


@pytest.mark.parametrize('broker_failure', [False, True])
async def test_queue_uses_original_source_and_deduplicates_requests(monkeypatch, broker_failure):
    from app.api import shipment_corrections as routes
    from app.worker.tasks import resend_shipment_marks_task
    from fastapi import HTTPException
    from unittest.mock import Mock
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def unlocked(*args):
        yield True
    monkeypatch.setattr(routes, 'processing_lock', unlocked)
    source = NS(id=uuid4(), user_id=uuid4(), moysklad_id='shipment', upd_meta={}, status=DocumentStatus.accepted)
    correction_id = uuid4()
    monkeypatch.setattr(routes, 'owned', AsyncMock(return_value=NS(id=correction_id)))
    monkeypatch.setattr(service, 'resolve_source', AsyncMock(return_value=source))
    db = NS(commit=AsyncMock(), execute=AsyncMock(return_value=Result(source)))
    dispatch = Mock(side_effect=RuntimeError('broker') if broker_failure else None)
    monkeypatch.setattr(resend_shipment_marks_task, 'delay', dispatch)
    if broker_failure:
        with pytest.raises(HTTPException) as error:
            await routes.resend_marks(correction_id, NS(), db)
        assert error.value.status_code == 503
        assert service.public_state(source)['status'] == 'error'
    else:
        first = await routes.resend_marks(correction_id, NS(), db)
        second = await routes.resend_marks(correction_id, NS(), db)
        assert first['status'] == 'queued' and first['id'] == second['id']
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[:2] == (str(source.id), str(source.user_id))


async def test_resolve_source_follows_correction_to_completed_assembly(monkeypatch):
    source = NS(id=uuid4(), status=DocumentStatus.accepted, moysklad_id='ms', upd_meta={})
    draft = NS(id=uuid4(), status=DocumentStatus.draft, user_id=uuid4(),
        organization_profile_id=uuid4(), moysklad_id='ms', upd_meta={service.corrections.MARKER: {'source_id': str(source.id)}})
    edits = AsyncMock()
    monkeypatch.setattr(service, 'empty_correction', edits)
    db = NS(execute=AsyncMock(return_value=Result(source)))
    assert await service.resolve_source(db, draft) is source
    edits.assert_awaited_once()
    source.upd_meta['superseded_by_document_id'] = str(uuid4())
    with pytest.raises(ValueError, match='актуальную'):
        await service.resolve_source(db, draft)
