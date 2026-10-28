"""Add only missing marks to existing shipment positions; never PUT financial fields."""
import copy
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services import shipment_corrections as corrections
from app.services.moysklad import MoySkladService

MARKER = 'mark_resend'
ACTIVE = {'queued', 'running'}
PUBLIC_FIELDS = {'id', 'status', 'expected', 'already', 'added', 'confirmed', 'error', 'updated_at'}


def public_state(doc):
    return {k: v for k, v in ((doc.upd_meta or {}).get(MARKER) or {}).items() if k in PUBLIC_FIELDS}


def available(doc):
    if not doc.moysklad_id or (doc.upd_meta or {}).get('superseded_by_document_id'):
        return False
    if getattr(doc, 'kind', DocumentKind.demand) != DocumentKind.demand:
        return False
    if doc.status == DocumentStatus.accepted:
        return True
    if corrections.correction(doc):
        return False
    if doc.status == DocumentStatus.draft:
        return bool(getattr(doc, 'error_message', None) and getattr(doc, 'processing_progress', None))
    return doc.status == DocumentStatus.processing and bool(((doc.upd_meta or {}).get(MARKER) or {}).get('retry_failed'))


def index_codes(rows):
    result = {}
    def visit(code, pid):
        key = corrections.code_key(code)
        if not key or key in result:
            raise ValueError('МС вернул неоднозначные или повторяющиеся марки. Повторная отправка остановлена.')
        result[key] = (pid, code)
        for child in code.get('trackingCodes') or []:
            visit(child, pid)
    for pid, row in rows.items():
        for code in row['codes']:
            visit(code, pid)
    return result


def missing_batches(ms, rows, desired):
    actual = index_codes(rows)
    expected, amounts = {}, {}
    for scan in desired:
        destinations = [(pid, row) for pid, row in rows.items() if row['product_id'] == scan.get('product_id')]
        if not destinations:
            raise ValueError('Товар из сборки отсутствует в отгрузке МС. Восстановите позицию перед повторной отправкой.')
        entry = ms._tracking_code_entry(scan, destinations[0][1]['tracking_type'])
        key = corrections.code_key(entry)
        if not key or key in expected:
            raise ValueError('В сборке есть повторяющиеся или неопределённые коды маркировки.')
        expected[key] = (scan['product_id'], entry)
        amounts[key] = int(scan.get('quantity') or 1)
        if key in actual and rows[actual[key][0]]['product_id'] != scan['product_id']:
            raise ValueError('Марка уже записана в другую позицию МС. Исправьте привязку товара.')
    capacity = {}
    for pid, row in rows.items():
        occupied = 0
        for code in row['codes']:
            key = corrections.code_key(code)
            count = amounts.get(key, corrections.units(code))
            if count is None:
                # No mutation can safely allocate around an opaque transport pack.
                if any(product == row['product_id'] and k not in actual for k, (product, _) in expected.items()):
                    raise ValueError('МС не вернул состав упаковки в позиции. Сначала проверьте её в МойСкладе.')
                count = 0
            occupied += count
        capacity[pid] = float(row['fields'].get('quantity') or 0) - occupied
    batches = defaultdict(list)
    for key, (product, entry) in expected.items():
        if key in actual:
            continue
        destination = next((pid for pid, row in rows.items()
                            if row['product_id'] == product and capacity[pid] >= amounts[key]), None)
        if destination is None:
            raise ValueError('Количество товара в МС меньше количества марок сборки. Повторная отправка не меняет количества.')
        capacity[destination] -= amounts[key]
        batches[destination].append(entry)
    return expected, actual, dict(batches)


async def assert_idle(db, doc):
    if doc.kind != DocumentKind.demand or not doc.moysklad_id:
        return
    active = (await db.execute(select(Document.id).where(Document.user_id == doc.user_id,
        Document.moysklad_id == doc.moysklad_id, Document.kind == DocumentKind.demand,
        Document.upd_meta[MARKER]['status'].astext.in_(ACTIVE)).limit(1))).scalar_one_or_none()
    if active:
        from fastapi import HTTPException
        raise HTTPException(409, 'Идёт повторная отправка марок. Дождитесь её завершения.')


