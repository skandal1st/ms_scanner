"""Isolated real-DB coverage; only test-owned rows are created/deleted."""
import asyncio
import os
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.db.models import Base, User, Document, Scan, ScanStatus, GtinPackagingFormat
from app.services import packaging_recheck as service, packaging_formats as formats
from app.worker import tasks

pytestmark=pytest.mark.skipif(os.getenv('RECHECK_POSTGRES')!='1',reason='isolated PostgreSQL required')


def gtin():
    body='09'+str(uuid4().int % 10**11).zfill(11)
    total=sum(int(d)*(3 if i%2==0 else 1) for i,d in enumerate(body))
    return body+str((-total)%10)


async def test_migration_up_and_down_in_a_transactional_isolated_schema():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text, inspect
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    schema='recheck_'+uuid4().hex
    async with engine.connect() as conn:
        transaction=await conn.begin()
        try:
            await conn.execute(text(f'CREATE SCHEMA {schema}'))
            await conn.execute(text(f'SET LOCAL search_path TO {schema}'))
            def migrate(sync):
                spec=importlib.util.spec_from_file_location('recheck_migration','alembic/versions/20261030_01_packaging_formats.py')
                module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                with Operations.context(MigrationContext.configure(sync)):
                    module.upgrade()
                    assert inspect(sync).has_table('gtin_packaging_formats',schema=schema)
                    module.downgrade()
                    assert not inspect(sync).has_table('gtin_packaging_formats',schema=schema)
            await conn.run_sync(migrate)
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_real_recheck_repairs_scan_and_persists_format_without_changing_other_scans(monkeypatch):
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    key=gtin(); uid,did,sid,other=uuid4(),uuid4(),uuid4(),uuid4()
    code=f'02{key}13260930102MSCS2111012213710'
    children=[f'{key}UNIT{i:03d}' for i in range(10)]
    monkeypatch.setattr(service,'AsyncSessionLocal',factory)
    monkeypatch.setattr(service,'publish_scan',AsyncMock())
    monkeypatch.setattr(tasks,'_get_cz_token',AsyncMock(return_value='fixture'))
    monkeypatch.setattr(tasks,'_get_cz_product_groups',AsyncMock(return_value=['otp']))
    queue=Mock(); monkeypatch.setattr(tasks.recheck_scan_packaging_task,'delay',queue)
    monkeypatch.setattr(service,'ChestnyZnakService',lambda **kw:NS(get_code_info=AsyncMock(
        return_value=NS(package_type='BOX',gtin=None,children=children))))
    try:
        async with factory() as db:
            db.add(User(id=uid,email=f'recheck-{uid}@example.test',password_hash='fixture')); await db.flush()
            db.add(Document(id=did,user_id=uid,name='Recheck QA',kind='demand',
                plan=[{'gtin':key,'product_id':'correct','product_name':'Test 40 g','expected_qty':10}])); await db.flush()
            db.add_all([Scan(id=sid,document_id=did,code=code,gtin='02008020841545',status=ScanStatus.invalid),
                Scan(id=other,document_id=did,code='unrelated',status=ScanStatus.invalid)])
            await db.commit()
            item=await db.get(Scan,sid)
            await service.start(db,item,uid)
        rid=queue.call_args.args[2]
        await service.run(str(sid),str(uid),rid)
        async with factory() as db:
            item=await db.get(Scan,sid)
            assert item.box_quantity==10 and item.status==ScanStatus.scanned and item.gtin==key
            assert item.moysklad_product_id=='correct' and item.child_codes==children
            assert (await db.get(Scan,other)).status==ScanStatus.invalid
            rule=await db.get(GtinPackagingFormat,(key,'gs1_ai02'))
            assert rule.type_mask==4 and rule.last_quantity==10
            assert await formats.confirmed_unit(db,code) is False
            assert await formats.confirmed_unit(db,f'01{key}21UNIT') is None
        async def observe(kind):
            async with factory() as db:
                await formats.learn(db,f'01{key}21UNIT',{'gtin':key,'package_type':kind,'box_quantity':None if kind=='UNIT' else 10})
                await db.commit()
        await asyncio.gather(observe('UNIT'),observe('GROUP'),observe('UNIT'))
        async with factory() as db:
            assert await formats.confirmed_unit(db,f'01{key}21UNIT') is False
            rule=await db.get(GtinPackagingFormat,(key,'gs1_ai01'))
            assert rule.type_mask==3 and rule.observations==3
    finally:
        async with factory() as db:
            await db.execute(delete(Scan).where(Scan.document_id==did))
            await db.execute(delete(Document).where(Document.id==did))
            await db.execute(delete(User).where(User.id==uid))
            await db.execute(delete(GtinPackagingFormat).where(GtinPackagingFormat.gtin==key))
            await db.commit()
        await engine.dispose()
