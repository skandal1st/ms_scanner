from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.services.supply_positions import prepare_supply_rows, prepare_supply_without_cz
from app.api import acceptance, products
from app.core.config import settings
from app.services.moysklad import MoySkladService
from app.worker.tasks import _build_moysklad_scans_data


def test_accessories_follow_xml_order_without_synthetic_marking_codes():
    plan = [{'product_id': 'marked', 'gtin': '04640195516113'},
            {'product_id': 'bowl', 'marked': False, 'expected_qty': 200, 'price': 315, 'vat': 22}]
    meta = {'physical_plan': [{'product_id': 'bowl'}, {'product_id': 'marked'}]}
    marks = [{'product_id': 'marked', 'code': 'REAL-MARK'}]
    result = prepare_supply_rows(marks, plan, meta)
    assert [p['product_id'] for p in result] == ['bowl', 'marked']
    assert result[0]['quantity'] == 200
    assert result[0]['is_barcode'] and result[0]['code'] == ''
    assert marks == [{'product_id': 'marked', 'code': 'REAL-MARK'}]
    assert len(prepare_supply_rows(result, plan, meta)) == 2


def test_late_gtin_link_uses_original_xml_position():
    plan = [{'product_id': 'first', 'gtin': '04640195516113'},
            {'product_id': 'last-linked', 'gtin': '04640195516892'}]
    meta = {'physical_plan': [{'product_id': None, 'gtins': ['04640195516892']},
                              {'product_id': 'first'}]}
    rows = [{'product_id': 'first'}, {'product_id': 'last-linked'}]
    assert [r['product_id'] for r in prepare_supply_rows(rows, plan, meta)] == ['last-linked', 'first']


def test_quantity_only_import_preserves_fractions_and_ignores_unscanned_marked_plan():
    plan = [{'product_id': 'marked', 'expected_qty': 20},
            {'product_id': 'accessory', 'marked': False, 'expected_qty': 2.5}]
    result = prepare_supply_rows([], plan, {})
    assert len(result) == 1 and result[0]['quantity'] == 2.5


async def test_link_accessory_with_numeric_article_without_scans(monkeypatch):
    doc = NS(id=uuid4(), kind=NS(value='supply'), plan=[], upd_meta={'physical_plan': [
        {'key': 'xml:2', 'position_key': 'line:3', 'product_name': 'Чаша Hype', 'product_id': None}]})
    monkeypatch.setattr(products, '_get_document_owned', AsyncMock(return_value=doc))
    import app.services.document_guard as guard
    monkeypatch.setattr(guard, 'editable_document', AsyncMock(return_value=doc))
    db = NS(commit=AsyncMock())
    user = NS(id=uuid4())
    data = products.LinkUpdPositionRequest(document_id=doc.id, line_number=3,
        position_name='Чаша Hype', article='178', gtin='00000000000178', quantity=200,
        price=315, vat=22, moysklad_product_id='bowl', product_name='Чаша Hype Turkish')
    await products.link_upd_position_to_product(data, user, db)
    assert doc.plan == [{'product_id': 'bowl', 'gtin': '00000000000178', 'marked': False,
                         'product_name': 'Чаша Hype Turkish', 'expected_qty': 200, 'price': 315, 'vat': 22}]
    assert doc.upd_meta['physical_plan'][0]['product_id'] == 'bowl'
    assert prepare_supply_rows([], doc.plan, doc.upd_meta)[0]['quantity'] == 200
    data.moysklad_product_id = 'other-bowl'
    await products.link_upd_position_to_product(data, user, db)
    assert [p['product_id'] for p in doc.plan] == ['other-bowl']


