from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api import tsd
from app.core.config import settings
from app.core.security import (
    create_access_token, create_refresh_token, create_tsd_access_token,
    create_tsd_refresh_token, decode_token,
)


def context(monkeypatch):
    device = NS(id=uuid4(), user_id=uuid4(), is_active=True, last_seen_at=None)
    db = NS(get=AsyncMock(return_value=device), commit=AsyncMock(), add=AsyncMock())
    guard = AsyncMock()
    scope = AsyncMock()
    monkeypatch.setattr(tsd, 'require_tsd_subscription', guard)
    monkeypatch.setattr(tsd, '_device_scope', scope)
    claims = {'sub': str(device.user_id), 'device_id': str(device.id)}
    return device, db, guard, scope, claims


@pytest.mark.parametrize('create', [create_tsd_refresh_token, create_tsd_access_token])
async def test_refresh_reuses_device_and_checks_access(monkeypatch, create):
    device, db, guard, scope, claims = context(monkeypatch)
    result = await tsd.refresh_device_auth(tsd.DeviceRefreshRequest(refresh_token=create(claims)), db)
    access, refresh = decode_token(result.access_token), decode_token(result.refresh_token)
    assert access['type'] == 'tsd_access' and refresh['type'] == 'tsd_refresh'
    assert access['device_id'] == refresh['device_id'] == str(device.id)
    lifetime_difference = int(timedelta(days=90, hours=-12).total_seconds())
    assert abs(refresh['exp'] - access['exp'] - lifetime_difference) <= 1
    guard.assert_awaited_once_with(db, device.user_id)
    scope.assert_awaited_once_with(db, device)
    db.commit.assert_awaited_once()
    db.add.assert_not_called()
    assert device.last_seen_at is not None


@pytest.mark.parametrize('create', [create_access_token, create_refresh_token])
async def test_regular_account_tokens_cannot_renew_terminal(monkeypatch, create):
    _, db, _, _, claims = context(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tsd.refresh_device_auth(tsd.DeviceRefreshRequest(refresh_token=create(claims)), db)
    assert error.value.status_code == 401
    db.get.assert_not_awaited()


@pytest.mark.parametrize('change', ['revoked', 'missing', 'other_user', 'expired', 'bad_signature'])
async def test_invalid_binding_cannot_refresh(monkeypatch, change):
    device, db, _, _, claims = context(monkeypatch)
    if change == 'revoked':
        device.is_active = False
    elif change == 'missing':
        db.get.return_value = None
    elif change == 'other_user':
        claims['sub'] = str(uuid4())
    token = create_tsd_refresh_token(claims)
    if change == 'expired':
        claims.update(type='tsd_refresh', exp=datetime.now(timezone.utc) - timedelta(seconds=1))
        token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    elif change == 'bad_signature':
        token = jwt.encode({**claims, 'type': 'tsd_refresh'}, 'wrong-secret', algorithm=settings.ALGORITHM)
    with pytest.raises(HTTPException) as error:
        await tsd.refresh_device_auth(tsd.DeviceRefreshRequest(refresh_token=token), db)
    assert error.value.status_code == 401
    db.commit.assert_not_awaited()


@pytest.mark.parametrize('restriction', ['subscription', 'workplace'])
async def test_refresh_preserves_subscription_and_scope_restrictions(monkeypatch, restriction):
    _, db, guard, scope, claims = context(monkeypatch)
    (guard if restriction == 'subscription' else scope).side_effect = HTTPException(403, 'Доступ отключён')
    with pytest.raises(HTTPException) as error:
        await tsd.refresh_device_auth(tsd.DeviceRefreshRequest(refresh_token=create_tsd_refresh_token(claims)), db)
    assert error.value.status_code == 403
    db.commit.assert_not_awaited()


async def test_refresh_token_cannot_be_used_as_terminal_access(monkeypatch):
    _, db, _, _, claims = context(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tsd.get_tsd_device(NS(credentials=create_tsd_refresh_token(claims)), db)
    assert error.value.status_code == 401
    db.get.assert_not_awaited()
