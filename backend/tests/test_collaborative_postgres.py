"""Real concurrent requests against an isolated PostgreSQL database."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.api import tsd, documents
from app.core.config import settings
from app.db.models import User, OrganizationProfile, Workplace, TsdDevice, Document, Scan, TsdScanAction
from app.services.document_guard import lock_ms_document as real_lock
from app.services.scan_events import publish_event as real_publish_event, publish_scan as real_publish_scan, publish_removed as real_publish_removed

pytestmark = pytest.mark.skipif(os.getenv("AUDIT_POSTGRES") != "1", reason="requires isolated PostgreSQL")


async def context(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    from app.db import session
    monkeypatch.setattr(session, 'engine', engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f"{uuid4()}@example.test", password_hash="")
        db.add(user)
        await db.flush()
        profile = OrganizationProfile(user_id=user.id, moysklad_organization_id="org", name="Audit")
        db.add(profile)
        await db.flush()
        workplace = Workplace(user_id=user.id, organization_profile_id=profile.id, name="Audit", store_ids=[])
        db.add(workplace)
        await db.flush()
        devices = [TsdDevice(user_id=user.id, workplace_id=workplace.id, name=f"Terminal {i}") for i in range(2)]
        db.add_all(devices)
        await db.commit()
    ms_id = str(uuid4())
    ms = NS(get_document=AsyncMock(return_value={"name": "Audit", "organization": {"id": "org"}, "store": {"id": "store"}}),
            build_plan=AsyncMock(return_value=[{"product_id": "p", "gtin": "04620543080527", "product_name": "Audit",
                                               "marked": False, "expected_qty": 100, "pack_quantities": {}}]))
    monkeypatch.setattr(tsd, "_ms_for_user", AsyncMock(return_value=ms))
    monkeypatch.setattr(documents, "_get_ms_service", AsyncMock(return_value=ms))
    monkeypatch.setattr(tsd, "lock_ms_document", real_lock)
    from app.services import document_guard
    monkeypatch.setattr(document_guard, "lock_ms_document", real_lock)
    return engine, factory, user, profile, devices, ms_id


async def open_terminal(factory, device, ms_id):
    async with factory() as db:
        return await tsd.select_tsd_document(tsd.SelectDocumentRequest(moysklad_id=ms_id), device, db)


async def test_workplace_mode_persists_and_other_user_cannot_change_it(monkeypatch):
    from app.api.organization_profiles import WorkplaceModeRequest, update_workplace_mode
    engine, factory, user, profile, devices, _ = await context(monkeypatch)
    workplace_id = devices[0].workplace_id
    try:
        async with factory() as db:
            assert (await db.get(Workplace, workplace_id)).scan_mode == "com"
            await update_workplace_mode(workplace_id, WorkplaceModeRequest(scan_mode="tsd"), user, db)
        async with factory() as db:
            assert (await db.get(Workplace, workplace_id)).scan_mode == "tsd"
            with pytest.raises(HTTPException) as error:
                await update_workplace_mode(workplace_id, WorkplaceModeRequest(scan_mode="com"), NS(id=uuid4()), db)
            assert error.value.status_code == 404
            assert (await db.get(Workplace, workplace_id)).scan_mode == "tsd"
    finally:
        await engine.dispose()


async def test_two_terminals_and_desktop_first_open_reuse_one_document(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    async def desktop():
        async with factory() as db:
            return await documents.create_document(documents.CreateDocumentRequest(name="Audit", moysklad_id=ms_id), user, profile, db)
    results = await asyncio.wait_for(asyncio.gather(open_terminal(factory, devices[0], ms_id),
        open_terminal(factory, devices[1], ms_id), desktop()), timeout=10)
    assert len({value.id for value in results}) == 1
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Document).where(Document.moysklad_id == ms_id))).scalar_one() == 1
    await engine.dispose()


async def test_repeated_terminal_open_with_statuses_reuses_session(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    state = uuid4()
    ms = tsd._ms_for_user.return_value
    ms.get_shipment_states = AsyncMock(return_value=[{'id': str(state)}])
    ms.change_collection_state = AsyncMock()
    try:
        async with factory() as db:
            saved = await db.get(OrganizationProfile, profile.id)
            saved.shipment_start_state_id = state
            await db.commit()
        results = await asyncio.wait_for(asyncio.gather(
            open_terminal(factory, devices[0], ms_id), open_terminal(factory, devices[0], ms_id)), timeout=10)
        assert results[0].session_id == results[1].session_id
        assert not results[0].collection_started and not results[1].collection_started
        ms.change_collection_state.assert_not_awaited()
        async def begin():
            async with factory() as db:
                return await tsd.start_tsd_collection(results[0].id, devices[0], db)
        await asyncio.wait_for(asyncio.gather(begin(), begin()), timeout=10)
        ms.change_collection_state.assert_awaited_once_with('demand', ms_id, str(state))
        reopened = await open_terminal(factory, devices[0], ms_id)
        assert reopened.collection_started
        ms.change_collection_state.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.getenv("AUDIT_REDIS") != "1", reason="requires local Redis")
async def test_committed_scans_reach_desktop_and_both_terminals_through_redis(monkeypatch):
    import json
    import socket
    from contextlib import AsyncExitStack
    import httpx
    import uvicorn
    from websockets.asyncio.client import connect
    from app.main import app
    from app.db import session
    from app.api import scans
    from app.services import scan_events
    from app.core.security import create_access_token, create_tsd_access_token

    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    document = await open_terminal(factory, devices[0], ms_id)
    await open_terminal(factory, devices[1], ms_id)
    monkeypatch.setattr(session, "AsyncSessionLocal", factory)
    monkeypatch.setattr(scan_events, "publish_event", real_publish_event)
    for module in (scans, tsd):
        monkeypatch.setattr(module, "publish_scan", real_publish_scan)
        monkeypatch.setattr(module, "publish_removed", real_publish_removed)
    tokens = [create_tsd_access_token({"sub": str(user.id), "device_id": str(device.id)}) for device in devices]
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        async with AsyncExitStack() as stack:
            paths = [f"{user.id}?token={create_access_token({'sub': str(user.id)})}"]
            paths += [f"tsd/{document.id}?token={token}" for token in tokens]
            sockets = [await stack.enter_async_context(connect(f"ws://127.0.0.1:{port}/ws/{path}")) for path in paths]
            client = await stack.enter_async_context(httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}"))
            for device_index, quantity, undo in [(0, 1, False), (1, 2, False), (0, 1, True)]:
                headers = {"Authorization": f"Bearer {tokens[device_index]}"}
                url = f"/tsd/documents/{document.id}/scans"
                response = await client.delete(url + "/last", headers=headers) if undo else await client.post(url, headers=headers, json={"code": "04620543080527"})
                assert response.status_code == 200, response.text
                for websocket in sockets:
                    event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                    assert event["type"] == "scan_upsert"
                    assert event["document_id"] == str(document.id)
                    assert event["scan"]["box_quantity"] == quantity
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()
        await engine.dispose()


async def test_shared_barcode_quantity_and_per_terminal_undo_are_atomic(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    first = await open_terminal(factory, devices[0], ms_id)
    await open_terminal(factory, devices[1], ms_id)
    async with factory() as db:
        await tsd.start_tsd_collection(first.id, devices[0], db)
    async def scan(device):
        async with factory() as db:
            return await tsd.create_tsd_scan(first.id, tsd.TsdScanRequest(code="04620543080527"), device, db)
    await asyncio.wait_for(asyncio.gather(*(scan(devices[i]) for i in [0, 1, 1, 0, 1])), timeout=10)
    async with factory() as db:
        rows = (await db.execute(select(Scan).where(Scan.document_id == first.id))).scalars().all()
        assert len(rows) == 1 and rows[0].box_quantity == 5
        assert (await db.execute(select(func.count()).select_from(TsdScanAction).where(TsdScanAction.document_id == first.id))).scalar_one() == 5
    for _ in range(2):
        async with factory() as db:
            await tsd.delete_last_tsd_scan(first.id, devices[0], db)
    async with factory() as db:
        with pytest.raises(HTTPException) as error:
            await tsd.delete_last_tsd_scan(first.id, devices[0], db)
        assert error.value.status_code == 404
    async with factory() as db:
        row = (await db.execute(select(Scan).where(Scan.document_id == first.id))).scalar_one()
        assert row.box_quantity == 3
    await engine.dispose()


async def test_simultaneous_same_mark_has_one_owner_and_cannot_be_undone_by_the_other_terminal(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    first = await open_terminal(factory, devices[0], ms_id)
    await open_terminal(factory, devices[1], ms_id)
    async with factory() as db:
        await tsd.start_tsd_collection(first.id, devices[0], db)
    async def scan(device):
        async with factory() as db:
            return await tsd.create_tsd_scan(first.id, tsd.TsdScanRequest(code="010462054308052721TEST000000001\x1d93ABCD"), device, db)
    results = await asyncio.wait_for(asyncio.gather(*(scan(device) for device in devices)), timeout=10)
    assert len({value.id for value in results}) == 1 and sorted(value.duplicate for value in results) == [False, True]
    winner = devices[next(i for i, value in enumerate(results) if not value.duplicate)]
    loser = devices[next(i for i, value in enumerate(results) if value.duplicate)]
    async with factory() as db:
        with pytest.raises(HTTPException):
            await tsd.delete_last_tsd_scan(first.id, loser, db)
    async with factory() as db:
        await tsd.delete_last_tsd_scan(first.id, winner, db)
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Scan).where(Scan.document_id == first.id))).scalar_one() == 0
        action = (await db.execute(select(TsdScanAction).where(TsdScanAction.document_id == first.id))).scalar_one()
        assert action.scan_id is None and action.undone_at is not None
    await engine.dispose()


async def test_terminal_live_channel_rechecks_scope_session_and_revocation(monkeypatch):
    from app.main import terminal_websocket_scope
    from app.db import session
    from app.core.security import create_tsd_access_token, create_access_token
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    document = await open_terminal(factory, devices[0], ms_id)
    monkeypatch.setattr(session, "AsyncSessionLocal", factory)
    token = create_tsd_access_token({"sub": str(user.id), "device_id": str(devices[0].id)})
    assert await terminal_websocket_scope(token, document.id) == (str(user.id), str(devices[0].id))
    for bad_token, doc_id in [
        (create_tsd_access_token({"sub": str(user.id), "device_id": str(devices[1].id)}), document.id),
        (create_access_token({"sub": str(user.id)}), document.id),
        (token, uuid4()),
    ]:
        with pytest.raises(HTTPException):
            await terminal_websocket_scope(bad_token, doc_id)
    async with factory() as db:
        saved = await db.get(TsdDevice, devices[0].id)
        saved.is_active = False
        await db.commit()
    with pytest.raises(HTTPException) as error:
        await terminal_websocket_scope(token, document.id)
    assert error.value.status_code == 401
    await engine.dispose()
