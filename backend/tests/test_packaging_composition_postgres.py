import os
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
from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services import packaging_composition as service
from app.worker import tasks
from tests.test_packaging_composition import BOX, BLOCK, A, B

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


async def setup(monkeypatch):
    from app.db.models import User
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session, 'engine', engine)
    monkeypatch.setattr(session, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(tasks.packaging_composition_task, 'delay', Mock())
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        doc = Document(id=uuid4(), user_id=user.id, kind=DocumentKind.demand, name='Hierarchy QA', status=DocumentStatus.accepted)
        db.add(doc)
        await db.flush()
        db.add(Scan(document_id=doc.id, code=A, status=ScanStatus.valid, package_type='UNIT'))
        await db.commit()
    return engine, factory, user.id, doc.id


async def test_hierarchy_survives_flattened_scans_and_nested_roots_are_not_duplicated(monkeypatch):
    engine, factory, user, doc = await setup(monkeypatch)
    cz = NS(aggregate_kinds={BOX: 'BOX', BLOCK: 'GROUP'}, aggregate_trees={
        BOX: {'code': BOX, 'kind': 'BOX', 'raw': {BLOCK: [A, B]}},
        BLOCK: {'code': BLOCK, 'kind': 'GROUP', 'raw': [A, B]}})
    try:
        async with factory() as db:
            await service.capture(db, doc, cz)
            await db.commit()
            result = await service.view(db, doc, user)
            assert len(result['roots']) == 1
            assert result['roots'][0]['children'][0]['kind'] == 'GROUP'
            assert len((await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()) == 1
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await service.view(db, doc, uuid4())
            assert error.value.status_code == 404
    finally:
        await engine.dispose()


async def test_background_lookup_uses_document_credentials_and_does_not_edit_assembly(monkeypatch):
    engine, factory, user, doc = await setup(monkeypatch)
    token = AsyncMock(return_value='test')
    monkeypatch.setattr(tasks, '_get_cz_token', token)
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['tobacco']))
    cz = NS(aggregate_kinds={}, aggregate_trees={})
    async def unpack(code):
        cz.aggregate_trees[code] = {'code': code, 'kind': 'BOX', 'raw': {BLOCK: [A, B]}}
        cz.aggregate_kinds[code] = 'BOX'
        return [A, B]
    async def info(code):
        cz.aggregate_kinds[code] = 'GROUP'
    cz.unpack_box, cz.get_code_info = AsyncMock(side_effect=unpack), AsyncMock(side_effect=info)
    monkeypatch.setattr(service, 'ChestnyZnakService', Mock(return_value=cz))
    try:
        async with factory() as db:
            job = await service.refresh(db, doc, user, BOX)
        await service.run_refresh(doc, user, job['id'])
        token.assert_awaited_once_with(__import__('unittest').mock.ANY, user, doc)
        async with factory() as db:
            result = await service.view(db, doc, user)
            assert result['job']['status'] == 'completed'
            assert result['roots'][0]['children'][0]['kind'] == 'GROUP'
            document = await db.get(Document, doc)
            assert document.status == DocumentStatus.accepted
            scans = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert [s.code for s in scans] == [A] and scans[0].status == ScanStatus.valid
    finally:
        await engine.dispose()


async def test_failed_refresh_preserves_last_successful_snapshot(monkeypatch):
    engine, factory, user, doc = await setup(monkeypatch)
    monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value=None))
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=[]))
    try:
        async with factory() as db:
            await service.capture(db, doc, NS(aggregate_trees={BOX: {'code': BOX, 'kind': 'BOX', 'raw': [A, B]}}))
            await db.commit()
            job = await service.refresh(db, doc, user)
        await service.run_refresh(doc, user, job['id'])
        async with factory() as db:
            result = await service.view(db, doc, user)
            assert result['job']['status'] == 'failed'
            assert [n['code'] for n in result['roots'][0]['children']] == [A, B]
            assert 'Войдите в ЧЗ' in result['roots'][0]['error']
    finally:
        await engine.dispose()


async def test_mixed_import_keeps_box_hierarchy_after_removing_package_scan_rows(monkeypatch):
    from tests.test_mixed_import_postgres import context
    from app.services import mixed_import
    engine, factory, user, doc, cz = await context(monkeypatch)
    cz.aggregate_trees, cz.aggregate_kinds = {}, {BOX: 'BOX', BLOCK: 'GROUP'}
    async def unpack(code):
        cz.aggregate_trees[code] = {'code': code, 'kind': 'BOX', 'raw': {BLOCK: [A, B]}}
        return [A, B]
    async def info(code):
        if code == BLOCK:
            cz.aggregate_trees[code] = {'code': code, 'kind': 'GROUP', 'raw': [A, B]}
        return NS(children=[A, B] if code == BLOCK else [], package_type='GROUP' if code == BLOCK else 'UNIT')
    cz.unpack_box, cz.get_code_info = AsyncMock(side_effect=unpack), AsyncMock(side_effect=info)
    try:
        async with factory() as db:
            job = await mixed_import.start(db, doc, user, [BOX, BLOCK, A, B], 'units')
        await mixed_import.run_import(doc, user, job['id'])
        async with factory() as db:
            rows = (await db.execute(select(Scan).where(Scan.document_id == doc))).scalars().all()
            assert {s.code for s in rows} == {A, B}
            result = await service.view(db, doc, user)
            assert len(result['roots']) == 1 and result['roots'][0]['code'] == BOX
            assert result['roots'][0]['children'][0]['code'] == BLOCK
            assert [c['code'] for c in result['roots'][0]['children'][0]['children']] == [A, B]
    finally:
        await engine.dispose()


async def test_flat_legacy_contents_are_not_presented_as_real_hierarchy(monkeypatch):
    engine, factory, user, doc = await setup(monkeypatch)
    try:
        async with factory() as db:
            db.add(Scan(document_id=doc, code=BOX, is_box=True, package_type='BOX',
                        child_codes=[A, B], box_quantity=2, status=ScanStatus.valid))
            await db.commit()
            result = await service.view(db, doc, user)
            assert len(result['roots']) == 1
            assert result['roots'][0]['incomplete']
            assert result['roots'][0]['children'] == []
            assert result['roots'][0]['expected_units'] == 2
    finally:
        await engine.dispose()


async def test_parallel_lookup_does_not_silently_replace_requested_code(monkeypatch):
    engine, factory, user, doc = await setup(monkeypatch)
    try:
        async with factory() as db:
            first = await service.refresh(db, doc, user, BOX)
        async with factory() as db:
            same = await service.refresh(db, doc, user, BOX)
            assert first['id'] == same['id']
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await service.refresh(db, doc, user, BLOCK)
            assert error.value.status_code == 409
        tasks.packaging_composition_task.delay.assert_called_once()
    finally:
        await engine.dispose()
