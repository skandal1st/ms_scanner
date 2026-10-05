"""Physical counts are observations, independent of XML marks and MS stock writes."""
import csv
import io
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.api.deps import get_current_user, get_active_organization_profile
from app.api.tsd import get_tsd_device, _device_scope, _ms_for_user, require_tsd_mode
from app.db.session import get_db
from app.db.models import (Document, DocumentKind, OrganizationProfile, PhysicalCountQuantity as Quantity,
                           PhysicalCountSession as Session, PhysicalCountScan as Scan)
from app.services.physical_counts import acceptance_plan, identify_count_scan

router = APIRouter(tags=['physical-counts'])


async def terminal_scope(device=Depends(get_tsd_device), db=Depends(get_db)):
    user, workplace, profile = await _device_scope(db, device)
    return user, profile, workplace, device


async def desktop_scope(user=Depends(get_current_user), profile=Depends(get_active_organization_profile)):
    return user, profile, None, None


def authorize_mode(scope, mode):
    if mode not in ('acceptance', 'inventory'):
        raise HTTPException(400, 'Неизвестный режим сверки')
    if scope[3]:
        require_tsd_mode(scope[3], mode)


def accessible(session, scope):
    user, profile, workplace, device = scope
    if session.user_id != user.id or session.organization_profile_id != profile.id:
        return False
    store = (session.settings or {}).get('store_id')
    if workplace:
        if workplace.store_ids and (not store or store not in workplace.store_ids):
            return False
        if not store and session.workplace_id and session.workplace_id != workplace.id:
            return False
    return True


