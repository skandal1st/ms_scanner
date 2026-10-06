"""Transient read failures recover without replaying document writes."""
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core.diagnostics import operation_scope
from app.services.moysklad import MoySkladService


@pytest.fixture
def pauses(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr('app.services.moysklad.asyncio.sleep', sleep)
    return sleep


@pytest.mark.parametrize('status', [502, 503, 504])
async def test_transient_read_recovers_and_keeps_failure_evidence(status, pauses):
    statuses = iter([status, 200])
    seen = []
    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(next(statuses), json={'rows': []})
    with operation_scope(uuid4(), uuid4(), 'moysklad.write_document') as operation:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            response = await MoySkladService('fake')._request_with_retry(
                client, 'get', 'https://x.test/trackingCodes', params={'codetype': 'gs1'})
        assert response.status_code == 200
        assert seen == ['https://x.test/trackingCodes?codetype=gs1'] * 2
        assert [r['status'] for r in operation.requests] == [status, 200]
    pauses.assert_awaited_once_with(1.0)


async def test_persistent_503_returns_final_response_after_three_retries(pauses):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503, text='Service Unavailable')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await MoySkladService('fake')._request_with_retry(client, 'GET', 'https://x.test')
    assert len(calls) == 4
    assert [c.args[0] for c in pauses.await_args_list] == [1.0, 2.0, 5.0]
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


@pytest.mark.parametrize('error_type', [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_transport_failure_then_success(error_type, pauses):
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise error_type('temporary failure', request=request)
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await MoySkladService('fake')._request_with_retry(client, 'GET', 'https://x.test')
    assert response.status_code == 200 and len(calls) == 2
    pauses.assert_awaited_once_with(1.0)


async def test_persistent_timeout_raises_final_error(pauses):
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('temporary failure', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await MoySkladService('fake')._request_with_retry(client, 'GET', 'https://x.test')
    assert len(calls) == 4 and pauses.await_count == 3


@pytest.mark.parametrize('method', ['POST', 'PUT', 'DELETE'])
@pytest.mark.parametrize('failure', [503, 'timeout'])
async def test_writes_are_never_replayed_on_ambiguous_failure(method, failure, pauses):
    calls = []
    def respond(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('ambiguous write result', request=request)
        return httpx.Response(failure)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if failure == 'timeout':
            with pytest.raises(httpx.ReadTimeout):
                await MoySkladService('fake')._request_with_retry(client, method, 'https://x.test')
        else:
            response = await MoySkladService('fake')._request_with_retry(client, method, 'https://x.test')
            assert response.status_code == failure
    assert len(calls) == 1
    pauses.assert_not_awaited()


@pytest.mark.parametrize('status', [400, 401, 403, 404, 412, 500])
async def test_other_errors_are_returned_without_retry(status, pauses):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
        response = await MoySkladService('fake')._request_with_retry(client, 'GET', 'https://x.test')
    assert response.status_code == status
    pauses.assert_not_awaited()


@pytest.mark.parametrize('method', ['GET', 'POST'])
async def test_rate_limit_retries_still_work_with_bounded_header_delay(method, pauses):
    statuses = iter([429, 200])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            next(statuses), headers={'Retry-After': '120'}))) as client:
        response = await MoySkladService('fake')._request_with_retry(client, method, 'https://x.test')
    assert response.status_code == 200
    pauses.assert_awaited_once_with(60.0)


async def test_mixed_failures_share_a_bounded_attempt_budget(pauses):
    statuses = iter([503, 429, 503, 429, 429, 429])
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(next(statuses))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await MoySkladService('fake')._request_with_retry(client, 'GET', 'https://x.test')
    assert len(calls) == 6 and response.status_code == 429
    assert pauses.await_count == 5