@pytest.mark.parametrize('without_cz', [False, True])
@pytest.mark.parametrize('existing_prices', [[], [12500], [12500, 10000]])
@pytest.mark.parametrize('pooled', [False, True])
async def test_two_upd_prices_survive_import_write_and_retry(monkeypatch, without_cz, existing_prices, pooled):
    gtin = '04660321205858'
    a, b, c = [f'01{gtin}21{i:07}' for i in (1, 2, 3)]
    first, second = ([a, b, c], []) if pooled else ([a, b], [c])
    def identification(codes):
        return '<НомСредИдентТов>' + ''.join(f'<КИЗ>{code}</КИЗ>' for code in codes) + '</НомСредИдентТов>'
    xml = f'''<Файл><ТаблСчФакт>
      <СведТов НомСтр="1" НаимТов="Товар" КолТов="2" СтТовУчНал="200" НалСт="10%">
        {identification(first)}</СведТов>
      <СведТов НомСтр="2" НаимТов="Товар" КолТов="1" СтТовУчНал="125" НалСт="22%">
        {identification(second)}</СведТов>
      </ТаблСчФакт></Файл>'''
    doc = NS(id=uuid4(), plan=[], upd_meta={}, organization_profile_id=uuid4())
    scans = []
    db = NS(add=scans.append, commit=AsyncMock(), execute=AsyncMock(return_value=NS(all=lambda: [])))
    monkeypatch.setattr(acceptance, '_maybe_ms_service', AsyncMock(return_value=None))
    monkeypatch.setattr(acceptance, '_maybe_cz_service', AsyncMock(return_value=None))
    monkeypatch.setattr(acceptance, '_enrich_unmatched_names_nk', AsyncMock())
    monkeypatch.setattr(acceptance, '_resolve_product', AsyncMock(return_value=('p', 'Товар')))
    imported = await acceptance._import_upd_bytes_traced(doc.id, doc, xml.encode(), NS(id=uuid4()), db)
    assert [(p.price, p.source_codes) for p in imported.positions] == [(100, first), (125, second)]
    if without_cz:
        rows = prepare_supply_without_cz(doc.plan, doc.upd_meta)
    else:
        rows = prepare_supply_rows(_build_moysklad_scans_data(reversed(scans), 'supply', {}), doc.plan, doc.upd_meta)

    monkeypatch.setattr(settings, 'CZ_MOCK_MODE', False)
    ms = MoySkladService('test')
    assortment = {'id': 'p', 'meta': {'type': 'product', 'href': f'{ms.base_url}/entity/product/p'}}
    stored = [dict(id=f'old-{price}', price=price, assortment=assortment, quantity=3,
                   vat=10 if price == 10000 else 22) for price in existing_prices]
    marks = {}
    writes = []
    async def request(client, method, url, json):
        if method == 'PUT':
            stored[:] = [{**p, 'assortment': assortment, 'id': p.get('id', f'new-{i}')}
                         for i, p in enumerate(json['positions'])]
            writes.append([(p['id'], p['quantity'], p['price'], p['vat']) for p in stored])
        else:
            assert method == 'POST' and url.endswith('/trackingCodes')
            marks.setdefault(url.split('/')[-2], []).extend(json)
        return httpx.Response(200, json={}, request=httpx.Request(method, url))
    ms._request_with_retry = request
    ms._load_positions_rows = AsyncMock(side_effect=lambda *args: list(reversed(stored)))
    ms._load_tracking_codes = AsyncMock(side_effect=lambda c, k, d, row_id: marks.get(row_id, []))
    for _ in range(2):
        await ms.update_document('supply', 'doc', rows)
    assert writes[0] == writes[1]
    assert [r[1:] for r in writes[0]] == [(2, 10000, 10), (1, 12500, 22)]
    for row_id, _, price, _ in writes[0]:
        if price in existing_prices:
            assert row_id == f'old-{price}'
    if without_cz:
        assert marks == {}
    else:
        for entry, originals in zip(stored, [[a, b], [c]]):
            assert sorted(ms._cis_dedup_key(t['cis']) for t in marks[entry['id']]) == sorted(
                ms._cis_dedup_key(code) for code in originals)


def test_expanded_box_and_verified_mark_keep_their_xml_line():
    lines = [{'price': 80, 'vat': 10, 'items': [{'product_id': 'p', 'quantity': 2, 'codes': ['box']}]},
             {'price': 95, 'vat': 22, 'items': [{'product_id': 'p', 'quantity': 1, 'codes': ['short']}]}]
    rows = [{'product_id': 'p', 'code': 'full', 'source_code': 'short'},
            {'product_id': 'p', 'code': 'unit1', 'source_code': 'box'},
            {'product_id': 'p', 'code': 'unit2', 'source_code': 'box'}]
    prepared = prepare_supply_rows(rows, [], {'physical_plan': lines})
    assert [(r['code'], r['price']) for r in prepared] == [('unit1', 80), ('unit2', 80), ('full', 95)]