async def owned(db, session_id, scope, lock=False):
    query = select(Session).where(Session.id == session_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    session = (await db.execute(query)).scalar_one_or_none()
    if not session or not accessible(session, scope):
        raise HTTPException(404, 'Сессия сверки не найдена')
    authorize_mode(scope, session.mode)
    return session


def summary(session):
    return dict(id=str(session.id), mode=session.mode, name=session.name, status=session.status,
                document_id=str(session.document_id) if session.document_id else None,
                created_at=session.created_at, completed_at=session.completed_at,
                settings=session.settings or {}, error_message=session.error_message)


async def progress(db, session):
    if (session.settings or {}).get('count_method') == 'quantity':
        entries = (await db.execute(select(Quantity).where(Quantity.session_id == session.id)
            .distinct(Quantity.product_key).order_by(Quantity.product_key, Quantity.revision.desc()))).scalars().all()
        return {**summary(session), 'counts': {v.product_key: float(v.quantity) for v in entries},
                'revisions': {v.product_key: v.revision for v in entries}, 'scans': []}
    totals = (await db.execute(select(Scan.product_key, func.sum(Scan.quantity)).where(
        Scan.session_id == session.id).group_by(Scan.product_key))).all()
    recent = (await db.execute(select(Scan).where(Scan.session_id == session.id).order_by(
        Scan.created_at.desc(), Scan.id.desc()).limit(50))).scalars().all()
    return {**summary(session), 'counts': {key: float(qty) for key, qty in totals},
            'scans': [dict(id=str(v.id), code=v.code, product_key=v.product_key,
                           quantity=float(v.quantity), device_id=str(v.device_id)) for v in recent]}


class CreateRequest(BaseModel):
    mode: Literal['acceptance', 'inventory']
    document_id: UUID | None = None
    store_id: UUID | None = None
    include_state_ids: list[UUID] | None = Field(default=None, max_length=100)
    count_method: Literal['scan', 'quantity'] = 'scan'


class ScanRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    product_key: str | None = Field(default=None, max_length=128)
    quantity: Decimal = Field(default=1, gt=0, le=1000000, decimal_places=3)
    brand: str | None = Field(default=None, max_length=1000)
    request_id: UUID = Field(default_factory=uuid4)


class BrandRequest(BaseModel):
    brand: str = Field(min_length=1, max_length=1000)


class InventorySettingsRequest(BaseModel):
    include_state_ids: list[UUID] = Field(default_factory=list, max_length=100)


class QuantityRequest(BaseModel):
    product_key: str = Field(min_length=1, max_length=128)
    quantity: Decimal = Field(ge=0, le=1000000, decimal_places=3)
    revision: int = Field(default=0, ge=0)
    request_id: UUID


async def list_sessions(mode: Literal['acceptance', 'inventory'], document_id: UUID | None = None,
                        scope=Depends(terminal_scope), db=Depends(get_db)):
    authorize_mode(scope, mode)
    query = select(Session).where(Session.user_id == scope[0].id,
        Session.organization_profile_id == scope[1].id, Session.mode == mode)
    if document_id:
        query = query.where(Session.document_id == document_id)
    sessions = (await db.execute(query.order_by(Session.created_at.desc()).limit(200))).scalars().all()
    return [summary(v) for v in sessions if accessible(v, scope)]


async def options(scope=Depends(terminal_scope), db=Depends(get_db)):
    authorize_mode(scope, 'inventory')
    ms = await _ms_for_user(db, scope[0].id)
    try:
        stores = await ms.get_stores()
        if scope[2] and scope[2].store_ids:
            stores = [v for v in stores if v['id'] in scope[2].store_ids]
        async with httpx.AsyncClient(timeout=30) as client:
            response = await ms._request_with_retry(client, 'GET', f'{ms.base_url}/entity/demand/metadata')
            response.raise_for_status()
            states = response.json().get('states', [])
        return {'stores': stores, 'states': [{'id': v['id'], 'name': v['name']} for v in states],
                'default_include_state_ids': getattr(scope[1], 'inventory_include_state_ids', None) or []}
    except httpx.HTTPError as exc:
        raise HTTPException(502, 'Не удалось загрузить склады и статусы отгрузок из МойСклада') from exc


@router.get('/tsd/acceptances')
async def acceptances(scope=Depends(terminal_scope), db=Depends(get_db)):
    authorize_mode(scope, 'acceptance')
    query = select(Document).where(Document.user_id == scope[0].id,
        Document.organization_profile_id == scope[1].id, Document.kind == DocumentKind.supply)
    docs = (await db.execute(query.order_by(Document.created_at.desc()).limit(200))).scalars().all()
    prepared = (await db.execute(select(Session).where(Session.user_id == scope[0].id,
        Session.organization_profile_id == scope[1].id, Session.mode == 'acceptance', Session.status == 'active'))).scalars().all()
    available_docs = {v.document_id for v in prepared if accessible(v, scope)}
    return [dict(id=str(v.id), name=v.name, created_at=v.created_at,
        positions=len((v.upd_meta or {}).get('physical_plan') or v.plan)) for v in docs
        if ((v.upd_meta or {}).get('physical_plan') or v.plan) and (not scope[2].store_ids or
            v.moysklad_store_id in scope[2].store_ids or v.id in available_docs)]


async def create_session(body: CreateRequest, scope=Depends(terminal_scope), db=Depends(get_db)):
    authorize_mode(scope, body.mode)
    if body.mode != 'inventory' and body.count_method == 'quantity':
        raise HTTPException(400, 'Ввод фактического остатка доступен в инвентаризации')
    user, profile, workplace, _ = scope
    if body.mode == 'acceptance':
        # Document lock also serializes concurrent creation of a shared verification session.
        doc = (await db.execute(select(Document).where(Document.id == body.document_id,
            Document.user_id == user.id, Document.organization_profile_id == profile.id,
            Document.kind == DocumentKind.supply).with_for_update())).scalar_one_or_none()
        if not doc or not ((doc.upd_meta or {}).get('physical_plan') or doc.plan):
            raise HTTPException(404, 'Загрузите XML приёмки на ПК в этом юрлице и для склада ТСД')
        previous = (await db.execute(select(Session).where(Session.document_id == doc.id,
            Session.mode == 'acceptance', Session.status == 'active'))).scalars().all()
        existing = next((v for v in previous if accessible(v, scope)), None)
        plan = acceptance_plan((doc.upd_meta or {}).get('physical_plan') or doc.plan)
        if existing and existing.plan == plan:
            return summary(existing)
        if existing:
            existing.status = 'superseded'
            existing.completed_at = datetime.now(timezone.utc)
            existing.error_message = 'XML или план приёмки изменился. Создана новая сверка; предыдущие наблюдения сохранены в истории.'
        selected_store = str(body.store_id) if body.store_id else doc.moysklad_store_id or (
            (existing.settings or {}).get('store_id') if existing else None)
        if doc.moysklad_store_id and selected_store != doc.moysklad_store_id:
            raise HTTPException(400, 'Склад сверки должен совпадать со складом поступления')
        if workplace and workplace.store_ids and selected_store not in workplace.store_ids:
            raise HTTPException(403, 'Склад приёмки недоступен этому ТСД. Подготовьте сверку с выбранным складом на ПК.')
        if body.store_id:
            ms = await _ms_for_user(db, user.id)
            if selected_store not in {v['id'] for v in await ms.get_stores()}:
                raise HTTPException(400, 'Склад недоступен')
        session = Session(user_id=user.id, organization_profile_id=profile.id,
            workplace_id=workplace.id if workplace else None, document_id=doc.id, mode='acceptance',
            name=f'Сверка приёмки {doc.name}', status='active', plan=plan,
            settings={'store_id': selected_store, 'snapshot_at': datetime.now(timezone.utc).isoformat()})
    else:
        if not body.store_id:
            raise HTTPException(400, 'Выберите склад')
        available = await options(scope, db)
        store = next((v for v in available['stores'] if v['id'] == str(body.store_id)), None)
        states = list(dict.fromkeys(str(v) for v in (body.include_state_ids if body.include_state_ids is not None
            else available['default_include_state_ids'])))
        if not store or not set(states).issubset({v['id'] for v in available['states']}):
            raise HTTPException(400, 'Склад или статусы отгрузок недоступны')
        session = Session(user_id=user.id, organization_profile_id=profile.id,
            workplace_id=workplace.id if workplace else None, mode='inventory',
            name=f'Инвентаризация · {store["name"]}', status='preparing', plan=[],
            settings={'store_id': str(body.store_id), 'store_name': store['name'],
                'include_state_ids': states, 'all_organizations': True,
                'count_method': body.count_method,
                'included_states': [v for v in available['states'] if v['id'] in states]})
    db.add(session)
    await db.commit()
    await db.refresh(session)
    if session.mode == 'inventory':
        from app.worker.tasks import prepare_physical_count_task
        try:
            prepare_physical_count_task.delay(str(session.id))
        except Exception:
            session.status, session.error_message = 'error', 'Не удалось запустить выгрузку. Создайте новую сессию.'
            await db.commit()
    return summary(session)


async def detail(session_id: UUID, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope)
    return {**await progress(db, session), 'plan': session.plan}


async def get_progress(session_id: UUID, scope=Depends(terminal_scope), db=Depends(get_db)):
    return await progress(db, await owned(db, session_id, scope))


@router.put('/physical-counts/settings')
async def save_inventory_settings(body: InventorySettingsRequest, scope=Depends(desktop_scope), db=Depends(get_db)):
    available = await options(scope, db)
    selected = list(dict.fromkeys(str(v) for v in body.include_state_ids))
    if not set(selected).issubset({v['id'] for v in available['states']}):
        raise HTTPException(400, 'Выбранный статус отгрузки недоступен. Обновите настройки.')
    profile = (await db.execute(select(OrganizationProfile).where(OrganizationProfile.id == scope[1].id,
        OrganizationProfile.user_id == scope[0].id).with_for_update())).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, 'Юрлицо не найдено')
    profile.inventory_include_state_ids = selected
    await db.commit()
    return {'include_state_ids': selected}


