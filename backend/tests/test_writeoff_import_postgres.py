"""Writeoff imports use a separate draft and an isolated PostgreSQL database."""
import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import (Base, Document, DocumentKind, DocumentStatus, OrganizationProfile,
                           Scan, ScanStatus, TsdDevice, TsdDocumentSession, User, Workplace)
from app.services.writeoff_import import import_shipment_codes

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
RAW = '04670163000207Hm7&lkpAAAAi2gC'


async def seed(*, ready=True, session=None):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Audit')
        db.add(profile)
        await db.flush()
        common = dict(user_id=user.id, organization_profile_id=profile.id, moysklad_id=str(uuid4()),
                      moysklad_store_id='store', name='Shipment')
        source = Document(id=uuid4(), kind=DocumentKind.demand, **common,
            status=DocumentStatus.accepted if ready else DocumentStatus.draft)
        target = Document(id=uuid4(), kind=DocumentKind.loss, **common, status=DocumentStatus.draft)
        db.add_all([source, target])
        await db.flush()
        db.add(Scan(document_id=source.id, code=RAW, status=ScanStatus.valid,
            product_name='Test', gtin='04670163000207', serial='Hm7&lkp',
            verification={'owner_result': 'match'}, verified_at=source.created_at))
        if session:
            workplace = Workplace(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Audit')
            db.add(workplace)
            await db.flush()
            device = TsdDevice(id=uuid4(), user_id=user.id, workplace_id=workplace.id)
            db.add(device)
            await db.flush()
            db.add(TsdDocumentSession(document_id=source.id, device_id=device.id, status=session))
        await db.commit()
    return engine, factory, user.id, source.id, target.id


@pytest.mark.parametrize('ready,session', [(True, None), (False, 'completed')])
async def test_import_finished_shipment_preserves_raw_codes_and_requires_new_verification(ready, session):
    engine, factory, user, source, target = await seed(ready=ready, session=session)
    try:
        async with factory() as db:
            result = await import_shipment_codes(db, target, user)
            assert result['imported'] == 1
        async with factory() as db:
            copied = (await db.execute(select(Scan).where(Scan.document_id == target))).scalar_one()
            assert copied.code == RAW and copied.serial == 'Hm7&lkp'
            assert copied.status == ScanStatus.scanned
            assert copied.verification is None and copied.verified_at is None
            original = (await db.execute(select(Scan).where(Scan.document_id == source))).scalar_one()
            assert original.status == ScanStatus.valid and original.verification == {'owner_result': 'match'}
            assert original.id != copied.id
            again = await import_shipment_codes(db, target, user)
            assert again['imported'] == 0 and again['skipped'] == 1
    finally:
        await engine.dispose()


async def test_copy_packages_skip_unmarked_invalid_and_deduplicate_short_identity():
    engine, factory, user, source, target = await seed()
    try:
        async with factory() as db:
            db.add_all([
                Scan(document_id=target, code=RAW[:21], status=ScanStatus.scanned),
                Scan(document_id=source, code='package', status=ScanStatus.valid, child_codes=['child1', 'child2'],
                     is_box=True, box_quantity=2, keep_aggregate=True, package_type='BOX'),
                Scan(document_id=source, code='barcode', status=ScanStatus.valid, is_barcode=True),
                Scan(document_id=source, code='invalid', status=ScanStatus.invalid),
            ])
            await db.commit()
            result = await import_shipment_codes(db, target, user)
            assert result['imported'] == 1 and result['skipped'] == 1
        async with factory() as db:
            scans = (await db.execute(select(Scan).where(Scan.document_id == target))).scalars().all()
            assert {scan.code for scan in scans} == {RAW[:21], 'package'}
            package = next(scan for scan in scans if scan.code == 'package')
            assert package.child_codes == ['child1', 'child2']
            assert package.is_box and package.keep_aggregate and package.box_quantity == 2
    finally:
        await engine.dispose()


@pytest.mark.parametrize('case', ['unfinished', 'active', 'foreign_user', 'foreign_profile',
                                  'foreign_store', 'superseded', 'sent_writeoff', 'processing', 'not_loss'])
async def test_unready_or_inaccessible_documents_are_not_imported(case):
    engine, factory, user, source, target = await seed(
        ready=case not in ('unfinished', 'active'), session='active' if case == 'active' else None)
    try:
        async with factory() as db:
            src, dst = await db.get(Document, source), await db.get(Document, target)
            if case == 'foreign_user':
                user = uuid4()
            elif case == 'foreign_profile':
                src.organization_profile_id = None
            elif case == 'foreign_store':
                dst.moysklad_store_id = 'elsewhere'
            elif case == 'superseded':
                src.upd_meta = {'superseded_by_document_id': str(uuid4())}
            elif case == 'sent_writeoff':
                dst.cz_doc_ids = [{'id': 'already-sent'}]
            elif case == 'processing':
                dst.status = DocumentStatus.processing
            elif case == 'not_loss':
                dst.kind = DocumentKind.demand
            await db.commit()
            with pytest.raises(HTTPException):
                await import_shipment_codes(db, target, user)
        async with factory() as db:
            assert not (await db.execute(select(Scan.id).where(Scan.document_id == target))).all()
    finally:
        await engine.dispose()


async def test_parallel_imports_into_same_draft_are_idempotent():
    engine, factory, user, source, target = await seed()
    async def run():
        async with factory() as db:
            return await import_shipment_codes(db, target, user)
    try:
        results = await asyncio.gather(run(), run())
        assert sorted(r['imported'] for r in results) == [0, 1]
    finally:
        await engine.dispose()


async def test_existing_other_writeoff_blocks_entire_import():
    engine, factory, user, source, target = await seed()
    try:
        async with factory() as db:
            dst = await db.get(Document, target)
            other = Document(id=uuid4(), user_id=user, organization_profile_id=dst.organization_profile_id,
                             name='Other writeoff', kind=DocumentKind.loss)
            db.add(other)
            await db.flush()
            db.add(Scan(document_id=other.id, code=RAW, status=ScanStatus.valid))
            await db.commit()
            with pytest.raises(HTTPException, match='') as error:
                await import_shipment_codes(db, target, user)
            assert error.value.status_code == 409
        async with factory() as db:
            assert not (await db.execute(select(Scan.id).where(Scan.document_id == target))).all()
    finally:
        await engine.dispose()


async def test_parallel_imports_into_different_writeoffs_cannot_reuse_the_same_codes():
    engine, factory, user, source, target = await seed()
    try:
        async with factory() as db:
            dst = await db.get(Document, target)
            other = Document(id=uuid4(), user_id=user, organization_profile_id=dst.organization_profile_id,
                             moysklad_id=dst.moysklad_id, name='Other writeoff', kind=DocumentKind.loss)
            db.add(other)
            await db.commit()
            other_id = other.id
        async def run(target_id):
            async with factory() as db:
                return await import_shipment_codes(db, target_id, user)
        results = await asyncio.gather(run(target), run(other_id), return_exceptions=True)
        assert sum(isinstance(result, dict) and result['imported'] == 1 for result in results) == 1
        assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    finally:
        await engine.dispose()


async def test_import_uses_saved_correction_instead_of_superseded_original():
    engine, factory, user, source, target = await seed()
    try:
        async with factory() as db:
            original = await db.get(Document, source)
            revision = Document(id=uuid4(), user_id=user, organization_profile_id=original.organization_profile_id,
                moysklad_id=original.moysklad_id, name='Corrected shipment', kind=DocumentKind.demand,
                status=DocumentStatus.accepted, upd_meta={'shipment_correction': {'source_id': str(source)}})
            db.add(revision)
            await db.flush()
            original.upd_meta = {'superseded_by_document_id': str(revision.id)}
            db.add(Scan(document_id=revision.id, code='corrected-mark', status=ScanStatus.valid))
            await db.commit()
            await import_shipment_codes(db, target, user)
        async with factory() as db:
            codes = (await db.execute(select(Scan.code).where(Scan.document_id == target))).scalars().all()
            assert codes == ['corrected-mark']
    finally:
        await engine.dispose()
