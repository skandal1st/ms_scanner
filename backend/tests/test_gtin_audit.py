from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api import deps, gtin_control
from app.services.gtin_audit import Catalog, analyze, safe_card_url
from app.services.gtin_audit_job import load_catalog

GTIN = '04640275972501'


def card(pid='peach-25', name='Персик 25 г', archived=False, barcodes=None, packs=None):
    return {'id': pid, 'name': name, 'archived': archived,
            'barcodes': [{'gtin': GTIN}] if barcodes is None else barcodes, 'packs': packs or []}


def mapping(pid='peach-25', name='Персик 25 г'):
    return {'gtin': GTIN, 'product_id': pid, 'product_name': name}


def audit(rows, mappings):
    catalog = Catalog()
    for row in rows:
        catalog.add(row)
    return analyze(mappings, catalog)


def test_peach_wrong_weight_is_not_hidden_by_same_display_name_or_old_assignment():
    results, summary = audit([card(), card('peach-200', 'Персик 200 г', barcodes=[{'gtin': '04640275970705'}])],
                             [mapping('peach-200', 'Персик 200 г')])
    row = next(r for r in results if r['kind'] == 'mapping')
    assert row['category'] == 'other_product'
    assert row['saved_card']['id'] == 'peach-200'
    assert [c['id'] for c in row['candidates']] == ['peach-25']
    assert summary['other_product'] == 1


def test_pack_barcodes_and_ean13_match_without_false_mapping_error():
    results, summary = audit([card(barcodes=[{'ean13': '2000000071978'}],
                                  packs=[{'barcodes': [{'ean13': GTIN[1:]}]}])], [mapping()])
    assert results == [] and summary['matched'] == 1


def test_repeated_barcode_inside_one_product_or_its_packs_is_not_a_duplicate():
    results, summary = audit([card(barcodes=[{'gtin': GTIN}, {'ean13': GTIN[1:]}],
                                  packs=[{'barcodes': [{'gtin': GTIN}]}])], [mapping()])
    assert not results and summary.get('duplicate_active', 0) == 0


def test_duplicate_scan_checks_entire_catalog_even_without_saved_mapping():
    results, summary = audit([card(), card('other-flavor', 'Другой вкус')], [])
    assert len(results) == 1 and results[0]['kind'] == 'duplicate'
    assert results[0]['category'] == 'duplicate_active' and len(results[0]['candidates']) == 2
    assert summary['mapping_count'] == 0 and summary['duplicate_active'] == 1


@pytest.mark.parametrize('archived', [(False, True, 'duplicate_with_archive'), (True, True, 'duplicate_archived')])
def test_archive_duplicates_have_separate_categories(archived):
    first, second, category = archived
    results, _ = audit([card(archived=first), card('second', archived=second)], [])
    assert results[0]['category'] == category


def test_active_mapping_is_not_flagged_because_same_code_is_also_archived():
    results, summary = audit([card(), card('old', archived=True)], [mapping()])
    assert all(r['kind'] == 'duplicate' for r in results)
    assert summary['matched'] == 1


@pytest.mark.parametrize('pid,category', [('04660515338966:SERIAL', 'invalid_product_id'),
                                         (str(uuid4()), 'missing_product')])
def test_missing_card_and_non_uuid_id_are_distinguished(pid, category):
    results, _ = audit([card()], [mapping(pid)])
    assert results[0]['category'] == category


def test_absent_gtin_is_unconfirmed_not_automatically_wrong():
    results, _ = audit([card(barcodes=[])], [mapping()])
    assert results[0]['category'] == 'unconfirmed_gtin'


def test_renaming_card_does_not_become_wrong_product():
    results, summary = audit([card(name='Новое название')], [mapping(name='Старое название')])
    assert results[0]['category'] == 'renamed_product'
    assert summary['renamed_product'] == 1


def test_duplicate_code_in_saved_mapping_is_reported_without_arbitrary_replacement():
    results, _ = audit([card(), card('second')], [mapping()])
    m = next(r for r in results if r['kind'] == 'mapping')
    assert m['category'] == 'ambiguous_mapping' and m['saved_product_id'] == 'peach-25'


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://evil.example/app/', 'https://online.moysklad.ru.evil.example/', None])
def test_card_link_does_not_accept_untrusted_urls(url):
    assert safe_card_url(url) is None


async def test_catalog_loader_reads_only_get_and_preserves_all_pages(monkeypatch):
    calls = []
    async def request(client, method, url, params):
        calls.append((method, params.copy()))
        if params['filter'] == 'archived=true':
            return httpx.Response(200, json={'meta': {'size': 1}, 'rows': [card('old', archived=True)]})
        offset = params['offset']
        rows = [card(str(i)) for i in range(offset, min(offset+1000, 1001))]
        return httpx.Response(200, json={'meta': {'size': 1001}, 'rows': rows})
    # Responses above use no network but have a request for raise_for_status().
    async def bound_request(*args, **kwargs):
        response = await request(*args, **kwargs)
        response.request = httpx.Request('GET', 'https://api.moysklad.ru/catalog')
        return response
    ms = NS(base_url='https://api.moysklad.ru', _request_with_retry=bound_request)
    progress = AsyncMock()
    catalog = await load_catalog(ms, progress)
    assert len(catalog.cards) == 1002
    assert [params['offset'] for method, params in calls] == [0, 1000, 0]
    assert all(method == 'GET' for method, _ in calls)
    assert progress.await_count == 3


async def test_incomplete_catalog_never_becomes_a_successful_empty_report():
    response = httpx.Response(200, json={'meta': {'size': 5}, 'rows': [card()]},
                              request=httpx.Request('GET', 'https://api.moysklad.ru/catalog'))
    ms = NS(base_url='https://api.moysklad.ru', _request_with_retry=AsyncMock(return_value=response))
    with pytest.raises(ValueError, match='полный каталог'):
        await load_catalog(ms, AsyncMock())


@pytest.mark.parametrize('token_type,status', [('tsd_access', 401), ('access', 403)])
async def test_control_rejects_terminal_tokens_and_lite_edition(monkeypatch, token_type, status):
    monkeypatch.setattr(deps, 'decode_token', lambda value: {'sub': str(uuid4()), 'type': token_type})
    user = NS(id=uuid4(), is_active=True, edition='ms_lite')
    from tests.test_release_processing import Result
    db = NS(execute=AsyncMock(return_value=Result(user)))
    async def session(): yield db
    app = FastAPI()
    app.include_router(gtin_control.router, dependencies=[Depends(deps.require_full_edition)])
    from app.db.session import get_db
    app.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/gtin-control', headers={'Authorization': 'Bearer fixture'})
    assert response.status_code == status
