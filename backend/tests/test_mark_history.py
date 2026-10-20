from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import mark_history as service

RAW = '04603743309261GKgWkbWAAAAdW6a'
CIS = RAW[:21]
KEY = '04603743309261|GKgWkbW'


def test_identity_unifies_base64_full_and_cis_without_lowercasing():
    encoded = 'MDQ2MDM3NDMzMDkyNjFHS2dXa2JXQUFBQWRXNmE='
    assert service.identity(encoded) == (RAW, KEY)
    assert service.identity(CIS)[1] == KEY
    assert service.identity(RAW.lower())[1] != KEY
    assert service.identity('00123456789012345678')[1] == '00123456789012345678'
    with pytest.raises(ValueError):
        service.identity('https://example.test')
    with pytest.raises(ValueError):
        service.identity('04603743309261' + 'A' * 300)


@pytest.mark.parametrize('address,expected', [
    ('обл. Ростовская, г. Ростов-на-Дону, пр-кт Ворошиловский, д 101', 'Ростовская область'),
    ('Саратовская область, г Саратов', 'Саратовская область'),
    ('г Саратов, ул Новоузенская, д 89 к 1', None),
    ('г Санкт-Петербург, ул Мира', 'Санкт-Петербург'),
    ('Московская область, г Химки', 'Московская область'),
])
def test_region_only_comes_from_explicit_address_not_owner_inn(address, expected):
    current = service.current_info({'ownerInn': '616806585567', 'ownerMod': {'address': address}})
    assert current['region'] == expected and current['owner_address'] == address
    assert current['region_source'] == ('address' if expected else None)


def test_current_withdrawn_even_when_mark_withdraw_false_and_no_invented_sale_date():
    result = service.current_info({'cis': CIS, 'status': 'WITHDRAWN', 'markWithdraw': False,
        'withdrawReason': 'RETAIL', 'ownerName': 'Продавец', 'ownerMod': {'regionName': 'Ростовская область'}})
    assert result['withdrawn'] and result['withdraw_reason_label'] == 'Розничная продажа'
    assert result['region_source'] == 'cz'
    assert 'operation_at' not in result
    assert service.current_info({'withdrawReason': 'OWN_USE'})['withdraw_reason_label'] == 'Для собственных нужд'
    assert service.current_info({'ownerInn': '781437759369'})['region'] is None


def test_history_keeps_distinct_operation_and_registration_dates_and_missing_values():
    earlier = {'cis': CIS, 'docId': '1', 'status': 'INTRODUCED', 'ownerInn': 'our-inn',
        'operationDate': '2026-08-01T00:00:00Z', 'timestamp': '2026-08-13T08:54:07Z'}
    undated = {'cis': CIS, 'docId': 'unknown', 'status': 'WITHDRAWN', 'timestamp': '2026-09-01T12:00:00Z'}
    events = service.history_events([undated, earlier, earlier], KEY)
    assert len(events) == 2
    assert events[0]['operation_at'].startswith('2026-08-01')
    assert events[0]['recorded_at'].startswith('2026-08-13')
    assert events[1]['operation_at'] is None
    assert events[1]['owner_inn'] is None
    assert service.date_value('garbage') is None
    assert service.date_value('2026-08-01T03:00:00+03:00') == '2026-08-01T00:00:00+00:00'
    with pytest.raises(ValueError):
        service.history_events([{'cis': '04603743309261OTHER'}], KEY)
    assert service.history_events([], KEY) == []


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    mock = httpx.MockTransport(handler)
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: client(transport=mock, **kwargs))
    monkeypatch.setattr(service, 'log_cz_request', AsyncMock())


async def test_fetch_correlates_requested_code_and_does_not_change_any_external_data(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/cises/info'):
            return httpx.Response(200, json=[{'cisInfo': {'cis': CIS, 'requestedCis': CIS,
                'status': 'WITHDRAWN', 'markWithdraw': False, 'withdrawReason': 'OWN_USE'}}])
        return httpx.Response(200, json=[{'cis': CIS, 'status': 'INTRODUCED', 'ownerInn': 'our-inn',
            'operationDate': '2026-08-01T00:00:00Z', 'docId': 'outgoing-upd'}])
    transport(monkeypatch, handler)
    result = await service.fetch_history(RAW, 'secret', ['otp'], '00000000-0000-0000-0000-000000000001')
    assert result['current']['withdrawn'] and len(result['events']) == 1
    assert not result['info_error'] and not result['history_error']
    assert all(c.method == 'POST' and c.url.path.endswith(('/cises/info', '/cises/history')) for c in calls)
    assert calls[1].url.params['cis'] == CIS
    assert 'secret' not in str(result)
    assert service.log_cz_request.await_count == 2


async def test_history_can_be_missing_while_current_snapshot_is_successful(monkeypatch):
    def handler(request):
        if request.url.path.endswith('/cises/info'):
            return httpx.Response(200, json=[{'cisInfo': {'cis': CIS, 'status': 'WITHDRAWN', 'withdrawReason': 'RETAIL'}}])
        return httpx.Response(404, json={'error_message': 'Не найдены операции'})
    transport(monkeypatch, handler)
    result = await service.fetch_history(RAW, 'token', ['otp'], '00000000-0000-0000-0000-000000000001')
    assert result['current']['withdraw_reason_label'] == 'Розничная продажа'
    assert result['events'] is None and 'не подтверждает отсутствие' in result['history_error']
    assert result['info_error'] is None


async def test_wrong_mark_info_is_ignored_and_history_is_checked_independently(monkeypatch):
    def handler(request):
        if request.url.path.endswith('/cises/info'):
            return httpx.Response(200, json=[{'cisInfo': {'cis': '04603743309261OTHER', 'requestedCis': CIS}}])
        return httpx.Response(200, json=[])
    transport(monkeypatch, handler)
    result = await service.fetch_history(RAW, 'token', ['otp'], '00000000-0000-0000-0000-000000000001')
    assert result['current'] is None and result['info_error']
    assert result['events'] == [] and result['history_error'] is None


@pytest.mark.parametrize('status', [401, 403, 429, 500])
async def test_external_failures_are_not_reported_as_no_movement(monkeypatch, status):
    transport(monkeypatch, lambda request: httpx.Response(status, json={'error': 'external'}))
    result = await service.fetch_history(RAW, 'token', ['otp'], '00000000-0000-0000-0000-000000000001')
    assert result['current'] is None and result['events'] is None
    assert result['info_error'] and result['history_error']
    if status == 401:
        assert result['info_error'] == service.AUTH_ERROR


async def test_timeout_preserves_unknown_result(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout('timeout')
    transport(monkeypatch, handler)
    result = await service.fetch_history(RAW, 'token', ['otp'], '00000000-0000-0000-0000-000000000001')
    assert result['current'] is None and result['events'] is None
    assert result['info_error'] and result['history_error']


async def test_no_token_never_calls_cz():
    result = await service.fetch_history(RAW, None, ['otp'], 'user')
    assert result['info_error'] == result['history_error'] == service.AUTH_ERROR
