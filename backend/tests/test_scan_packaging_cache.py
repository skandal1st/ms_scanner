from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

# Bind the real functions before the global fixture replaces service entrypoints.
from app.services.scan_packaging_cache import has_confirmed_unit, remember_unit_evidence
from app.services import scan_packaging_cache as cache


@pytest.fixture
def redis(monkeypatch):
    client = NS(get=AsyncMock(return_value=b'UNIT'), set=AsyncMock(),
                delete=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(cache.aioredis, 'from_url', lambda *args, **kwargs: client)
    return client


async def test_only_exact_explicit_unit_evidence_is_cached_for_the_current_document(redis):
    await remember_unit_evidence('user', 'doc', '04640195516373',
        NS(package_type='UNIT', children=[], gtin='04640195516373'))
    redis.set.assert_awaited_once_with('scan_unit:user:doc:04640195516373', 'UNIT', ex=600)
    assert await has_confirmed_unit('user', 'doc', '04640195516373')
    assert not await has_confirmed_unit('user', 'doc', None)
    await has_confirmed_unit('other-user', 'other-doc', '04640195516373')
    assert redis.get.await_args.args == ('scan_unit:other-user:other-doc:04640195516373',)


@pytest.mark.parametrize('info', [
    NS(package_type='GROUP', children=['child'], gtin='04640195516373'),
    NS(package_type='BOX', children=[], gtin='04640195516373'),
    NS(package_type=None, children=[], gtin='04640195516373'),
    NS(package_type='UNIT', children=['child'], gtin='04640195516373'),
    NS(package_type='UNIT', children=[], gtin='04630339608897'),
])
async def test_aggregates_missing_types_and_aliases_invalidate_unit_evidence(redis, info):
    await remember_unit_evidence('user', 'doc', '04640195516373', info)
    redis.set.assert_not_awaited()
    redis.delete.assert_awaited_once_with('scan_unit:user:doc:04640195516373')


async def test_cache_failure_falls_back_to_normal_discovery(redis):
    redis.get.side_effect = TimeoutError()
    redis.aclose.side_effect = OSError()
    assert not await has_confirmed_unit('user', 'doc', '04640195516373')
