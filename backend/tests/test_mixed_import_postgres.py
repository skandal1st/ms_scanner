import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db import session
from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus, User
from app.services import mixed_import as mixed
from app.services.document_guard import editable_document
from app.worker import tasks
from tests.test_mixed_import import A, B, BLOCK

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


async def context(monkeypatch, existing=()):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session, 'engine', engine)
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(tasks.mixed_import_task, 'delay', Mock())
    monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value='test'))
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['tobacco']))
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='GROUP')))
    monkeypatch.setattr(mixed, 'ChestnyZnakService', Mock(return_value=cz))
    from app.services import scan_events
    monkeypatch.setattr(scan_events, 'publish_event', AsyncMock())
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        doc = Document(id=uuid4(), user_id=user.id, name='Mixed QA', kind=DocumentKind.demand,
                       status=DocumentStatus.draft)
        db.add(doc)
        await db.flush()
        for code, children in existing:
            db.add(Scan(document_id=doc.id, code=code, status=ScanStatus.valid,
                        gtin='04640275973096', package_type='GROUP' if children else 'UNIT',
                        child_codes=children, box_quantity=len(children) if children else None))
        await db.commit()
    return engine, factory, user.id, doc.id, cz


async def test_import_is_atomic_idempotent_and_replaces_existing_parent(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch, [(BLOCK, [A, B]), (A, None)])
    try:
        async with factory() as db:
            before = (await db.execute(select(Scan).where(Scan.document_id == doc, Scan.code == A))).scalar_one().id
            job = await mixed.start(db, doc, user, [BLOCK, A, B, A])
        await mixed.run_import(doc, user, job['id'])
        await mixed.run_import(doc, user, job['id'])  # Redelivery after a committed import.
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {A, B}
            assert next(s for s in rows if s.code == A).id == before
            assert next(s for s in rows if s.code == B).status == ScanStatus.scanned
            result = await mixed.status(db, doc, user)
            assert result['status'] == 'completed' and result['added'] == 0 and result['duplicates'] == 5
            assert 'codes' not in result
            await editable_document(db, doc, user)
    finally:
        await engine.dispose()


async def test_unknown_block_changes_no_scans_and_releases_document(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    cz.get_code_info.return_value = None
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK, B])
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.code for s in rows] == [A]
            result = await mixed.status(db, doc, user)
            assert result['status'] == 'conflict' and len(result['conflicts']) == 2
            await editable_document(db, doc, user)
    finally:
        await engine.dispose()


async def test_parallel_requests_coalesce_and_block_other_edits_and_accounts(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch)
    try:
        async def begin():
            async with factory() as db:
                return await mixed.start(db, doc, user, [BLOCK])
        first, second = await asyncio.gather(begin(), begin())
        assert first['id'] == second['id']
        tasks.mixed_import_task.delay.assert_called_once()
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await editable_document(db, doc, user)
            assert error.value.status_code == 409
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await mixed.start(db, doc, uuid4(), [BLOCK])
            assert error.value.status_code == 404
        await mixed.run_import(doc, user, first['id'])
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK, A])
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {A, B}
            assert (await mixed.status(db, doc, user))['added'] == 0
    finally:
        await engine.dispose()


async def test_background_changes_during_resolution_abort_without_partial_import(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    async def changed(code):
        async with factory() as db:
            scan = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalar_one()
            scan.error_message = 'Changed by another worker'
            await db.commit()
        return NS(children=[A, B], package_type='GROUP')
    cz.get_code_info.side_effect = changed
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK])
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            assert (await mixed.status(db, doc, user))['status'] == 'conflict'
            assert len((await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()) == 1
    finally:
        await engine.dispose()


async def test_queue_failure_and_expiry_release_document_without_adding_marks(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch)
    try:
        tasks.mixed_import_task.delay.side_effect = RuntimeError('broker offline')
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK])
            assert job['status'] == 'failed'
            await editable_document(db, doc, user)
        tasks.mixed_import_task.delay.side_effect = None
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK])
            document = await db.get(Document, doc)
            expired = dict(document.upd_meta['mixed_import'])
            expired['deadline'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            mixed.store_job(document, expired)
            await db.commit()
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            assert (await mixed.status(db, doc, user))['status'] == 'failed'
            await editable_document(db, doc, user)
            assert not (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
    finally:
        await engine.dispose()


async def test_replacement_does_not_clear_existing_other_document_restriction(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch, [(BLOCK, [A, B])])
    try:
        async with factory() as db:
            parent = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalar_one()
            parent.status = ScanStatus.used_in_other_doc
            parent.error_message = 'Упаковка уже в другом документе'
            await db.commit()
            job = await mixed.start(db, doc, user, [BLOCK])
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert len(rows) == 2
            assert all(s.status == ScanStatus.used_in_other_doc for s in rows)
    finally:
        await engine.dispose()


async def test_failed_commit_path_rolls_back_all_units_and_is_recoverable(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    original = mixed.apply_units
    async def fail_after_inserts(*args):
        await original(*args)
        raise RuntimeError('simulated DB failure')
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK])
        monkeypatch.setattr(mixed, 'apply_units', fail_after_inserts)
        with pytest.raises(RuntimeError):
            await mixed.run_import(doc, user, job['id'])
        await mixed.fail_import(doc, user, job['id'])
        async with factory() as db:
            assert (await mixed.status(db, doc, user))['status'] == 'failed'
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.code for s in rows] == [A]
    finally:
        await engine.dispose()


