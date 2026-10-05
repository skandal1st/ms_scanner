import os
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
from decimal import Decimal
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.api import physical_counts as api, tsd
from app.core.config import settings
from app.db.models import User, OrganizationProfile, Workplace, TsdDevice, Document, DocumentKind, PhysicalCountSession, PhysicalCountScan, Scan
from app.services.physical_counts import acceptance_plan, identify_count_scan, prepare_inventory_plan

GTIN = '04620543080527'

def row(**extra):
    return dict(key='p', product_name='Товар', gtins=[GTIN], folder_name='Бренд', expected_qty=2, base_qty=2, shipment_qty=0, **extra)


def test_unique_mark_identity_ignores_crypto_tail_and_pack_count_is_correct():
    first = identify_count_scan([row(pack_quantities={GTIN: 10})], f'01{GTIN}21ABC\x1d93aaaa')
    second = identify_count_scan([row(pack_quantities={GTIN: 10})], f'01{GTIN}21ABC\x1d93bbbb')
    assert first[1] == 10
    assert first[4] == second[4]
    assert identify_count_scan([row()], GTIN)[1] == 1
    assert identify_count_scan([row()], 'MANUAL:any', 'p', Decimal('2.5'))[1] == Decimal('2.5')


def test_ambiguous_wrong_product_and_unknown_package_rejected():
    another = {**row(), 'key': 'other'}
    for plan, code, target in [([row(), another], GTIN, None),
        ([row(), {**another, 'gtins': ['04620543080503']}], GTIN, 'other'),
        ([row(pack_quantities={GTIN: 0})], GTIN, None), ([row()], 'garbage1', 'p')]:
        with pytest.raises(HTTPException):
            identify_count_scan(plan, code, target)


def test_xml_unknown_block_is_not_silently_counted_as_one_unit():
    package = f'01{GTIN}21PACK\x1d93crypto'
    plan = [row(package_codes=[package])]
    with pytest.raises(HTTPException):
        identify_count_scan(plan, package)
    assert identify_count_scan(plan, f'01{GTIN}21UNIT\x1d93crypto')[1] == 1
    assert identify_count_scan(plan, 'MANUAL:pack', 'p', 10)[1] == 10


def test_modes_and_scope_do_not_grant_extra_access():
    tsd.require_tsd_mode(NS(allowed_modes=['acceptance']), 'acceptance')
    for modes in ([], ['shipment']):
        with pytest.raises(HTTPException) as err:
            tsd.require_tsd_mode(NS(allowed_modes=modes), 'inventory')
        assert err.value.status_code == 403
    user, profile, workplace = NS(id=uuid4()), NS(id=uuid4()), NS(id=uuid4(), store_ids=['allowed'])
    session = NS(user_id=user.id, organization_profile_id=profile.id, settings={'store_id': 'foreign'}, workplace_id=workplace.id)
    assert not api.accessible(session, (user, profile, workplace, NS()))
    session.settings['store_id'] = 'allowed'
    assert api.accessible(session, (user, profile, workplace, NS()))
    assert not api.accessible(session, (NS(id=uuid4()), profile, workplace, NS()))


async def test_full_stock_preserves_zero_stock_instead_of_available_minus_reserve(monkeypatch):
    from app.services.moysklad import MoySkladService
    service = MoySkladService('fixture')
    response = httpx.Response(200, json={'rows': [{'meta': {'href': 'https://example.test/entity/variant/p'},
        'name': 'Модификация', 'stock': 0, 'quantity': -5}]}, request=httpx.Request('GET', 'https://example.test'))
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(service, '_request_with_retry', request)
    result = await service.get_stock_map(['https://example.test/entity/store/s'], group_by='variant', full_snapshot=True)
    assert result['p']['qty'] == 0
    params = request.await_args.kwargs['params']
    assert params['groupBy'] == 'variant'
    assert 'quantityMode=all' in params['filter'] and 'archived=true' in params['filter']