async def empty_correction(db, doc):
    state = corrections.correction(doc)
    if not state or state.get('job') or state.get('add_positions'):
        raise ValueError('В исправлении есть изменения или незавершённая отправка. Сначала сохраните или отмените исправление.')
    scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
    baseline = state.get('baseline_scans') or {}
    if {str(s.id) for s in scans} != set(baseline) or any(
        MoySkladService._cis_dedup_key(s.code) != corrections.code_key(baseline[str(s.id)]['code']) for s in scans):
        raise ValueError('В исправлении есть несохранённые изменения. Сначала сохраните или отмените их.')
    return scans


async def resolve_source(db, doc, *, check_edits=True):
    seen = set()
    while corrections.correction(doc) and doc.status == DocumentStatus.draft:
        if str(doc.id) in seen:
            raise ValueError('Не удалось определить исходную сборку отгрузки.')
        seen.add(str(doc.id))
        if check_edits:
            await empty_correction(db, doc)
        parent = (await db.execute(select(Document).where(
            Document.id == corrections.correction(doc)['source_id'], Document.user_id == doc.user_id,
            Document.organization_profile_id == doc.organization_profile_id,
            Document.moysklad_id == doc.moysklad_id, Document.kind == DocumentKind.demand))).scalar_one_or_none()
        if not parent:
            raise ValueError('Исходная сборка недоступна.')
        doc = parent
    if (doc.upd_meta or {}).get('superseded_by_document_id'):
        raise ValueError('Откройте актуальную версию отгрузки после исправлений.')
    if not available(doc):
        raise ValueError('Повторная отправка доступна для завершённой отгрузки или после ошибки отправки.')
    return doc


async def save(db, doc, job):
    job['updated_at'] = datetime.now(timezone.utc).isoformat()
    doc.upd_meta = {**(doc.upd_meta or {}), MARKER: copy.deepcopy(job)}
    await db.commit()


def stop_interrupted(doc, message):
    """Release the edit guard on failure without losing an uncertain batch."""
    state = copy.deepcopy((doc.upd_meta or {}).get(MARKER) or {})
    if state.get('status') in ACTIVE:
        state.update(status='error', error=message, updated_at=datetime.now(timezone.utc).isoformat())
        doc.upd_meta = {**doc.upd_meta, MARKER: state}


async def refresh_correction(db, doc, scans, rows, source_scans):
    from app.services.chestnyznak import extract_gtin
    actual = index_codes(rows)
    mapping, seen, additions = {}, set(), []
    roots = {corrections.code_key(c) for row in rows.values() for c in row['codes']}
    source_map = {MoySkladService._cis_dedup_key(s.code): s for s in source_scans}
    for scan in scans:
        key = MoySkladService._cis_dedup_key(scan.code)
        if key not in roots:
            raise ValueError('Состав исправления изменился. Откройте его заново после сверки с МС.')
        pid, code = actual[key]
        mapping[str(scan.id)] = {'position_id': pid, 'code': code}
        seen.add(key)
    for pid, row in rows.items():
        for code in row['codes']:
            key = corrections.code_key(code)
            if key in seen:
                continue
            original = source_map.get(key)
            scan = Scan(id=uuid4(), document_id=doc.id, code=code['cis'], gtin=extract_gtin(code['cis']),
                status=ScanStatus.valid, moysklad_product_id=row['product_id'], product_name=row['product_name'],
                is_box=code.get('type') == 'transportpack', keep_aggregate=True, box_quantity=corrections.units(code))
            if original:
                for field in ('package_type', 'verified_at', 'verification', 'owner_inn', 'owner_name',
                              'producer_name', 'withdrawn', 'withdraw_reason', 'child_codes', 'box_expanded'):
                    setattr(scan, field, getattr(original, field))
            else:
                scan.verification = {'source': 'ms', 'checked_at': None, 'owner_result': 'unknown'}
            db.add(scan)
            additions.append(scan)
            mapping[str(scan.id)] = {'position_id': pid, 'code': code}
    state = copy.deepcopy(corrections.correction(doc))
    state.update(baseline=rows, baseline_scans=mapping, add_positions={})
    doc.upd_meta = {**doc.upd_meta, corrections.MARKER: state}
    if corrections.build_delta(doc, [*scans, *additions])['operations']:
        raise ValueError('Не удалось обновить пустое исправление без изменений состава. Откройте его заново.')