@pytest.mark.parametrize('target', ['blocks', 'units'])
async def test_box_block_pack_file_materializes_only_selected_level(monkeypatch, target):
    from tests.test_mixed_import import SSCC
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    cz.unpack_box = AsyncMock(return_value=[A, B])
    async def info(code):
        return NS(children=[A, B] if code == BLOCK else [], package_type='GROUP' if code == BLOCK else 'UNIT')
    cz.get_code_info.side_effect = info
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [SSCC, BLOCK, A, B], target)
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert (await mixed.status(db, doc, user))['status'] == 'completed'
            if target == 'blocks':
                assert len(rows) == 1 and rows[0].code == BLOCK
                assert rows[0].keep_aggregate and rows[0].package_type == 'GROUP'
                assert rows[0].box_quantity == 2 and set(rows[0].child_codes) == {A, B}
                assert rows[0].status == ScanStatus.scanned
                assert [r['code'] for r in tasks._build_moysklad_scans_data(rows, 'demand', {})] == [BLOCK]
            else:
                assert {s.code for s in rows} == {A, B}
                assert all(s.package_type == 'UNIT' and not s.keep_aggregate for s in rows)
    finally:
        await engine.dispose()


async def test_blocks_target_does_not_join_an_active_packs_job(monkeypatch):
    engine, factory, user, doc, cz = await context(monkeypatch)
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK], 'units')
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await mixed.start(db, doc, user, [BLOCK], 'blocks')
            assert error.value.status_code == 409
            assert (await mixed.status(db, doc, user))['id'] == job['id']
    finally:
        await engine.dispose()


@pytest.mark.parametrize('initial_block', [False, True])
async def test_hybrid_removes_covered_packs_preserves_loose_and_is_idempotent(monkeypatch, initial_block):
    loose = '010464027597309621LOOSE001'
    new_loose = '010464027597309621LOOSE002'
    existing = [(BLOCK, [A, B]), (A, None), (loose, None)] if initial_block else [(A, None), (loose, None)]
    engine, factory, user, doc, cz = await context(monkeypatch, existing)
    async def info(code):
        return NS(children=[A, B] if code == BLOCK else [], package_type='GROUP' if code == BLOCK else 'UNIT')
    cz.get_code_info.side_effect = info
    try:
        async with factory() as db:
            old_loose = (await db.execute(select(Scan).where(Scan.document_id == doc, Scan.code == loose))).scalar_one()
            old_id = old_loose.id
            # A second encoding of the same already loaded loose mark must collapse too.
            db.add(Scan(document_id=doc, code=loose + '\x1d91abcd', status=ScanStatus.scanned, package_type='UNIT'))
            await db.commit()
            job = await mixed.start(db, doc, user, [BLOCK, A, B, loose, new_loose, new_loose], 'blocks_loose')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {BLOCK, loose, new_loose}
            saved_loose = next(s for s in rows if s.code == loose)
            assert saved_loose.id == old_id and saved_loose.status == ScanStatus.valid
            block = next(s for s in rows if s.code == BLOCK)
            assert block.keep_aggregate and block.box_quantity == 2
            payload = tasks._build_moysklad_scans_data(rows, 'demand', {})
            assert {item['code'] for item in payload} == {BLOCK, loose, new_loose}
            assert sum(item['quantity'] for item in payload) == 4
            result = await mixed.status(db, doc, user)
            assert result['target'] == 'blocks_loose' and result['blocks'] == 1
            assert result['loose_units'] == 2 and result['units'] == 4
            row_ids = {s.id for s in rows}
            again = await mixed.start(db, doc, user, [BLOCK, A, B, loose, new_loose], 'blocks_loose')
        await mixed.run_import(doc, user, again['id'])
        await mixed.run_import(doc, user, again['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.id for s in rows} == row_ids
            assert (await mixed.status(db, doc, user))['added'] == 0
    finally:
        await engine.dispose()


async def test_hybrid_packs_loaded_after_block_are_not_added_twice(monkeypatch):
    loose = '010464027597309621LOOSE001'
    engine, factory, user, doc, cz = await context(monkeypatch, [(BLOCK, [A, B])])
    cz.get_code_info.return_value = NS(children=[], package_type='UNIT')
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [A, B, loose], 'blocks_loose')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {BLOCK, loose}
            result = await mixed.status(db, doc, user)
            assert result['blocks'] == 1 and result['loose_units'] == 1 and result['units'] == 3
    finally:
        await engine.dispose()


async def test_hybrid_preserves_blocked_parent_restriction_for_loose_children(monkeypatch):
    from tests.test_mixed_import import SSCC
    loose = '010464027597309621LOOSE001'
    engine, factory, user, doc, cz = await context(monkeypatch, [(SSCC, [A, B, loose])])
    try:
        async with factory() as db:
            parent = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalar_one()
            parent.is_box, parent.package_type = True, 'BOX'
            parent.status, parent.error_message = ScanStatus.used_in_other_doc, 'Короб используется в другом документе'
            await db.commit()
            job = await mixed.start(db, doc, user, [BLOCK, loose], 'blocks_loose')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {BLOCK, loose}
            assert all(s.status == ScanStatus.used_in_other_doc for s in rows)
    finally:
        await engine.dispose()


async def test_hybrid_failure_rolls_back_blocks_and_loose_as_one_operation(monkeypatch):
    loose = '010464027597309621LOOSE001'
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    async def info(code):
        return NS(children=[A, B] if code == BLOCK else [], package_type='GROUP' if code == BLOCK else 'UNIT')
    cz.get_code_info.side_effect = info
    original = mixed.apply_units
    async def fail_after_units(*args):
        await original(*args)
        raise RuntimeError('simulated loose insert failure')
    monkeypatch.setattr(mixed, 'apply_units', fail_after_units)
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [BLOCK, loose], 'blocks_loose')
        with pytest.raises(RuntimeError):
            await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.code for s in rows] == [A]
    finally:
        await engine.dispose()


