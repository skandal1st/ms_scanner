"""Marketplace tariff IDs are authoritative; names never grant access."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Integration, TsdDevice, User


class SubscriptionResponse(BaseModel):
    managed: bool
    active: bool
    plan: str | None
    name: str
    price_monthly: int | None
    trial: bool
    expires_at: datetime | None
    active_devices: int
    tsd_limit: int | None
    can_pair: bool
    can_use_tsd: bool
    message: str | None


def subscription_state(integration, count: int, now=None) -> SubscriptionResponse:
    managed = bool(integration and integration.subscription_managed)
    if not managed:
        return SubscriptionResponse(managed=False, active=True, plan=None, name='Вне маркетплейса',
            price_monthly=None, trial=False, expires_at=None, active_devices=count,
            tsd_limit=None, can_pair=True, can_use_tsd=True, message=None)
    plans = (
        (settings.MOYSKLAD_TARIFF_BASIC_ID, 'basic', 'Базовый', 5000, 0),
        (settings.MOYSKLAD_TARIFF_TSD5_ID, 'tsd5', 'Склад', 7000, 5),
        (settings.MOYSKLAD_TARIFF_UNLIMITED_ID, 'unlimited', 'Безлимит', 10000, None),
    )
    matches = [p for p in plans if p[0] and p[0] == integration.subscription_tariff_id]
    plan = matches[0] if len(matches) == 1 else None
    expiry = integration.subscription_expires_at
    now = now or datetime.now(timezone.utc)
    message = None
    if not integration.subscription_active:
        message = 'Подписка МойСклад не активна. Проверьте подписку в маркетплейсе.'
    elif expiry and expiry <= now:
        message = 'Срок подписки истёк. Продлите её в маркетплейсе МойСклад.'
    elif not plan:
        message = 'Тариф МойСклад не настроен. Обратитесь в поддержку.'
    active = message is None
    if active and plan[4] == 0:
        message = 'ТСД недоступны на базовом тарифе. Выберите тариф с ТСД в МойСклад.'
    elif active and plan[4] is not None and count > plan[4]:
        message = f'Подключено {count} ТСД при лимите {plan[4]}. Отключите лишние устройства в настройках.'
    can_use = message is None
    limit = plan[4] if plan else 0
    can_pair = can_use and (limit is None or count < limit)
    if can_use and not can_pair:
        message = 'Достигнут лимит ТСД. Отключите старое устройство или смените тариф в МойСклад.'
    return SubscriptionResponse(managed=True, active=active, plan=plan[1] if plan else None,
        name=plan[2] if plan else 'Неизвестный тариф', price_monthly=plan[3] if plan else None,
        trial=integration.subscription_trial, expires_at=expiry, active_devices=count,
        tsd_limit=limit, can_pair=can_pair, can_use_tsd=can_use, message=message)


async def get_subscription(db: AsyncSession, user_id: UUID, *, lock=False):
    # Pairing and vendor updates serialize on the same account row. Lock before count.
    if lock:
        user = (await db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(403, 'Аккаунт отключён')
    integration = (await db.execute(select(Integration).where(Integration.user_id == user_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()
    count = (await db.execute(select(func.count()).select_from(TsdDevice).where(
        TsdDevice.user_id == user_id, TsdDevice.is_active.is_(True)))).scalar_one()
    return subscription_state(integration, count)


async def require_tsd_subscription(db: AsyncSession, user_id: UUID, *, pairing=False):
    state = await get_subscription(db, user_id, lock=pairing)
    if not (state.can_pair if pairing else state.can_use_tsd):
        raise HTTPException(403, state.message)
    return state


def update_subscription(integration, subscription, *, active=True):
    integration.subscription_managed = True
    integration.subscription_tariff_id = subscription.tariffId
    integration.subscription_trial = bool(subscription.trial)
    integration.subscription_expires_at = subscription.expiryMoment
    integration.subscription_active = active
    integration.subscription_updated_at = datetime.now(timezone.utc)
