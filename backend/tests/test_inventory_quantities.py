import asyncio
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, func

from app.api import physical_counts as api
from app.db.models import PhysicalCountSession, PhysicalCountQuantity, OrganizationProfile
from tests.test_physical_counts import real_context, row


async def seed(factory, scope):
    async with factory() as db:
        session = PhysicalCountSession(user_id=scope[0].id, organization_profile_id=scope[1].id,
            mode='inventory', name='Тест', status='active',
            plan=[{**row(), 'product_name': "Troffimoff`s Персик", 'expected_qty': 10},
                  {**row(), 'key': 'zero'}, {**row(), 'key': 'other', 'folder_name': 'Другой бренд'}],
            settings={'store_id': 'store', 'count_method': 'quantity'})
        db.add(session); await db.commit()
        return session.id


def body(qty, key='p', revision=0, request_id=None):
    return api.QuantityRequest(product_key=key, quantity=Decimal(str(qty)), revision=revision,
                               request_id=request_id or uuid4())


async def test_absolute_quantity_and_zero_count_are_exported_correctly(real_context):
    factory, scope, _, _ = real_context
    sid = await seed(factory, scope)
    request = body(8)
    async with factory() as db:
        result = await api.set_quantity(sid, request, scope, db)
        assert result['counts'] == {'p': 8} and result['revisions'] == {'p': 1}
        await api.set_quantity(sid, request, scope, db)
        result = await api.set_quantity(sid, body(7, revision=1), scope, db)
        assert result['counts']['p'] == 7  # replaces, never adds 7 to 8
        await api.set_quantity(sid, body(0, key='zero'), scope, db)
        assert (await db.execute(select(func.count()).select_from(PhysicalCountQuantity)
            .where(PhysicalCountQuantity.session_id == sid))).scalar_one() == 3
        report = (await api.export(sid, scope, db)).body.decode('utf-8-sig').splitlines()
        assert report[1].endswith(';7.0;-3.0;Да')
        assert report[2].endswith(';0.0;-2.0;Да')
        assert report[3].endswith(';;;Нет')  # unentered fact and difference are blank
        await api.complete_brand(sid, api.BrandRequest(brand='Бренд'), scope, db)
        with pytest.raises(HTTPException):
            await api.set_quantity(sid, body(9, revision=2), scope, db)


async def test_two_terminals_cannot_silently_overwrite_each_other(real_context):
    factory, scope, devices, _ = real_context
    sid = await seed(factory, scope)
    async def enter(device, qty):
        async with factory() as db:
            try:
                return await api.set_quantity(sid, body(qty), (*scope[:3], device), db)
            except HTTPException as exc:
                return exc.status_code
    results = await asyncio.gather(enter(devices[0], 8), enter(devices[1], 9))
    assert sum(v == 409 for v in results) == 1
    async with factory() as db:
        result = await api.detail(sid, scope, db)
        assert result['counts']['p'] in (8, 9)
        assert result['revisions']['p'] == 1


async def test_unentered_positions_and_scanning_are_blocked(real_context):
    factory, scope, _, _ = real_context
    sid = await seed(factory, scope)
    async with factory() as db:
        with pytest.raises(HTTPException) as err:
            await api.complete_brand(sid, api.BrandRequest(brand='Бренд'), scope, db)
        assert err.value.status_code == 409 and 'не заполнено' in err.value.detail
        with pytest.raises(HTTPException):
            await api.scan(sid, api.ScanRequest(code='MANUAL:any', product_key='p', brand='Бренд'), scope, db)
        with pytest.raises(HTTPException):
            await api.set_quantity(sid, body(1, key='missing'), scope, db)


async def test_request_reuse_and_role_revocation_are_rejected(real_context):
    factory, scope, _, _ = real_context
    sid = await seed(factory, scope)
    request = body(8)
    async with factory() as db:
        await api.set_quantity(sid, request, scope, db)
        with pytest.raises(HTTPException):
            await api.set_quantity(sid, body(9, request_id=request.request_id), scope, db)
        scope[3].allowed_modes = ['shipment']
        with pytest.raises(HTTPException) as err:
            await api.set_quantity(sid, body(9, revision=1), scope, db)
        assert err.value.status_code == 403


async def test_desktop_can_count_without_terminal(real_context):
    factory, scope, _, _ = real_context
    sid = await seed(factory, scope)
    desktop = (*scope[:2], None, None)
    async with factory() as db:
        result = await api.desktop_quantity(sid, body(8), desktop, db)
        assert result['counts']['p'] == 8
        entry = (await db.execute(select(PhysicalCountQuantity).where(PhysicalCountQuantity.session_id == sid))).scalar_one()
        assert entry.device_id is None


async def test_lock_refreshes_session_loaded_before_completion(real_context):
    factory, scope, _, _ = real_context
    sid = await seed(factory, scope)
    async with factory() as reader, factory() as writer:
        cached = await reader.get(PhysicalCountSession, sid)
        finished = await writer.get(PhysicalCountSession, sid)
        finished.status = 'completed'
        await writer.commit()
        with pytest.raises(HTTPException) as err:
            await api.set_quantity(sid, body(8), scope, reader)
        assert err.value.status_code == 409
        assert cached.status == 'completed'


async def test_saved_shipment_statuses_are_profile_scoped_and_used_as_defaults(real_context, monkeypatch):
    from app.worker.tasks import prepare_physical_count_task
    factory, scope, _, _ = real_context
    state_id, store_id = str(uuid4()), str(uuid4())
    available = {'stores': [{'id': store_id, 'name': 'Склад'}], 'states': [{'id': state_id, 'name': 'Собран'}],
                 'default_include_state_ids': []}
    monkeypatch.setattr(api, 'options', AsyncMock(return_value=available))
    monkeypatch.setattr(prepare_physical_count_task, 'delay', lambda *args: None)
    desktop = (*scope[:2], None, None)
    async with factory() as db:
        result = await api.save_inventory_settings(api.InventorySettingsRequest(include_state_ids=[state_id]), desktop, db)
        assert result['include_state_ids'] == [state_id]
    async with factory() as db:
        profile = await db.get(OrganizationProfile, scope[1].id)
        assert profile.inventory_include_state_ids == [state_id]
        available['default_include_state_ids'] = profile.inventory_include_state_ids
        session = await api.create_session(api.CreateRequest(mode='inventory', store_id=store_id, count_method='quantity'), desktop, db)
        assert session['settings']['include_state_ids'] == [state_id]
        assert session['settings']['count_method'] == 'quantity'
        empty = await api.create_session(api.CreateRequest(mode='inventory', store_id=store_id, include_state_ids=[]), desktop, db)
        assert empty['settings']['include_state_ids'] == []


@pytest.mark.parametrize('value', [-1, '1.0001', 1000001])
def test_invalid_quantities_rejected(value):
    with pytest.raises(ValidationError):
        body(value)