async def set_quantity(session_id: UUID, body: QuantityRequest, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope, lock=True)
    if session.mode != 'inventory' or session.settings.get('count_method') != 'quantity' or session.status != 'active':
        raise HTTPException(409, 'Сессия не открыта для ввода фактического количества')
    row = next((v for v in session.plan if v['key'] == body.product_key), None)
    if not row:
        raise HTTPException(400, 'Позиция отсутствует в плане инвентаризации')
    previous = (await db.execute(select(Quantity).where(Quantity.session_id == session.id,
        Quantity.request_id == body.request_id))).scalar_one_or_none()
    if previous:
        if previous.product_key != body.product_key or previous.quantity != body.quantity:
            raise HTTPException(409, 'Идентификатор запроса уже использован для другого количества')
        return await progress(db, session)
    if row['folder_name'] in session.settings.get('reviewed_brands', []):
        raise HTTPException(409, 'Сверка этого бренда уже завершена')
    revision = (await db.execute(select(func.max(Quantity.revision)).where(Quantity.session_id == session.id,
        Quantity.product_key == body.product_key))).scalar_one_or_none() or 0
    if revision != body.revision:
        raise HTTPException(409, 'Количество уже изменено другим пользователем. Обновите позицию и проверьте фактический остаток.')
    db.add(Quantity(session_id=session.id, device_id=scope[3].id if scope[3] else None,
        product_key=body.product_key, quantity=body.quantity, revision=revision + 1, request_id=body.request_id))
    await db.flush()
    result = await progress(db, session)
    await db.commit()
    return result


