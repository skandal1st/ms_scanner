import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.db.models import DocumentKind, DocumentStatus, ScanStatus
from app.services import shipment_corrections as service
from app.api import shipment_corrections as routes


def tc(code, identifier=None, **extra):
    return {'id': identifier or str(uuid4()), 'cis': code, 'type': 'trackingcode', **extra}


def row(codes, product='product', quantity=3):
    return {'fields': {'id': 'position', 'quantity': quantity, 'price': 12500,
            'assortment': {'meta': {'href': f'product/{product}', 'type': 'product'}}},
            'codes': codes, 'product_id': product, 'product_name': 'Товар', 'tracking_type': None}


def fixture():
    old, retained = tc('old'), tc('retained')
    baseline = {'position': row([old, retained])}
    kept_scan, removed_scan = uuid4(), uuid4()
    state = {'baseline': baseline, 'baseline_scans': {
        str(kept_scan): {'position_id': 'position', 'code': retained},
        str(removed_scan): {'position_id': 'position', 'code': old}}, 'add_positions': {}}
    doc = NS(id=uuid4(), user_id=uuid4(), kind=DocumentKind.demand, moysklad_id='shipment',
             status=DocumentStatus.draft, upd_meta={service.MARKER: state},
             error_message=None, moysklad_store_id=None, moysklad_organization_id=None)
    scan = NS(id=kept_scan, status=ScanStatus.valid)
    return doc, state, [scan]


def test_only_removed_and_added_codes_form_delta_and_quantity_stays_unchanged():
    doc, state, scans = fixture()
    new = NS(id=uuid4(), status=ScanStatus.valid, code='new', is_barcode=False,
             is_box=False, keep_aggregate=False, child_codes=None)
    state['add_positions'][str(new.id)] = 'position'
    delta = service.build_delta(doc, scans + [new])
    assert [(op['action'], op['code']['cis']) for op in delta['operations']] == [('delete', 'old'), ('add', 'new')]
    assert delta['quantities'] == []
    assert len(delta['preview_hash']) == 64
    assert 'retained' not in [op['code']['cis'] for op in delta['operations']]


def test_delete_all_and_optional_quantity_change():
    doc, state, scans = fixture()
    delta = service.build_delta(doc, [], True)
    assert len(delta['removed']) == 2
    assert delta['quantities'] == [{'product_name': 'Товар', 'before': 3, 'after': 1}]


def test_moving_mark_between_same_product_rows_uses_position_identity():
    doc, state, scans = fixture()
    state['baseline']['second'] = row([], quantity=2)
    new = NS(id=uuid4(), status=ScanStatus.valid, code='old', is_barcode=False,
             is_box=False, keep_aggregate=False, child_codes=None)
    state['add_positions'][str(new.id)] = 'second'
    delta = service.build_delta(doc, scans + [new])
    assert [(op['action'], op['position_id']) for op in delta['operations']] == [('delete', 'position'), ('add', 'second')]


@pytest.mark.parametrize('status', [ScanStatus.scanned, ScanStatus.pending, ScanStatus.invalid, ScanStatus.used_in_other_doc])
def test_unverified_or_invalid_additions_block_save(status):
    doc, _, scans = fixture()
    with pytest.raises(ValueError, match='Проверьте новые марки'):
        service.build_delta(doc, scans + [NS(id=uuid4(), status=status)])


def test_whole_package_deletion_keeps_children_and_unknown_quantity_is_not_guessed():
    doc, state, scans = fixture()
    package = tc('package', type='transportpack')
    state['baseline']['position']['codes'][0] = package
    removed_id = next(key for key, val in state['baseline_scans'].items() if val['code']['cis'] == 'old')
    state['baseline_scans'][removed_id]['code'] = package
    assert service.build_delta(doc, scans)['removed'][0]['package']
    with pytest.raises(ValueError, match='Количество единиц'):
        service.build_delta(doc, scans, True)
    assert service.units(tc('pack', type='consumerpack', trackingCodes=[tc('child1'), tc('child2')])) == 2


def test_external_change_detection_ignores_code_order_and_unrelated_positions():
    doc, state, _ = fixture()
    actual = copy.deepcopy(state['baseline'])
    actual['position']['codes'].reverse()
    actual['other'] = row([tc('elsewhere')])
    assert service.matches(actual, state['baseline'], {'position'})
    actual['position']['codes'].append(tc('externally-added'))
    assert not service.matches(actual, state['baseline'], {'position'})
    actual = copy.deepcopy(state['baseline'])
    actual['position']['fields']['price'] += 1
    assert not service.matches(actual, state['baseline'], {'position'})


