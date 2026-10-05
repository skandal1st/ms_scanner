"""Release only this terminal; retain marks, document and other terminal sessions."""
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from app.api import tsd
from app.db.models import Document, Scan, TsdDocumentSession, DocumentStatus, ScanStatus
from tests.test_collaborative_postgres import context, open_terminal

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='requires isolated PostgreSQL')


async def test_release_keeps_marks_and_other_terminal_and_can_reopen(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    try:
        first = await open_terminal(factory, devices[0], ms_id)
        second = await open_terminal(factory, devices[1], ms_id)
        async with factory() as db:
            db.add(Scan(document_id=first.id, code='audit-release-code', status=ScanStatus.valid))
            await db.commit()
            assert (await tsd.release_tsd_session(first.id, devices[0], db))['status'] == 'released'
            # Retry after a lost HTTP response is safe.
            await tsd.release_tsd_session(first.id, devices[0], db)
        async with factory() as db:
            assert (await db.get(Document, first.id)).status == DocumentStatus.draft
            assert (await db.get(TsdDocumentSession, first.session_id)).status == 'released'
            assert (await db.get(TsdDocumentSession, second.session_id)).status == 'active'
            assert (await db.execute(select(func.count()).select_from(Scan).where(Scan.document_id == first.id))).scalar_one() == 1
            with pytest.raises(HTTPException) as error:
                await tsd._owned_tsd_document(db, devices[0], first.id)
            assert error.value.status_code == 409
        async with factory() as db:
            assert (await tsd.get_tsd_document(first.id, devices[1], db)).scans
        reopened = await open_terminal(factory, devices[0], ms_id)
        assert reopened.session_id != first.session_id and len(reopened.scans) == 1
    finally:
        await engine.dispose()


async def test_release_removes_only_own_work_flag_and_other_user_is_denied(monkeypatch):
    engine, factory, user, profile, devices, ms_id = await context(monkeypatch)
    order = str(uuid4())
    ms = tsd._ms_for_user.return_value
    ms.get_customer_orders = AsyncMock(return_value=[{'id': order, 'name': 'Audit', 'demands': [{'id': ms_id}]}])
    try:
        first = await open_terminal(factory, devices[0], ms_id)
        await open_terminal(factory, devices[1], ms_id)
        async with factory() as db:
            before = await tsd.list_tsd_orders(device=devices[0], db=db)
            assert before[0].in_work and before[0].active_on_this_device
            await tsd.release_tsd_session(first.id, devices[0], db)
            after = await tsd.list_tsd_orders(device=devices[0], db=db)
            assert after[0].in_work and not after[0].active_on_this_device
            other = await tsd.list_tsd_orders(device=devices[1], db=db)
            assert other[0].active_on_this_device
        async with factory() as db:
            alien = NS(id=uuid4(), user_id=uuid4(), workplace_id=devices[0].workplace_id, allowed_modes=['shipment'])
            with pytest.raises(HTTPException):
                await tsd.release_tsd_session(first.id, alien, db)
    finally:
        await engine.dispose()
