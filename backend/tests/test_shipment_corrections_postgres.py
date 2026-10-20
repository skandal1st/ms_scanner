"""Real JSONB persistence and correction access checks in an isolated audit database."""
import copy
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import (Base, Document, DocumentKind, DocumentStatus, OrganizationProfile,
                           Scan, ScanStatus, TsdDevice, TsdDocumentSession, User, Workplace)
from app.services import shipment_corrections as service
from app.api import shipment_corrections as routes
from app.services.document_guard import editable_document

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


@pytest.mark.parametrize('mode', ['valid', 'invalid', 'duplicate', 'base64', 'wrong_position', 'auto', 'ambiguous'])
async def test_replace_preserves_baseline_and_only_removes_original_for_a_new_valid_code(monkeypatch, mode):
    import base64
    from unittest.mock import Mock
    from app.worker.tasks import persist_assigned_gtin_task
    monkeypatch.setattr(persist_assigned_gtin_task, 'delay', Mock())
    engine, factory, user, profile, source = await seed()
    pos_id, other_pos, product_id = str(uuid4()), str(uuid4()), str(uuid4())
    old_code, new_code = '04603743309261GKgWkbWAAAAdW6a', '04603743309261BBBXCJJAAAAem7G'
    baseline = {pos_id: {'fields': {'id': pos_id, 'quantity': 1, 'price': 12500},
        'product_id': product_id, 'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': str(uuid4()), 'cis': old_code, 'type': 'trackingcode'}]}}
    baseline[other_pos] = {**copy.deepcopy(baseline[pos_id]), 'codes': []}
    if mode == 'auto':
        baseline[other_pos]['product_id'] = str(uuid4())
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(
        {'organization': {'id': 'org'}, 'store': {'id': 'store'}}, baseline)))
    ms = NS(build_plan=AsyncMock(return_value=[{'product_id': product_id, 'gtin': '04603743309261', 'product_name': 'Test', 'expected_qty': 1}]))
    access = routes.Scope(user, profile.id, ['store'])
    try:
        async with factory() as db:
            revision = await service.start(db, await db.get(Document, source.id), ms, 'actor')
            revision_id = revision.id
            old = (await db.execute(select(Scan).where(Scan.document_id == revision_id))).scalar_one()
            old_id = old.id
        raw = old_code if mode == 'duplicate' else '010460374330926221BAD' if mode == 'invalid' else new_code
        if mode == 'base64':
            raw = base64.b64encode(raw.encode()).decode()
        async with factory() as db:
            body = routes.AddCode(code=raw,
                position_id=None if mode in ('auto', 'ambiguous') else other_pos if mode == 'wrong_position' else pos_id,
                replace_scan_id=old_id if mode not in ('auto', 'ambiguous') else None)
            if mode in ('wrong_position', 'ambiguous'):
                with pytest.raises(HTTPException) as error:
                    await routes.add_code(revision_id, body, access, db)
                assert error.value.status_code == 400
            else:
                result = await routes.add_code(revision_id, body, access, db)
                assert result.get('replaced', False) == (mode in ('valid', 'base64'))
        async with factory() as db:
            revision = await db.get(Document, revision_id)
            assert service.correction(revision)['baseline'] == baseline
            scans = (await db.execute(select(Scan).where(Scan.document_id == revision_id))).scalars().all()
            if mode in ('valid', 'base64'):
                assert len(scans) == 1 and scans[0].code == new_code and scans[0].status == ScanStatus.scanned
                if mode == 'base64':
                    assert scans[0].original_code == raw
                scans[0].status = ScanStatus.valid
                delta = service.build_delta(revision, scans)
                assert len(delta['removed']) == 1 and len(delta['added']) == 1
                assert not delta['quantities']
            elif mode == 'auto':
                assert len(scans) == 2
                new = next(s for s in scans if s.code == new_code)
                assert service.correction(revision)['add_positions'][str(new.id)] == pos_id
            else:
                assert any(s.id == old_id for s in scans)
                assert len(scans) == (2 if mode == 'invalid' else 1)
    finally:
        await engine.dispose()


async def seed():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Test', moysklad_organization_id='org')
        db.add(profile)
        await db.flush()
        source = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id,
            name='Sent', kind=DocumentKind.demand, status=DocumentStatus.accepted,
            moysklad_id=str(uuid4()), moysklad_organization_id='org', moysklad_store_id='store')
        db.add(source)
        await db.commit()
    return engine, factory, user, profile, source


