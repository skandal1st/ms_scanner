from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.api.organization_profiles import ShipmentStateRequest, save_shipment_status
from app.services.collection_time import CollectionStartRequest, shipment_moment
from app.services.moysklad import MoySkladService


@pytest.mark.parametrize('zone', ['', 'Moscow', '../Europe/Moscow', '/etc/localtime'])
def test_unknown_or_path_timezone_rejected_in_both_requests(zone):
    with pytest.raises(ValidationError):
        CollectionStartRequest(timezone=zone)
    with pytest.raises(ValidationError):
        ShipmentStateRequest(shipment_timezone=zone)


def test_local_day_and_dst_are_calculated_from_server_instant():
    at = datetime(2026, 11, 7, 22, 30, 59, tzinfo=timezone.utc)
    assert shipment_moment(at, 'Asia/Vladivostok') == '2026-11-08 08:30:00'
    assert shipment_moment(at, 'America/New_York') == '2026-11-07 17:30:00'
    summer = datetime(2026, 7, 7, 22, 30, tzinfo=timezone.utc)
    assert shipment_moment(summer, 'America/New_York') == '2026-07-07 18:30:00'


async def test_profile_timezone_can_be_set_and_cleared_without_changing_other_profile():
    profile = NS(shipment_start_state_id=None, shipment_timezone=None)
    other = NS(shipment_timezone='Asia/Vladivostok')
    db = NS(commit=AsyncMock())
    result = await save_shipment_status(ShipmentStateRequest(shipment_timezone='Asia/Omsk'), NS(), profile, db)
    assert result['shipment_timezone'] == 'Asia/Omsk'
    await save_shipment_status(ShipmentStateRequest(), NS(), profile, db)
    assert profile.shipment_timezone == 'Asia/Omsk'  # old clients don't clear it
    await save_shipment_status(ShipmentStateRequest(shipment_timezone=None), NS(), profile, db)
    assert profile.shipment_timezone is None
    assert other.shipment_timezone == 'Asia/Vladivostok'


@pytest.mark.parametrize('state', [None, 'collecting'])
async def test_date_and_optional_shipment_status_use_one_small_put(state):
    ms = MoySkladService('test')
    moment = '2026-11-08 08:30:00'
    ms._request_with_retry = AsyncMock(return_value=httpx.Response(200, json={'moment': moment}, request=httpx.Request('PUT', 'https://ms')))
    await ms.change_collection_state('demand', 'shipment', state, moment=moment)
    ms._request_with_retry.assert_awaited_once()
    call = ms._request_with_retry.call_args
    assert call.args[1:] == ('PUT', f'{ms.base_url}/entity/demand/shipment')
    payload = call.kwargs['json']
    assert set(payload) == ({'moment', 'state'} if state else {'moment'})
    assert payload['moment'] == moment


async def test_order_date_cannot_be_changed_and_ignored_shipment_date_is_not_success():
    ms = MoySkladService('test')
    ms._request_with_retry = AsyncMock()
    with pytest.raises(ValueError, match='только у отгрузки'):
        await ms.change_collection_state('customerorder', 'order', 'state', moment='2026-11-08 08:30:00')
    ms._request_with_retry.assert_not_awaited()
    ms._request_with_retry.return_value = httpx.Response(200, json={'moment': '2026-11-07 10:00:00'}, request=httpx.Request('PUT', 'https://ms'))
    with pytest.raises(ValueError, match='не подтвердил'):
        await ms.change_collection_state('demand', 'shipment', None, moment='2026-11-08 08:30:00')
