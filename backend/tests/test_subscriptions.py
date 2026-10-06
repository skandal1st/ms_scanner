from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api import moysklad_vendor as vendor, tsd
from app.core.config import settings
from app.services import subscriptions as sub

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def tariffs(monkeypatch):
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_BASIC_ID', 'basic-id')
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_TSD5_ID', 'five-id')
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_UNLIMITED_ID', 'unlimited-id')


def integration(tariff='five-id', **kwargs):
    return NS(subscription_managed=True, subscription_active=True, subscription_tariff_id=tariff,
        subscription_trial=False, subscription_expires_at=NOW + timedelta(days=1), **kwargs)


@pytest.mark.parametrize('tariff,count,pair,use', [
    ('basic-id', 0, False, False), ('basic-id', 3, False, False),
    ('five-id', 4, True, True), ('five-id', 5, False, True),
    ('five-id', 6, False, False), ('unlimited-id', 100, True, True),
    ('unknown', 0, False, False),
])
def test_tariff_limits(tariff, count, pair, use):
    state = sub.subscription_state(integration(tariff), count, NOW)
    assert (state.can_pair, state.can_use_tsd) == (pair, use)


@pytest.mark.parametrize('expired', [False, True])
def test_inactive_or_expired_subscription_blocks_tsd(expired):
    row = integration('unlimited-id')
    if expired:
        row.subscription_expires_at = NOW
    else:
        row.subscription_active = False
    state = sub.subscription_state(row, 2, NOW)
    assert not state.active and not state.can_pair and not state.can_use_tsd


def test_basic_subscription_keeps_desktop_access():
    assert sub.subscription_state(integration('basic-id'), 0, NOW).active


def test_unmanaged_accounts_keep_existing_access():
    assert sub.subscription_state(None, 80, NOW).can_pair
    assert sub.subscription_state(NS(subscription_managed=False), 80, NOW).can_use_tsd


def test_duplicate_or_empty_tariff_configuration_does_not_grant_access(monkeypatch):
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_UNLIMITED_ID', 'five-id')
    assert not sub.subscription_state(integration(), 0, NOW).active
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_BASIC_ID', '')
    assert not sub.subscription_state(integration(''), 0, NOW).active


async def test_pairing_locks_account_before_loading_subscription_and_count(monkeypatch):
    row = integration()
    row.subscription_expires_at = None
    db = NS(execute=AsyncMock(side_effect=[NS(scalar_one_or_none=lambda: NS(is_active=True)),
        NS(scalar_one_or_none=lambda: row), NS(scalar_one=lambda: 5)]))
    with pytest.raises(HTTPException) as error:
        await sub.require_tsd_subscription(db, uuid4(), pairing=True)
    assert error.value.status_code == 403
    calls = db.execute.await_args_list
    assert 'FOR UPDATE' in str(calls[0].args[0])
    assert 'tsd_devices.is_active IS true' in str(calls[2].args[0])
    assert 'tsd_devices.user_id' in str(calls[2].args[0])


async def test_connected_device_checks_subscription_each_request(monkeypatch):
    device = NS(id=uuid4(), user_id=uuid4(), is_active=True)
    monkeypatch.setattr(tsd, 'decode_token', lambda _: {'sub': str(device.user_id), 'type': 'tsd_access', 'device_id': str(device.id)})
    guard = AsyncMock(side_effect=HTTPException(403, 'Подписка истекла'))
    monkeypatch.setattr(tsd, 'require_tsd_subscription', guard)
    db = NS(get=AsyncMock(return_value=device))
    with pytest.raises(HTTPException) as error:
        await tsd.get_tsd_device(NS(credentials='token'), db)
    assert error.value.status_code == 403
    guard.assert_awaited_once_with(db, device.user_id)


async def test_exchange_rechecks_limit_before_creating_device(monkeypatch):
    import json
    workplace = NS(id=uuid4(), user_id=uuid4())
    redis = NS(getdel=AsyncMock(return_value=json.dumps({'workplace_id': str(workplace.id),
        'user_id': str(workplace.user_id)})), aclose=AsyncMock())
    monkeypatch.setattr(tsd.aioredis, 'from_url', lambda _: redis)
    guard = AsyncMock(side_effect=HTTPException(403, 'Достигнут лимит ТСД'))
    monkeypatch.setattr(tsd, 'require_tsd_subscription', guard)
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: workplace)),
        add=AsyncMock(), commit=AsyncMock())
    with pytest.raises(HTTPException):
        await tsd.exchange_pairing(tsd.PairingExchangeRequest(code='x' * 32), db)
    guard.assert_awaited_once_with(db, workplace.user_id, pairing=True)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


async def test_duplicate_callback_does_not_change_subscription(monkeypatch):
    monkeypatch.setattr(vendor, '_verify_vendor_jwt', AsyncMock())
    monkeypatch.setattr(vendor, '_idempotent_response', AsyncMock(return_value={'status': 'Activated'}))
    db = NS(execute=AsyncMock(), commit=AsyncMock())
    await vendor.activate('app-id', 'account-id', vendor.ActivateRequest(appUid='app', cause='TariffChanged'), Response(), db, None, 'request-id')
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize('cause', ['Install', 'TariffChanged', 'Autoprolongation', 'Resume'])
async def test_callback_stores_subscription_without_erasing_api_token(monkeypatch, cause):
    row = integration(user_id=uuid4(), moysklad_token='encrypted-existing')
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: row)), refresh=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(vendor, '_verify_vendor_jwt', AsyncMock())
    monkeypatch.setattr(vendor, '_idempotent_response', AsyncMock(return_value=None))
    monkeypatch.setattr(vendor, '_save_idempotent', AsyncMock())
    body = vendor.ActivateRequest(appUid='app', cause=cause, subscription={
        'tariffId': 'unlimited-id', 'trial': True, 'expiryMoment': '2026-11-06T00:00:00+03:00'})
    await vendor.activate('app-id', 'account-id', body, Response(), db, None, None)
    assert row.subscription_tariff_id == 'unlimited-id' and row.subscription_trial
    assert row.subscription_active and row.subscription_updated_at
    assert row.moysklad_token == 'encrypted-existing'


async def test_deactivation_blocks_previously_connected_devices(monkeypatch):
    row = integration(user_id=uuid4(), moysklad_token='encrypted')
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: row)), refresh=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(vendor, '_verify_vendor_jwt', AsyncMock())
    monkeypatch.setattr(vendor, '_idempotent_response', AsyncMock(return_value=None))
    monkeypatch.setattr(vendor, '_save_idempotent', AsyncMock())
    await vendor.deactivate('app-id', 'account-id', db, None, None)
    assert not row.subscription_active and row.moysklad_token is None
    assert not sub.subscription_state(row, 2, NOW).can_use_tsd


def test_callback_rejects_ambiguous_expiry():
    with pytest.raises(ValidationError):
        vendor.Subscription(expiryMoment='2026-11-06T00:00:00')


def test_downgrade_can_be_resolved_by_revoking_excess_devices():
    row = integration()
    assert not sub.subscription_state(row, 6, NOW).can_use_tsd
    assert sub.subscription_state(row, 5, NOW).can_use_tsd
    assert sub.subscription_state(row, 4, NOW).can_pair