async def test_resume_after_lost_delete_response_does_not_delete_twice(monkeypatch):
    doc, state, scans = fixture()
    delta = service.build_delta(doc, scans)
    doc.status = DocumentStatus.processing
    state['job'] = {'delta': delta, 'expected': copy.deepcopy(state['baseline']), 'completed': 0, 'in_flight': 0}
    actual = service.after_operation(state['baseline'], delta['operations'][0])
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({}, actual)))
    monkeypatch.setattr(service, 'save_state', AsyncMock())
    db = NS(execute=AsyncMock(return_value=NS(scalars=lambda: NS(all=lambda: []))))
    ms = NS(_request_with_retry=AsyncMock())
    await service.execute(db, doc, ms)
    ms._request_with_retry.assert_not_awaited()
    assert doc.status == DocumentStatus.accepted


async def test_resume_after_lost_add_response_does_not_add_twice(monkeypatch):
    doc, state, scans = fixture()
    op = {'action': 'add', 'position_id': 'position', 'code': {'cis': 'new', 'type': 'trackingcode'}}
    state['job'] = {'delta': {'operations': [op]}, 'expected': copy.deepcopy(state['baseline']), 'completed': 0, 'in_flight': 0}
    actual = service.after_operation(state['baseline'], op)
    actual['position']['codes'][-1]['id'] = str(uuid4())
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({}, actual)))
    monkeypatch.setattr(service, 'save_state', AsyncMock())
    db = NS(execute=AsyncMock(return_value=NS(scalars=lambda: NS(all=lambda: []))))
    ms = NS(_request_with_retry=AsyncMock())
    await service.execute(db, doc, ms)
    ms._request_with_retry.assert_not_awaited()


async def test_conflict_before_write_makes_no_external_mutation(monkeypatch):
    doc, state, scans = fixture()
    state['job'] = {'delta': service.build_delta(doc, scans), 'expected': copy.deepcopy(state['baseline']), 'completed': 0}
    actual = copy.deepcopy(state['baseline'])
    actual['position']['codes'].append(tc('external'))
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({}, actual)))
    ms = NS(_request_with_retry=AsyncMock())
    with pytest.raises(ValueError, match='изменены'):
        await service.execute(NS(), doc, ms)
    ms._request_with_retry.assert_not_awaited()


async def test_delta_uses_tracking_code_delete_and_add_not_full_document_put(monkeypatch):
    doc, state, scans = fixture()
    new = NS(id=uuid4(), status=ScanStatus.valid, code='new', is_barcode=False, is_box=False, keep_aggregate=False, child_codes=None)
    state['add_positions'][str(new.id)] = 'position'
    state['job'] = {'delta': service.build_delta(doc, scans + [new]), 'expected': copy.deepcopy(state['baseline']), 'completed': 0}
    actual = copy.deepcopy(state['baseline'])
    async def snap(ms, ms_id): return {}, copy.deepcopy(actual)
    calls = []
    async def request(client, method, url, **kwargs):
        calls.append((method, url, kwargs['json']))
        if url.endswith('/delete'):
            removed_id = kwargs['json'][0]['meta']['href'].rsplit('/', 1)[-1]
            actual['position']['codes'] = [c for c in actual['position']['codes'] if c['id'] != removed_id]
        else:
            actual['position']['codes'].append({**kwargs['json'][0], 'id': str(uuid4())})
        return httpx.Response(200, json=[], request=httpx.Request(method, url))
    monkeypatch.setattr(service, 'snapshot', snap)
    monkeypatch.setattr(service, 'save_state', AsyncMock())
    db = NS(execute=AsyncMock(return_value=NS(scalars=lambda: NS(all=lambda: []))))
    await service.execute(db, doc, NS(base_url='https://example.test', _request_with_retry=request))
    assert [method for method, _, _ in calls] == ['POST', 'POST']
    assert calls[0][1].endswith('/positions/position/trackingCodes/delete')
    assert calls[1][1].endswith('/positions/position/trackingCodes')
    assert calls[1][2] == [{'cis': 'new', 'type': 'trackingcode'}]
    assert len(actual['position']['codes']) == 2


async def test_save_requires_the_reviewed_delta_hash(monkeypatch):
    doc, _, scans = fixture()
    monkeypatch.setattr(routes, 'owned', AsyncMock(return_value=doc))
    db = NS(execute=AsyncMock(return_value=NS(scalars=lambda: NS(all=lambda: scans))))
    with pytest.raises(HTTPException) as error:
        await routes.save(doc.id, routes.PreviewRequest(preview_hash='stale'), NS(user=NS(id=doc.user_id)), db)
    assert error.value.status_code == 409
    assert not service.correction(doc).get('job')


async def test_partial_correction_cannot_be_cancelled(monkeypatch):
    doc, state, _ = fixture()
    state['job'] = {'completed': 0, 'in_flight': 0}
    monkeypatch.setattr(routes, 'owned', AsyncMock(return_value=doc))
    with pytest.raises(HTTPException) as error:
        await routes.cancel(doc.id, NS(), NS())
    assert error.value.status_code == 409


def test_warehouse_or_organization_change_blocks_correction():
    with pytest.raises(ValueError, match='Склад или юрлицо'):
        service.assert_scope(NS(moysklad_store_id='original', moysklad_organization_id=None), {'store': {'id': 'foreign'}})
