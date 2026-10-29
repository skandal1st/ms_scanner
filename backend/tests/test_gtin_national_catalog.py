import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import gtin_control
from app.services import national_catalog as nk
from tests.test_release_processing import Result

GTIN = '04640340395822'


def request(**changes):
    return gtin_control.NationalCatalogRequest(run_id=uuid4(), gtin=GTIN, **changes)


def database(cached=None, owned=True):
    return NS(execute=AsyncMock(side_effect=[Result(uuid4() if owned else None), Result(None)]),
              get=AsyncMock(return_value=cached), rollback=AsyncMock(), commit=AsyncMock())


async def test_nk_query_requires_gtin_in_owned_completed_report(monkeypatch):
    lookup = AsyncMock(); monkeypatch.setattr(nk, 'lookup_products_detailed', lookup)
    db = database(owned=False)
    with pytest.raises(HTTPException) as error:
        await gtin_control.national_catalog(request(), NS(id=uuid4()), db)
    assert error.value.status_code == 404
    db.get.assert_not_awaited(); lookup.assert_not_awaited(); db.commit.assert_not_awaited()


@pytest.mark.parametrize('found', [True, False])
async def test_recent_cache_has_explicit_status_and_check_time_without_external_call(monkeypatch, found):
    at = datetime.now(timezone.utc)
    cached = NS(gtin=GTIN, found=found, good_name='Апельсин 120 г' if found else None,
                brand_name='NAШ' if found else None, category=None, fetched_at=at)
    lookup = AsyncMock(); monkeypatch.setattr(nk, 'lookup_products_detailed', lookup)
    result = await gtin_control.national_catalog(request(), NS(id=uuid4()), database(cached))
    assert result['source'] == 'cache' and result['checked_at'] == at
    assert result['status'] == ('found' if found else 'not_found')
    lookup.assert_not_awaited()


@pytest.mark.parametrize('found', [True, False])
async def test_live_answer_updates_only_nk_cache_and_preserves_missing_vs_failure(monkeypatch, found):
    monkeypatch.setattr(gtin_control.settings, 'NK_ENABLED', True)
    monkeypatch.setattr(gtin_control.settings, 'NK_API_KEY', 'fixture')
    card = nk.NkCard(gtin=GTIN, good_name='Апельсин 120 г', brand_name='NAШ', category='Табак')
    lookup = AsyncMock(return_value=({GTIN:card}, set()) if found else ({}, {GTIN}))
    monkeypatch.setattr(nk, 'lookup_products_detailed', lookup)
    db = database()
    result = await gtin_control.national_catalog(request(), NS(id=uuid4()), db)
    assert result['status'] == ('found' if found else 'not_found')
    assert result['source'] == 'national_catalog'
    assert result['good_name'] == (card.good_name if found else None)
    db.rollback.assert_awaited_once(); db.commit.assert_awaited_once()
    statement = db.execute.call_args_list[-1].args[0]
    assert statement.table.name == 'nk_product'
    assert statement.compile().params['gtin'] == GTIN
    lookup.assert_awaited_once_with([GTIN])


async def test_temporary_error_is_not_cached_as_missing(monkeypatch):
    monkeypatch.setattr(gtin_control.settings, 'NK_ENABLED', True)
    monkeypatch.setattr(gtin_control.settings, 'NK_API_KEY', 'fixture')
    monkeypatch.setattr(nk, 'lookup_products_detailed', AsyncMock(return_value=({}, set())))
    db = database()
    with pytest.raises(HTTPException) as error:
        await gtin_control.national_catalog(request(), NS(id=uuid4()), db)
    assert error.value.status_code == 502
    db.commit.assert_not_awaited()
    assert db.execute.await_count == 1


async def test_explicit_refresh_bypasses_fresh_cache(monkeypatch):
    monkeypatch.setattr(gtin_control.settings, 'NK_ENABLED', True)
    monkeypatch.setattr(gtin_control.settings, 'NK_API_KEY', 'fixture')
    cached = NS(gtin=GTIN, found=True, good_name='Старое имя', brand_name=None, category=None,
                fetched_at=datetime.now(timezone.utc))
    card = nk.NkCard(gtin=GTIN, good_name='Новое имя 120 г', brand_name=None, category=None)
    lookup = AsyncMock(return_value=({GTIN:card}, set()))
    monkeypatch.setattr(nk, 'lookup_products_detailed', lookup)
    result = await gtin_control.national_catalog(request(refresh=True), NS(id=uuid4()), database(cached))
    assert result['good_name'] == 'Новое имя 120 г' and result['source'] == 'national_catalog'
    lookup.assert_awaited_once()


async def test_disabled_integration_does_not_report_product_missing(monkeypatch):
    monkeypatch.setattr(gtin_control.settings, 'NK_ENABLED', False)
    with pytest.raises(HTTPException) as error:
        await gtin_control.national_catalog(request(), NS(id=uuid4()), database())
    assert error.value.status_code == 503


async def test_timeout_leaves_cache_unchanged(monkeypatch):
    monkeypatch.setattr(gtin_control.settings, 'NK_ENABLED', True)
    monkeypatch.setattr(gtin_control.settings, 'NK_API_KEY', 'fixture')
    monkeypatch.setattr(nk, 'lookup_products_detailed', AsyncMock(side_effect=asyncio.TimeoutError))
    db = database()
    with pytest.raises(HTTPException) as error:
        await gtin_control.national_catalog(request(), NS(id=uuid4()), db)
    assert error.value.status_code == 504
    db.commit.assert_not_awaited()


def test_http_request_logs_do_not_reveal_nk_api_key(caplog):
    with caplog.at_level(logging.INFO, logger='httpx'):
        logging.getLogger('httpx').info('HTTP Request: GET %s "HTTP/1.1 200 OK"',
                                      'https://catalog.example/v3/product?apikey=fixture-secret&gtin='+GTIN)
    assert 'fixture-secret' not in caplog.text
    assert GTIN in caplog.text


@pytest.mark.parametrize('value', ['123', '04640340395822?apikey=secret', '(01)04640340395822', 'x'*14])
def test_only_canonical_gtin_can_be_queried(value):
    with pytest.raises(ValidationError):
        gtin_control.NationalCatalogRequest(run_id=uuid4(), gtin=value)
