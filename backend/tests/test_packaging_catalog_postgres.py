import asyncio
import os

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import Base, GtinPackaging, CzLog
from app.services import packaging_catalog as catalog
from app.services.packaging_catalog import record_observations, is_confirmed_unit
from app.services.scan_packaging_cache import has_confirmed_unit

pytestmark = pytest.mark.skipif(os.getenv('PACKAGING_POSTGRES') != '1', reason='isolated packaging PostgreSQL required')
GTIN = '04640195516373'


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(delete(GtinPackaging))
        await conn.execute(delete(CzLog))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(catalog, 'AsyncSessionLocal', factory)
    yield factory
    await engine.dispose()


async def test_confirmations_survive_new_sessions_and_are_shared_across_documents(factory):
    await record_observations([(GTIN,GTIN,'UNIT',False)])
    async with factory() as db:
        assert await has_confirmed_unit('client-a', 'first-document', GTIN, db=db)
    async with factory() as db:
        assert await has_confirmed_unit('client-b', 'next-document', GTIN, db=db)
        row = await db.get(GtinPackaging, GTIN)
        assert row.observations == 1 and row.last_source == 'cz_info'


async def test_concurrent_contradictory_cz_responses_disable_fast_path_permanently(factory):
    await asyncio.gather(*[record_observations([(GTIN,GTIN,kind,kind!='UNIT')])
                          for kind in ['UNIT','GROUP','UNIT','BOX','UNIT']])
    async with factory() as db:
        row = await db.get(GtinPackaging, GTIN)
        assert row.observations == 5 and row.type_mask == 7
        assert not await is_confirmed_unit(db, GTIN)
    await record_observations([(GTIN,GTIN,'UNIT',False)])
    async with factory() as db:
        assert not await is_confirmed_unit(db, GTIN)


async def test_same_batch_conflict_is_one_atomic_row_and_unmatched_gtin_is_ignored(factory):
    await record_observations([(GTIN,GTIN,'UNIT',False), (GTIN,GTIN,'GROUP',True),
                              (GTIN,'04630339608897','UNIT',False)], source='cz_batch')
    async with factory() as db:
        rows=(await db.execute(select(GtinPackaging))).scalars().all()
        assert len(rows)==1 and rows[0].observations==2 and rows[0].type_mask==3


async def test_existing_cz_log_seeds_catalogue_without_external_requests(factory, monkeypatch):
    from app.services import packaging_catalog_import as importer
    monkeypatch.setattr(importer, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(importer, 'record_observations', record_observations)
    async with factory() as db:
        db.add(CzLog(request_method='POST', request_url='https://example.test/cises/info',
            request_body=[f'01{GTIN}21PRIVATE'], response_status=200,
            response_body=[{'cisInfo':{'gtin':GTIN,'generalPackageType':'UNIT', 'ownerInn':'private'}}]))
        await db.commit()
    preview = await importer.import_history(apply=False)
    assert preview['gtins']==1 and not preview['applied']
    async with factory() as db:
        assert not await is_confirmed_unit(db, GTIN)
    applied = await importer.import_history(apply=True)
    assert applied['unit_gtins']==1
    async with factory() as db:
        assert await is_confirmed_unit(db, GTIN)
