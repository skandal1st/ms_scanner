from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.db.models import DocumentStatus
from app.services.acceptance_mode import acceptance_cz_check_enabled
from app.services.supply_positions import prepare_supply_without_cz
from app.services.mark_search import mark_key
from app.api import acceptance, integrations


def test_quantity_only_acceptance_uses_document_quantity_and_retains_fractional_goods():
    plan = [{'product_id': 'tea', 'expected_qty': 80, 'marked': True},
            {'product_id': 'goods', 'expected_qty': 2.5, 'marked': False}]
    rows = prepare_supply_without_cz(plan, {})
    assert [r['quantity'] for r in rows] == [80, 2.5]
    assert all(r['code'] == '' and r['is_barcode'] for r in rows)


@pytest.mark.parametrize('plan,meta', [([], {}),
    ([{'expected_qty': 2}], {}),
    ([{'product_id': 'p', 'expected_qty': 2}], {'physical_plan': [{'product_id': None, 'gtins': []}]}),
])
def test_quantity_mode_does_not_silently_drop_unmapped_positions(plan, meta):
    with pytest.raises(ValueError):
        prepare_supply_without_cz(plan, meta)


@pytest.mark.parametrize('status,meta,expected', [
    (DocumentStatus.accepted, {}, True),
    (DocumentStatus.accepted, {'acceptance_cz_check_enabled': False}, False),
    (DocumentStatus.processing, {'acceptance_cz_check_enabled': True}, True),
])
async def test_processing_and_historical_documents_use_frozen_policy(status, meta, expected):
    db = NS(execute=AsyncMock(side_effect=AssertionError('No current profile lookup')))
    assert await acceptance_cz_check_enabled(db, NS(status=status, upd_meta=meta)) == expected


async def test_disabled_acceptance_does_not_construct_cz_service(monkeypatch):
    profile = NS(acceptance_cz_check_enabled=False, cz_token='encrypted')
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: profile)))
    monkeypatch.setattr(acceptance, 'ChestnyZnakService', lambda **kw: pytest.fail('CZ must not be created'))
    assert await acceptance._maybe_cz_service(NS(id=uuid4()), db, uuid4()) is None


def test_setting_is_returned_even_without_connected_integrations():
    result = integrations._to_response(None, profile=NS(acceptance_cz_check_enabled=False))
    assert result.acceptance_cz_check_enabled is False


@pytest.mark.parametrize('raw,short', [
    ('04670163000207Hm7&lkpAAAAi2gC', '04670163000207Hm7&lkp'),
    ('010467016300020721AbC,123\x1d93TAIL', '(01)04670163000207(21)AbC,123'),
])
def test_full_scanner_code_matches_short_upd_identity(raw, short):
    assert mark_key(raw) == mark_key(short)


def test_case_and_longer_serial_remain_different():
    assert mark_key('010467016300020721AbC,123') != mark_key('010467016300020721AbC,123XYZ')
    assert mark_key('04670163000207Hm7&lkp') != mark_key('04670163000207hm7&lkp')