async def test_inventory_adds_only_applied_selected_demands_once_with_scope_and_pagination():
    calls = []
    def demand(id, **extra):
        return dict(id=id, applied=True, store={'id': 'store'}, organization={'id': 'org'}, state={'id': 'selected'},
            positions={'meta': {'size': 2}, 'rows': [{'assortment': {'id': 'p'}, 'quantity': 999}]}, **extra)
    async def request(client, method, url, params):
        calls.append((url, params))
        if url.endswith('entity/variant'):
            body = {'rows': []}
        elif url.endswith('entity/product'):
            body = {'rows': [{'id': 'p', 'name': 'Товар', 'pathName': 'Бренд', 'barcodes': [{'ean13': GTIN[1:]}]}]}
        else:
            body = {'rows': [demand('valid'), demand('valid'), {**demand('unapplied'), 'applied': False},
                {**demand('foreign'), 'store': {'id': 'other'}}, {**demand('wrong-status'), 'state': {'id': 'other'}}]}
        return httpx.Response(200, json=body, request=httpx.Request('GET', url))
    ms = NS(base_url='https://example.test', get_stock_map=AsyncMock(return_value={'p': {'qty': 5}, 'missing': {'qty': 3}}),
        _request_with_retry=request, _id_from_href=lambda href: href.rsplit('/', 1)[-1],
        _load_positions_rows=AsyncMock(return_value=[{'assortment': {'id': 'p'}, 'quantity': 2}, {'assortment': {'id': 'p', 'meta': {'type': 'service'}}, 'quantity': 5}]))
    plan = await prepare_inventory_plan(ms, 'store', ['selected'])
    assert next(v for v in plan if v['key'] == 'p')['expected_qty'] == 7
    assert next(v for v in plan if v['key'] == 'missing')['expected_qty'] == 3
    ms._load_positions_rows.assert_awaited_once_with('demand', 'valid')
    assert calls[-1][1]['limit'] == 100
    assert 'applied=true' in calls[-1][1]['filter']
    assert 'organization=' not in calls[-1][1]['filter']
    ms.get_stock_map.assert_awaited_once_with(['https://example.test/entity/store/store'], group_by='variant', full_snapshot=True)


@pytest.fixture
async def real_context():
    if os.getenv('AUDIT_POSTGRES') != '1':
        pytest.skip('requires isolated PostgreSQL')
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f'{uuid4()}@example.test', password_hash='')
        db.add(user); await db.flush()
        profile = OrganizationProfile(id=uuid4(), user_id=user.id, name='Тест', moysklad_organization_id='org')
        db.add(profile); await db.flush()
        workplace = Workplace(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, name='Тест', store_ids=['store'])
        db.add(workplace); await db.flush()
        devices = [TsdDevice(id=uuid4(), user_id=user.id, workplace_id=workplace.id, name=f'ТСД {i}', allowed_modes=['acceptance', 'inventory']) for i in range(2)]
        db.add_all(devices)
        doc = Document(id=uuid4(), user_id=user.id, organization_profile_id=profile.id, kind=DocumentKind.supply,
            name='XML', moysklad_store_id='store', plan=[row()], upd_meta={'physical_plan': [row()]})
        db.add(doc); await db.flush()
        db.add(Scan(document_id=doc.id, code=f'01{GTIN}21imported', gtin=GTIN))
        await db.commit()
    yield factory, (user, profile, workplace, devices[0]), devices, doc
    await engine.dispose()


async def test_two_terminals_share_acceptance_without_counting_xml_and_duplicate_mark(real_context):
    factory, scope, devices, doc = real_context
    async def open(device):
        async with factory() as db:
            return await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), (*scope[:3], device), db)
    sessions = await asyncio.gather(*(open(v) for v in devices))
    assert sessions[0]['id'] == sessions[1]['id']
    sid = sessions[0]['id']
    async with factory() as db:
        initial = await api.detail(sid, scope, db)
        assert initial['counts'] == {}
    async def scan(device):
        async with factory() as db:
            return await api.scan(sid, api.ScanRequest(code=f'01{GTIN}21ABC\x1d93test'), (*scope[:3], device), db)
    result = await asyncio.gather(*(scan(v) for v in devices))
    assert sum(v['duplicate'] for v in result) == 1
    async with factory() as db:
        data = await api.detail(sid, scope, db)
        assert data['counts'] == {'p': 1}
        assert (await db.execute(select(func.count()).select_from(Scan).where(Scan.document_id == doc.id))).scalar_one() == 1