async def seed_preview(factory, user, profile, source, *, status=DocumentStatus.draft, meta=None, session=None, scan=False):
    async with factory() as db:
        preview = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id,
            name=source.name, kind=DocumentKind.demand, status=status, moysklad_id=source.moysklad_id,
            moysklad_organization_id='org', moysklad_store_id='store', upd_meta=meta)
        db.add(preview)
        await db.flush()
        if scan:
            db.add(Scan(document_id=preview.id, code='unsent-mark', status=ScanStatus.invalid))
        if session:
            workplace = Workplace(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Audit')
            db.add(workplace)
            await db.flush()
            device = TsdDevice(id=uuid4(), user_id=user.id, workplace_id=workplace.id)
            db.add(device)
            await db.flush()
            db.add(TsdDocumentSession(device_id=device.id, document_id=preview.id, status=session))
        await db.commit()
        return preview.id


@pytest.mark.parametrize('session', [None, 'completed', 'released'])
async def test_empty_unstarted_preview_is_replaced_atomically_without_deleting_history(monkeypatch, session):
    engine, factory, user, profile, source = await seed()
    from app.db import session as db_session
    monkeypatch.setattr(db_session, 'engine', engine)
    preview_id = await seed_preview(factory, user, profile, source, meta={'keep': 'metadata'}, session=session)
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(
        {'organization': {'id': 'org'}, 'store': {'id': 'store'}}, {})))
    monkeypatch.setattr(routes, '_get_ms_service', AsyncMock(return_value=NS(build_plan=AsyncMock(return_value=[]))))
    try:
        async with factory() as db:
            opened = await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
        async with factory() as db:
            preview = await db.get(Document, preview_id)
            revision = await db.get(Document, opened['id'])
            assert preview.upd_meta == {'keep': 'metadata', 'superseded_by_document_id': opened['id']}
            assert service.correction(revision)['source_id'] == str(source.id)
            assert (await db.get(Document, source.id)).status == DocumentStatus.accepted
            with pytest.raises(HTTPException) as error:
                await editable_document(db, preview_id, user.id)
            assert error.value.status_code == 409
        async with factory() as db:
            resumed = await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
            assert resumed == opened
        service.snapshot.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.parametrize('preview_options', [
    {'scan': True},
    {'meta': {'collection_start': {'done': True}}},
    {'meta': {'collection_start': {'started_at': '2026-10-19T12:00:00Z'}}},
    {'status': DocumentStatus.processing},
    {'session': 'active'},
])
async def test_real_assembly_still_blocks_correction_without_loading_ms(monkeypatch, preview_options):
    engine, factory, user, profile, source = await seed()
    preview_id = await seed_preview(factory, user, profile, source, **preview_options)
    monkeypatch.setattr(service, 'start', AsyncMock())
    try:
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
            assert error.value.status_code == 409
        async with factory() as db:
            assert not ((await db.get(Document, preview_id)).upd_meta or {}).get('superseded_by_document_id')
        service.start.assert_not_awaited()
    finally:
        await engine.dispose()


async def test_failed_ms_snapshot_keeps_empty_preview_available(monkeypatch):
    engine, factory, user, profile, source = await seed()
    from app.db import session as db_session
    monkeypatch.setattr(db_session, 'engine', engine)
    preview_id = await seed_preview(factory, user, profile, source)
    monkeypatch.setattr(routes, '_get_ms_service', AsyncMock(return_value=NS()))
    monkeypatch.setattr(service, 'snapshot', AsyncMock(side_effect=ValueError('Ошибка загрузки МС')))
    try:
        async with factory() as db:
            with pytest.raises(HTTPException):
                await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
        async with factory() as db:
            assert (await editable_document(db, preview_id, user.id)).status == DocumentStatus.draft
            revisions = (await db.execute(select(Document).where(Document.moysklad_id == source.moysklad_id))).scalars().all()
            assert len(revisions) == 2
    finally:
        await engine.dispose()


async def test_concurrent_scan_waits_for_preview_replacement_and_cannot_write_to_old_draft(monkeypatch):
    import asyncio
    from app.db import session as db_session
    engine, factory, user, profile, source = await seed()
    monkeypatch.setattr(db_session, 'engine', engine)
    preview_id = await seed_preview(factory, user, profile, source)
    loading, finish_loading, editing = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def snapshot(*args):
        loading.set()
        await finish_loading.wait()
        return {'organization': {'id': 'org'}, 'store': {'id': 'store'}}, {}

    monkeypatch.setattr(service, 'snapshot', snapshot)
    monkeypatch.setattr(routes, '_get_ms_service', AsyncMock(return_value=NS(build_plan=AsyncMock(return_value=[]))))

    async def open_revision():
        async with factory() as db:
            return await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)

    async def scan_preview():
        async with factory() as db:
            editing.set()
            try:
                await editable_document(db, preview_id, user.id)
                db.add(Scan(document_id=preview_id, code='concurrent-mark', status=ScanStatus.scanned))
                await db.commit()
                return None
            except HTTPException as exc:
                return exc.status_code

    opening = asyncio.create_task(open_revision())
    scanning = None
    try:
        await asyncio.wait_for(loading.wait(), 5)
        scanning = asyncio.create_task(scan_preview())
        await asyncio.wait_for(editing.wait(), 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(scanning), .1)
        finish_loading.set()
        opened = await asyncio.wait_for(opening, 5)
        assert await asyncio.wait_for(scanning, 5) == 409
        async with factory() as db:
            preview = await db.get(Document, preview_id)
            assert preview.upd_meta['superseded_by_document_id'] == opened['id']
            assert not (await db.execute(select(Scan.id).where(Scan.document_id == preview_id))).all()
    finally:
        finish_loading.set()
        for task in [opening, scanning]:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[task for task in [opening, scanning] if task is not None], return_exceptions=True)
        await engine.dispose()