router.add_api_route('/tsd/counts/{session_id}/quantity', set_quantity, methods=['PUT'])


@router.put('/physical-counts/{session_id}/quantity')
async def desktop_quantity(session_id: UUID, body: QuantityRequest, scope=Depends(desktop_scope), db=Depends(get_db)):
    return await set_quantity(session_id, body, scope, db)


@router.post('/tsd/counts/{session_id}/scans')
async def scan(session_id: UUID, body: ScanRequest, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope, lock=True)
    if session.status != 'active':
        raise HTTPException(409, 'Сессия сверки не открыта для сканирования')
    if (session.settings or {}).get('count_method') == 'quantity':
        raise HTTPException(409, 'Эта инвентаризация проводится вводом фактического количества, без сканирования')
    row, units, _, marked, code_hash = identify_count_scan(session.plan, body.code, body.product_key, body.quantity)
    if session.mode == 'inventory' and body.brand != row['folder_name']:
        raise HTTPException(400, 'Код относится к другому бренду. Выберите нужный бренд.')
    if row['folder_name'] in (session.settings or {}).get('reviewed_brands', []):
        raise HTTPException(409, 'Сверка этого бренда уже завершена')
    if not marked:
        code_hash = hashlib.sha256(f'barcode:{scope[3].id}:{body.request_id}'.encode()).hexdigest()
    duplicate = (await db.execute(select(Scan.id).where(Scan.session_id == session.id,
        Scan.code_hash == code_hash))).scalar_one_or_none() is not None
    if not duplicate:
        # Ordinary barcodes/manual entries may repeat; each is a distinct physical observation.
        db.add(Scan(session_id=session.id, device_id=scope[3].id, product_key=row['key'],
            code=body.code.strip(), code_hash=code_hash,
            quantity=units, is_barcode=not marked))
        await db.flush()
    result = {**await progress(db, session), 'duplicate': bool(duplicate), 'product_key': row['key']}
    await db.commit()
    return result


@router.delete('/tsd/counts/{session_id}/scans/{scan_id}')
async def remove_scan(session_id: UUID, scan_id: UUID, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope, lock=True)
    if session.status != 'active':
        raise HTTPException(409, 'Сессия сверки завершена')
    observation = (await db.execute(select(Scan).where(Scan.id == scan_id, Scan.session_id == session.id,
        Scan.device_id == scope[3].id))).scalar_one_or_none()
    if not observation:
        raise HTTPException(404, 'Можно удалить только свой скан')
    row = next(v for v in session.plan if v['key'] == observation.product_key)
    if row['folder_name'] in (session.settings or {}).get('reviewed_brands', []):
        raise HTTPException(409, 'Сверка бренда завершена')
    await db.delete(observation)
    await db.flush()
    result = await progress(db, session)
    await db.commit()
    return result


@router.post('/tsd/counts/{session_id}/brands/complete')
async def complete_brand(session_id: UUID, body: BrandRequest, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope, lock=True)
    if session.mode != 'inventory' or session.status != 'active' or not any(v['folder_name'] == body.brand for v in session.plan):
        raise HTTPException(400, 'Бренд недоступен для завершения сверки')
    if session.settings.get('count_method') == 'quantity':
        data = await progress(db, session)
        missing = [v for v in session.plan if v['folder_name'] == body.brand and v['key'] not in data['counts']]
        if missing:
            raise HTTPException(409, f'Введите остаток для всех позиций бренда ({len(missing)} не заполнено). Для отсутствующих товаров укажите 0.')
    session.settings = {**session.settings, 'reviewed_brands': list(dict.fromkeys([
        *session.settings.get('reviewed_brands', []), body.brand]))}
    await db.commit()
    return await progress(db, session)