async def test_barcode_repeats_add_and_undo_cannot_remove_other_terminals_count(real_context):
    factory, scope, devices, doc = real_context
    async with factory() as db:
        session = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
    sid = session['id']
    async def scan(device):
        async with factory() as db:
            return await api.scan(sid, api.ScanRequest(code=GTIN), (*scope[:3], device), db)
    await asyncio.gather(*(scan(v) for v in devices))
    async with factory() as db:
        data = await api.detail(sid, scope, db)
        assert data['counts'] == {'p': 2}
        foreign = next(v for v in data['scans'] if v['device_id'] != str(scope[3].id))
        with pytest.raises(HTTPException):
            await api.remove_scan(sid, foreign['id'], scope, db)
    async with factory() as db:
        own = next(v for v in data['scans'] if v['device_id'] == str(scope[3].id))
        assert (await api.remove_scan(sid, own['id'], scope, db))['counts'] == {'p': 1}
        await api.complete(sid, scope, db)
        with pytest.raises(HTTPException):
            await api.scan(sid, api.ScanRequest(code=GTIN), scope, db)


async def test_retried_barcode_request_does_not_add_quantity_twice(real_context):
    factory, scope, devices, doc = real_context
    async with factory() as db:
        session = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
    body = api.ScanRequest(code=GTIN)
    async def scan():
        async with factory() as db:
            return await api.scan(session['id'], body, scope, db)
    result = await asyncio.gather(scan(), scan())
    assert sum(v['duplicate'] for v in result) == 1
    assert result[-1]['counts'] == {'p': 1}


async def test_unreviewed_brands_not_exported_as_shortage_and_brand_close_stops_scan(real_context):
    factory, scope, _, _ = real_context
    async with factory() as db:
        session = PhysicalCountSession(id=uuid4(), user_id=scope[0].id, organization_profile_id=scope[1].id,
            mode='inventory', name='Тест', status='active', plan=[row(), {**row(), 'key': 'other', 'folder_name': 'Другой бренд'}], settings={'store_id': 'store'})
        db.add(session); await db.commit()
        await api.complete_brand(session.id, api.BrandRequest(brand='Бренд'), scope, db)
        report = await api.export(session.id, scope, db)
        lines = report.body.decode('utf-8-sig').splitlines()
        assert lines[1].endswith(';0;-2;Да')
        assert lines[2].endswith(';0;;Нет')
        with pytest.raises(HTTPException) as err:
            await api.scan(session.id, api.ScanRequest(code=GTIN, product_key='p', brand='Бренд'), scope, db)
        assert err.value.status_code == 409


async def test_revoked_mode_and_foreign_warehouse_block_every_report(real_context):
    factory, scope, _, doc = real_context
    async with factory() as db:
        session = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
    async with factory() as db:
        scope[3].allowed_modes = ['shipment']
        with pytest.raises(HTTPException) as err:
            await api.detail(session['id'], scope, db)
        assert err.value.status_code == 403
        scope[3].allowed_modes = ['acceptance']
        scope[2].store_ids = ['foreign']
        with pytest.raises(HTTPException) as err:
            await api.export(session['id'], scope, db)
        assert err.value.status_code == 404


async def test_worker_prepares_snapshot_once_and_sanitizes_external_errors(real_context, monkeypatch):
    from app.worker import tasks
    from app.db import session as db_module
    from app.services import physical_counts as service
    factory, scope, _, _ = real_context
    monkeypatch.setattr(db_module, 'AsyncSessionLocal', factory)
    monkeypatch.setattr(tsd, '_ms_for_user', AsyncMock(return_value=NS()))
    prepare = AsyncMock(return_value=[row()])
    monkeypatch.setattr(service, 'prepare_inventory_plan', prepare)
    async with factory() as db:
        session = PhysicalCountSession(id=uuid4(), user_id=scope[0].id, organization_profile_id=scope[1].id,
            mode='inventory', name='Тест', status='preparing', plan=[], settings={'store_id': 'store', 'include_state_ids': []})
        db.add(session); await db.commit()
    await asyncio.gather(tasks._prepare_physical_count_async(str(session.id)), tasks._prepare_physical_count_async(str(session.id)))
    prepare.assert_awaited_once()
    async with factory() as db:
        stored = await db.get(PhysicalCountSession, session.id)
        assert stored.status == 'active' and stored.plan == [row()]
        assert stored.settings['snapshot_at']
        stored.status = 'preparing'; await db.commit()
    prepare.side_effect = RuntimeError('sensitive remote error')
    await tasks._prepare_physical_count_async(str(session.id))
    async with factory() as db:
        stored = await db.get(PhysicalCountSession, session.id)
        assert stored.status == 'error' and 'sensitive' not in stored.error_message


