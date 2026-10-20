import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import scans, tsd
from app.core.config import settings
from app.db.models import Base, User, Document, DocumentKind, DocumentStatus, Scan, ScanStatus

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')
RAW = '04603743309261GKgWkbWAAAAdW6a'
ENCODED = 'MDQ2MDM3NDMzMDkyNjFHS2dXa2JXQUFBQWRXNmE='


@pytest.fixture
async def context():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='test')
        db.add(user)
        await db.flush()
        doc = Document(id=uuid4(), user_id=user.id, kind=DocumentKind.demand, name='KRAKEN',
            status=DocumentStatus.draft, plan=[{'gtin': RAW[:14], 'product_id': 'kraken',
                'product_name': 'KRAKEN Чеддер', 'expected_qty': 2}])
        db.add(doc)
        await db.commit()
    yield factory, user.id, doc.id
    await engine.dispose()


@pytest.mark.parametrize('terminal', [False, True])
async def test_tsd_and_com_routes_use_decoded_code_before_checks_without_external_api(context, monkeypatch, terminal):
    factory, user_id, doc_id = context
    async with factory() as db:
        user = await db.get(User, user_id)
        if terminal:
            doc = await db.get(Document, doc_id)
            monkeypatch.setattr(tsd, '_owned_tsd_document', AsyncMock(return_value=doc))
            monkeypatch.setattr(tsd, '_device_scope', AsyncMock(return_value=(user, None, None)))
            # No persisted action is needed for this route test; device-scope authorization is tested separately.
            device = NS(id=None, user_id=user_id)
            result = await tsd.create_tsd_scan(doc_id, tsd.TsdScanRequest(code=ENCODED), device, db)
        else:
            result = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=ENCODED), user, db)
        assert result.code == RAW and result.original_code == ENCODED
        assert result.status == ScanStatus.scanned and result.gtin == RAW[:14]
        assert result.moysklad_product_id == 'kraken' and result.verification is None
        again = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=RAW), user, db)
        assert again.duplicate and again.id == result.id
        assert (await db.execute(select(func.count()).select_from(Scan).where(Scan.document_id == doc_id))).scalar_one() == 1


@pytest.mark.parametrize('verification', [None, {}])
async def test_rescan_repairs_only_an_unverified_legacy_format_error(context, verification):
    factory, user_id, doc_id = context
    async with factory() as db:
        old = Scan(document_id=doc_id, code=ENCODED, status=ScanStatus.invalid,
                   error_message='Некорректный формат', verification=verification)
        db.add(old)
        await db.commit()
        old_id = old.id
        result = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=ENCODED), await db.get(User, user_id), db)
        assert result.code == RAW and result.original_code == ENCODED
        if verification is None:
            assert result.id == old_id and result.error_message is None
            assert (await db.execute(select(func.count()).select_from(Scan).where(Scan.document_id == doc_id))).scalar_one() == 1
        else:
            assert result.id != old_id


async def test_duplicate_in_another_shipment_cannot_be_bypassed_by_base64(context):
    factory, user_id, doc_id = context
    async with factory() as db:
        other = Document(user_id=user_id, kind=DocumentKind.demand, name='Already used')
        db.add(other)
        await db.flush()
        db.add(Scan(document_id=other.id, code=RAW, status=ScanStatus.valid))
        await db.commit()
        result = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=ENCODED), await db.get(User, user_id), db)
        assert result.status == ScanStatus.used_in_other_doc and result.code == RAW
        assert 'Already used' in result.error_message


async def test_bulk_and_search_use_the_same_identity_and_keep_original_after_verification(context):
    factory, user_id, doc_id = context
    async with factory() as db:
        user = await db.get(User, user_id)
        result = await scans.create_bulk_scans(scans.BulkScanRequest(document_id=doc_id, codes=[ENCODED, RAW]), user, db)
        assert len(result) == 2 and result[0].id == result[1].id and result[1].duplicate
        stored = await db.get(Scan, result[0].id)
        stored.verification = {'source': 'cz_info'}
        await db.commit()
        assert (await db.get(Scan, stored.id)).original_code == ENCODED
        # A duplicate insert rolls back the request session; a subsequent HTTP
        # request loads its authenticated user afresh.
        await db.refresh(user)
        for query in [RAW, ENCODED]:
            found = await scans.search_scans_by_code(query, user, db)
            assert [hit.scan_id for hit in found.scans] == [stored.id]


async def test_raw_first_then_encoded_duplicate_retains_input_and_status(context):
    factory, user_id, doc_id = context
    async with factory() as db:
        user = await db.get(User, user_id)
        first = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=RAW), user, db)
        result = await scans.create_scan(scans.CreateScanRequest(document_id=doc_id, code=ENCODED), user, db)
        assert result.duplicate and result.id == first.id and result.original_code == ENCODED
        assert result.status == first.status
