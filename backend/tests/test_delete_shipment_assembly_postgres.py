import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.documents import delete_shipment_assembly
from app.api.scans import _create_scan_record
from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus, TsdDocumentSession
from tests.test_shipment_corrections_postgres import seed, seed_preview

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


@pytest.mark.parametrize('status', [DocumentStatus.draft, DocumentStatus.accepted])
async def test_delete_releases_marks_for_rescan_in_replacement(status):
    engine, factory, user, profile, source = await seed()
    code = '04603743309261BBBXCJJAAAAem7G'
    try:
        target = await seed_preview(factory, user, profile, source, session='completed')
        async with factory() as db:
            original = await db.get(Document, target)
            original.status = status
            # A replacement can have the same visible number and a different MS ID.
            replacement = await db.get(Document, source.id)
            replacement.status, replacement.moysklad_id = DocumentStatus.draft, str(uuid4())
            db.add(Scan(document_id=target, code=code, status=ScanStatus.valid))
            await db.commit()
            blocked, _ = await _create_scan_record(db, source.id, code, user.id)
            assert blocked.status == ScanStatus.used_in_other_doc
            blocked_id = blocked.id
        async with factory() as db:
            result = await delete_shipment_assembly(target, user, profile, db)
            assert result.status_code == 204
        async with factory() as db:
            assert await db.get(Document, target) is None
            assert await db.scalar(select(TsdDocumentSession.id).where(TsdDocumentSession.document_id == target)) is None
            assert await db.scalar(select(Scan.id).where(Scan.document_id == target)) is None
            scan, duplicate = await _create_scan_record(db, source.id, code, user.id)
            assert not duplicate and scan.id == blocked_id
            assert scan.status in (ScanStatus.pending, ScanStatus.scanned)
    finally:
        await engine.dispose()


@pytest.mark.parametrize('case,status_code', [
    ('foreign_profile', 404), ('foreign_user', 404), ('loss', 404),
    ('processing', 409), ('correction', 409),
])
async def test_denied_delete_keeps_document_and_marks(case, status_code):
    engine, factory, user, profile, source = await seed()
    try:
        async with factory() as db:
            doc = await db.get(Document, source.id)
            if case == 'processing':
                doc.status = DocumentStatus.processing
            if case == 'loss':
                doc.kind = DocumentKind.loss
            db.add(Scan(document_id=doc.id, code='protected', status=ScanStatus.valid))
            await db.commit()
        if case == 'correction':
            await seed_preview(factory, user, profile, source, meta={'shipment_correction': {'source_id': str(source.id)}})
        async with factory() as db:
            other = SimpleNamespace(id=uuid4())
            with pytest.raises(HTTPException) as error:
                await delete_shipment_assembly(source.id, other if case == 'foreign_user' else user,
                                              other if case == 'foreign_profile' else profile, db)
            assert error.value.status_code == status_code
        async with factory() as db:
            assert await db.get(Document, source.id)
            assert await db.scalar(select(Scan.id).where(Scan.document_id == source.id))
    finally:
        await engine.dispose()
