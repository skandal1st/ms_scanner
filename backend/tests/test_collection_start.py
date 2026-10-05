"""Collection start survives reopening, parallel terminals and partial MS failures."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import documents
from app.core.config import settings
from app.db import session
from app.db.models import Document, DocumentKind, DocumentStatus, OrganizationProfile, User
from app.services.collection_start import start_collection

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='requires isolated PostgreSQL')


async def context(monkeypatch, enabled=True):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(session, 'engine', engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='')
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Документ',
            shipment_start_state_id=uuid4() if enabled else None,
            customer_order_start_state_id=uuid4() if enabled else None)
        other = OrganizationProfile(id=uuid4(), user_id=user.id, name='Другое', is_default=True,
            shipment_start_state_id=uuid4())
        doc = Document(id=uuid4(), user_id=user.id, name='Audit', moysklad_id=str(uuid4()),
            organization_profile_id=profile.id, kind=DocumentKind.demand, moysklad_customer_order_id='order')
        db.add(user)
        await db.flush()
        db.add_all([profile, other])
        await db.flush()
        db.add(doc)
        await db.commit()
    ms = NS(get_document=AsyncMock(return_value={}),
        get_shipment_states=AsyncMock(return_value=[{'id': str(profile.shipment_start_state_id)}]),
        get_customer_order_states=AsyncMock(return_value=[{'id': str(profile.customer_order_start_state_id)}]),
        resolve_shipment_order=AsyncMock(return_value='order'), change_collection_state=AsyncMock())
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=ms))
    return engine, factory, user, profile, doc, ms


async def test_open_applies_document_profile_once_and_checks_owner(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        assert ms.change_collection_state.call_args_list[0].args == ('demand', doc.moysklad_id, str(profile.shipment_start_state_id))
        assert ms.change_collection_state.call_args_list[1].args == ('customerorder', 'order', str(profile.customer_order_start_state_id))
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['done']
        assert ms.change_collection_state.await_count == 2
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await documents.begin_collection(doc.id, NS(id=uuid4()), db)
            assert error.value.status_code == 404
    finally:
        await engine.dispose()


async def test_two_terminals_open_once_and_duplicate_local_document_reuses_marker(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async def open_doc():
            async with factory() as db:
                await documents.begin_collection(doc.id, user, db)
        await asyncio.wait_for(asyncio.gather(open_doc(), open_doc()), timeout=10)
        assert ms.change_collection_state.await_count == 2
        async with factory() as db:
            duplicate = Document(user_id=user.id, name='Old duplicate', moysklad_id=doc.moysklad_id,
                organization_profile_id=profile.id, kind=DocumentKind.demand)
            db.add(duplicate)
            await db.commit()
            await start_collection(db, duplicate, ms)
        assert ms.change_collection_state.await_count == 2
    finally:
        await engine.dispose()


async def test_partial_failure_retries_only_order_step(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    ms.change_collection_state.side_effect = [None, httpx.ReadTimeout('audit'), None]
    try:
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await documents.begin_collection(doc.id, user, db)
            assert error.value.status_code == 502
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['shipment_done']
            assert not saved.upd_meta['collection_start'].get('done')
            await documents.begin_collection(doc.id, user, db)
        assert [call.args[0] for call in ms.change_collection_state.call_args_list] == ['demand', 'customerorder', 'customerorder']
    finally:
        await engine.dispose()


async def test_disabled_setting_and_orderless_shipment(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False)
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_not_awaited()
        ms.get_document.assert_not_awaited()
    finally:
        await engine.dispose()
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    ms.resolve_shipment_order.return_value = None
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        assert ms.change_collection_state.await_count == 1
        assert ms.change_collection_state.call_args.args[0] == 'demand'
    finally:
        await engine.dispose()


async def test_missing_state_or_wrong_order_prevents_all_status_writes(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        ms.get_customer_order_states.return_value = []
        async with factory() as db:
            with pytest.raises(HTTPException):
                await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_not_awaited()
        ms.get_customer_order_states.return_value = [{'id': str(profile.customer_order_start_state_id)}]
        ms.resolve_shipment_order.side_effect = ValueError('Связь отгрузки с заказом изменилась')
        async with factory() as db:
            with pytest.raises(HTTPException):
                await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_not_awaited()
    finally:
        await engine.dispose()