async def test_whole_box_import_is_atomic_idempotent_and_sends_transportpack(monkeypatch):
    from tests.test_mixed_import import SSCC
    from app.worker.tasks import _build_moysklad_scans_data
    from app.services.moysklad import MoySkladService
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None), (BLOCK, [A, B])])
    cz.unpack_box = AsyncMock(return_value=[A, B])
    try:
        async with factory() as db:
            parent = (await db.execute(select(Scan).where(Scan.document_id == doc, Scan.code == BLOCK))).scalar_one()
            parent.moysklad_product_id, parent.product_name = 'manual-product', 'Manual product'
            await db.commit()
            job = await mixed.start(db, doc, user, [SSCC, BLOCK, A, B, SSCC], 'boxes')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert len(rows) == 1
            box = rows[0]
            before = box.id
            assert box.code == SSCC and box.is_box and not box.box_expanded
            assert box.package_type == 'BOX' and box.keep_aggregate
            assert box.box_quantity == 2 and box.child_codes == [A, B]
            assert box.moysklad_product_id == 'manual-product' and box.product_name == 'Manual product'
            assert box.status == ScanStatus.scanned  # composition is not certification
            result = await mixed.status(db, doc, user)
            assert result['boxes'] == 1 and result['blocks'] == 0 and result['units'] == 2
            payload = _build_moysklad_scans_data(rows, 'demand', {})
            assert len(payload) == 1 and payload[0]['code'] == SSCC
            assert payload[0]['is_box'] and payload[0]['quantity'] == 2
            assert MoySkladService('test')._tracking_code_entry(payload[0], 'TOBACCO')['type'] == 'transportpack'
            job = await mixed.start(db, doc, user, [SSCC, A, B], 'boxes')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.id for s in rows] == [before]
            assert (await mixed.status(db, doc, user))['added'] == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize('nested', [False, True])
async def test_whole_box_preserves_cross_document_restrictions_including_nested_crypto_marks(monkeypatch, nested):
    from tests.test_mixed_import import SSCC
    engine, factory, user, doc, cz = await context(monkeypatch)
    cz.unpack_box = AsyncMock(return_value=[A, B])
    try:
        async with factory() as db:
            other = Document(id=uuid4(), user_id=user, name='Other assembly', kind=DocumentKind.demand,
                             status=DocumentStatus.draft)
            db.add(other)
            await db.flush()
            db.add(Scan(document_id=other.id, code=BLOCK if nested else A,
                        child_codes=[A + '\x1d91CRYPTO'] if nested else None,
                        package_type='GROUP' if nested else 'UNIT', status=ScanStatus.valid))
            await db.commit()
            job = await mixed.start(db, doc, user, [SSCC], 'boxes')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            box = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalar_one()
            assert box.status == ScanStatus.used_in_other_doc
            assert 'Other assembly' in box.error_message
    finally:
        await engine.dispose()


async def test_boxes_conflict_keeps_original_scans_and_disallows_non_shipment(monkeypatch):
    from tests.test_mixed_import import SSCC
    engine, factory, user, doc, cz = await context(monkeypatch, [(A, None)])
    cz.unpack_box = AsyncMock(return_value=[B])
    try:
        async with factory() as db:
            job = await mixed.start(db, doc, user, [SSCC], 'boxes')
        await mixed.run_import(doc, user, job['id'])
        async with factory() as db:
            assert (await mixed.status(db, doc, user))['status'] == 'conflict'
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.code for s in rows] == [A]
            document = await db.get(Document, doc)
            document.kind = DocumentKind.loss
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await mixed.start(db, doc, user, [SSCC], 'boxes')
            assert exc.value.status_code == 400
    finally:
        await engine.dispose()