async def test_baseline_persists_old_source_stays_accepted_and_cancel_is_local(monkeypatch):
    engine, factory, user, profile, source = await seed()
    pos_id = str(uuid4())
    baseline = {pos_id: {'fields': {'id': pos_id, 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': str(uuid4()), 'cis': 'old-code', 'type': 'trackingcode'}]}}
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({'organization': {'id': 'org'}, 'store': {'id': 'store'}}, baseline)))
    ms = NS(build_plan=AsyncMock(return_value=[]))
    access = routes.Scope(user, profile.id, ['store'])
    try:
        async with factory() as db:
            revision = await service.start(db, await db.get(Document, source.id), ms, 'actor')
            revision_id = revision.id
        async with factory() as db:
            original, revision = await db.get(Document, source.id), await db.get(Document, revision_id)
            assert original.status == DocumentStatus.accepted
            assert service.correction(revision)['baseline'] == baseline
            scans = (await db.execute(select(Scan).where(Scan.document_id == revision_id))).scalars().all()
            assert len(scans) == 1 and scans[0].status == ScanStatus.valid
            assert str(scans[0].id) in service.correction(revision)['baseline_scans']
            detail = await routes.detail(revision_id, access, db)
            assert detail['scans'][0]['existing']
            assert detail['scans'][0]['position_id'] == pos_id
            await routes.remove_code(revision_id, scans[0].id, access, db)
            delta = await routes.preview(revision_id, routes.PreviewRequest(), access, db)
            assert delta['removed'][0]['code'] == 'old-code'
            await routes.cancel(revision_id, access, db)
        async with factory() as db:
            listed = await routes.list_sources(access, db)
            assert [row['id'] for row in listed] == [str(source.id)]
            assert (await db.get(Document, source.id)).status == DocumentStatus.accepted
    finally:
        await engine.dispose()


@pytest.mark.parametrize('addition_already_saved', [False, True])
async def test_rebase_preserves_partial_writes_external_codes_and_remaining_raw_scan(monkeypatch, addition_already_saved):
    engine, factory, user, profile, source = await seed()
    pid = str(uuid4())
    raw = '010460123456789021NEW123\x1d91ABCD\x1d92signature'
    old = {'id': str(uuid4()), 'cis': 'old', 'type': 'trackingcode'}
    baseline = {pid: {'fields': {'id': pid, 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None, 'codes': [old]}}
    remote = {'organization': {'id': 'org'}, 'store': {'id': 'store'}}
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(remote, baseline)))
    ms = NS(build_plan=AsyncMock(return_value=[]))
    try:
        async with factory() as db:
            revision = await service.start(db, await db.get(Document, source.id), ms, 'actor')
            scans = (await db.execute(select(Scan).where(Scan.document_id == revision.id))).scalars().all()
            await db.delete(scans[0])
            new_scan = Scan(id=uuid4(), document_id=revision.id, code=raw, status=ScanStatus.valid)
            db.add(new_scan)
            state = copy.deepcopy(service.correction(revision))
            state['add_positions'][str(new_scan.id)] = pid
            revision.upd_meta = {service.MARKER: state}
            delta = service.build_delta(revision, [new_scan], True)
            expected = service.after_operation(baseline, delta['operations'][0])
            state['job'] = {'delta': delta, 'expected': expected, 'completed': 1, 'in_flight': 1}
            revision.upd_meta = {service.MARKER: state}
            await db.commit()
            actual = copy.deepcopy(expected)
            # Another operator added a mark and changed quantity in MS.
            actual[pid]['fields']['quantity'] = 5
            actual[pid]['codes'].append({'id': str(uuid4()), 'cis': 'external', 'type': 'trackingcode'})
            if addition_already_saved:
                actual[pid]['codes'].append({**delta['operations'][1]['code'], 'id': str(uuid4())})
            monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(remote, actual)))
            fresh = await service.rebase(db, revision, ms, 'actor')
            fresh_id, old_id = fresh.id, revision.id
        async with factory() as db:
            fresh = await db.get(Document, fresh_id)
            scans = (await db.execute(select(Scan).where(Scan.document_id == fresh_id))).scalars().all()
            pending = service.build_delta(fresh, scans, True)
            assert len(pending['added']) == (0 if addition_already_saved else 1)
            assert pending['removed'] == []
            assert pending['quantities'] == []  # Replacement must not increase live quantity 5.
            assert any(scan.code == 'external' for scan in scans)
            if not addition_already_saved:
                assert any(scan.code == raw for scan in scans)
            assert not service.correction(fresh).get('job')
            await editable_document(db, fresh_id, user.id)
            assert (await db.get(Document, old_id)).upd_meta['superseded_by_document_id'] == str(fresh_id)
            assert (await db.get(Document, source.id)).status == DocumentStatus.accepted
    finally:
        await engine.dispose()


