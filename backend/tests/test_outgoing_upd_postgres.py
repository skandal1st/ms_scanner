"""Concurrent POSTs across real transactions cannot create two Saby documents."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api import documents, mark_control
from app.core.config import settings
from app.db.models import Document, DocumentKind, DocumentStatus, User, Integration, OutgoingUpd
from app.schemas.upd_export import UpdExportOptions
from app.services import outgoing_upd as service
from app.services.saby import SabyDocumentNotFound

pytestmark = pytest.mark.skipif(os.getenv('AUDIT_POSTGRES') != '1', reason='isolated PostgreSQL required')


async def test_two_simultaneous_submissions_and_lost_response(monkeypatch):
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    uid,did=uuid4(),uuid4()
    party=dict(name='ИП Тест',inn='781437759369',address='Тула',last_name='Тестов',first_name='Иван')
    opts=UpdExportOptions.model_validate(dict(number='QA-42',document_date='2026-10-23',sender_id='2BE-SELLER',receiver_id='2BE-BUYER',
        seller=party,buyer=party,signer_last_name='Тестов',signer_first_name='Иван',signer_position='ИП',
        signer_is_principal=True,no_excise=True,cargo_parties_match=True))
    source={'name':'QA-42','moment':'2026-10-23 10:00:00','sum':12200,'vatSum':2200,'vatIncluded':True,'vatEnabled':True,
        'rate':{'currency':{'code':'643'}},'positions':{'rows':[{'quantity':1,'price':12200,'discount':0,'vat':22,'vatEnabled':True,
        'assortment':{'name':'QA товар','uom':{'code':'796','name':'шт'},'trackingType':'NOT_TRACKED'}}]}}
    seen=[]
    remote=None
    async def call(method,params,auth):
        nonlocal remote
        seen.append(method)
        if method=='СБИС.ЗаписатьДокумент':
            # Leave time for the second HTTP request to observe uploading intent.
            await asyncio.sleep(.15)
            remote={**params['Документ'],'Состояние':{'Код':'0','Название':'Документ редактируется'}}
            return remote
        if remote is None:raise SabyDocumentNotFound()
        return remote
    client=NS(call=call)
    monkeypatch.setattr(documents,'_upd_source',AsyncMock(return_value=source))
    monkeypatch.setattr(mark_control,'_client_from_integration',lambda _:client)
    monkeypatch.setattr(mark_control,'_get_auth',AsyncMock(return_value=('Auth','fake')))
    try:
        async with factory() as db:
            db.add(User(id=uid,email=f'{uid}@test.invalid',password_hash='qa-unused'))
            await db.flush()
            db.add_all([Integration(user_id=uid,saby_login='qa',saby_account='qa'),
                Document(id=did,user_id=uid,name='QA',kind=DocumentKind.demand,status=DocumentStatus.accepted,moysklad_id=str(uuid4()))])
            await db.commit()
        async def submit():
            async with factory() as db:
                return await documents.create_outgoing_upd_draft(did,opts,NS(id=uid),db)
        results=await asyncio.gather(submit(),submit())
        assert seen.count('СБИС.ЗаписатьДокумент')==1
        final=await submit()
        assert final['status']=='created'
        assert len({r['id'] for r in results+[final]})==1
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(OutgoingUpd).where(OutgoingUpd.document_id==did))==1
            row=await db.scalar(select(OutgoingUpd).where(OutgoingUpd.document_id==did))
            assert row.filename.endswith('_0_0_0_0_0_00.xml')
            # Another user cannot read or download the persisted XML.
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as error:
                await documents.download_outgoing_upd_xml(did,NS(id=uuid4()),db)
            assert error.value.status_code==404
    finally:
        from sqlalchemy import delete
        async with factory() as db:
            await db.execute(delete(OutgoingUpd).where(OutgoingUpd.document_id==did))
            await db.execute(delete(Document).where(Document.id==did))
            await db.execute(delete(Integration).where(Integration.user_id==uid))
            await db.execute(delete(User).where(User.id==uid))
            await db.commit()
        await engine.dispose()
