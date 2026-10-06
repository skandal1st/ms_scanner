"""Run only against an isolated, migrated PostgreSQL database (AUDIT_POSTGRES=1)."""
import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import User, Integration, OrganizationProfile, Workplace, TsdDevice
from app.services.subscriptions import require_tsd_subscription

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='requires isolated PostgreSQL')


async def test_concurrent_pairing_allows_only_fifth_device(monkeypatch):
    monkeypatch.setattr(settings, 'MOYSKLAD_TARIFF_TSD5_ID', 'five-id')
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id, profile_id, workplace_id = uuid4(), uuid4(), uuid4()
    try:
        async with factory() as db:
            db.add(User(id=user_id, email=f'{user_id}@example.test', password_hash=''))
            await db.flush()
            db.add(Integration(user_id=user_id, subscription_managed=True,
                subscription_active=True, subscription_tariff_id='five-id'))
            db.add(OrganizationProfile(id=profile_id, user_id=user_id, name='Тест'))
            await db.flush()
            db.add(Workplace(id=workplace_id, user_id=user_id, organization_profile_id=profile_id, name='Тест'))
            await db.flush()
            db.add_all([TsdDevice(user_id=user_id, workplace_id=workplace_id) for _ in range(4)])
            await db.commit()

        async def pair():
            async with factory() as db:
                try:
                    await require_tsd_subscription(db, user_id, pairing=True)
                    await asyncio.sleep(0.05)  # Keep the lock while the other request arrives.
                    db.add(TsdDevice(user_id=user_id, workplace_id=workplace_id))
                    await db.commit()
                    return 200
                except HTTPException as exc:
                    await db.rollback()
                    return exc.status_code

        results = await asyncio.wait_for(asyncio.gather(pair(), pair()), timeout=10)
        assert sorted(results) == [200, 403]
        async with factory() as db:
            assert (await db.execute(select(func.count()).select_from(TsdDevice)
                .where(TsdDevice.user_id == user_id))).scalar_one() == 5
    finally:
        async with factory() as db:
            for model in (TsdDevice, Workplace, OrganizationProfile, Integration):
                await db.execute(delete(model).where(model.user_id == user_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()
