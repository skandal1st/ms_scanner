"""Collection start survives reopening, parallel terminals and partial MS failures."""
import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import documents
from app.services.collection_time import CollectionStartRequest
from app.core.config import settings
from app.db import session
from app.db.models import Document, DocumentKind, DocumentStatus, OrganizationProfile, User
from app.services.collection_start import start_collection

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='requires isolated PostgreSQL')


async def context(monkeypatch, enabled=True, date_enabled=True):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(session, 'engine', engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='')
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Документ',
            shipment_timezone='Europe/Moscow', shipment_date_on_collection_start=date_enabled,
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
    ms = NS(get_document=AsyncMock(return_value={'customerOrder': {'id': 'order'}}),
        get_shipment_states=AsyncMock(return_value=[{'id': str(profile.shipment_start_state_id)}]),
        get_customer_order_states=AsyncMock(return_value=[{'id': str(profile.customer_order_start_state_id)}]),
        change_collection_state=AsyncMock())
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=ms))
    return engine, factory, user, profile, doc, ms


async def test_open_applies_document_profile_once_and_checks_owner(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        assert ms.change_collection_state.call_args_list[0].args == ('demand', doc.moysklad_id, str(profile.shipment_start_state_id))
        moment = ms.change_collection_state.call_args_list[0].kwargs['moment']
        assert len(moment) == 19
        assert ms.change_collection_state.call_args_list[1].args == ('customerorder', 'order', str(profile.customer_order_start_state_id))
        assert not ms.change_collection_state.call_args_list[1].kwargs
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db, CollectionStartRequest(timezone='Asia/Kamchatka'))
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['done']
            assert saved.upd_meta['collection_start']['shipment_moment'] == moment
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


async def test_disabled_statuses_still_update_date(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False)
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_awaited_once()
        assert ms.change_collection_state.call_args.args == ('demand', doc.moysklad_id, None)
        assert ms.change_collection_state.call_args.kwargs['moment']
        ms.get_document.assert_awaited_once()
    finally:
        await engine.dispose()


class FrozenDatetime(datetime):
    instant = datetime(2026, 11, 7, 22, 30, 59, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


@pytest.mark.parametrize('configured,device,expected', [
    ('Asia/Vladivostok', 'Europe/Moscow', '2026-11-08 08:30:00'),
    (None, 'America/New_York', '2026-11-07 17:30:00'),
])
async def test_local_accounting_date_uses_document_profile_before_device(monkeypatch, configured, device, expected):
    from app.services import collection_start
    monkeypatch.setattr(collection_start, 'datetime', FrozenDatetime)
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False)
    try:
        async with factory() as db:
            saved = await db.get(OrganizationProfile, profile.id)
            saved.shipment_timezone = configured
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db, CollectionStartRequest(timezone=device))
        assert ms.change_collection_state.call_args.kwargs == {'moment': expected}
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['shipment_timezone'] == (configured or device)
            assert saved.upd_meta['collection_start']['started_at'] == '2026-11-07T22:30:59+00:00'
    finally:
        await engine.dispose()


async def test_uncertain_shipment_write_retries_same_date_after_midnight_and_timezone_change(monkeypatch):
    from app.services import collection_start
    monkeypatch.setattr(collection_start, 'datetime', FrozenDatetime)
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    ms.change_collection_state.side_effect = [httpx.ReadTimeout('lost response'), None, None]
    try:
        async with factory() as db:
            with pytest.raises(HTTPException):
                await documents.begin_collection(doc.id, user, db)
        first = ms.change_collection_state.call_args
        monkeypatch.setattr(FrozenDatetime, 'instant', datetime(2026, 11, 9, 2, tzinfo=timezone.utc))
        async with factory() as db:
            profile = await db.get(OrganizationProfile, profile.id)
            profile.shipment_timezone = 'Asia/Kamchatka'
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db, CollectionStartRequest(timezone='America/New_York'))
        assert ms.change_collection_state.call_args_list[1] == first
        assert first.kwargs['moment'] == '2026-11-08 01:30:00'
        assert ms.change_collection_state.call_args_list[2].args[0] == 'customerorder'
    finally:
        await engine.dispose()


async def test_completed_legacy_collection_does_not_backfill_or_change_date(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            saved.upd_meta = {'collection_start': {'done': True, 'started_at': '2026-10-01T12:00:00+00:00'}}
            profile = await db.get(OrganizationProfile, profile.id)
            profile.shipment_timezone = None
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
            saved = await db.get(Document, doc.id)
            assert 'shipment_moment' not in saved.upd_meta['collection_start']
        ms.change_collection_state.assert_not_awaited()
        ms.get_document.assert_not_awaited()
    finally:
        await engine.dispose()


async def test_missing_timezone_prevents_external_writes(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async with factory() as db:
            profile = await db.get(OrganizationProfile, profile.id)
            profile.shipment_timezone = None
            await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await documents.begin_collection(doc.id, user, db)
            assert error.value.status_code == 400
        ms.change_collection_state.assert_not_awaited()
    finally:
        await engine.dispose()


async def test_tsd_offset_fallback_is_saved_and_reused_when_device_offset_changes(monkeypatch):
    from app.services import collection_start
    monkeypatch.setattr(collection_start, 'datetime', FrozenDatetime)
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False)
    try:
        async with factory() as db:
            profile = await db.get(OrganizationProfile, profile.id)
            profile.shipment_timezone = None
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db, CollectionStartRequest(utc_offset_minutes=345))
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['shipment_timezone'] == 'UTC+05:45'
            assert saved.upd_meta['collection_start']['shipment_moment'] == '2026-11-08 04:15:00'
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db, CollectionStartRequest(utc_offset_minutes=-300))
        ms.change_collection_state.assert_awaited_once()
        assert ms.change_collection_state.call_args.kwargs['moment'] == '2026-11-08 04:15:00'
    finally:
        await engine.dispose()