async def restore(ms, doc, desired, job, persist):
    remote, before = await corrections.snapshot(ms, doc.moysklad_id)
    corrections.assert_scope(doc, remote)
    expected, actual, batches = missing_batches(ms, before, desired)
    in_flight = job.get('in_flight')
    if in_flight and not set(in_flight['keys']) <= set(actual):
        raise ValueError('Результат предыдущей записи не подтверждён МС. Повторная запись не выполнялась: требуется сверка этой позиции в МС.')
    job.pop('in_flight', None)
    job.update(status='running', expected=len(expected), already=len(set(expected) & set(actual)), added=0, confirmed=0, error=None)
    await persist()
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
        for pid, entries in batches.items():
            for offset in range(0, len(entries), ms.TRACKING_BATCH_SIZE):
                batch = entries[offset:offset + ms.TRACKING_BATCH_SIZE]
                keys = [corrections.code_key(c) for c in batch]
                job['in_flight'] = {'position_id': pid, 'keys': keys}
                await persist()
                response = await ms._request_with_retry(client, 'POST',
                    f'{ms.base_url}/entity/demand/{doc.moysklad_id}/positions/{pid}/trackingCodes', json=batch)
                if response.status_code == 412:
                    # MS explicitly rejected this batch; there is no unknown outcome.
                    job.pop('in_flight', None)
                    await persist()
                response.raise_for_status()
                codes = await ms._load_tracking_codes(client, 'demand', doc.moysklad_id, pid)
                saved = index_codes({pid: {**before[pid], 'codes': codes}})
                if not set(keys) <= set(saved):
                    raise ValueError('МС не подтвердил сохранение отправленных марок. Повторная запись остановлена, выполните сверку.')
                job.pop('in_flight', None)
                job['added'] += len(batch)
                await persist()
    remote, after = await corrections.snapshot(ms, doc.moysklad_id)
    corrections.assert_scope(doc, remote)
    if set(before) != set(after) or any(before[pid]['fields'] != after[pid]['fields'] for pid in before):
        raise ValueError('Товары, количества или цены изменились в МС во время отправки. Проверьте отгрузку.')
    confirmed = index_codes(after)
    if not set(actual) <= set(confirmed) or not set(expected) <= set(confirmed):
        raise ValueError('МС не подтвердил полный набор марок. Повторите сверку, уже записанные марки не дублируются.')
    if any(after[confirmed[key][0]]['product_id'] != product for key, (product, _) in expected.items()):
        raise ValueError('МС вернул марку в другой позиции. Повторная отправка остановлена.')
    job['confirmed'] = len(expected)
    return after


