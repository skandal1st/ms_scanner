"""Shared PC/TSD correction workspace. Every request checks organization/warehouse scope."""
import copy
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import bearer, get_current_user, get_active_organization_profile, require_active_subscription
from app.api.documents import _get_ms_service, verify_document
from app.api.scans import ScanResponse, _create_scan_record
from app.db.models import Document, DocumentKind, DocumentStatus, Scan, TsdDocumentSession, User
from app.db.session import get_db
from app.core.security import decode_token
from app.services import shipment_corrections as service
from app.services.document_guard import editable_document, lock_ms_document, processing_lock

router = APIRouter(prefix='/shipment-corrections', tags=['shipment-corrections'])


@dataclass
class Scope:
    user: User
    profile_id: UUID
    stores: list
    device_id: UUID | None = None


async def scope(credentials: HTTPAuthorizationCredentials = Depends(bearer),
                profile_header: UUID | None = Header(default=None, alias='X-Organization-Profile'),
                db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(401, 'Сессия истекла. Войдите заново.') from exc
    if payload.get('type') == 'tsd_access':
        from app.api.tsd import get_tsd_device, require_tsd_mode, _device_scope
        device = await get_tsd_device(credentials, db)
        require_tsd_mode(device, 'shipment')
        user, workplace, profile = await _device_scope(db, device)
        return Scope(user, profile.id, workplace.store_ids or [], device.id)
    user = await get_current_user(credentials, db)
    await require_active_subscription(user, db)
    profile = await get_active_organization_profile(profile_header, user, db)
    return Scope(user, profile.id, [])


async def owned(db, access, document_id, *, revision=False, mutating=False, lock=True):
    query = select(Document).where(Document.id == document_id,
        Document.user_id == access.user.id, Document.organization_profile_id == access.profile_id,
        Document.kind == DocumentKind.demand)
    doc = (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if not doc or access.stores and doc.moysklad_store_id not in access.stores:
        raise HTTPException(404, 'Отгрузка недоступна на этом рабочем месте')
    if revision and not service.correction(doc):
        raise HTTPException(409, 'Сначала откройте исправление отгрузки')
    if (doc.upd_meta or {}).get('superseded_by_document_id'):
        raise HTTPException(409, 'Эта версия уже заменена. Откройте актуальное исправление из списка отгрузок.')
    if mutating:
        from app.services.shipment_resend import assert_idle
        await assert_idle(db, doc)
    return doc


def bad_request(exc):
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(409, str(exc)) if isinstance(exc, ValueError) else HTTPException(502, 'Не удалось загрузить или сверить отгрузку в МойСкладе. Повторите попытку.')


@router.get('/{source_id}/resend-marks')
async def resend_status(source_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    from app.services import shipment_resend as resend
    try:
        source = await resend.resolve_source(db, await owned(db, access, source_id, lock=False), check_edits=False)
        return resend.public_state(source)
    except Exception as exc:
        raise bad_request(exc) from exc


@router.post('/{source_id}/resend-marks')
async def resend_marks(source_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone, timedelta
    from uuid import uuid4
    from app.services import shipment_resend as resend
    from app.worker.tasks import resend_shipment_marks_task
    try:
        source = await resend.resolve_source(db, await owned(db, access, source_id, lock=False))
        state = copy.deepcopy((source.upd_meta or {}).get(resend.MARKER) or {})
        async with processing_lock(f'ms:{source.user_id}:demand:{source.moysklad_id}') as acquired:
            if not acquired:
                if state.get('status') in resend.ACTIVE:
                    return resend.public_state(source)
                raise ValueError('Отгрузка занята другой операцией. Повторите позже.')
            # Lock only after acquiring the MS lock, matching the worker's lock order.
            source = (await db.execute(select(Document).where(Document.id == source.id)
                .with_for_update().execution_options(populate_existing=True))).scalar_one()
            if not resend.available(source):
                raise ValueError('Версия отгрузки изменилась. Откройте актуальную сборку.')
            state = copy.deepcopy((source.upd_meta or {}).get(resend.MARKER) or {})
            updated = datetime.fromisoformat(state['updated_at']) if state.get('updated_at') else None
            if state.get('status') == 'queued' and updated and updated > datetime.now(timezone.utc) - timedelta(minutes=15):
                return resend.public_state(source)
            job = {k: v for k, v in state.items() if k == 'in_flight'}
            job.update(id=str(uuid4()), status='queued', expected=0, already=0, added=0, confirmed=0, error=None)
            if source.status != DocumentStatus.accepted:
                from app.db.models import ScanStatus
                scans = (await db.execute(select(Scan).where(Scan.document_id == source.id))).scalars().all()
                if (not any(s.status in {ScanStatus.valid, ScanStatus.overflow} for s in scans)
                        or any(s.status in {ScanStatus.scanned, ScanStatus.pending, ScanStatus.unknown_product} for s in scans)):
                    raise ValueError('Перед повторной отправкой дождитесь проверки марок и сопоставьте неизвестные товары.')
                job['retry_failed'] = True
                source.status = DocumentStatus.processing
                source.error_message = None
            await resend.save(db, source, job)
        # Release the MS lock before dispatch: a fast worker must be able to acquire it.
        try:
            resend_shipment_marks_task.delay(str(source.id), str(source.user_id), job['id'])
        except Exception as exc:
            job.update(status='error', error='Не удалось поставить повторную отправку в очередь. Повторите попытку.')
            if job.get('retry_failed'):
                source.status = DocumentStatus.draft
                source.error_message = job['error']
            await resend.save(db, source, job)
            raise HTTPException(503, job['error']) from exc
        return resend.public_state(source)
    except Exception as exc:
        raise bad_request(exc) from exc


@router.get('')
async def list_sources(access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    query = select(Document.id, Document.moysklad_id, Document.name, Document.moysklad_name,
        Document.customer_order_name, Document.agent_name, Document.status,
        Document.upd_meta[service.MARKER].astext.isnot(None).label('is_correction')).where(Document.user_id == access.user.id,
        Document.organization_profile_id == access.profile_id, Document.kind == DocumentKind.demand,
        Document.moysklad_id.isnot(None),
        Document.upd_meta['superseded_by_document_id'].astext.is_(None),
        (Document.status == DocumentStatus.accepted) | Document.upd_meta[service.MARKER].astext.isnot(None),
    ).order_by(Document.created_at.desc())
    if access.stores:
        query = query.where(Document.moysklad_store_id.in_(access.stores))
    rows = (await db.execute(query.limit(500))).all()
    seen, result = set(), []
    from app.services.shipment_labels import shipment_display_name
    for doc in rows:
        if doc.moysklad_id in seen:
            continue
        seen.add(doc.moysklad_id)
        result.append({'id': str(doc.id), 'name': shipment_display_name(doc),
                       'resume': bool(doc.is_correction and doc.status != DocumentStatus.accepted)})
    return result


@router.post('/{source_id}/open')
async def open_correction(source_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    source = await owned(db, access, source_id, mutating=True)
    if service.correction(source) and source.status != DocumentStatus.accepted:
        return {'id': str(source.id)}
    if source.status != DocumentStatus.accepted:
        raise HTTPException(409, 'Исправление доступно только для отправленной отгрузки')
    await lock_ms_document(db, access.user.id, 'demand', source.moysklad_id)
    siblings = (await db.execute(select(Document).where(Document.user_id == access.user.id,
        Document.moysklad_id == source.moysklad_id, Document.kind == DocumentKind.demand,
        Document.status != DocumentStatus.accepted).with_for_update()
        .execution_options(populate_existing=True))).scalars().all()
    empty_previews = []
    for sibling in siblings:
        if (sibling.upd_meta or {}).get('superseded_by_document_id'):
            continue
        if service.correction(sibling):
            await owned(db, access, sibling.id, revision=True)
            return {'id': str(sibling.id)}
        # Opening a shipment for preview creates a draft, but does not start picking.
        # Hold its row lock until replacement commits, so a concurrent scan cannot
        # turn an empty preview into a real assembly while we read the MS baseline.
        if sibling.status != DocumentStatus.draft:
            raise HTTPException(409, 'Для этой отгрузки уже начата сборка или выполняется отправка. Завершите её перед исправлением.')
        has_scans = (await db.execute(select(Scan.id).where(
            Scan.document_id == sibling.id).limit(1))).scalar_one_or_none()
        if has_scans is not None:
            raise HTTPException(409, 'Для этой отгрузки есть незавершённая сборка с марками. Завершите её перед исправлением.')
        active_session = (await db.execute(select(TsdDocumentSession.id).where(
            TsdDocumentSession.document_id == sibling.id,
            TsdDocumentSession.status == 'active').limit(1))).scalar_one_or_none()
        if active_session is not None:
            raise HTTPException(409, 'Эта отгрузка открыта на ТСД. Завершите сборку или уберите её из работы на ТСД перед исправлением.')
        marker = (sibling.upd_meta or {}).get('collection_start')
        if marker and marker != (source.upd_meta or {}).get('collection_start'):
            raise HTTPException(409, 'Для этой отгрузки уже начата сборка. Завершите её перед исправлением.')
        empty_previews.append(sibling)
    try:
        async with processing_lock(f'ms:{access.user.id}:demand:{source.moysklad_id}') as acquired:
            if not acquired:
                raise HTTPException(409, 'Отгрузка занята другой операцией')
            doc = await service.start(db, source, await _get_ms_service(access.user, db),
                                      str(access.device_id or access.user.id), commit=False)
            for preview in empty_previews:
                preview.upd_meta = {**(preview.upd_meta or {}), 'superseded_by_document_id': str(doc.id)}
            await db.commit()
            return {'id': str(doc.id)}
    except Exception as exc:
        await db.rollback()
        raise bad_request(exc) from exc


@router.get('/{document_id}')
async def detail(document_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True)
    state = service.correction(doc)
    scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id).order_by(Scan.scanned_at.desc()))).scalars().all()
    return {'id': str(doc.id), 'name': doc.name, 'status': doc.status.value, 'error_message': doc.error_message,
        'locked': bool(state.get('job')), 'processing_progress': doc.processing_progress,
        'baseline_mark_count': len(state['baseline_scans']),
        'positions': [{'id': pid, 'name': row['product_name'], 'quantity': row['fields'].get('quantity', 0)} for pid, row in state['baseline'].items() if row['product_id']],
        'scans': [{**ScanResponse.model_validate(scan).model_dump(mode='json'),
            'position_id': (state['baseline_scans'].get(str(scan.id)) or {}).get('position_id') or state['add_positions'].get(str(scan.id)),
            'existing': str(scan.id) in state['baseline_scans'],
            'package': bool((state['baseline_scans'].get(str(scan.id), {}).get('code') or {}).get('trackingCodes')) or scan.is_box} for scan in scans],
        'delta': state.get('job', {}).get('delta'),
    }


class AddCode(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    position_id: UUID | None = None
    replace_scan_id: UUID | None = None


@router.post('/{document_id}/scans')
async def add_code(document_id: UUID, body: AddCode, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    await editable_document(db, doc.id, access.user.id)
    state = copy.deepcopy(service.correction(doc))
    from app.services.chestnyznak import strip_ai_brackets, extract_gtin, normalize_gtin_key
    from app.services.scan_input import decode_base64_mark
    decoded = decode_base64_mark(body.code)
    raw = strip_ai_brackets(decoded or body.code).strip()
    pid = str(body.position_id) if body.position_id else None
    if pid is None:
        gtin = normalize_gtin_key(extract_gtin(raw))
        products = {item.get('product_id') for item in (doc.plan or []) if gtin and gtin in {
            normalize_gtin_key(g) for g in [item.get('gtin'), *(item.get('gtins') or [])]}}
        candidates = [key for key, item in state['baseline'].items() if item['product_id'] in products]
        if len(candidates) != 1:
            raise HTTPException(400, 'Не удалось однозначно выбрать позицию по GTIN. Выберите товар вручную в составе отгрузки.')
        pid = candidates[0]
    row = state['baseline'].get(pid)
    if not row or not row['product_id']:
        raise HTTPException(400, 'Выберите существующую позицию отгрузки')
    replaced_scan = None
    if body.replace_scan_id:
        replaced_scan = (await db.execute(select(Scan).where(
            Scan.id == body.replace_scan_id, Scan.document_id == doc.id))).scalar_one_or_none()
        if not replaced_scan:
            raise HTTPException(404, 'Заменяемая марка не найдена. Обновите список марок.')
        saved = state['baseline_scans'].get(str(replaced_scan.id)) or {}
        original_position = saved.get('position_id') or state['add_positions'].get(str(replaced_scan.id))
        if original_position != pid:
            raise HTTPException(400, 'Заменяющая марка должна остаться в той же позиции отгрузки')
        if replaced_scan.is_box or (saved.get('code') or {}).get('trackingCodes') or (
                saved and saved['code'].get('type') != 'trackingcode'):
            raise HTTPException(400, 'Заменяйте отдельные марки. Состав упаковок исправляется в МойСкладе.')
    if not raw.startswith('01') and not (len(raw) == 29 and raw[:14].isdigit()):
        raise HTTPException(400, 'Добавляйте отдельную марку товара. Состав упаковок исправляется в МойСкладе.')
    candidate = service.MoySkladService('')._tracking_code_entry({'code': raw}, row['tracking_type'])
    current_scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
    for current in current_scans:
        saved = state['baseline_scans'].get(str(current.id))
        current_pos = saved['position_id'] if saved else state['add_positions'].get(str(current.id))
        entry = saved['code'] if saved else service.MoySkladService('')._tracking_code_entry(
            {'code': current.code}, state['baseline'].get(current_pos, row)['tracking_type'])
        if service.code_key(entry) == service.code_key(candidate):
            return {'duplicate': True, 'id': str(current.id)}
    scan_input = body.code if decoded else raw
    scan, duplicate = await _create_scan_record(db, doc.id, scan_input, access.user.id, row['product_id'], device_id=access.device_id)
    await db.refresh(doc)
    # Core scanning commits before returning; preserve current state under a fresh row lock.
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    state = copy.deepcopy(service.correction(doc))
    if state.get('job'):
        raise HTTPException(409, 'Сохранение исправления уже началось')
    if str(scan.id) not in state['baseline_scans'] and str(scan.id) not in state['add_positions']:
        state['add_positions'][str(scan.id)] = pid
    # Keep the original when the replacement is invalid or belongs elsewhere.
    # Both the new binding and removal are committed together, before any MS write.
    replaced = False
    if replaced_scan and not duplicate and scan.status.value in ('scanned', 'valid', 'overflow'):
        original = (await db.execute(select(Scan).where(
            Scan.id == body.replace_scan_id, Scan.document_id == doc.id))).scalar_one_or_none()
        if original:
            await db.delete(original)
            replaced = True
    await service.save_state(db, doc, state)
    return {'duplicate': duplicate, 'id': str(scan.id), 'replaced': replaced}


@router.delete('/{document_id}/scans/{scan_id}')
async def remove_code(document_id: UUID, scan_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    await editable_document(db, doc.id, access.user.id)
    scan = (await db.execute(select(Scan).where(Scan.id == scan_id, Scan.document_id == doc.id))).scalar_one_or_none()
    if not scan:
        raise HTTPException(404, 'Марка не найдена')
    await db.delete(scan)
    await db.commit()
    return {'status': 'removed'}


@router.post('/{document_id}/verify')
async def verify(document_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    await owned(db, access, document_id, revision=True, mutating=True)
    return await verify_document(document_id, access.user, db)


class PreviewRequest(BaseModel):
    adjust_quantities: Literal[False] = False
    preview_hash: str | None = None


@router.post('/{document_id}/preview')
async def preview(document_id: UUID, body: PreviewRequest, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    state = service.correction(doc)
    if state.get('job'):
        return state['job']['delta']
    scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
    try:
        return service.build_delta(doc, scans, body.adjust_quantities)
    except Exception as exc:
        raise bad_request(exc) from exc


@router.post('/{document_id}/save')
async def save(document_id: UUID, body: PreviewRequest, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    if doc.status != DocumentStatus.draft:
        raise HTTPException(409, 'Исправление уже сохраняется или завершено')
    state = copy.deepcopy(service.correction(doc))
    if state.get('job') and any(op['action'] == 'quantity' for op in state['job']['delta']['operations']):
        raise HTTPException(409, 'Старая версия исправления меняет количество товаров. Обновите состав из МС и просмотрите изменения заново.')
    if not state.get('job'):
        scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
        try:
            delta = service.build_delta(doc, scans, body.adjust_quantities)
        except Exception as exc:
            raise bad_request(exc) from exc
        if not delta['operations']:
            raise HTTPException(409, 'Нет изменений для сохранения')
        if body.preview_hash != delta['preview_hash']:
            raise HTTPException(409, 'Состав исправления изменился. Просмотрите изменения перед сохранением заново.')
        state['job'] = {'delta': delta, 'expected': state['baseline'], 'completed': 0}
    elif body.preview_hash != state['job']['delta']['preview_hash']:
        raise HTTPException(409, 'Сначала просмотрите изменения этой версии исправления.')
    doc.status = DocumentStatus.processing
    doc.error_message = None
    await service.save_state(db, doc, state)
    from app.worker.tasks import correct_shipment_task
    try:
        correct_shipment_task.delay(str(doc.id), str(access.user.id))
    except Exception as exc:
        doc.status = DocumentStatus.draft
        doc.error_message = 'Не удалось поставить исправление в очередь. Повторите сохранение.'
        await db.commit()
        raise HTTPException(503, doc.error_message) from exc
    return {'status': 'processing'}


@router.post('/{document_id}/cancel')
async def cancel(document_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    state = service.correction(doc)
    job = state.get('job') or {}
    if doc.status != DocumentStatus.draft or job.get('completed') or job.get('in_flight') is not None:
        raise HTTPException(409, 'Часть исправлений могла уйти в МС. Сначала завершите сохранение или сверку.')
    doc.status = DocumentStatus.accepted
    doc.upd_meta = {**doc.upd_meta, 'superseded_by_document_id': state['source_id']}
    await db.commit()
    return {'status': 'cancelled'}


@router.post('/{document_id}/rebase')
async def rebase(document_id: UUID, access: Scope = Depends(scope), db: AsyncSession = Depends(get_db)):
    doc = await owned(db, access, document_id, revision=True, mutating=True)
    if doc.status != DocumentStatus.draft or not service.correction(doc).get('job'):
        raise HTTPException(409, 'Обновление состава доступно после остановки сохранения исправлений.')
    await lock_ms_document(db, access.user.id, 'demand', doc.moysklad_id)
    try:
        async with processing_lock(f'process:{document_id}') as own:
            if not own:
                raise HTTPException(409, 'Сохранение ещё выполняется. Повторите сверку после его завершения.')
            async with processing_lock(f'ms:{access.user.id}:demand:{doc.moysklad_id}') as acquired:
                if not acquired:
                    raise HTTPException(409, 'Отгрузка занята другой операцией. Повторите сверку.')
                fresh = await service.rebase(db, doc, await _get_ms_service(access.user, db), str(access.device_id or access.user.id))
                return {'id': str(fresh.id)}
    except Exception as exc:
        await db.rollback()
        raise bad_request(exc) from exc
