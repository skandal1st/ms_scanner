from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.supply_positions import prepare_supply_rows
from app.api import products


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