async def test_http_routes_enforce_mode_and_keep_desktop_reports_available(real_context):
    from app.main import app
    from app.db.session import get_db
    from app.api.deps import get_current_user, get_active_organization_profile
    factory, scope, devices, doc = real_context
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[tsd.get_tsd_device] = lambda: devices[0]
    app.dependency_overrides[get_current_user] = lambda: scope[0]
    app.dependency_overrides[get_active_organization_profile] = lambda: scope[1]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/tsd/orders')).status_code == 403
            created = await client.post('/physical-counts', json={'mode': 'acceptance', 'document_id': str(doc.id)})
            assert created.status_code == 200
            sid = created.json()['id']
            assert (await client.get(f'/tsd/counts/{sid}')).status_code == 200
            devices[0].allowed_modes = []
            assert (await client.get(f'/tsd/counts/{sid}/export')).status_code == 403
            assert (await client.get(f'/physical-counts/{sid}/export')).status_code == 200
            assert (await client.post(f'/physical-counts/{sid}/scans', json={'code': GTIN})).status_code in (404, 405)
    finally:
        app.dependency_overrides.clear()


async def test_xml_keeps_unmatched_and_unmarked_lines_for_terminal_verification(real_context, monkeypatch):
    from app.api import acceptance
    from app.services.upd_parser import ParsedUpd, ParsedPosition
    factory, scope, _, doc = real_context
    monkeypatch.setattr(acceptance, 'parse_upd_503', lambda raw: ParsedUpd(positions=[
        ParsedPosition(name='Без марки и без МС', gtin=None, article='SKU1', quantity=2.5, line_number=1),
        ParsedPosition(name='Не сопоставлен', gtin=GTIN, article=None, quantity=3, line_number=2)]))
    monkeypatch.setattr(acceptance, '_maybe_ms_service', AsyncMock(return_value=None))
    monkeypatch.setattr(acceptance, '_maybe_cz_service', AsyncMock(return_value=None))
    monkeypatch.setattr(acceptance, '_resolve_product', AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(acceptance, '_enrich_unmatched_names_nk', AsyncMock())
    async with factory() as db:
        stored = await db.get(Document, doc.id)
        await acceptance._import_upd_bytes(doc.id, stored, b'fixture', scope[0], db)
        session = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
        detail = await api.detail(session['id'], scope, db)
        assert [v['product_name'] for v in detail['plan']] == ['Без марки и без МС', 'Не сопоставлен']
        assert [v['expected_qty'] for v in detail['plan']] == [2.5, 3]
        assert detail['counts'] == {}


async def test_updated_xml_starts_fresh_count_without_deleting_previous_observations(real_context):
    factory, scope, _, doc = real_context
    async with factory() as db:
        first = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
        await api.scan(first['id'], api.ScanRequest(code=GTIN), scope, db)
        stored = await db.get(Document, doc.id)
        stored.upd_meta = {'physical_plan': [{**row(), 'expected_qty': 5}]}
        await db.commit()
        second = await api.create_session(api.CreateRequest(mode='acceptance', document_id=doc.id), scope, db)
        assert first['id'] != second['id']
        assert (await api.detail(first['id'], scope, db))['status'] == 'superseded'
        assert (await api.detail(first['id'], scope, db))['counts'] == {'p': 1}
        assert (await api.detail(second['id'], scope, db))['counts'] == {}
