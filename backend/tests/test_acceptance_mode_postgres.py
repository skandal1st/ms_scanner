import os
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import Base, User, OrganizationProfile, Integration, Document, DocumentKind, DocumentStatus, Scan, ScanStatus, EdoDocument, EdoMark
from app.api import documents, integrations, scans, acceptance
from app.services import document_guard, moysklad
from app.worker import tasks

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
RAW = '04670163000207Hm7&lkpAAAAi2gC'


async def seed(enabled=False):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Audit', acceptance_cz_check_enabled=enabled)
        db.add_all([profile, Integration(user_id=user.id, moysklad_token='encrypted')])
        await db.flush()
        doc = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Receipt',
            kind=DocumentKind.supply, moysklad_id='ms-supply',
            plan=[{'product_id': 'p', 'expected_qty': 40, 'price': 125, 'vat': 22}])
        db.add(doc)
        await db.flush()
        db.add_all([Scan(document_id=doc.id, code=RAW, status=ScanStatus.valid),
            Scan(document_id=doc.id, code='010467016300020721AbC,123', status=ScanStatus.valid,
                 is_box=True, box_quantity=100, child_codes=None),
            Scan(document_id=doc.id, code='raw-unknown', status=ScanStatus.scanned)])
        await db.commit()
    return engine, factory, user.id, profile.id, doc.id


@asynccontextmanager
async def lock(*args, **kwargs):
    yield True


async def test_no_cz_receipt_keeps_all_raw_marks_and_sends_only_goods_to_ms(monkeypatch):
    from app.db import session
    from app.core import security
    engine, factory, user_id, profile_id, doc_id = await seed()
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=NS()))
    delay = Mock()
    monkeypatch.setattr(tasks.process_document_task, 'delay', delay)
    update = AsyncMock(return_value={})
    find_product = AsyncMock(side_effect=AssertionError('No mark-based product lookup'))
    monkeypatch.setattr(moysklad, 'MoySkladService', lambda *args: NS(update_document=update, find_product_by_gtin=find_product))
    monkeypatch.setattr(security, 'decrypt_token', lambda *args: 'test')
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(document_guard, 'processing_lock', lock)
    monkeypatch.setattr(tasks, 'monitoring_emit', AsyncMock())
    cz_calls = AsyncMock(side_effect=AssertionError('No CZ requests'))
    monkeypatch.setattr(tasks, '_get_cz_token', cz_calls)
    monkeypatch.setattr(tasks, '_expand_aggregate_for_processing', cz_calls)
    try:
        async with factory() as db:
            await documents.process_document(doc_id, await db.get(User, user_id), db)
        delay.assert_called_once()
        # Toggling the profile while queued must not change the queued operation.
        async with factory() as db:
            (await db.get(OrganizationProfile, profile_id)).acceptance_cz_check_enabled = True
            await db.commit()
        await tasks._process_document_unlocked_async(str(doc_id), str(user_id))
        cz_calls.assert_not_awaited()
        assert update.await_args.args[2] == [{'product_id': 'p', 'code': '', 'is_barcode': True, 'is_box': False, 'quantity': 40}]
        assert update.await_args.kwargs['position_prices'] == {'p': {'price': 125, 'vat': 22}}
        async with factory() as db:
            doc = await db.get(Document, doc_id)
            assert doc.status == DocumentStatus.accepted
            saved = (await db.execute(select(Scan).where(Scan.document_id == doc_id))).scalars().all()
            assert {s.code for s in saved} == {RAW, '010467016300020721AbC,123', 'raw-unknown'}
            assert all(s.verification is None for s in saved)
            response = await acceptance.get_acceptance_document(doc_id, await db.get(User, user_id), db)
            assert response.cz_check_enabled is False
            found = await scans.search_scans_by_code(RAW, await db.get(User, user_id), db)
            assert len(found.scans) == 1
            assert found.scans[0].accepted_without_cz and found.scans[0].document_status == DocumentStatus.accepted
    finally:
        await engine.dispose()


