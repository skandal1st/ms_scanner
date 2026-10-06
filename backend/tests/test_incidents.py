"""Document diagnostics: secret redaction, correlation and external failure evidence."""
import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core import diagnostics, monitoring
from app.services import cz_logger, incidents
from app.services.chestnyznak import ChestnyZnakService
from app.services.moysklad import MoySkladService


def test_nested_secrets_and_url_credentials_are_redacted():
    raw = {'access_token': 'secret-a', 'inner': [{'signed_data': 'secret-b', 'password': 'secret-c'}],
           'error': 'GET https://x.test/?apikey=secret-d&token=secret-e Bearer secret-f'}
    value = json.dumps(diagnostics.sanitize(raw))
    for secret in ('secret-a', 'secret-b', 'secret-c', 'secret-d', 'secret-e', 'secret-f'):
        assert secret not in value
    assert 'user:pass' not in diagnostics.safe_url('https://user:pass@x.test/?api_key=secret-g&pg=otp')
    assert 'secret-g' not in diagnostics.safe_url('https://x.test/?api_key=secret-g&pg=otp')
    assert cz_logger._redact('https://x/auth/cert/', {'data': 'signature'})['data'] == '<redacted>'
    assert cz_logger._redact('https://x/auth/cert/', {'accessToken': 'jwt'})['accessToken'] == '<redacted>'
    assert 'secret-h' not in diagnostics.safe_text('{"token":"secret-h"}')
    assert 'eyJ' not in diagnostics.safe_text('eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0In0.signature')


async def test_context_is_isolated_between_concurrent_operations():
    async def gather_evidence(code):
        with diagnostics.operation_scope(uuid4(), uuid4(), code) as operation:
            await asyncio.sleep(0)
            diagnostics.capture_request('cz', 'POST', 'https://x', [code], original_code=code)
            return operation.requests
    first, second = await asyncio.gather(gather_evidence('a'), gather_evidence('b'))
    assert first[0]['original_code'] == 'a' and second[0]['original_code'] == 'b'
    assert diagnostics.current_operation.get() is None


def test_failed_request_survives_many_successful_batches():
    with diagnostics.operation_scope(uuid4(), uuid4(), 'ms.write') as operation:
        diagnostics.capture_request('ms', 'POST', 'https://x', ['broken'], 412, {'errors': ['bad mark']})
        for _ in range(100):
            diagnostics.capture_request('ms', 'POST', 'https://x', ['good'], 200)
        assert len(operation.requests) == 30
        assert operation.requests[0]['status'] == 412
        assert operation.dropped_requests == 71


async def test_cz_404_keeps_original_sent_code_group_and_error_body(monkeypatch):
    from app.services import chestnyznak, cz_pg_cache
    raw = '01146056480636891326071421000010824073883'
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={'error': 'Код не найден'}))
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(chestnyznak.httpx, 'AsyncClient', lambda **kwargs: client_factory(transport=transport, **kwargs))
    monkeypatch.setattr(cz_pg_cache, 'get_cached_pg', AsyncMock(return_value=None))
    logs = []
    async def log(**kwargs):
        logs.append(kwargs)
        diagnostics.capture_request('chestnyznak', kwargs['method'], kwargs['url'], kwargs['request_body'],
            kwargs['response_status'], kwargs['response_body'], original_code=kwargs['original_code'])
    monkeypatch.setattr(cz_logger, 'log_cz_request', log)
    with diagnostics.operation_scope(uuid4(), uuid4(), 'cz.expand') as operation:
        assert await ChestnyZnakService('fake', mock=False, product_groups=['otp']).get_code_info(raw) is None
        assert logs[0]['request_body'] == [raw]
        assert operation.requests[0]['original_code'] == raw
        assert operation.requests[0]['response']['error'] == 'Код не найден'
        assert 'pg=otp' in operation.requests[0]['url']


async def test_ms_412_response_and_sent_marks_are_captured():
    transport = httpx.MockTransport(lambda request: httpx.Response(412, json={'errors': [{'code': 17102}]}))
    with diagnostics.operation_scope(uuid4(), uuid4(), 'ms.write') as operation:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await MoySkladService('secret')._request_with_retry(client, 'POST',
                        'https://x.test/trackingCodes', json=[{'cis': 'mark'}])
        assert response.status_code == 412
        assert operation.requests[0]['request'] == [{'cis': 'mark'}]
        assert operation.requests[0]['response']['errors'][0]['code'] == 17102
        assert 'secret' not in json.dumps(operation.requests)


async def test_incidents_are_saved_even_with_external_monitoring_disabled(monkeypatch):
    monkeypatch.setattr(monitoring.settings, 'MONITORING_ENABLED', False)
    saved = AsyncMock(return_value='incident-id')
    monkeypatch.setattr(incidents, 'record', saved)
    with diagnostics.operation_scope(uuid4(), uuid4(), 'cz.expand'):
        assert await monitoring.emit('process_document.boxes_unexpanded', level='warning') == 'incident-id'
    saved.assert_awaited_once()


async def test_logging_database_failure_does_not_break_cz_request(monkeypatch):
    def broken():
        raise RuntimeError('database down')
    monkeypatch.setattr(cz_logger, 'AsyncSessionLocal', broken)
    with diagnostics.operation_scope(uuid4(), uuid4(), 'cz.expand') as operation:
        await cz_logger.log_cz_request('POST', 'https://x', ['code'], 404, {'error': 'missing'}, 1,
                                      original_code='code')
        assert operation.requests[0]['status'] == 404


@pytest.mark.parametrize('receipt', [{'accepted': 0}, {'ok': True}])
async def test_http_200_without_event_acceptance_is_a_delivery_failure(monkeypatch, receipt):
    factory = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=receipt))
    monkeypatch.setattr(monitoring.httpx, 'AsyncClient', lambda **kwargs: factory(transport=transport, **kwargs))
    monkeypatch.setattr(monitoring.settings, 'MONITORING_URL', 'https://x.test')
    with pytest.raises(RuntimeError, match='не подтвердила'):
        await monitoring.send_payload({'events': [{'event': 'incident.opened'}]}, delivery_id='delivery-1')
