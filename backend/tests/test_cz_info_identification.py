import json
from unittest.mock import AsyncMock
import httpx
import pytest
from app.services import chestnyznak, cz_logger, cz_pg_cache
from app.services.chestnyznak import cis_for_cz_info, ChestnyZnakService

GS = '010464027597189421AbC_123\x1d93ZSBQ'
COMPACT = '00802084125507ejQRs_7AAAABBBB'


@pytest.mark.parametrize('raw,expected', [
    (GS, GS.split('\x1d')[0]),
    (COMPACT, COMPACT[:21]),
    ('010464027597189421ABC123\x1d93ZSBQ', '010464027597189421ABC123'),
    ('010464027597189421AbC_12393ZSBQ', '010464027597189421AbC_123'),
    ('010464027597189421AbC_123xyz9876', '010464027597189421AbC_123xyz9876'),
])
def test_info_uses_identity_without_changing_registered_form_or_guessing_serial(raw, expected):
    assert cis_for_cz_info(raw, 'otp') == expected


@pytest.fixture
def transport(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(cz_logger, 'log_cz_request', AsyncMock())
    monkeypatch.setattr(cz_pg_cache, 'set_cached_pg', AsyncMock())
    monkeypatch.setattr(cz_pg_cache, 'get_cached_pg', AsyncMock(return_value='otp'))
    def install(handler):
        monkeypatch.setattr(chestnyznak.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return install


async def test_batch_correlates_full_and_short_aliases_and_compact_codes(transport):
    short = GS.split('\x1d')[0]
    def reply(request):
        assert request.url.path.endswith('/info')
        assert json.loads(request.content) == [short, COMPACT[:21]]
        return httpx.Response(200, json=[{'cisInfo': {'requestedCis': c, 'cis': c,
            'status': 'INTRODUCED', 'ownerInn': '123', 'ownerName': 'Test'}} for c in [short,COMPACT[:21]]])
    transport(reply)
    result = await ChestnyZnakService(token='test',mock=False,product_groups=['otp']).check_codes([GS,short,COMPACT])
    assert [c.code for c in result] == [GS,short,COMPACT]
    assert all(c.owner_inn == '123' and c.verification_source == 'cz_info' for c in result)


async def test_single_lookup_uses_same_identity_and_keeps_owner(transport):
    def reply(request):
        assert json.loads(request.content) == [COMPACT[:21]]
        return httpx.Response(200,json=[{'cisInfo': {'requestedCis': COMPACT[:21], 'cis': COMPACT[:21],
            'status': 'INTRODUCED', 'ownerInn': '123', 'ownerName': 'Test', 'generalPackageType': 'UNIT'}}])
    transport(reply)
    result = await ChestnyZnakService(token='test',mock=False,product_groups=['otp']).get_code_info(COMPACT)
    assert result.owner_inn == '123'