async def test_search_finds_receipt_short_code_children_and_edo_without_prefix_false_positives():
    engine, factory, user_id, _, doc_id = await seed()
    try:
        async with factory() as db:
            edo = EdoDocument(user_id=user_id, external_id=str(uuid4()), number='UPD-1', direction='Входящий')
            db.add(edo)
            await db.flush()
            db.add(EdoMark(user_id=user_id, document_id=edo.id, cis_raw=RAW[:21], cis_canonical=RAW[:21]))
            db.add_all([
                Scan(document_id=doc_id, code='010467016300020721AbC,123XYZ', status=ScanStatus.valid),
                Scan(document_id=doc_id, code='package', child_codes=[RAW[:21]], status=ScanStatus.valid),
                Scan(document_id=doc_id, code='010467016300020721Ab%_/_7', status=ScanStatus.valid),
                Scan(document_id=doc_id, code='010467016300020721AbXXXXX7', status=ScanStatus.valid),
            ])
            await db.commit()
            user = await db.get(User, user_id)
            for query in [RAW, RAW[:21]]:
                found = await scans.search_scans_by_code(query, user, db)
                assert {hit.code for hit in found.scans} == {RAW, 'package'}
                assert [hit.number for hit in found.upds] == ['UPD-1']
            found = await scans.search_scans_by_code('010467016300020721AbC,123\x1d93TAIL', user, db)
            assert [hit.code for hit in found.scans] == ['010467016300020721AbC,123']
            found = await scans.search_scans_by_code('010467016300020721Ab%_/_7\x1d93TAIL', user, db)
            assert [hit.code for hit in found.scans] == ['010467016300020721Ab%_/_7']
            found = await scans.search_scans_by_code(RAW.lower(), user, db)
            assert not found.scans
            found = await scans.search_scans_by_code(RAW, NS(id=uuid4()), db)
            assert not found.scans
    finally:
        await engine.dispose()


async def test_setting_is_scoped_to_organization_and_persists_without_cz_token():
    engine, factory, user_id, profile_id, _ = await seed(enabled=True)
    try:
        async with factory() as db:
            other = OrganizationProfile(user_id=user_id, name='Other')
            db.add(other)
            await db.commit()
            assert other.acceptance_cz_check_enabled is True
            result = await integrations.update_integration(integrations.UpdateIntegrationRequest(acceptance_cz_check_enabled=False),
                await db.get(User, user_id), await db.get(OrganizationProfile, profile_id), db)
            assert result.acceptance_cz_check_enabled is False and not result.has_cz
            await db.refresh(other)
            assert other.acceptance_cz_check_enabled is True
    finally:
        await engine.dispose()


@pytest.mark.parametrize('enabled', [False, True])
async def test_default_mode_keeps_pending_guard_and_no_cz_mode_blocks_manual_verification(monkeypatch, enabled):
    engine, factory, user_id, _, doc_id = await seed(enabled)
    monkeypatch.setattr(documents, '_get_ms_service', AsyncMock(return_value=NS()))
    verify = Mock()
    monkeypatch.setattr(tasks.verify_document_task, 'delay', verify)
    try:
        async with factory() as db:
            user = await db.get(User, user_id)
            if enabled:
                with pytest.raises(HTTPException) as exc:
                    await documents.process_document(doc_id, user, db)
            else:
                with pytest.raises(HTTPException) as exc:
                    await documents.verify_document(doc_id, user, db)
            assert exc.value.status_code == 409
            verify.assert_not_called()
    finally:
        await engine.dispose()


async def test_queued_cz_verification_does_not_run_after_disabling_acceptance_checks(monkeypatch):
    from app.db import session
    import redis.asyncio as aioredis
    engine, factory, user_id, _, doc_id = await seed()
    redis = NS(set=AsyncMock(return_value=True), delete=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(aioredis, 'from_url', lambda *args, **kwargs: redis)
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    cz_calls = AsyncMock(side_effect=AssertionError('CZ must not be contacted'))
    monkeypatch.setattr(tasks, '_get_cz_token', cz_calls)
    done = AsyncMock()
    monkeypatch.setattr(tasks, '_push_verify_done', done)
    try:
        await tasks._verify_document_async(str(doc_id), str(user_id))
        cz_calls.assert_not_awaited()
        done.assert_awaited_once_with(str(user_id), str(doc_id), checked=0, failed=0)
        redis.delete.assert_awaited_once()
    finally:
        await engine.dispose()