async def test_orderless_shipment_only_updates_shipment(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    ms.get_document.return_value = {}
    try:
        async with factory() as db:
            (await db.get(Document, doc.id)).moysklad_customer_order_id = None
            await db.commit()
            await documents.begin_collection(doc.id, user, db)
        assert ms.change_collection_state.await_count == 1
        assert ms.change_collection_state.call_args.args[0] == 'demand'
    finally:
        await engine.dispose()


async def test_parallel_organizations_keep_independent_dates(monkeypatch):
    from app.services import collection_start
    monkeypatch.setattr(collection_start, 'datetime', FrozenDatetime)
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False)
    entered, both_entered, release = [], asyncio.Event(), asyncio.Event()

    async def write(*args, **kwargs):
        entered.append((args[1], kwargs['moment']))
        if len(entered) == 2:
            both_entered.set()
        await release.wait()

    ms.change_collection_state.side_effect = write
    try:
        async with factory() as db:
            first = await db.get(OrganizationProfile, profile.id)
            first.shipment_timezone = 'Asia/Vladivostok'
            second = OrganizationProfile(user_id=user.id, name='Другой город', shipment_timezone='America/New_York', shipment_date_on_collection_start=True)
            db.add(second)
            await db.flush()
            second_doc = Document(user_id=user.id, name='Вторая сборка', kind=DocumentKind.demand,
                moysklad_id=str(uuid4()), organization_profile_id=second.id)
            db.add(second_doc)
            await db.commit()

        async def begin(document_id):
            async with factory() as db:
                await documents.begin_collection(document_id, user, db, CollectionStartRequest(timezone='Europe/Moscow'))

        running = asyncio.gather(begin(doc.id), begin(second_doc.id))
        try:
            await asyncio.wait_for(both_entered.wait(), timeout=10)
        finally:
            release.set()
            await asyncio.wait_for(running, timeout=10)
        assert dict(entered) == {doc.moysklad_id: '2026-11-08 08:30:00', second_doc.moysklad_id: '2026-11-07 17:30:00'}
    finally:
        await engine.dispose()


async def test_legacy_partial_start_retries_order_without_changing_shipment_date(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch)
    try:
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            saved.upd_meta = {'collection_start': {'started_at': '2026-10-01T12:00:00+00:00',
                'shipment_state': str(profile.shipment_start_state_id), 'shipment_done': True,
                'order_state': str(profile.customer_order_start_state_id)}}
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_awaited_once_with('customerorder', 'order', str(profile.customer_order_start_state_id))
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
        ms.get_document.return_value = {'customerOrder': {'id': 'other'}}
        async with factory() as db:
            with pytest.raises(HTTPException):
                await documents.begin_collection(doc.id, user, db)
        ms.change_collection_state.assert_not_awaited()
    finally:
        await engine.dispose()


async def test_date_setting_defaults_off_for_existing_and_new_profiles(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, date_enabled=False)
    try:
        async with factory() as db:
            profile = await db.get(OrganizationProfile, profile.id)
            profile.shipment_timezone = None
            other = OrganizationProfile(user_id=user.id, name='New profile')
            db.add(other)
            await db.commit()
            assert other.shipment_date_on_collection_start is False
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
            saved = await db.get(Document, doc.id)
            assert 'shipment_moment' not in saved.upd_meta['collection_start']
        assert ms.change_collection_state.await_count == 2
        assert all(not call.kwargs for call in ms.change_collection_state.call_args_list)
    finally:
        await engine.dispose()


async def test_date_and_statuses_disabled_make_no_external_writes(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, enabled=False, date_enabled=False)
    try:
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
            saved = await db.get(Document, doc.id)
            assert saved.upd_meta['collection_start']['done']
        ms.change_collection_state.assert_not_awaited()
        ms.get_document.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.parametrize('shipment_done', [False, True])
async def test_disabling_setting_cancels_pending_date_from_previous_version(monkeypatch, shipment_done):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, date_enabled=False)
    try:
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            saved.upd_meta = {'collection_start': {
                'started_at': '2026-11-07T10:00:00+00:00',
                'shipment_state': str(profile.shipment_start_state_id),
                'order_state': str(profile.customer_order_start_state_id),
                'shipment_moment': '2026-11-07 13:00:00',
                'shipment_done': shipment_done, 'moment_done': shipment_done}}
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        assert ms.change_collection_state.await_count == (1 if shipment_done else 2)
        assert all(not call.kwargs for call in ms.change_collection_state.call_args_list)
    finally:
        await engine.dispose()


async def test_enabling_date_does_not_retrofit_started_collection(monkeypatch):
    engine, factory, user, profile, doc, ms = await context(monkeypatch, date_enabled=False)
    ms.change_collection_state.side_effect = [None, httpx.ReadTimeout('order timeout'), None]
    try:
        async with factory() as db:
            with pytest.raises(HTTPException):
                await documents.begin_collection(doc.id, user, db)
        async with factory() as db:
            saved = await db.get(OrganizationProfile, profile.id)
            saved.shipment_date_on_collection_start = True
            await db.commit()
        async with factory() as db:
            await documents.begin_collection(doc.id, user, db)
        assert [call.args[0] for call in ms.change_collection_state.call_args_list] == ['demand', 'customerorder', 'customerorder']
        assert all(not call.kwargs for call in ms.change_collection_state.call_args_list)
    finally:
        await engine.dispose()
