"""SSCC write mode persists across sessions and worker completion."""
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import scans
from app.core.config import settings
from app.db.models import User, Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.worker import tasks

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
SSCC = '00046402759700334510'
CHILDREN = ['010464027597309621UNIT001', '010464027597309621UNIT002']


@pytest.mark.parametrize('cached', [True, False])
async def test_sscc_mode_survives_reopen_and_can_be_restored(monkeypatch, cached):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            user = User(email=f'{uuid4()}@example.test', password_hash='test')
            db.add(user)
            await db.flush()
            doc = Document(user_id=user.id, kind=DocumentKind.demand, name='SSCC QA', status=DocumentStatus.draft)
            db.add(doc)
            await db.flush()
            scan = Scan(document_id=doc.id, code=SSCC, is_box=True, box_quantity=20,
                        status=ScanStatus.valid, child_codes=CHILDREN if cached else None)
            db.add(scan)
            await db.commit()
            assert scan.box_expanded is False  # Existing/new boxes default to intact.
            scan_id, user_id = scan.id, user.id
            queue = Mock()
            monkeypatch.setattr(tasks.unpack_scan_task, 'delay', queue)
            await scans.set_scan_pack_mode(db, scan, user_id, True)
            assert scan.status == (ScanStatus.valid if cached else ScanStatus.pending)
            assert queue.call_count == (0 if cached else 1)

        if not cached:
            import app.db.session
            monkeypatch.setattr(app.db.session, 'AsyncSessionLocal', factory)
            monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value='fake'))
            monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['tobacco']))
            cz = NS(unpack_box=AsyncMock(return_value=CHILDREN), get_code_info=AsyncMock())
            monkeypatch.setattr(tasks, 'ChestnyZnakService', Mock(return_value=cz))
            monkeypatch.setattr(tasks, '_push_ws_update', AsyncMock())
            await tasks._unpack_scan_async(str(scan_id), str(user_id), 'valid')
            cz.get_code_info.assert_not_awaited()

        async with factory() as db:
            scan = await db.get(Scan, scan_id)
            assert scan.code == SSCC and scan.is_box and scan.box_expanded and scan.box_quantity == 2
            assert scan.status == ScanStatus.valid and scan.child_codes == CHILDREN
            assert scans.ScanResponse.model_validate(scan).box_expanded
            assert [row['code'] for row in tasks._build_moysklad_scans_data([scan], 'demand', {})] == CHILDREN
            await scans.set_scan_pack_mode(db, scan, user_id, False)
        async with factory() as db:
            scan = await db.get(Scan, scan_id)
            assert not scan.box_expanded and scan.code == SSCC
            rows = tasks._build_moysklad_scans_data([scan], 'demand', {})
            assert len(rows) == 1 and rows[0]['code'] == SSCC and rows[0]['is_box']
    finally:
        await engine.dispose()
