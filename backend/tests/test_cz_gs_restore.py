import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import chestnyznak, cz_logger, cz_pg_cache
from app.services.chestnyznak import ChestnyZnakService, restore_tobacco_gs_for_check


RAW = '010461011399135321OtKd+c_93ZSBQ'
FIXED = '010461011399135321OtKd+c_\x1d93ZSBQ'


@pytest.mark.parametrize('pg', ['tobacco', 'otp', 'ncp'])
def test_exact_tobacco_format_restores_only_missing_separator(pg):
    assert restore_tobacco_gs_for_check(RAW, pg) == FIXED
    assert restore_tobacco_gs_for_check(FIXED, pg) == FIXED


@pytest.mark.parametrize('code,pg', [
    (RAW, 'milk'), (RAW, 'unknown'), (RAW[:-1], 'tobacco'),
    (RAW + 'x', 'tobacco'), (RAW[:25] + '91ZSBQ', 'tobacco'),
    ('04670163000214AVXDfrbAAAAR1KU', 'tobacco'),
    (RAW[:25], 'tobacco'), ('00' + '1' * 18, 'tobacco'),
])
def test_ambiguous_other_group_and_compact_codes_are_unchanged(code, pg):
    assert restore_tobacco_gs_for_check(code, pg) == code


@pytest.fixture
def mock_cz(monkeypatch):
    real_client = httpx.AsyncClient
    log = AsyncMock()
    monkeypatch.setattr(cz_logger, 'log_cz_request', log)
    monkeypatch.setattr(cz_pg_cache, 'set_cached_pg', AsyncMock())
    def install(handler):
        monkeypatch.setattr(chestnyznak.httpx, 'AsyncClient',
                            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
        return log
    return install


async def test_batch_keeps_originals_and_requires_cz_confirmation(mock_cz):
    sent = []
    def respond(request):
        if request.url.path.endswith('/info'):
            codes = json.loads(request.content)
            return httpx.Response(200, json=[{'cisInfo': {'requestedCis': c},
                'errorCode': 4, 'errorMessage': 'КМ/КИ не найден'} for c in codes])
        payload = json.loads(request.content)
        sent.append(payload['codes'])
        return httpx.Response(200, json={'result': payload['codes'] == [FIXED]})
    log = mock_cz(respond)
    results = await ChestnyZnakService('fake', mock=False, product_groups=['tobacco']).check_codes([RAW, FIXED])
    assert sent == [[FIXED]]
    assert [r.code for r in results] == [RAW, FIXED]
    assert all(r.found and not r.uncertain for r in results)
    last_log = log.await_args_list[-1].kwargs
    assert last_log['request_body']['gs_restored'] == [{'original': RAW, 'sent': FIXED}]


async def test_reconstructed_but_rejected_code_stays_invalid(mock_cz):
    mock_cz(lambda request: httpx.Response(200, json={'result': False, 'codes': [FIXED]}))
    valid, uncertain = await ChestnyZnakService('fake', mock=False, product_groups=['tobacco'])._cises_check_valid([RAW, FIXED])
    assert valid == set() and not uncertain


async def test_partial_response_maps_corrected_codes_back_to_all_originals(mock_cz):
    other = RAW[:18] + 'abcdefg' + RAW[25:]
    mock_cz(lambda request: httpx.Response(200, json={'result': False, 'codes': [FIXED]}))
    valid, uncertain = await ChestnyZnakService('fake', mock=False, product_groups=['tobacco'])._cises_check_valid([RAW, FIXED, other])
    assert valid == {other} and not uncertain


@pytest.mark.parametrize('status,body', [(503, {}), (200, {'result': False, 'codes': ['unexpected']})])
async def test_failure_or_unmatched_response_cannot_validate_restored_code(status, body, mock_cz):
    mock_cz(lambda request: httpx.Response(status, json=body))
    valid, uncertain = await ChestnyZnakService('fake', mock=False, product_groups=['tobacco'])._cises_check_valid([RAW])
    assert not valid and uncertain


async def test_restoration_is_applied_per_product_group(mock_cz):
    sent = []
    def respond(request):
        codes = json.loads(request.content)['codes']
        sent.append((request.url.params['pg'], codes))
        return httpx.Response(200, json={'result': codes == [FIXED]})
    mock_cz(respond)
    valid, uncertain = await ChestnyZnakService('fake', mock=False, product_groups=['milk', 'otp'])._cises_check_valid([RAW])
    assert sent == [('milk', [RAW]), ('otp', [FIXED])]
    assert valid == {RAW} and not uncertain