def test_spare_slots_preserve_other_lines_marks_and_do_not_cross_products():
    lines = [{'items': [{'product_id': pid, 'codes': codes, 'quantity': qty}]}
             for pid, codes, qty in [('p', [], 2), ('other', [], 5), ('p', ['a', 'b', 'c'], 1),
                                     ('p', ['d'], 1)]]
    def prepare(codes):
        return prepare_supply_rows([{'product_id': 'p', 'code': c} for c in codes], [], {'physical_plan': lines})
    rows = prepare(['c', 'd', 'b', 'a', 'b'])
    assert [(r['upd_line'], r['code']) for r in rows if r['code']] == [(0, 'b'), (0, 'c'), (2, 'a'), (3, 'd')]
    assert [(r['product_id'], r['quantity']) for r in rows if not r['code']] == [('other', 5)]
    assert prepare(['a', 'b', 'c', 'd']) == rows
    with pytest.raises(ValueError, match='Количество марок товара'):
        prepare(['a', 'b', 'c', 'd', 'extra'])


def test_mixed_xml_line_and_late_resolved_package_use_the_financial_quantity():
    lines = [
        {'expected_qty': 3, 'items': [{'product_id': 'a', 'quantity': 3, 'codes': ['a1']},
                                      {'product_id': 'b', 'quantity': None, 'codes': ['b1']}]},
        {'expected_qty': 4, 'items': [{'product_id': None, 'quantity': 4, 'codes': ['box']}]},
    ]
    rows = [{'product_id': 'a', 'code': 'a1'}, {'product_id': 'b', 'code': 'b1'},
            {'product_id': 'resolved', 'source_code': 'box', 'code': 'child'}]
    result = prepare_supply_rows(rows, [], {'physical_plan': lines})
    assert [(r['product_id'], r['expected_qty']) for r in result] == [('a', 2), ('b', 1), ('resolved', 4)]


def test_unmarked_lines_with_one_product_keep_fractional_quantities_and_zero_price():
    lines = [{'price': price, 'items': [{'product_id': 'p', 'quantity': qty, 'codes': []}]}
             for price, qty in [(315, 2.5), (0, 0.5)]]
    rows = prepare_supply_rows([], [], {'physical_plan': lines})
    assert [(r['upd_line'], r['quantity'], r['price']) for r in rows] == [(0, 2.5, 315), (1, 0.5, 0)]


def test_old_receipt_with_merged_prices_requires_the_original_xml():
    meta = {'physical_plan': [{'product_id': 'p', 'expected_qty': 2}, {'product_id': 'p', 'expected_qty': 3}]}
    with pytest.raises(ValueError, match='Загрузите исходный УПД'):
        prepare_supply_rows([], [{'product_id': 'p', 'price': 80, 'expected_qty': 5}], meta)


async def test_manual_link_of_repeated_unmarked_line_only_changes_its_product(monkeypatch):
    lines = [{'position_key': f'xml:{i}', 'product_name': 'Чаша', 'product_id': None,
              'price': price, 'items': [{'product_id': None, 'quantity': 2.5, 'codes': []}]}
             for i, price in enumerate([100, 125])]
    doc = NS(id=uuid4(), kind='supply', plan=[], upd_meta={'physical_plan': lines})
    from app.services import document_guard
    monkeypatch.setattr(products, '_get_document_owned', AsyncMock(return_value=doc))
    monkeypatch.setattr(document_guard, 'editable_document', AsyncMock(return_value=doc))
    for i, pid in [(0, 'p'), (1, 'p'), (1, 'other')]:
        request = products.LinkUpdPositionRequest(document_id=doc.id, xml_index=i, position_name='Чаша',
            quantity=2.5, price=lines[i]['price'], moysklad_product_id=pid)
        await products.link_upd_position_to_product(request, NS(id=uuid4()), NS(commit=AsyncMock()))
    rows = prepare_supply_without_cz(doc.plan, doc.upd_meta)
    assert [(r['product_id'], r['price'], r['quantity']) for r in rows] == [('p', 100, 2.5), ('other', 125, 2.5)]

