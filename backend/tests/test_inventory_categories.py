from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api import physical_counts as api
from app.services.moysklad import MoySkladService
from app.services.physical_counts import prepare_inventory_plan


def response(rows, url='https://example.test'):
    return httpx.Response(200, json={'rows': rows}, request=httpx.Request('GET', url))


async def test_category_picker_pages_only_folder_names_and_sorts_full_paths(monkeypatch):
    ms = MoySkladService('fixture')
    batch = [{'id': str(i), 'name': f'Папка {i}', 'pathName': 'Табак'} for i in range(1000)]
    request = AsyncMock(side_effect=[response(batch), response([{'id': 'child', 'name': 'Бренд', 'pathName': 'Табак/Смеси'}])])
    monkeypatch.setattr(ms, '_request_with_retry', request)
    result = await ms.get_product_folders()
    assert len(result) == 1001
    assert next(v for v in result if v['id'] == 'child')['path_name'] == 'Табак/Смеси/Бренд'
    assert all(call.args[2].endswith('/entity/productfolder') for call in request.await_args_list)
    assert [call.kwargs['params']['offset'] for call in request.await_args_list] == [0, 1000]


async def test_stock_filters_categories_with_children_before_pagination(monkeypatch):
    ms = MoySkladService('fixture')
    request = AsyncMock(return_value=response([]))
    monkeypatch.setattr(ms, '_request_with_retry', request)
    await ms.get_stock_map([ms.base_url + '/entity/store/store'], group_by='variant', full_snapshot=True,
                           folder_ids=['first', 'child', 'first'])
    params = request.await_args.kwargs['params']
    assert params['groupBy'] == 'variant'
    assert params['filter'].count('productFolder=') == 2
    assert f'productFolder={ms.base_url}/entity/productfolder/first' in params['filter']
    assert 'withSubFolders=true' in params['filter'] and 'stockMode=all' in params['filter']
    request.reset_mock()
    with pytest.raises(ValueError, match='категории'):
        await ms.get_stock_map([], folder_ids=[])
    request.assert_not_awaited()


async def test_selected_catalog_keeps_zero_archived_variants_packs_and_excludes_foreign_demand_goods():
    calls = []
    parent = {'id': 'parent', 'pathName': 'Табак/Бренд', 'productFolder': {'id': 'child'}}
    catalog = [
        {'id': 'zero', 'name': 'Нулевой остаток', 'pathName': 'Табак/Бренд', 'barcodes': [{'ean13': '4620543080527'}]},
        {'id': 'variant', 'name': 'Модификация', 'meta': {'type': 'variant'}, 'archived': True,
         'product': parent, 'barcodes': [{'ean13': '4620543080503'}],
         'packs': [{'quantity': 10, 'barcodes': [{'ean13': '4620543080527'}]}]},
    ]
    # A complete 100-row page tests pagination even after category filtering.
    catalog += [{'id': f'p{i}', 'name': str(i), 'pathName': 'Табак/Бренд'} for i in range(98)]
    async def request(client, method, url, params):
        calls.append((url, params))
        assert not url.endswith('/entity/product') and not url.endswith('/entity/variant')
        if url.endswith('/entity/assortment'):
            return response(catalog if params['offset'] == 0 else [], url)
        return response([{'id': 'demand', 'applied': True, 'store': {'id': 'store'}, 'state': {'id': 'ready'},
            'positions': {'rows': [
                {'assortment': {'id': 'zero'}, 'quantity': 2.5},
                {'assortment': {'id': 'variant'}, 'quantity': 3},
                {'assortment': {'id': 'foreign'}, 'quantity': 999},
            ]}}], url)
    ms = NS(base_url='https://example.test', _request_with_retry=request,
            _id_from_href=lambda href: href.rsplit('/', 1)[-1],
            get_stock_map=AsyncMock(return_value={'variant': {'qty': 0}}))
    plan = await prepare_inventory_plan(ms, 'store', ['ready'], folder_ids=['root'])
    by_id = {row['key']: row for row in plan}
    assert 'foreign' not in by_id
    assert by_id['zero']['base_qty'] == 0 and by_id['zero']['expected_qty'] == 2.5
    assert by_id['variant']['folder_name'] == 'Табак/Бренд'
    assert by_id['variant']['folder_id'] == 'child'
    assert by_id['variant']['pack_quantities']['04620543080527'] == 10
    assert by_id['variant']['expected_qty'] == 3
    assert len(by_id) == 100
    ms.get_stock_map.assert_awaited_once_with(['https://example.test/entity/store/store'],
        group_by='variant', full_snapshot=True, folder_ids=['root'])
    assert [p['offset'] for url, p in calls if url.endswith('/assortment')] == [0, 100]
    for url, params in calls:
        if url.endswith('/assortment'):
            assert 'productFolder=https://example.test/entity/productfolder/root' in params['filter']
            assert 'withSubFolders=true' in params['filter'] and 'archived=true' in params['filter']


@pytest.mark.parametrize('selected', [None, []])
async def test_new_inventory_requires_selection_before_ms_calls(monkeypatch, selected):
    load = AsyncMock()
    monkeypatch.setattr(api, 'options', load)
    with pytest.raises(HTTPException, match='категорию') as exc:
        await api.create_session(api.CreateRequest(mode='inventory', store_id=uuid4(), folder_ids=selected),
                                 (NS(), NS(), None, None), NS())
    assert exc.value.status_code == 400
    load.assert_not_awaited()


async def test_unavailable_category_does_not_create_or_queue_count(monkeypatch):
    monkeypatch.setattr(api, 'folders', AsyncMock(return_value=[]))
    db = NS(add=Mock(), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await api.create_session(api.CreateRequest(mode='inventory', store_id=uuid4(), folder_ids=[uuid4()]),
                                 (NS(), NS(), None, None), db)
    assert exc.value.status_code == 400
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


async def test_creation_freezes_selected_category_names_and_worker_receives_ids(monkeypatch):
    from app.worker import tasks
    store, folder = str(uuid4()), str(uuid4())
    category = {'id': folder, 'name': 'Бренд', 'path_name': 'Табак/Бренд'}
    monkeypatch.setattr(api, 'folders', AsyncMock(return_value=[category]))
    monkeypatch.setattr(api, 'options', AsyncMock(return_value={
        'stores': [{'id': store, 'name': 'Основной склад'}], 'states': [], 'default_include_state_ids': []}))
    queue = Mock()
    monkeypatch.setattr(tasks.prepare_physical_count_task, 'delay', queue)
    saved = []
    async def refresh(session):
        session.id, session.created_at = uuid4(), None
    db = NS(add=lambda session: saved.append(session), commit=AsyncMock(), refresh=refresh)
    result = await api.create_session(api.CreateRequest(mode='inventory', store_id=store,
        folder_ids=[folder, folder], count_method='quantity'), (NS(id=uuid4()), NS(id=uuid4()), None, None), db)
    assert result['settings']['folder_ids'] == [folder]
    assert result['settings']['included_folders'] == [category]
    assert saved[0].plan == [] and saved[0].status == 'preparing'
    queue.assert_called_once_with(result['id'])


async def test_folders_do_not_grant_inventory_mode_to_acceptance_only_terminal():
    with pytest.raises(HTTPException) as exc:
        await api.folders((NS(), NS(), NS(), NS(allowed_modes=['acceptance'])), NS())
    assert exc.value.status_code == 403
