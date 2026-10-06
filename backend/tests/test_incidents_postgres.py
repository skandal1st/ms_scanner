"""Run against a disposable migrated PostgreSQL database with AUDIT_POSTGRES=1."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core import monitoring
from app.core.config import settings
from app.core.diagnostics import operation_scope, capture_request
from app.db import session
from app.db.models import User, Document, Incident, MonitoringDelivery, DocumentKind, DocumentStatus, CzLog
from app.services import incidents
from app.api.support import get_incident, list_incidents

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='requires isolated PostgreSQL')


async def setup(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    user_id, doc_id = uuid4(), uuid4()
    async with factory() as db:
        db.add(User(id=user_id, email=f'{user_id}@example.test', password_hash=''))
        await db.flush()
        db.add(Document(id=doc_id, user_id=user_id, name='Диагностика', kind=DocumentKind.supply,
                        status=DocumentStatus.draft, error_message='Не раскрылись короба'))
        await db.commit()
    return engine, factory, user_id, doc_id


async def test_concurrent_failures_create_one_card_and_one_notification(monkeypatch):
    engine, factory, user_id, doc_id = await setup(monkeypatch)
    try:
        async def fail():
            with operation_scope(doc_id, user_id, 'cz.expand'):
                capture_request('cz', 'POST', 'https://x?pg=otp', ['sent'], 404,
                                {'error': 'missing', 'access_token': 'secret'}, original_code='original')
                return await incidents.record('boxes_unexpanded', 'Короба не раскрылись')
        ids = await asyncio.gather(fail(), fail(), fail())
        assert ids[0] is not None and len(set(ids)) == 1
        async with factory() as db:
            card = (await db.execute(select(Incident).where(Incident.document_id == doc_id))).scalar_one()
            assert card.occurrences == 3
            assert card.diagnostics['requests'][0]['original_code'] == 'original'
            assert card.diagnostics['requests'][0]['response']['access_token'] == '<redacted>'
            assert (await db.execute(select(func.count()).select_from(MonitoringDelivery)
                                     .where(MonitoringDelivery.incident_id == card.id))).scalar_one() == 1
            doc = await db.get(Document, doc_id)
            assert str(card.id) in doc.error_message
            with pytest.raises(HTTPException) as exc:
                await get_incident(card.id, SimpleNamespace(id=uuid4()), db)
            assert exc.value.status_code == 404
            assert await list_incidents(None, None, 50, SimpleNamespace(id=uuid4()), db) == []
    finally:
        await engine.dispose()


async def test_delivery_failure_retry_resolution_and_reopening(monkeypatch):
    engine, factory, user_id, doc_id = await setup(monkeypatch)
    try:
        with operation_scope(doc_id, user_id, 'ms.write'):
            card_id = await incidents.record('ms.error', 'МойСклад недоступен')
        assert card_id
        monkeypatch.setattr(monitoring, '_enabled', lambda: True)
        transport = AsyncMock(side_effect=httpx.ReadTimeout('timeout'))
        monkeypatch.setattr(monitoring, 'send_payload', transport)
        assert await incidents.deliver_pending() == 0
        async with factory() as db:
            delivery = (await db.execute(select(MonitoringDelivery).where(
                MonitoringDelivery.incident_id == card_id))).scalar_one()
            assert delivery.attempts == 1 and delivery.delivered_at is None
            assert delivery.next_attempt_at > datetime.now(timezone.utc)
            delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        transport.side_effect = None
        assert await incidents.deliver_pending() == 1
        assert await incidents.deliver_pending() == 0
        assert transport.call_args.kwargs['delivery_id']
        with operation_scope(doc_id, user_id, 'ms.write'):
            await incidents.resolve_current()
            await incidents.resolve_current()
        async with factory() as db:
            card = (await db.execute(select(Incident).where(Incident.document_id == doc_id))).scalar_one()
            assert card.status == 'resolved'
            rows = (await db.execute(select(MonitoringDelivery).where(
                MonitoringDelivery.incident_id == card.id))).scalars().all()
            assert len(rows) == 2
        with operation_scope(doc_id, user_id, 'ms.write'):
            assert await incidents.record('ms.error', 'МойСклад недоступен') == card_id
        async with factory() as db:
            card = await db.get(Incident, card.id)
            assert card.status == 'open' and card.occurrences == 2
            assert (await db.execute(select(func.count()).select_from(MonitoringDelivery)
                                     .where(MonitoringDelivery.incident_id == card.id))).scalar_one() == 3
    finally:
        await engine.dispose()


async def test_cz_log_correlates_original_code_and_redacts_secrets(monkeypatch):
    from app.services import cz_logger
    engine, factory, user_id, doc_id = await setup(monkeypatch)
    monkeypatch.setattr(cz_logger, 'AsyncSessionLocal', factory)
    try:
        with operation_scope(doc_id, user_id, 'cz.expand') as operation:
            await cz_logger.log_cz_request('POST', 'https://x.test/cises/info?pg=otp&apikey=secret',
                ['changed-code'], 404, {'error': 'not found', 'access_token': 'secret'}, 20,
                original_code='original-code')
        async with factory() as db:
            log = (await db.execute(select(CzLog).where(CzLog.document_id == doc_id))).scalar_one()
            assert str(log.trace_id) == operation.trace_id and log.user_id == user_id
            assert log.original_code == 'original-code'
            assert log.request_body == ['changed-code']
            assert log.response_body['error'] == 'not found'
            assert log.response_body['access_token'] == '<redacted>'
            assert 'secret' not in log.request_url
    finally:
        await engine.dispose()


async def test_different_failure_causes_get_different_cards(monkeypatch):
    engine, factory, user_id, doc_id = await setup(monkeypatch)
    try:
        with operation_scope(doc_id, user_id, 'ms.write'):
            a = await incidents.record('process_document.error', 'Неверные марки', reason='ValueError:marks')
            b = await incidents.record('process_document.error', 'Нет товара', reason='ValueError:product')
        assert a and b and a != b
    finally:
        await engine.dispose()
