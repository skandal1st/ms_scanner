import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import mark_history as api
from app.api.deps import get_active_organization_profile
from app.core.config import settings
from app.core.security import encrypt_token
from app.db.models import User, OrganizationProfile, MarkHistorySnapshot
from app.services import mark_history_refresh as worker
from app.services.mark_history import AUTH_ERROR
from app.worker.tasks import refresh_mark_history_task

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
RAW = '04603743309261GKgWkbWAAAAdW6a'
CIS = RAW[:21]
KEY = '04603743309261|GKgWkbW'


@pytest.fixture
async def context(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        other = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add_all([user, other])
        await db.flush()
        profiles = [OrganizationProfile(id=uuid4(), user_id=owner.id, name=f'Юрлицо {n}',
            cz_token=encrypt_token('fixture-secret'), cz_token_expires_at=datetime.now(timezone.utc)+timedelta(hours=2),
            cz_product_groups=['otp']) for n, owner in enumerate([user, user, other])]
        db.add_all(profiles)
        await db.commit()
    monkeypatch.setattr(api.settings, 'CZ_MOCK_MODE', False)
    monkeypatch.setattr(refresh_mark_history_task, 'delay', Mock())
    monkeypatch.setattr(worker, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(worker, 'get_cached_pg', AsyncMock(return_value='otp'))
    yield factory, user, profiles
    await engine.dispose()


async def claim(factory, user, profile, *, code=RAW, force=False):
    async with factory() as db:
        return await api.refresh_history(api.HistoryRequest(code=code, force=force), user, profile, db)


async def saved(factory, profile):
    async with factory() as db:
        return (await db.execute(select(MarkHistorySnapshot).where(
            MarkHistorySnapshot.organization_profile_id==profile.id, MarkHistorySnapshot.mark_key==KEY))).scalar_one()


async def test_concurrent_requests_claim_once_and_share_canonical_identity(context):
    factory, user, profiles = context
    first, second = await asyncio.gather(claim(factory,user,profiles[0]),claim(factory,user,profiles[0],code=CIS))
    assert first.refreshing and second.refreshing
    refresh_mark_history_task.delay.assert_called_once()
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(MarkHistorySnapshot).where(
            MarkHistorySnapshot.organization_profile_id==profiles[0].id))).scalar_one()==1


async def test_snapshot_is_separate_for_each_profile_and_user(context):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    async with factory() as db:
        assert (await api.read_history(RAW,user,profiles[1],db)).current is None
        assert not (await api.read_history(RAW,NS(id=profiles[2].user_id),profiles[0],db)).refreshing
        with pytest.raises(HTTPException) as error:
            await get_active_organization_profile(profiles[2].id,user,db)
        assert error.value.status_code==403
    await claim(factory,user,profiles[1])
    assert refresh_mark_history_task.delay.call_count==2