@router.post('/tsd/counts/{session_id}/complete')
async def complete(session_id: UUID, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope, lock=True)
    if session.status != 'active':
        raise HTTPException(409, 'Сессия сверки не активна')
    if session.mode == 'inventory' and not session.settings.get('reviewed_brands'):
        raise HTTPException(400, 'Сначала завершите сверку хотя бы одного бренда')
    session.status, session.completed_at = 'completed', datetime.now(timezone.utc)
    await db.commit()
    return summary(session)


async def export(session_id: UUID, scope=Depends(terminal_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope)
    data = await progress(db, session)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Бренд', 'Товар', 'GTIN', 'Остаток МС', 'В отгрузках выбранных статусов',
                     'Ожидалось', 'Посчитано', 'Разница', 'Проверен'])
    def safe(value):
        text = str(value or '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
    for row in session.plan:
        manual = session.settings.get('count_method') == 'quantity'
        entered = row['key'] in data['counts']
        counted = data['counts'].get(row['key'], 0)
        reviewed = (session.mode == 'acceptance' and session.status == 'completed') or row['folder_name'] in session.settings.get('reviewed_brands', [])
        if manual:
            reviewed = entered
        writer.writerow([safe(row['folder_name']), safe(row.get('product_name')), safe(row.get('gtin') or ', '.join(row.get('gtins', []))),
            row['base_qty'], row['shipment_qty'], row['expected_qty'], counted if not manual or entered else '',
            counted - row['expected_qty'] if reviewed else '', 'Да' if reviewed else 'Нет'])
    return Response(output.getvalue().encode('utf-8-sig'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="count-{session.id}.csv"'})


for path, endpoint, method in [('', list_sessions, 'GET'), ('', create_session, 'POST'),
    ('/options', options, 'GET'), ('/{session_id}', detail, 'GET'),
    ('/{session_id}/progress', get_progress, 'GET'), ('/{session_id}/export', export, 'GET')]:
    router.add_api_route('/tsd/counts' + path, endpoint, methods=[method])


@router.get('/physical-counts')
async def desktop_list(mode: Literal['acceptance', 'inventory'], document_id: UUID | None = None,
                       scope=Depends(desktop_scope), db=Depends(get_db)):
    return await list_sessions(mode, document_id, scope, db)


@router.post('/physical-counts')
async def desktop_create(body: CreateRequest, scope=Depends(desktop_scope), db=Depends(get_db)):
    return await create_session(body, scope, db)


@router.get('/physical-counts/options')
async def desktop_options(scope=Depends(desktop_scope), db=Depends(get_db)):
    return await options(scope, db)


@router.get('/physical-counts/{session_id}')
async def desktop_detail(session_id: UUID, scope=Depends(desktop_scope), db=Depends(get_db)):
    return await detail(session_id, scope, db)


@router.get('/physical-counts/{session_id}/progress')
async def desktop_progress(session_id: UUID, scope=Depends(desktop_scope), db=Depends(get_db)):
    return await get_progress(session_id, scope, db)


@router.get('/physical-counts/{session_id}/export')
async def desktop_export(session_id: UUID, scope=Depends(desktop_scope), db=Depends(get_db)):
    return await export(session_id, scope, db)


@router.post('/physical-counts/{session_id}/brands/complete')
async def desktop_brand(session_id: UUID, body: BrandRequest, scope=Depends(desktop_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope)
    if session.settings.get('count_method') != 'quantity':
        raise HTTPException(409, 'Завершение сканируемой сверки выполняется на ТСД')
    return await complete_brand(session_id, body, scope, db)


@router.post('/physical-counts/{session_id}/complete')
async def desktop_complete(session_id: UUID, scope=Depends(desktop_scope), db=Depends(get_db)):
    session = await owned(db, session_id, scope)
    if session.settings.get('count_method') != 'quantity':
        raise HTTPException(409, 'Завершение сканируемой сверки выполняется на ТСД')
    return await complete(session_id, scope, db)
