"""Optional real PostgreSQL concurrency tests (AUDIT_POSTGRES=1)."""
import asyncio
import os
from uuid import uuid4
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import documents
from app.core.config import settings
from app.db import session
from app.db.models import User, Document, DocumentStatus, Integration, Scan, ScanStatus
from app.services.document_guard import editable_document, processing_lock
from app.worker import tasks

pytestmark = pytest.mark.skipif(os.getenv("AUDIT_POSTGRES") != "1", reason="requires isolated PostgreSQL")


async def seed(factory):
    async with factory() as db:
        user = User(id=uuid4(), email=f"{uuid4()}@example.test", password_hash="")
        doc = Document(id=uuid4(), user_id=user.id, name="Audit", moysklad_id="ms-id")
        db.add_all([user, doc])
        await db.flush()
        db.add(Integration(user_id=user.id, moysklad_token="fake"))
        db.add(Scan(document_id=doc.id, code=str(uuid4()), status=ScanStatus.valid))
        await db.commit()
        return user, doc


async def test_advisory_lock_is_exclusive_and_released(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(session, "engine", engine)
    key = str(uuid4())
    async with processing_lock(key) as first:
        assert first
        async with processing_lock(key) as second:
            assert not second
    async with processing_lock(key) as third:
        assert third
    await engine.dispose()


async def test_concurrent_process_only_enqueues_once(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user, doc = await seed(factory)
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=SimpleNamespace(get_document=AsyncMock(return_value={}))))
    queued = []
    monkeypatch.setattr(tasks.process_document_task, "delay", lambda *args: queued.append(args))

    async def process():
        async with factory() as db:
            try:
                return await documents.process_document(doc.id, user, db)
            except HTTPException as exc:
                return exc.status_code

    results = await asyncio.gather(process(), process())
    assert len(queued) == 1
    assert 409 in results
    async with factory() as db:
        saved = await db.get(Document, doc.id)
        assert saved.status == DocumentStatus.processing
        assert saved.processing_progress["stage"] == "preparing"
    await engine.dispose()


async def test_waiting_editor_sees_processing_after_row_lock(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user, doc = await seed(factory)
    async with factory() as writer, factory() as editor:
        locked = await editable_document(writer, doc.id, user.id)
        locked.status = DocumentStatus.processing
        blocked_edit = asyncio.create_task(editable_document(editor, doc.id, user.id))
        await asyncio.sleep(0.05)
        assert not blocked_edit.done()
        await writer.commit()
        with pytest.raises(HTTPException) as exc:
            await blocked_edit
        assert exc.value.status_code == 409
    await engine.dispose()


@pytest.mark.parametrize("failure", ["timeout", "412"])
async def test_worker_failure_returns_draft_with_a_visible_reason(monkeypatch, failure):
    import httpx
    from app.core import security
    from app.services import moysklad
    from sqlalchemy import select
    from types import SimpleNamespace

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session, "engine", engine)
    monkeypatch.setattr(session, "AsyncSessionLocal", factory)
    user, doc = await seed(factory)
    async with factory() as db:
        saved = await db.get(Document, doc.id)
        saved.status = DocumentStatus.processing
        scan = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalar_one()
        scan.moysklad_product_id = "p1"
        await db.commit()
    update = AsyncMock()
    if failure == "timeout":
        update.side_effect = httpx.ReadTimeout("lost response")
    else:
        update.return_value = {"__moysklad_412__": True, "body": '{"errors":[{"error":"Неверная марка"}]}'}
    monkeypatch.setattr(security, "decrypt_token", lambda token: "fake")
    monkeypatch.setattr(moysklad, "MoySkladService", lambda token: SimpleNamespace(update_document=update))
    with pytest.raises((httpx.ReadTimeout, ValueError)):
        await tasks._process_document_async(str(doc.id), str(user.id))
    async with factory() as db:
        saved = await db.get(Document, doc.id)
        assert saved.status == DocumentStatus.draft
        assert saved.error_message
        if failure == "412":
            assert "Неверная марка" in saved.error_message
    await engine.dispose()


async def test_worker_transfer_ignores_legacy_status_targets(monkeypatch):
    from unittest.mock import create_autospec
    from sqlalchemy import select
    from app.core import security
    from app.services import moysklad
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session, 'engine', engine)
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    user, doc = await seed(factory)
    try:
        async with factory() as db:
            saved = await db.get(Document, doc.id)
            saved.status = DocumentStatus.processing
            saved.upd_meta = {'shipment_sent_state_id': str(uuid4()), 'customer_order_sent_state_id': str(uuid4())}
            scan = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalar_one()
            scan.moysklad_product_id = 'p1'
            await db.commit()
        ms = create_autospec(moysklad.MoySkladService, instance=True)
        ms.update_document.return_value = {}
        monkeypatch.setattr(security, 'decrypt_token', lambda token: 'fake')
        monkeypatch.setattr(moysklad, 'MoySkladService', lambda token: ms)
        await tasks._process_document_async(str(doc.id), str(user.id))
        ms.update_document.assert_awaited_once()
        ms.change_collection_state.assert_not_awaited()
        assert not {'shipment_state_id', 'order_state_id', 'customer_order_id'} & ms.update_document.call_args.kwargs.keys()
        async with factory() as db:
            assert (await db.get(Document, doc.id)).status == DocumentStatus.accepted
    finally:
        await engine.dispose()
