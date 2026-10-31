import os
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import pytest
from sqlalchemy import delete, select, update, func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import HTTPException
from app.core.config import settings
from app.core.security import encrypt_token
from app.db.models import User,Integration,GtinProductMap,GtinAuditRun,GtinAuditFinding,Document,Scan
from app.services import gtin_mapping_sync as service
from app.api import gtin_control

pytestmark=pytest.mark.skipif(os.getenv('SYNC_POSTGRES')!='1',reason='isolated PostgreSQL required')
GTIN='04640275972501'


async def test_live_sync_is_scoped_atomic_and_does_not_change_scans_or_report(monkeypatch):
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    uid,other,rid,fid,pid,did,sid=[uuid4() for _ in range(7)]
    selection=service.Selection(run_id=rid,finding_id=fid,target_product_id=pid)
    product={'id':str(pid),'name':'Peach 25 g','barcodes':[{'gtin':GTIN}]}
    get=AsyncMock(return_value=product)
    monkeypatch.setattr(service,'MoySkladService',lambda token:NS(get_product_by_id=get))
    try:
        async with factory() as db:
            db.add_all([User(id=x,email=f'sync-{x}@example.test',password_hash='fixture') for x in [uid,other]]); await db.flush()
            db.add_all([Integration(user_id=uid,moysklad_token=encrypt_token('fake')),
                GtinProductMap(user_id=uid,gtin=GTIN,product_id='old',product_name='Peach 200 g'),
                GtinProductMap(user_id=other,gtin=GTIN,product_id='other-card',product_name='Other'),
                GtinAuditRun(id=rid,user_id=uid,status='done',finished_at=datetime.now(timezone.utc)),
                Document(id=did,user_id=uid,name='Unchanged assembly',plan=[])]); await db.flush()
            db.add_all([GtinAuditFinding(id=fid,run_id=rid,gtin=GTIN,kind='mapping',category='other_product',priority=1,
                saved_product_id='old',saved_product_name='Peach 200 g',candidates=[{'id':str(pid)}],search_text=GTIN),
                Scan(id=sid,document_id=did,code='fixture-only',gtin=GTIN,moysklad_product_id='old',product_name='Peach 200 g')])
            await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException): await service.preview(db,other,selection)
            get.assert_not_awaited()
        async with factory() as db:
            view=await service.preview(db,uid,selection)
        confirmation=service.Confirmation(**selection.model_dump(),expected_product_id=view['before']['product_id'],
            expected_product_name=view['before']['product_name'],expected_updated_at=view['before']['updated_at'],target_name=view['target']['name'])
        async with factory() as db:
            result=await service.apply(db,uid,confirmation)
            assert result['mapping']['product_id']==str(pid)
        async with factory() as db:
            page=await gtin_control.findings(rid,'mapping','','',False,1,50,NS(id=uid),db)
            assert page['items'][0]['current_mapping']['product_name']=='Peach 25 g'
            assert page['items'][0]['saved_product_name']=='Peach 200 g'
            scan=await db.get(Scan,sid)
            assert scan.moysklad_product_id=='old' and scan.product_name=='Peach 200 g'
        async with factory() as db:
            with pytest.raises(HTTPException) as err: await service.apply(db,uid,confirmation)
            assert err.value.status_code==409  # Old preview cannot overwrite a newer mapping.
        async with factory() as db:
            fresh=await service.preview(db,uid,selection)
        current_confirmation=service.Confirmation(**selection.model_dump(),expected_product_id=fresh['before']['product_id'],
            expected_product_name=fresh['before']['product_name'],expected_updated_at=fresh['before']['updated_at'],target_name=fresh['target']['name'])
        async def concurrent_edit(product_id):
            async with factory() as db:
                await db.execute(update(GtinProductMap).where(GtinProductMap.user_id==uid,GtinProductMap.gtin==GTIN)
                    .values(product_name='Manual concurrent change',updated_at=func.now()))
                await db.commit()
            return product
        get.side_effect=concurrent_edit
        async with factory() as db:
            with pytest.raises(HTTPException) as err: await service.apply(db,uid,current_confirmation)
            assert err.value.status_code==409
        async with factory() as db:
            own=(await db.execute(select(GtinProductMap).where(GtinProductMap.user_id==uid,GtinProductMap.gtin==GTIN))).scalar_one()
            foreign=(await db.execute(select(GtinProductMap).where(GtinProductMap.user_id==other,GtinProductMap.gtin==GTIN))).scalar_one()
            assert own.product_name=='Manual concurrent change'
            assert foreign.product_id=='other-card' and foreign.product_name=='Other'
    finally:
        async with factory() as db:
            await db.execute(delete(Scan).where(Scan.id==sid)); await db.execute(delete(Document).where(Document.id==did))
            await db.execute(delete(GtinAuditRun).where(GtinAuditRun.id==rid))
            await db.execute(delete(GtinProductMap).where(GtinProductMap.user_id.in_([uid,other])))
            await db.execute(delete(Integration).where(Integration.user_id==uid))
            await db.execute(delete(User).where(User.id.in_([uid,other]))); await db.commit()
        await engine.dispose()
