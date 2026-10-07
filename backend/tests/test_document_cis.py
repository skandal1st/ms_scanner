import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import document_cis
from app.services.chestnyznak import ChestnyZnakService


RAW = '04680621050477eq%Z_i?AAAA'
OTHER = '04680621050477MQt?*PwAAAA'


def answer(raw=RAW, **overrides):
    return {'cisInfo': {'requestedCis': raw, 'cis': raw[:21],
                        'status': 'INTRODUCED', 'generalPackageType': 'UNIT', **overrides}}


@pytest.fixture
def install(monkeypatch):
    real = httpx.AsyncClient
    log = AsyncMock()
    monkeypatch.setattr(document_cis, 'log_cz_request', log)
    def mock(handler):
        monkeypatch.setattr(document_cis.httpx, 'AsyncClient',
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        return log
    return mock


def cz(groups=None):
    return ChestnyZnakService('fake', mock=False, product_groups=groups or ['otp'])


async def test_confirmed_aliases_keep_source_and_order_and_log_request(install):
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=[answer(OTHER), answer()])
    log = install(respond)
    rows = [{'code': RAW, 'product_id': 'p1'}, {'code': OTHER}, {'code': RAW}]
    original = copy.deepcopy(rows)
    result = await document_cis.confirm_document_cis(rows, cz())
    assert rows == original
    assert [row['code'] for row in result] == [RAW[:21], OTHER[:21], RAW[:21]]
    assert result[0]['product_id'] == 'p1'
    assert sent == [[RAW, OTHER]]
    assert log.await_args.args[2] == [RAW, OTHER]


@pytest.mark.parametrize('change', [
    {'cis': RAW}, {'cis': OTHER[:21]}, {'requestedCis': OTHER},
    {'status': 'WITHDRAWN'}, {'markWithdraw': True},
    {'generalPackageType': 'GROUP'}, {'child': ['child']}, {'errorCode': 4},
])
async def test_unconfirmed_or_different_identity_is_never_trimmed(install, change):
    install(lambda request: httpx.Response(200, json=[answer(**change)]))
    rows = [{'code': RAW}]
    with pytest.raises(ValueError, match='не подтвердил'):
        await document_cis.confirm_document_cis(rows, cz())
    assert rows == [{'code': RAW}]


@pytest.mark.parametrize('body', [[], {}, [None], [{'cisInfo': None}],
                                  [answer(), answer()], [{'errorCode': 4, **answer()}]])
async def test_missing_malformed_duplicate_and_error_responses_block(install, body):
    install(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match='не подтвердил'):
        await document_cis.confirm_document_cis([{'code': RAW}], cz())


async def test_partial_response_falls_back_for_only_missing_codes(install):
    sent = []
    def respond(request):
        codes = json.loads(request.content)
        sent.append((request.url.params['pg'], codes))
        return httpx.Response(200, json=[answer()] if len(sent) == 1 else [answer(OTHER)])
    install(respond)
    result = await document_cis.confirm_document_cis([{'code': RAW}, {'code': OTHER}], cz(['milk', 'tobacco', 'otp']))
    assert sent == [('tobacco', [RAW, OTHER]), ('otp', [OTHER])]
    assert result == [{'code': RAW[:21]}, {'code': OTHER[:21]}]


@pytest.mark.parametrize('failure', ['timeout', '503', '401'])
async def test_transport_and_auth_failures_block(install, failure):
    def respond(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(int(failure), json={})
    install(respond)
    with pytest.raises(ValueError, match='не подтвердил'):
        await document_cis.confirm_document_cis([{'code': RAW}], cz())


async def test_partial_batch_failure_leaves_all_source_rows_unchanged(install):
    install(lambda request: httpx.Response(200, json=[answer()]))
    rows = [{'code': RAW}, {'code': OTHER}]
    with pytest.raises(ValueError):
        await document_cis.confirm_document_cis(rows, cz())
    assert rows == [{'code': RAW}, {'code': OTHER}]


async def test_large_batch_is_bounded(install):
    sizes = []
    def respond(request):
        codes = json.loads(request.content)
        sizes.append(len(codes))
        return httpx.Response(200, json=[answer(code) for code in codes])
    install(respond)
    rows = [{'code': f'04680621050477{i:07d}AAAA'} for i in range(205)]
    assert len(await document_cis.confirm_document_cis(rows, cz())) == 205
    assert sizes == [100, 100, 5]


@pytest.mark.parametrize('service', [SimpleNamespace(mock=False, token=None), SimpleNamespace(mock=True, token='fake')])
async def test_no_real_token_never_synthesizes_cis(service):
    with pytest.raises(ValueError, match='УКЭП'):
        await document_cis.confirm_document_cis([{'code': RAW}], service)


async def test_unrelated_codes_and_whole_boxes_need_no_token():
    rows = [{'code': RAW, 'is_box': True}, {'code': RAW, 'is_barcode': True},
            {'code': RAW[:21]}, {'code': '01' + RAW[:14] + '21' + RAW[14:]},
            {'code': RAW + 'x'}, {'code': RAW[:-1]}]
    assert await document_cis.confirm_document_cis(rows, None) is rows
