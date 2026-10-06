"""Synchronize already installed marketplace accounts: python -m app.sync_subscriptions --app-id UUID."""
import argparse
import asyncio
from uuid import UUID

import httpx
from sqlalchemy import select

from app.api.auth import _build_vendor_jwt
from app.api.moysklad_vendor import Subscription
from app.core.config import settings
from app.db.models import Integration, User
from app.db.session import AsyncSessionLocal
from app.services.subscriptions import update_subscription


async def synchronize(app_id: UUID):
    if not settings.MOYSKLAD_APP_UID or not settings.MOYSKLAD_VENDOR_SECRET_KEY:
        raise RuntimeError('Vendor API не настроен')
    async with AsyncSessionLocal() as db:
        accounts = (await db.execute(select(Integration.user_id, Integration.moysklad_account_id)
            .where(Integration.moysklad_account_id.is_not(None)))).all()
    async with httpx.AsyncClient(timeout=15) as client:
        for user_id, account_id in accounts:
            async with AsyncSessionLocal() as db:
                # Serialize the snapshot with callbacks and pairing for this account.
                await db.execute(select(User).where(User.id == user_id).with_for_update())
                integration = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one()
                response = await client.get(
                    f'{settings.MOYSKLAD_VENDOR_BASE}/apps/{app_id}/{account_id}/status',
                    headers={'Authorization': f'Bearer {_build_vendor_jwt()}', 'Accept-Encoding': 'gzip'},
                )
                if response.status_code == 404:
                    integration.subscription_active = False
                else:
                    response.raise_for_status()
                    data = response.json()
                    # Suspended installations can omit subscription; they still belong to the marketplace.
                    integration.subscription_managed = True
                    if data.get('subscription'):
                        update_subscription(integration, Subscription.model_validate(data['subscription']),
                            active=data.get('status') in ('Activated', 'SettingsRequired'))
                    else:
                        integration.subscription_active = False
                await db.commit()
                print(f'Подписка аккаунта {account_id} синхронизирована')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Синхронизация подписок из МойСклад')
    parser.add_argument('--app-id', type=UUID, required=True)
    asyncio.run(synchronize(parser.parse_args().app_id))
