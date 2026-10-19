from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.api import moysklad_vendor as vendor


@pytest.fixture
def button_env(monkeypatch):
    verify = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(vendor, '_verify_vendor_jwt', verify)
    monkeypatch.setattr(vendor, '_idempotent_response', AsyncMock(return_value=None))
    monkeypatch.setattr(vendor, '_save_idempotent', save)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(id='integration'))))
    return db, verify, save


@pytest.mark.parametrize('button,extension,kind', [
    ('open-shipments', 'document.demand.list', 'demand'),
    ('open-acceptance', 'document.supply.list', 'supply'),
])
@pytest.mark.parametrize('selection', [None, [{'id': 'ignored-selected-document'}]])
async def test_unselected_buttons_open_picker_even_with_selected_rows(button_env, button, extension, kind, selection):
    db, verify, save = button_env
    result = await vendor.handle_button('app', 'account', vendor.ButtonRequest(
        buttonName=button, extensionPoint=extension, objectId='ignored-object', selected=selection),
        authorization='Bearer vendor-token', x_lognex_request_id='request', db=db)
    assert result == {'action': 'showPopup', 'params': {
        'popupName': 'scan-popup', 'popupParameters': {'kind': kind, 'entryMode': 'select'}}}
    verify.assert_awaited_once_with('Bearer vendor-token')
    save.assert_awaited_once_with('request', 'button:account', result)


@pytest.mark.parametrize('button,extension,object_id,selection,kind', [
    ('start-shipment', 'document.demand.edit', 'shipment', None, 'demand'),
    ('start-acceptance', 'document.supply.list', None, [{'id': 'receipt'}], 'supply'),
    ('start-writeoff', 'document.loss.edit', 'loss', None, 'loss'),
])
async def test_contextual_launch_keeps_document_binding(button_env, button, extension, object_id, selection, kind):
    db, _, _ = button_env
    result = await vendor.handle_button('app', 'account', vendor.ButtonRequest(
        buttonName=button, extensionPoint=extension, objectId=object_id, selected=selection), db=db)
    assert result['params']['popupParameters'] == {
        'kind': kind, 'msObjectId': object_id or selection[0]['id']}


@pytest.mark.parametrize('button,extension', [
    ('start-acceptance', 'document.supply.list'),
    ('unknown', 'document.demand.list'),
    ('open-shipments', 'document.supply.list'),
    ('open-acceptance', 'document.demand.edit'),
])
async def test_missing_context_and_mismatched_picker_do_not_open(button_env, button, extension):
    db, _, _ = button_env
    result = await vendor.handle_button('app', 'account', vendor.ButtonRequest(
        buttonName=button, extensionPoint=extension), db=db)
    assert result['action'] == 'showNotification'
    db.execute.assert_not_awaited()


async def test_unselected_launch_requires_active_integration(button_env):
    db, _, _ = button_env
    db.execute.return_value.scalar_one_or_none = lambda: None
    result = await vendor.handle_button('app', 'account', vendor.ButtonRequest(
        buttonName='open-shipments', extensionPoint='document.demand.list'), db=db)
    assert result['action'] == 'showNotification'
    assert result['params']['text'] == 'Решение не активировано на аккаунте.'


def test_descriptor_and_handler_agree_on_unselected_buttons():
    root = ET.parse(Path(__file__).resolve().parents[2] / 'moysklad-descriptor.xml').getroot()
    ns = '{https://apps-api.moysklad.ru/xml/ns/appstore/app/v2}'
    buttons = {b.attrib['name']: b for b in root.find(f'{ns}buttons')}
    for name, (extension, _) in vendor.UNSELECTED_BUTTONS.items():
        button = buttons[name]
        assert button.attrib['useSelected'] == 'false'
        assert [child.tag.removeprefix(ns) for child in button.find(f'{ns}locations')] == [extension]
    assert all(buttons[name].attrib.get('useSelected', 'true') == 'true'
               for name in ('start-shipment', 'start-acceptance', 'start-writeoff'))
