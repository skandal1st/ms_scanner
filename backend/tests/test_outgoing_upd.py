"""Draft-only upload contracts, exact XML, crash recovery and no write replay."""
import base64
import hashlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.db.models import OutgoingUpd
from app.services import outgoing_upd as service
from app.services.saby import SabyClient, SabyError, SabyRpcError, SabyDocumentNotFound


@pytest.fixture
def row():
    party = dict(name='ИП Тест', inn='781437759369', kpp='', address='Тула', last_name='Тестов', first_name='Иван', middle_name='')
    options = dict(function='СЧФДОП', number='42', document_date='2026-10-23',
                   sender_id='2BE-SELLER', receiver_id='2BE-BUYER', seller=party, buyer=party,
                   signer_last_name='Тестов', signer_first_name='Иван', signer_middle_name='',
                   signer_position='ИП', signer_is_principal=True, no_excise=True, cargo_parties_match=True)
    xml = '<?xml version="1.0" encoding="windows-1251"?><Файл />'
    return OutgoingUpd(id=uuid4(), document_id=uuid4(), user_id=uuid4(), status='prepared',
                       connection_key='fake', options=options, filename='ON_NSCHFDOPPR_TEST.xml', xml_text=xml,
                       xml_sha256=hashlib.sha256(xml.encode('cp1251')).hexdigest())


def remote(row, **extra):
    return {'Идентификатор':str(row.id), 'Состояние':{'Код':'0','Название':'Документ редактируется'},
            'СсылкаДляНашаОрганизация':'https://online.saby.ru/opendoc.html?id='+str(row.id),
            'Вложение':[{'Идентификатор':service.attachment_id(row), 'Файл':{
                'Имя':row.filename, 'ДвоичныеДанные':base64.b64encode(row.xml_text.encode('cp1251')).decode()}}], **extra}


def db_for(row):
    return NS(scalar=AsyncMock(return_value=row), commit=AsyncMock(), rollback=AsyncMock())


def test_payload_has_only_unsigned_xml_and_explicit_parties(row):
    payload = service.payload(row)['Документ']
    assert payload['Идентификатор'] == str(row.id)
    assert payload['Тип'] == 'ДокОтгрИсх'
    assert payload['Контрагент']['Идентификатор'] == '2BE-BUYER'
    assert payload['НашаОрганизация']['СвФЛ']['ИНН'] == '781437759369'
    assert payload['Дата'] == '23.10.2026'
    assert 'Подпись' not in str(payload)
    assert base64.b64decode(payload['Вложение'][0]['Файл']['ДвоичныеДанные']) == row.xml_text.encode('cp1251')


async def test_success_and_double_click_only_one_write(row):
    db = db_for(row)
    client = NS(call=AsyncMock(side_effect=[SabyDocumentNotFound(), {}, remote(row)]))
    assert (await service.deliver(db,row.id,client,('Auth','fake'))).status == 'created'
    assert (await service.deliver(db,row.id,client,('Auth','fake'))).status == 'created'
    assert [c.args[0] for c in client.call.await_args_list] == ['СБИС.ПрочитатьДокумент','СБИС.ЗаписатьДокумент','СБИС.ПрочитатьДокумент']
    # The upload intent was durable before the write, even if its response is lost.
    assert db.commit.await_count >= 3


@pytest.mark.parametrize('accepted', [True, False])
async def test_timeout_is_reconciled_without_replaying_write(row, accepted):
    db = db_for(row)
    calls=[]
    exists=False
    async def call(method,params,auth):
        nonlocal exists
        calls.append(method)
        if method == 'СБИС.ЗаписатьДокумент':
            assert row.status == 'uploading'
            exists=accepted
            raise httpx.ReadTimeout('lost response')
        if not exists: raise SabyDocumentNotFound()
        return remote(row)
    client=NS(call=call)
    assert (await service.deliver(db,row.id,client,('Auth','fake'))).status == 'uncertain'
    result=await service.deliver(db,row.id,client,('Auth','fake'))
    assert result.status == ('created' if accepted else 'uncertain')
    assert calls.count('СБИС.ЗаписатьДокумент') == 1


async def test_invalid_json_response_is_not_treated_as_rejected_write(row):
    client=NS(call=AsyncMock(side_effect=[SabyDocumentNotFound(), SabyError('не JSON')]))
    assert (await service.deliver(db_for(row),row.id,client,('Auth','fake'))).status == 'uncertain'


async def test_rejected_write_can_retry_only_after_definitive_read(row):
    client=NS(call=AsyncMock(side_effect=[SabyDocumentNotFound(), SabyRpcError('Нет прав'), SabyDocumentNotFound(), {}, remote(row)]))
    db=db_for(row)
    assert (await service.deliver(db,row.id,client,('Auth','fake'))).status == 'failed'
    assert (await service.deliver(db,row.id,client,('Auth','fake'))).status == 'created'
    assert [c.args[0] for c in client.call.await_args_list].count('СБИС.ЗаписатьДокумент') == 2


@pytest.mark.parametrize('mutation', ['sent','signed','changed','partial','extra'])
async def test_existing_changed_document_is_never_overwritten(row, mutation):
    r=remote(row)
    row.status='uncertain'
    if mutation=='sent':r['Состояние']['Код']='3'
    if mutation=='signed':r['Вложение'][0]['Подпись']=[{'Файл':{}}]
    if mutation=='changed':r['Вложение'][0]['Файл']['ДвоичныеДанные']=base64.b64encode(b'different').decode()
    if mutation=='partial':r['ЧастичныеДанные']='Да'
    if mutation=='extra':r['Вложение'].append({'Идентификатор':'other'})
    client=NS(call=AsyncMock(return_value=r))
    assert (await service.deliver(db_for(row),row.id,client,('Auth','fake'))).status == 'uncertain'
    assert [c.args[0] for c in client.call.await_args_list] == ['СБИС.ПрочитатьДокумент']


async def test_empty_read_response_does_not_authorize_creation(row):
    client=NS(call=AsyncMock(return_value=None))
    result=await service.deliver(db_for(row),row.id,client,('Auth','fake'))
    assert result.status=='prepared'
    assert len(client.call.await_args_list)==1


async def test_downloaded_attachment_hash_is_checked(row):
    r=remote(row)
    r['Вложение'][0]['Файл']={'Имя':row.filename,'Ссылка':'https://online.sbis.ru/files/test.xml'}
    c=NS(download=AsyncMock(return_value=row.xml_text.encode('cp1251')))
    await service.verify(c,('Auth','fake'),row,r)
    c.download.assert_awaited_once()


@pytest.mark.parametrize('url', ['http://online.sbis.ru/x','https://evil.test/x','https://sbis.ru.evil.test/x','javascript:alert(1)'])
def test_external_links_are_restricted_to_saby(url):
    assert service.trusted_saby_url(url) is None


async def test_not_found_requires_explicit_saby_error(monkeypatch):
    original=httpx.AsyncClient
    def respond(request):
        return httpx.Response(500,json={'error':{'code':-32000,'message':'Не найден документ с идентификатором 123',
                                               'details':'https://saby.ru/help/integration/api/error_solution/not_doc'}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    c=SabyClient()
    with pytest.raises(SabyDocumentNotFound): await c.call('СБИС.ПрочитатьДокумент',{},('Auth','fake'))
    with pytest.raises(SabyRpcError): await c.call('СБИС.ЗаписатьДокумент',{},('Auth','fake'))