async def process(document_id, user_id, job_id):
    from app.db.session import AsyncSessionLocal
    from app.api.documents import _get_ms_service
    from app.db.models import User
    from app.services.document_guard import processing_lock
    from app.services.scan_events import publish_event
    from app.core.logging import logger
    from app.worker.tasks import _build_moysklad_scans_data, _get_cz_token, _get_cz_product_groups
    from app.services.plan_matching import plan_gtin_keys, unique_plan_product
    from app.services.document_cis import needs_cis_confirmation, confirm_document_cis
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, document_id)
        if not doc or str(doc.user_id) != str(user_id):
            return
        job = (doc.upd_meta or {}).get(MARKER) or {}
        if job.get('retry_failed'):
            if job.get('id') == job_id and job.get('status') in ACTIVE and doc.status == DocumentStatus.processing:
                # Reuse normal finalization, validation, locking and incident resolution.
                from app.worker.tasks import _process_document_async
                await _process_document_async(document_id, user_id)
            return
        async with processing_lock(f'ms:{user_id}:demand:{doc.moysklad_id}') as acquired:
            if not acquired:
                job = copy.deepcopy((doc.upd_meta or {}).get(MARKER) or {})
                if job.get('id') == job_id:
                    job.update(status='error', error='Отгрузка занята другой операцией. Повторите сверку позже.')
                    await save(db, doc, job)
                return
            doc = (await db.execute(select(Document).where(Document.id == document_id)
                    .with_for_update().execution_options(populate_existing=True))).scalar_one()
            job = copy.deepcopy((doc.upd_meta or {}).get(MARKER) or {})
            if job.get('id') != job_id or job.get('status') not in ACTIVE:
                return
            try:
                if doc.kind != DocumentKind.demand or doc.status != DocumentStatus.accepted or (doc.upd_meta or {}).get('superseded_by_document_id'):
                    raise ValueError('Версия отгрузки изменилась. Откройте актуальную сборку.')
                siblings = (await db.execute(select(Document).where(Document.user_id == doc.user_id,
                    Document.moysklad_id == doc.moysklad_id, Document.kind == DocumentKind.demand,
                    Document.id != doc.id, Document.status != DocumentStatus.accepted)
                    .with_for_update())).scalars().all()
                drafts = []
                for sibling in siblings:
                    if (sibling.upd_meta or {}).get('superseded_by_document_id'):
                        continue
                    if corrections.correction(sibling):
                        drafts.append((sibling, await empty_correction(db, sibling)))
                    else:
                        raise ValueError('Есть незавершённая сборка этой отгрузки. Сначала завершите её.')
                source_scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
                if any(s.status in {ScanStatus.pending, ScanStatus.scanned, ScanStatus.unknown_product} for s in source_scans if not s.is_barcode):
                    raise ValueError('В сборке есть непроверенные или несопоставленные марки.')
                valid = [s for s in source_scans if not s.is_barcode and s.status in {ScanStatus.valid, ScanStatus.overflow}]
                aliases = {g: match['product_id'] for p in doc.plan or [] for g in plan_gtin_keys(p)
                           if (match := unique_plan_product(doc.plan, g))}
                desired = _build_moysklad_scans_data(valid, 'demand', aliases)
                if not desired:
                    raise ValueError('В завершённой сборке нет марок для повторной отправки.')
                if any(needs_cis_confirmation(s['code']) and not s.get('is_box') for s in desired):
                    from app.services.chestnyznak import ChestnyZnakService
                    token = await _get_cz_token(db, user_id, document_id)
                    groups = await _get_cz_product_groups(db, user_id, document_id)
                    desired = await confirm_document_cis(desired, ChestnyZnakService(token=token, mock=False, product_groups=groups), kind='demand')
                ms = await _get_ms_service(await db.get(User, doc.user_id), db)
                after = await restore(ms, doc, desired, job, lambda: save(db, doc, job))
                for sibling, scans in drafts:
                    # Re-read after progress commits; editing is gated by the active resend marker.
                    await empty_correction(db, sibling)
                    await refresh_correction(db, sibling, scans, after, source_scans)
                job.update(status='done', error=None)
                await save(db, doc, job)
                for sibling, _ in drafts:
                    await publish_event(user_id, {'type': 'scans_changed', 'document_id': str(sibling.id)})
                logger.info('shipment_resend.done', document_id=document_id, confirmed=job['confirmed'], added=job['added'])
            except Exception as exc:
                await db.rollback()
                await db.refresh(doc)
                job.update(status='error', error=str(exc) if isinstance(exc, ValueError)
                           else 'Повторная отправка прервалась. Часть марок могла сохраниться; повторите сверку.')
                await save(db, doc, job)
                logger.warning('shipment_resend.failed', document_id=document_id, error_type=type(exc).__name__)
