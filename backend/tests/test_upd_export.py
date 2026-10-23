"""Outgoing UPD: real FNS XSD, financial totals and aggregate identity."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from lxml import etree as ET

from app.api import documents
from app.db.models import DocumentKind, DocumentStatus
from app.schemas.upd_export import UpdExportOptions
from app.services.moysklad import MoySkladService
from app.services.upd_export import generate_upd, UpdExportError
from app.services.upd_parser import parse_upd_503


@pytest.fixture
def options():
    return UpdExportOptions.model_validate({
        "number": "42", "document_date": "2026-10-23", "sender_id": "2BE-SENDER", "receiver_id": "2BE-BUYER",
        "seller": {"name": "ООО Продавец", "inn": "7701234567", "kpp": "770101001", "address": "Москва, дом 1"},
        "buyer": {"name": "ИП Покупатель", "inn": "781437759369", "last_name": "Петров", "first_name": "Иван", "address": "Тула, дом 2"},
        "signer_last_name": "Иванов", "signer_first_name": "Пётр", "signer_position": "Директор",
        "signer_is_principal": True, "no_excise": True, "cargo_parties_match": True,
    })


@pytest.fixture
def source():
    return {"name": "42", "moment": "2026-10-23 10:20:00.000", "sum": 21960, "vatSum": 3960,
            "vatEnabled": True, "vatIncluded": True, "rate": {"currency": {"code": "643"}},
            "positions": {"rows": [{"id": "p", "quantity": 2, "price": 12200, "discount": 10, "vat": 22.0,
                "vatEnabled": True, "assortment": {"name": 'Товар <A> & "Б"', "article": "ABC",
                    "trackingType": "TOBACCO", "uom": {"code": "796", "name": "шт"}},
                "trackingCodes": [{"cis": "010460000000000021ABC1234", "type": "trackingcode"},
                                  {"cis": "010460000000000021ABC1235", "type": "trackingcode"}]}]}}


def root(source, options):
    return ET.fromstring(generate_upd(source, options)[1])


def test_valid_2026_xml_roundtrip(source, options):
    filename, xml = generate_upd(source, options, now=datetime(2026, 10, 23, 10, 30), file_uuid=uuid4())
    r = ET.fromstring(xml)
    assert filename == r.get("ИдФайл") + ".xml"
    assert filename.startswith("ON_NSCHFDOPPR_2BE-BUYER_2BE-SENDER_20261023_")
    assert filename.endswith('_0_1_0_1_0_00.xml')
    assert b"windows-1251" in xml
    row = r.find(".//СведТов")
    assert row.get("НалСт") == "22%"
    assert row.get("ЦенаТов") == "90.00000000000"
    assert row.get("СтТовУчНал") == "219.60"
    assert row.get("НаимТов") == 'Товар <A> & "Б"'
    assert r.find(".//СвИП").get("ИННФЛ") == "781437759369"
    parsed = parse_upd_503(xml)
    assert parsed.invoice_number == "42"
    assert parsed.total_amount == 219.6
    assert parsed.total_vat == 39.6
    assert len(parsed.positions[0].codes) == 2


def test_preserves_transport_and_consumer_packages_without_children(source, options):
    source["positions"]["rows"][0]["trackingCodes"] = [
        {"type": "transportpack", "cis": "00123456789012345678", "trackingCodes": [{"cis": "nested"}]},
        {"type": "consumerpack", "cis": "010460000000000021PACK"}]
    r = root(source, options)
    assert r.find(".//НомСредИдентТов").get("ИдентТрансУпак") == "123456789012345678"
    assert r.find(".//НомУпак").text.endswith("PACK")
    assert not r.findall(".//КИЗ")


@pytest.mark.parametrize("included,enabled,rate,price,total,vat", [
    (False, True, 22, 9000, 21960, 3960),
    (True, False, 0, 10000, 20000, 0),
    (True, True, 0, 10000, 20000, 0),
    (True, True, 5, 10500, 21000, 1000),
    (True, True, 7, 10700, 21400, 1400),
    (True, True, 10, 11000, 22000, 2000),
    (True, True, 20, 12000, 24000, 4000),
])
def test_tax_modes(source, options, included, enabled, rate, price, total, vat):
    source.update(vatIncluded=included, sum=total, vatSum=vat)
    source['positions']['rows'][0].update(price=price, vat=rate, vatEnabled=enabled, discount=0)
    r = root(source, options)
    assert (r.find('.//БезНДС') is not None) is (not enabled)


def test_fractional_quantity_precise_net_price(source, options):
    source.update(sum=101, vatSum=18)
    source['positions']['rows'][0].update(price=101, quantity=1, discount=0)
    r = root(source, options)
    assert r.find('.//СведТов').get('ЦенаТов') == '0.82786885246'


def test_negative_discount_is_markup(source, options):
    source.update(sum=26840, vatSum=4840)
    source['positions']['rows'][0]['discount'] = -10
    assert root(source, options).find('.//СведТов').get('СтТовУчНал') == '268.40'


@pytest.mark.parametrize('mutation,match', [
    (lambda s: s.update(sum=21961), 'не совпал'),
    (lambda s: s.update(vatSum=3959), 'не совпал'),
    (lambda s: s['rate']['currency'].update(code='840'), 'рублях'),
    (lambda s: s['positions']['rows'][0].update(quantity=0), 'Нулевое'),
    (lambda s: s['positions']['rows'][0].update(trackingCodes=[]), 'нет записанных'),
    (lambda s: s['positions']['rows'][0]['trackingCodes'].append(s['positions']['rows'][0]['trackingCodes'][0]), 'несколько раз'),
    (lambda s: s['positions']['rows'][0]['trackingCodes'][0].update(cis='abc\x1dDEF'), 'управляющие'),
    (lambda s: s['positions']['rows'][0]['assortment'].update(uom={}), 'ОКЕИ'),
    (lambda s: s.update(organization={'inn':'9999999999'}), 'ИНН'),
    (lambda s: s.update(invoice={'name':'999', 'moment':'2026-10-23'}), 'счётом-фактурой'),
    (lambda s: s['positions']['rows'][0].update(declaration=[{'rnpt':'123'}]), 'ГТД/РНПТ'),
    (lambda s: s.update(consignee={'meta':{'href':'another-buyer'}}), 'грузополучатель'),
])
def test_bad_source_never_produces_xml(source, options, mutation, match):
    mutation(source)
    with pytest.raises(UpdExportError, match=match): generate_upd(source, options)


def test_schema_is_applied(source, options):
    source['positions']['rows'][0]['assortment']['uom']['code'] = 'NOT-OKEI'
    with pytest.raises(UpdExportError, match='схемы ФНС'): generate_upd(source, options)


def test_dop_and_mixed_vat(source, options):
    options.function = 'ДОП'
    second = deepcopy(source['positions']['rows'][0])
    second.update(vatEnabled=False, price=10000, discount=0, trackingCodes=[])
    second['assortment']['trackingType'] = 'NOT_TRACKED'
    source['positions']['rows'].append(second)
    source['sum'] += 20000
    r = root(source, options)
    assert r.find('Документ').get('Функция') == 'ДОП'
    assert r.find('.//СумНалВсего/СумНал').text == '39.60'


async def test_source_loads_all_pages_and_never_writes():
    ms = MoySkladService('fake')
    requests = []
    async def request(client, method, url, **kwargs):
        requests.append((method, url, kwargs))
        assert method == 'GET'
        params = kwargs.get('params') or {}
        if url.endswith('/trackingCodes'):
            payload = {'rows': [{'type':'trackingcode','cis':str(i)} for i in range(100)] if params['offset'] == 0 else [{'cis':'last','type':'trackingcode'}]}
        elif url.endswith('/positions'):
            payload = {'rows': [{'id':str(i)} for i in range(100)] if params['offset'] == 0 else [{'id':'last'}]}
        elif '/factureout/' in url:
            return httpx.Response(403, request=httpx.Request(method,url))
        else:
            payload = {'updated':'same', 'factureOut':{'meta':{'href':ms.base_url+'/entity/factureout/invoice'}}}
        return httpx.Response(200,json=payload,request=httpx.Request(method,url))
    ms._request_with_retry = request
    result = await ms.get_upd_source('demand')
    assert len(result['positions']['rows']) == 101
    assert len(result['positions']['rows'][-1]['trackingCodes']) == 101
    assert result['invoice_unavailable']


async def test_changed_document_is_not_exported():
    ms = MoySkladService('fake')
    responses = iter([{'updated':'first'}, {'rows':[]}, {'updated':'changed'}])
    async def request(client, method, url, **kwargs):
        return httpx.Response(200,json=next(responses),request=httpx.Request(method,url))
    ms._request_with_retry = request
    with pytest.raises(UpdExportError, match='изменилась'): await ms.get_upd_source('demand')


@pytest.mark.parametrize('doc,status', [(None,404), (NS(kind=DocumentKind.supply,moysklad_id='x'),400),
    (NS(kind=DocumentKind.demand,moysklad_id='x',status=DocumentStatus.processing),409),
    (NS(kind=DocumentKind.demand,moysklad_id='x',status=DocumentStatus.draft),409)])
async def test_api_requires_own_completed_shipment(doc, status, monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(documents,'_get_ms_service',service)
    db = NS(scalar=AsyncMock(return_value=doc))
    with pytest.raises(HTTPException) as exc:
        await documents._upd_source(uuid4(),NS(id=uuid4()),db)
    assert exc.value.status_code == status
    service.assert_not_awaited()


async def test_api_download_has_original_filename_and_no_store(source, options, monkeypatch):
    loader = AsyncMock(return_value=source)
    monkeypatch.setattr(documents, '_upd_source', loader)
    result = await documents.export_document_upd(uuid4(), options, NS(), NS())
    r = ET.fromstring(result.body)
    assert r.get('ИдФайл') + '.xml' in result.headers['content-disposition']
    assert result.headers['cache-control'] == 'no-store'
    assert result.media_type == 'application/xml'


async def test_context_does_not_guess_restricted_invoice_number(source, monkeypatch):
    source.update(invoice_unavailable=True, factureOut={'meta':{'href':'invoice'}})
    source['positions']['meta'] = {'size': 26}
    loader = AsyncMock(return_value=source)
    monkeypatch.setattr(documents, '_upd_source', loader)
    result = await documents.upd_export_context(uuid4(), NS(), NS())
    assert result['number'] == ''
    assert result['function'] == 'СЧФДОП'
    assert result['positions'] == 26
    assert len(result['warnings']) == 2
    assert loader.await_args.kwargs == {'include_positions': False}
