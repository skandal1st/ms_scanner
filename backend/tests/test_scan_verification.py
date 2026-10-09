from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from app.services.chestnyznak import CisCheck
from app.services.scan_verification import check_scans, evidence, acceptable


RAW = '010460123456789021ABC'


def scan(code=RAW, **kw):
    return NS(code=code, is_box=False, child_codes=None, **kw)


def cz():
    return NS(token='test', mock=False, check_codes=AsyncMock(), get_code_info=AsyncMock(), unpack_box=AsyncMock())


async def test_without_token_never_reports_cz_success():
    result = (await check_scans(None, [scan()]))[RAW]
    assert result.uncertain and not acceptable(result)
    assert result.verification['checked_at'] is None
    assert result.verification['source'] == 'format'
    assert 'вход' in result.verification['owner_reason']


@pytest.mark.parametrize('owner,reference,result', [('123','123','match'), ('123','456','mismatch'), (None,'123','unknown'), ('123',None,'unknown')])
def test_owner_requires_both_inns(owner, reference, result):
    check = CisCheck(code=RAW, found=True, owner_inn=owner, status='INTRODUCED')
    assert evidence(check, reference)['owner_result'] == result


async def test_fallback_does_not_claim_owner_was_checked():
    client = cz()
    client.check_codes.return_value = [CisCheck(code=RAW, found=True, status='INTRODUCED', verification_source='cz_check')]
    result = (await check_scans(client, [scan()], '123'))[RAW]
    assert acceptable(result)
    assert result.verification['owner_result'] == 'unknown'
    assert 'без сведений' in result.verification['owner_reason']


async def test_withdrawn_flag_cannot_be_green_even_with_introduced_status():
    client = cz()
    client.check_codes.return_value = [CisCheck(code=RAW, found=True, status='INTRODUCED', mark_withdraw=True)]
    result = (await check_scans(client, [scan()]))[RAW]
    assert not acceptable(result)
    assert result.status == 'WITHDRAWN'


@pytest.mark.parametrize('uncertain', [False, True])
async def test_every_aggregate_leaf_is_checked_and_bad_leaf_is_reported(uncertain):
    client = cz()
    parent = CisCheck(code=RAW, found=True, status='INTRODUCED', package_type='GROUP')
    client.get_code_info.return_value = NS(children=['good', 'bad'])
    client.check_codes.side_effect = [[parent], [CisCheck(code='good', found=True, status='INTRODUCED'),
        CisCheck(code='bad', found=not uncertain, uncertain=uncertain, status='WITHDRAWN')]]
    result = (await check_scans(client, [scan()]))[RAW]
    assert not acceptable(result)
    assert result.uncertain == uncertain
    assert result.verified_children == ['good', 'bad']
    assert result.verification['child_issues'][0]['code'] == 'bad'
    assert client.check_codes.await_args_list[1].args[0] == ['good', 'bad']


async def test_package_timeout_is_not_a_success():
    client = cz()
    client.check_codes.return_value = [CisCheck(code=RAW, found=True, status='INTRODUCED', package_type='BOX')]
    client.get_code_info.side_effect = TimeoutError()
    result = (await check_scans(client, [scan()]))[RAW]
    assert result.uncertain and result.verification['checked_at'] is None


async def test_sscc_checks_contents_without_ordinary_cis_lookup():
    client = cz()
    client.unpack_box.return_value = ['child']
    client.check_codes.return_value = [CisCheck(code='child', found=True, status='INTRODUCED', owner_inn='123')]
    result = (await check_scans(client, [NS(code='SSCC', is_box=True, child_codes=None)], '123'))['SSCC']
    assert acceptable(result)
    assert result.verification['source'] == 'cz_contents'
    assert result.verification['owner_result'] == 'match'
    assert result.verification['children_checked'] == 1
    client.get_code_info.assert_not_called()


async def test_large_batch_preserves_owner_information_for_all_codes(monkeypatch):
    import httpx
    from app.services import chestnyznak, cz_logger, cz_pg_cache
    real_client = httpx.AsyncClient
    sizes = []
    def response(request):
        import json
        codes = json.loads(request.content)
        sizes.append(len(codes))
        return httpx.Response(200, json=[{'cisInfo': {'requestedCis': code, 'status': 'INTRODUCED',
                                                     'ownerInn': '123'}} for code in codes])
    monkeypatch.setattr(chestnyznak.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(response), **kw))
    monkeypatch.setattr(cz_logger, 'log_cz_request', AsyncMock())
    monkeypatch.setattr(cz_pg_cache, 'set_cached_pg', AsyncMock())
    codes = [RAW + str(index) for index in range(205)]
    results = await chestnyznak.ChestnyZnakService(token='test', mock=False, product_groups=['tobacco']).check_codes(codes)
    assert sizes == [100, 100, 5]
    assert [check.code for check in results] == codes
    assert all(check.owner_inn == '123' and check.verification_source == 'cz_info' for check in results)


def test_ms_error_matches_exact_code_and_does_not_guess_from_gtin():
    from app.services.scan_verification import record_ms_errors
    a = NS(id='a', code=RAW, child_codes=None, verification=None)
    b = NS(id='b', code=RAW.lower(), child_codes=None, verification=None)
    assert record_ms_errors([a,b], f'Марка "{RAW}" не принадлежит товару') == [a]
    assert a.verification['ms_error'] and 'МойСклад' in a.error_message
    assert b.verification is None
    assert record_ms_errors([b], 'Товар 04601234567890 не найден') == []