async def test_background_result_and_cache_do_not_repeat_external_fetch(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    fetch=AsyncMock(return_value={'current':{'status':'WITHDRAWN','owner_name':'Продавец'},
        'events':[], 'info_error':None,'history_error':None})
    monkeypatch.setattr(worker,'fetch_history',fetch)
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    result=await claim(factory,user,profiles[0],code=CIS)
    assert not result.refreshing and result.current['status']=='WITHDRAWN' and result.events==[]
    assert result.info_checked_at and result.history_checked_at
    refresh_mark_history_task.delay.assert_called_once()
    assert 'fixture-secret' not in result.model_dump_json()


async def test_failed_refresh_preserves_last_success_and_dates(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    first=AsyncMock(return_value={'current':{'owner_name':'Сохранённый владелец'},
        'events':[{'status':'INTRODUCED'}],'info_error':None,'history_error':None})
    monkeypatch.setattr(worker,'fetch_history',first)
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    old=await saved(factory,profiles[0])
    await claim(factory,user,profiles[0],force=True)
    row=await saved(factory,profiles[0])
    monkeypatch.setattr(worker,'fetch_history',AsyncMock(return_value={'current':None,'events':None,
        'info_error':AUTH_ERROR,'history_error':AUTH_ERROR}))
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    result=await saved(factory,profiles[0])
    assert not result.refreshing and result.current==old.current and result.events==old.events
    assert result.info_checked_at==old.info_checked_at and result.history_checked_at==old.history_checked_at
    assert result.info_error==AUTH_ERROR


async def test_partial_history_failure_keeps_old_history_but_updates_current(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    old_time=datetime.now(timezone.utc)-timedelta(days=1)
    async with factory() as db:
        old=await db.get(MarkHistorySnapshot,row.id)
        old.events=[{'owner_inn':'previous'}];old.history_checked_at=old_time
        await db.commit()
    monkeypatch.setattr(worker,'fetch_history',AsyncMock(return_value={'current':{'status':'WITHDRAWN'},
        'events':None,'info_error':None,'history_error':'Нет доступа к истории'}))
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    result=await saved(factory,profiles[0])
    assert result.current=={'status':'WITHDRAWN'} and result.events==[{'owner_inn':'previous'}]
    assert result.history_checked_at==old_time and result.history_error


async def test_stale_task_cannot_overwrite_new_refresh(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    old=await saved(factory,profiles[0])
    async def fetch(*args):
        async with factory() as db:
            row=await db.get(MarkHistorySnapshot,old.id)
            row.refresh_id=uuid4();row.current={'owner_name':'Новый ответ'}
            await db.commit()
        return {'current':{'owner_name':'Старый ответ'},'events':[],'info_error':None,'history_error':None}
    monkeypatch.setattr(worker,'fetch_history',fetch)
    await worker.refresh_snapshot(str(old.id),str(old.refresh_id))
    result=await saved(factory,profiles[0])
    assert result.current=={'owner_name':'Новый ответ'} and result.refreshing


async def test_auth_expiry_and_broker_failure_do_not_erase_evidence(context):
    factory,user,profiles=context
    profile=profiles[0]
    profile.cz_token_expires_at=datetime.now(timezone.utc)-timedelta(hours=1)
    result=await claim(factory,user,profile)
    assert not result.refreshing and result.info_error==AUTH_ERROR
    refresh_mark_history_task.delay.assert_not_called()
    profile.cz_token_expires_at=datetime.now(timezone.utc)+timedelta(hours=1)
    refresh_mark_history_task.delay.side_effect=RuntimeError('broker')
    result=await claim(factory,user,profile,force=True)
    assert not result.refreshing and 'Не удалось запустить' in result.history_error


async def test_expired_claim_can_be_retried_and_read_does_not_touch_cz(context):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    async with factory() as db:
        current=await db.get(MarkHistorySnapshot,row.id)
        current.requested_at=datetime.now(timezone.utc)-timedelta(minutes=3)
        await db.commit()
        result=await api.read_history(RAW,user,profiles[0],db)
        assert not result.refreshing and 'не завершилось' in result.info_error
    await claim(factory,user,profiles[0])
    updated=await saved(factory,profiles[0])
    assert updated.refresh_id!=row.refresh_id
    assert refresh_mark_history_task.delay.call_count==2


async def test_later_private_answer_keeps_last_known_owner_with_original_timestamp(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    old_time=datetime.now(timezone.utc)-timedelta(days=2)
    async with factory() as db:
        old=await db.get(MarkHistorySnapshot,row.id)
        old.current={'owner_name':'Последний владелец','owner_inn':'123456789012','region':'Ростовская область'}
        old.info_checked_at=old_time
        await db.commit()
    monkeypatch.setattr(worker,'fetch_history',AsyncMock(return_value={'current':{'status':'WITHDRAWN','owner_name':None,'owner_inn':None},
        'events':None,'info_error':None,'history_error':'Нет доступа'}))
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    result=await saved(factory,profiles[0])
    assert result.current['owner_name']=='Последний владелец' and result.current['owner_is_stale']
    assert result.current['owner_checked_at']==old_time.isoformat()
    assert result.info_checked_at>old_time


async def test_empty_new_history_does_not_erase_previously_available_events(context,monkeypatch):
    factory,user,profiles=context
    await claim(factory,user,profiles[0])
    row=await saved(factory,profiles[0])
    old_time=datetime.now(timezone.utc)-timedelta(days=1)
    async with factory() as db:
        old=await db.get(MarkHistorySnapshot,row.id)
        old.events=[{'status':'WITHDRAWN'}];old.history_checked_at=old_time
        await db.commit()
    monkeypatch.setattr(worker,'fetch_history',AsyncMock(return_value={'current':None,'events':[],
        'info_error':'Сведения не получены','history_error':None}))
    await worker.refresh_snapshot(str(row.id),str(row.refresh_id))
    result=await saved(factory,profiles[0])
    assert result.events==[{'status':'WITHDRAWN'}] and result.history_checked_at==old_time
    assert 'ранее сохранённая история' in result.history_error
