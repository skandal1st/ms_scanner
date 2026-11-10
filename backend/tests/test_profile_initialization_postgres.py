"""First-login initialization uses one account-scoped profile under concurrency."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_active_organization_profile
from app.api import organization_profiles
from app.core.config import settings
from app.core.security import encrypt_token
from app.db.models import Document, Integration, OrganizationProfile, User

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


def sessions():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def user(db):
    row = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
    db.add(row)
    await db.flush()
    return row


@pytest.mark.parametrize('with_integration', [False, True])
async def test_concurrent_first_login_creates_one_profile(with_integration):
    engine, factory = sessions()
    try:
        async with factory() as db:
            owner = await user(db)
            other = await user(db)
            legacy = Document(user_id=owner.id, name='Legacy document')
            untouched = Document(user_id=other.id, name='Other account')
            db.add_all([legacy, untouched])
            encrypted = encrypt_token('test-cz-secret')
            if with_integration:
                db.add(Integration(user_id=owner.id, cz_token=encrypted, cz_auth_method='cert',
                                   cz_inn='1234567890', cz_product_groups=['otp']))
            await db.commit()
        arrivals = 0
        ready = asyncio.Event()
        async def enter():
            nonlocal arrivals
            async with factory() as db:
                execute = db.execute
                first = True
                async def synchronized(statement, *args, **kwargs):
                    nonlocal first, arrivals
                    result = await execute(statement, *args, **kwargs)
                    columns = getattr(statement, 'column_descriptions', [])
                    if first and columns and columns[0].get('entity') is OrganizationProfile:
                        first = False
                        arrivals += 1
                        if arrivals == 6:
                            ready.set()
                        await asyncio.wait_for(ready.wait(), timeout=10)
                    return result
                db.execute = synchronized
                profile = await get_active_organization_profile(None, owner, db)
                return profile.id
        ids = await asyncio.wait_for(asyncio.gather(*(enter() for _ in range(6))), timeout=20)
        async with factory() as db:
            rows = (await db.execute(select(OrganizationProfile).where(OrganizationProfile.user_id == owner.id))).scalars().all()
            assert len(rows) == 1
            assert set(ids) == {rows[0].id}
            assert rows[0].is_default and rows[0].name == 'Основное юрлицо'
            assert (await db.get(Document, legacy.id)).organization_profile_id == rows[0].id
            assert (await db.get(Document, untouched.id)).organization_profile_id is None
            if with_integration:
                integration = (await db.execute(select(Integration).where(Integration.user_id == owner.id))).scalar_one()
                assert rows[0].cz_token == encrypted == integration.cz_token
                assert rows[0].cz_auth_method == 'cert' and rows[0].cz_product_groups == ['otp']
            else:
                assert rows[0].cz_token is None
    finally:
        await engine.dispose()


async def test_existing_profile_selection_and_foreign_profile_rejection_do_not_create_defaults():
    engine, factory = sessions()
    try:
        async with factory() as db:
            owner, other = await user(db), await user(db)
            first = OrganizationProfile(user_id=owner.id, name='First', is_default=True)
            second = OrganizationProfile(user_id=owner.id, name='Second')
            foreign = OrganizationProfile(user_id=other.id, name='Foreign', is_default=True)
            db.add_all([first, second, foreign])
            await db.commit()
            assert (await get_active_organization_profile(None, owner, db)).id == first.id
            assert (await get_active_organization_profile(second.id, owner, db)).id == second.id
            with pytest.raises(HTTPException) as error:
                await get_active_organization_profile(foreign.id, owner, db)
            assert error.value.status_code == 403
            empty = await user(db)
            await db.commit()
            with pytest.raises(HTTPException):
                await get_active_organization_profile(foreign.id, empty, db)
            assert not (await db.execute(select(OrganizationProfile).where(OrganizationProfile.user_id == empty.id))).scalars().all()
    finally:
        await engine.dispose()


async def test_first_login_does_not_lock_other_accounts():
    engine, factory = sessions()
    try:
        async with factory() as db:
            blocked, free = await user(db), await user(db)
            await db.commit()
        async with factory() as locker:
            await locker.execute(select(User.id).where(User.id == blocked.id).with_for_update())
            async with factory() as db:
                profile = await asyncio.wait_for(get_active_organization_profile(None, free, db), timeout=5)
                assert profile.user_id == free.id
            await locker.rollback()
    finally:
        await engine.dispose()


async def test_sync_completed_during_first_login_is_reused(monkeypatch):
    engine, factory = sessions()
    try:
        async with factory() as db:
            owner = await user(db)
            db.add(Integration(user_id=owner.id, moysklad_token=encrypt_token('test-ms-secret')))
            await db.commit()
        service = NS(get_organizations=AsyncMock(return_value=[{'id': 'ms-org', 'name': 'MS organization'}]))
        monkeypatch.setattr(organization_profiles, 'MoySkladService', Mock(return_value=service))
        checked = asyncio.Event()
        synced = asyncio.Event()
        async def enter():
            async with factory() as db:
                execute = db.execute
                first = True
                async def synchronized(statement, *args, **kwargs):
                    nonlocal first
                    result = await execute(statement, *args, **kwargs)
                    columns = getattr(statement, 'column_descriptions', [])
                    if first and columns and columns[0].get('entity') is OrganizationProfile:
                        first = False
                        checked.set()
                        await asyncio.wait_for(synced.wait(), timeout=10)
                    return result
                db.execute = synchronized
                return (await get_active_organization_profile(None, owner, db)).id
        async def sync():
            await asyncio.wait_for(checked.wait(), timeout=10)
            async with factory() as db:
                await organization_profiles.sync_profiles(owner, db)
            synced.set()
        profile_id, _ = await asyncio.wait_for(asyncio.gather(enter(), sync()), timeout=20)
        async with factory() as db:
            rows = (await db.execute(select(OrganizationProfile).where(OrganizationProfile.user_id == owner.id))).scalars().all()
            assert len(rows) == 1 and rows[0].id == profile_id
            assert rows[0].moysklad_organization_id == 'ms-org'
    finally:
        await engine.dispose()


async def test_concurrent_syncs_share_the_first_profile(monkeypatch):
    engine, factory = sessions()
    try:
        async with factory() as db:
            owner = await user(db)
            db.add(Integration(user_id=owner.id, moysklad_token=encrypt_token('test-ms-secret')))
            await db.commit()
        arrivals = 0
        ready = asyncio.Event()
        async def organizations():
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)
            return [{'id': 'ms-org', 'name': 'MS organization'}]
        monkeypatch.setattr(organization_profiles, 'MoySkladService', Mock(return_value=NS(get_organizations=organizations)))
        async def sync():
            async with factory() as db:
                return await organization_profiles.sync_profiles(owner, db)
        results = await asyncio.wait_for(asyncio.gather(sync(), sync()), timeout=20)
        async with factory() as db:
            rows = (await db.execute(select(OrganizationProfile).where(OrganizationProfile.user_id == owner.id))).scalars().all()
            assert len(rows) == 1
            assert results[0][0].id == results[1][0].id == rows[0].id
    finally:
        await engine.dispose()
