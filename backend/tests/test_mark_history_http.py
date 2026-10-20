"""The history endpoints retain the same organization authorization as Settings."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.api import mark_history
from app.api.deps import get_active_organization_profile
from tests.test_release_processing import Result


@pytest.mark.parametrize('method', ['GET','POST'])
async def test_invalid_identity_is_400_and_does_not_queue_cz_work(method):
    app=FastAPI();app.include_router(mark_history.router)
    app.dependency_overrides[mark_history.get_current_user]=lambda:NS(id=uuid4())
    app.dependency_overrides[get_active_organization_profile]=lambda:NS(id=uuid4(),name='Our company')
    db=NS(execute=AsyncMock())
    app.dependency_overrides[mark_history.get_db]=lambda:db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.request(method,'/marks/history',params={'code':'not-a-mark'} if method=='GET' else None,
                                      json={'code':'not-a-mark'} if method=='POST' else None)
    assert response.status_code==400 and 'Введите код' in response.json()['detail']
    db.execute.assert_not_awaited()


async def test_poll_response_contains_only_scoped_snapshot_and_no_secrets():
    app=FastAPI();app.include_router(mark_history.router)
    user=NS(id=uuid4());profile=NS(id=uuid4(),name='Our company',cz_token='do-not-expose')
    row=NS(current={'owner_name':'Owner'},events=[],info_checked_at=datetime.now(timezone.utc),
        history_checked_at=None,requested_at=None,refreshing=False,info_error=None,history_error='Нет доступа')
    db=NS(execute=AsyncMock(return_value=Result(row)))
    app.dependency_overrides[mark_history.get_current_user]=lambda:user
    app.dependency_overrides[get_active_organization_profile]=lambda:profile
    app.dependency_overrides[mark_history.get_db]=lambda:db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/marks/history',params={'code':'04603743309261GKgWkbWAAAAdW6a'})
    assert response.status_code==200
    body=response.json()
    assert body['organization_profile_id']==str(profile.id) and body['events']==[]
    assert body['history_error']=='Нет доступа' and 'do-not-expose' not in response.text
    statement=db.execute.call_args.args[0]
    params=statement.compile().params
    assert user.id in params.values() and profile.id in params.values()