async def test_foreign_account_profile_and_warehouse_cannot_access_revision():
    engine, factory, user, profile, source = await seed()
    try:
        for scope in [routes.Scope(NS(id=uuid4()), profile.id, []),
                      routes.Scope(user, uuid4(), []), routes.Scope(user, profile.id, ['foreign'])]:
            async with factory() as db:
                with pytest.raises(HTTPException) as error:
                    await routes.owned(db, scope, source.id)
                assert error.value.status_code == 404
    finally:
        await engine.dispose()


async def test_concurrent_open_reuses_one_revision(monkeypatch):
    import asyncio
    from app.db import session
    engine, factory, user, profile, source = await seed()
    monkeypatch.setattr(session, 'engine', engine)
    pos_id = str(uuid4())
    baseline = {pos_id: {'fields': {'id': pos_id, 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': str(uuid4()), 'cis': 'old-code', 'type': 'trackingcode'}]}}
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=({'organization': {'id': 'org'}, 'store': {'id': 'store'}}, baseline)))
    monkeypatch.setattr(routes, '_get_ms_service', AsyncMock(return_value=NS(build_plan=AsyncMock(return_value=[]))))
    async def open_one():
        async with factory() as db:
            return await routes.open_correction(source.id, routes.Scope(user, profile.id, []), db)
    try:
        opened = await asyncio.gather(open_one(), open_one())
        assert opened[0]['id'] == opened[1]['id']
        async with factory() as db:
            revisions = (await db.execute(select(Document).where(Document.moysklad_id == source.moysklad_id, Document.status == DocumentStatus.draft))).scalars().all()
            assert len(revisions) == 1
    finally:
        await engine.dispose()


async def test_queue_freezes_reviewed_revision_and_duplicate_save_does_not_enqueue(monkeypatch):
    engine, factory, user, profile, source = await seed()
    scan_id, tc_id = uuid4(), str(uuid4())
    baseline = {'position': {'fields': {'id': 'position', 'quantity': 1}, 'product_id': 'product',
        'product_name': 'Test', 'tracking_type': None,
        'codes': [{'id': tc_id, 'cis': 'old', 'type': 'trackingcode'}]}}
    state = {'source_id': str(source.id), 'baseline': baseline,
        'baseline_scans': {str(scan_id): {'position_id': 'position', 'code': baseline['position']['codes'][0]}}, 'add_positions': {}}
    access = routes.Scope(user, profile.id, [])
    from app.worker.tasks import correct_shipment_task
    queued = []
    monkeypatch.setattr(correct_shipment_task, 'delay', lambda *args: queued.append(args))
    try:
        async with factory() as db:
            revision = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Revision',
                kind=DocumentKind.demand, moysklad_id=source.moysklad_id, status=DocumentStatus.draft,
                upd_meta={service.MARKER: copy.deepcopy(state)})
            db.add(revision)
            await db.commit()
            revision_id = revision.id
            delta = await routes.preview(revision_id, routes.PreviewRequest(), access, db)
            await routes.save(revision_id, routes.PreviewRequest(preview_hash=delta['preview_hash']), access, db)
        async with factory() as db:
            saved = await db.get(Document, revision_id)
            assert saved.status == DocumentStatus.processing
            assert service.correction(saved)['job']['completed'] == 0
            with pytest.raises(HTTPException):
                await routes.save(revision_id, routes.PreviewRequest(preview_hash=delta['preview_hash']), access, db)
            with pytest.raises(HTTPException):
                await editable_document(db, revision_id, user.id)
            assert len(queued) == 1
            saved.status = DocumentStatus.draft
            await db.commit()
            with pytest.raises(HTTPException, match=''):
                await editable_document(db, revision_id, user.id)
    finally:
        await engine.dispose()
