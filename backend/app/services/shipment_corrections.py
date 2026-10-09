"""Immutable shipment baseline, explicit deltas and resumable MS mutations."""
import copy
import json
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services.moysklad import MoySkladService
from app.services.shipment_guard import ensure_active_shipment

MARKER = 'shipment_correction'


def entity_id(value):
    return (value or {}).get('id') or ((value or {}).get('meta') or {}).get('href', '').rstrip('/').rsplit('/', 1)[-1] or None


def assert_scope(doc, remote):
    for name, field in [('organization', 'moysklad_organization_id'), ('store', 'moysklad_store_id')]:
        saved = getattr(doc, field, None)
        if saved and saved != entity_id(remote.get(name)):
            raise ValueError('Склад или юрлицо отгрузки изменились в МойСкладе. Исправление остановлено.')


def correction(doc):
    return (doc.upd_meta or {}).get(MARKER)


def code_key(code):
    return MoySkladService._cis_dedup_key(code.get('cis') or code.get('cis_1162'))


def code_content(code):
    return {'key': code_key(code), 'type': code.get('type'),
            'children': sorted((code_content(c) for c in code.get('trackingCodes') or []),
                               key=lambda c: json.dumps(c, sort_keys=True))}


def position_content(row):
    return {**row['fields'], 'codes': sorted((code_content(c) for c in row['codes']),
                                           key=lambda c: json.dumps(c, sort_keys=True))}


def units(code):
    children = code.get('trackingCodes') or []
    if children:
        amounts = [units(child) for child in children]
        return sum(amounts) if all(n is not None for n in amounts) else None
    return 1 if code.get('type') == 'trackingcode' else None


async def snapshot(ms, ms_id):
    document = await ms.get_document('demand', ms_id)
    ensure_active_shipment(document)
    rows = await ms._load_positions_rows('demand', ms_id)
    result = {}
    async with httpx.AsyncClient(timeout=90) as client:
        for row in rows:
            if not row.get('id'):
                raise ValueError('МойСклад не вернул идентификатор позиции')
            codes = await ms._load_tracking_codes(client, 'demand', ms_id, row['id'])
            if any(not c.get('id') or not code_key(c) for c in codes):
                raise ValueError('МойСклад вернул марку без идентификатора или кода')
            fields = ms._position_put_payload(row)
            for key in ('meta', 'trackingCodes', 'trackingCodes_1162'):
                fields.pop(key, None)
            result[row['id']] = {'fields': fields, 'codes': codes,
                'product_id': ms._product_id_from_position(row),
                'product_name': (row.get('assortment') or {}).get('name') or 'Товар',
                'tracking_type': ms._moysklad_tracking_type_from_position(row)}
    return document, result


async def start(db, source, ms, actor, *, commit=True):
    document, baseline = await snapshot(ms, source.moysklad_id)
    assert_scope(source, document)
    from app.db.models import OrganizationProfile
    profile = await db.get(OrganizationProfile, source.organization_profile_id)
    if profile and profile.moysklad_organization_id and profile.moysklad_organization_id != entity_id(document.get('organization')):
        raise ValueError('Отгрузка относится к другому юрлицу')
    doc = Document(id=uuid4(), user_id=source.user_id, kind=DocumentKind.demand,
        moysklad_id=source.moysklad_id, organization_profile_id=source.organization_profile_id,
        workplace_id=source.workplace_id, moysklad_organization_id=entity_id(document.get('organization')),
        moysklad_store_id=entity_id(document.get('store')), moysklad_customer_order_id=source.moysklad_customer_order_id,
        customer_order_name=source.customer_order_name, moysklad_name=source.moysklad_name,
        agent_name=source.agent_name, name=source.name, plan=await ms.build_plan('demand', source.moysklad_id))
    db.add(doc)
    mapping = {}
    seen = set()
    for pos_id, row in baseline.items():
        for code in row['codes']:
            raw = code.get('cis')
            if not raw or raw in seen:
                raise ValueError('Состав МС содержит повторяющиеся или неподдерживаемые коды. Исправьте их в МойСкладе.')
            seen.add(raw)
            scan = Scan(id=uuid4(), document_id=doc.id, code=raw, status=ScanStatus.valid,
                moysklad_product_id=row['product_id'], product_name=row['product_name'],
                is_box=code.get('type') == 'transportpack', keep_aggregate=True,
                box_quantity=units(code), verified_at=datetime.now(timezone.utc))
            db.add(scan)
            mapping[str(scan.id)] = {'position_id': pos_id, 'code': code}
    doc.upd_meta = {'collection_start': {'done': True}, MARKER: {
        'source_id': str(source.id), 'opened_at': datetime.now(timezone.utc).isoformat(),
        'actor': actor, 'baseline': baseline, 'baseline_scans': mapping, 'add_positions': {},
    }}
    if commit:
        await db.commit()
    else:
        await db.flush()
    return doc


async def rebase(db, doc, ms, actor):
    """Keep already applied MS changes and carry remaining intent into a fresh local revision."""
    old_state = copy.deepcopy(correction(doc))
    old_scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id))).scalars().all()
    fresh = await start(db, doc, ms, actor, commit=False)
    fresh_state = copy.deepcopy(correction(fresh))
    new_scans = (await db.execute(select(Scan).where(Scan.document_id == fresh.id))).scalars().all()
    remaining = old_state['job']['delta']['operations'][old_state['job']['completed']:]
    for op in remaining:
        pid = op['position_id']
        if op['action'] == 'quantity':
            continue  # Recompute quantities from the fresh baseline during the new review.
        if pid not in fresh_state['baseline'] or fresh_state['baseline'][pid]['product_id'] != old_state['baseline'][pid]['product_id']:
            raise ValueError('Позиция исправления удалена или заменена другим товаром в МС. Сначала восстановите нужную позицию в МойСкладе.')
        matches_code = [scan for scan in new_scans if
            (fresh_state['baseline_scans'].get(str(scan.id)) or {}).get('position_id') == pid
            and code_key(fresh_state['baseline_scans'][str(scan.id)]['code']) == code_key(op['code'])]
        if op['action'] == 'delete':
            for scan in matches_code:
                await db.delete(scan)
                new_scans.remove(scan)
        elif not matches_code:
            original = next((scan for scan in old_scans if old_state['add_positions'].get(str(scan.id)) == pid
                and code_key(MoySkladService('')._tracking_code_entry({'code': scan.code}, old_state['baseline'][pid]['tracking_type'])) == code_key(op['code'])), None)
            if original is None:
                raise ValueError('Не удалось восстановить исходный скан новой марки. Сохранённая версия исправления не изменена.')
            scan = Scan(id=uuid4(), document_id=fresh.id, code=original.code,
                gtin=original.gtin, serial=original.serial, status=original.status,
                moysklad_product_id=original.moysklad_product_id, product_name=original.product_name,
                verified_at=original.verified_at)
            db.add(scan)
            fresh_state['add_positions'][str(scan.id)] = pid
    doc.upd_meta = {**doc.upd_meta, 'superseded_by_document_id': str(fresh.id)}
    fresh_state['rebased_from'] = str(doc.id)
    fresh.upd_meta = {**fresh.upd_meta, MARKER: fresh_state}
    await db.flush()
    pending_scans = (await db.execute(select(Scan).where(Scan.document_id == fresh.id))).scalars().all()
    pending_delta = build_delta(fresh, pending_scans)
    # Preserve the originally reviewed quantity target after partial mark writes.
    # Further local edits still change that target by their unit difference.
    old_operations = old_state['job']['delta']['operations']
    targets = {op['position_id']: old_state['baseline'][op['position_id']]['fields'].get('quantity', 0)
               for op in old_operations}
    for op in old_operations:
        if op['action'] == 'quantity':
            targets[op['position_id']] = op['after']
    offsets = {}
    for pid, target in targets.items():
        if pid not in fresh_state['baseline']:
            continue
        live_quantity = fresh_state['baseline'][pid]['fields'].get('quantity', 0)
        expected_quantity = old_state['job']['expected'][pid]['fields'].get('quantity', 0)
        target = live_quantity + target - expected_quantity
        changes = [op for op in pending_delta['operations'] if op['position_id'] == pid]
        amounts = [units(op['code']) for op in changes]
        if all(n is not None for n in amounts):
            net = sum(units(op['code']) * (1 if op['action'] == 'add' else -1) for op in changes)
            offsets[pid] = target - live_quantity - net
    fresh_state['quantity_offsets'] = offsets
    fresh.upd_meta = {**fresh.upd_meta, MARKER: fresh_state}
    await db.commit()
    return fresh


def build_delta(doc, scans, adjust_quantities=False):
    state = correction(doc)
    baseline = state['baseline']
    desired = {pid: {} for pid in baseline}
    seen = set()
    for scan in scans:
        saved = state['baseline_scans'].get(str(scan.id))
        if saved:
            pid, entry = saved['position_id'], saved['code']
        else:
            if scan.status not in (ScanStatus.valid, ScanStatus.overflow):
                raise ValueError('Проверьте новые марки и удалите ошибочные сканы перед сохранением исправлений.')
            pid = state['add_positions'].get(str(scan.id))
            if pid not in baseline:
                raise ValueError('У новой марки не выбрана позиция отгрузки')
            if scan.is_barcode or scan.is_box or scan.child_codes or scan.keep_aggregate:
                raise ValueError('В исправлении добавляйте отдельные марки. Изменение состава упаковок выполняется в МойСкладе.')
            entry = MoySkladService('')._tracking_code_entry({'code': scan.code}, baseline[pid]['tracking_type'])
        key = code_key(entry)
        if not key or key in seen:
            raise ValueError('Одна марка не может находиться в нескольких позициях исправления')
        seen.add(key)
        desired[pid][key] = entry
    operations, added, removed, quantities = [], [], [], []
    for pid, row in baseline.items():
        old = {code_key(c): c for c in row['codes']}
        new = desired[pid]
        deletes = [c for key, c in old.items() if key not in new]
        inserts = [c for key, c in new.items() if key not in old]
        # An existing code cannot be silently rewritten by a new scan representation.
        for key in old.keys() & new.keys():
            if str(new[key].get('id') or '') and code_content(new[key]) != code_content(old[key]):
                raise ValueError('Изменять вложенный состав упаковки в этом режиме нельзя')
        for action, codes, display in [('delete', deletes, removed), ('add', inserts, added)]:
            for code in codes:
                operations.append({'action': action, 'position_id': pid, 'code': code})
                display.append({'position_id': pid, 'product_name': row['product_name'],
                                'code': code.get('cis'), 'units': units(code), 'package': bool(code.get('trackingCodes')) or code.get('type') != 'trackingcode'})
        offset = state.get('quantity_offsets', {}).get(pid, 0)
        if adjust_quantities and (deletes or inserts or offset):
            amounts = [units(c) for c in deletes + inserts]
            if any(n is None for n in amounts):
                raise ValueError('Количество единиц в упаковке неизвестно. Сохраните марки без изменения количества или исправьте упаковку в МойСкладе.')
            before = row['fields'].get('quantity', 0)
            after = before + sum(units(c) for c in inserts) - sum(units(c) for c in deletes) + offset
            if after < 0:
                raise ValueError('После исправления количество товара станет отрицательным')
            if after != before:
                operations.append({'action': 'quantity', 'position_id': pid, 'before': before, 'after': after})
                quantities.append({'product_name': row['product_name'], 'before': before, 'after': after})
    # Remove all old bindings first: a moved mark must not exist in two positions.
    operations.sort(key=lambda op: {'delete': 0, 'add': 1, 'quantity': 2}[op['action']])
    return {'operations': operations, 'added': added, 'removed': removed, 'quantities': quantities,
            'preview_hash': hashlib.sha256(json.dumps(operations, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}


def after_operation(expected, op):
    after = copy.deepcopy(expected)
    row = after[op['position_id']]
    if op['action'] == 'delete':
        row['codes'] = [c for c in row['codes'] if code_key(c) != code_key(op['code'])]
    elif op['action'] == 'add':
        row['codes'].append(op['code'])
    else:
        row['fields']['quantity'] = op['after']
    return after


def matches(actual, expected, touched):
    return all(pid in actual and position_content(actual[pid]) == position_content(expected[pid]) for pid in touched)


async def save_state(db, doc, state):
    doc.upd_meta = {**(doc.upd_meta or {}), MARKER: copy.deepcopy(state)}
    await db.commit()


async def execute(db, doc, ms):
    state = copy.deepcopy(correction(doc))
    job = state['job']
    operations = job['delta']['operations']
    touched = {op['position_id'] for op in operations}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
        while job['completed'] < len(operations):
            index = job['completed']
            op = operations[index]
            remote, actual = await snapshot(ms, doc.moysklad_id)
            assert_scope(doc, remote)
            expected = job['expected']
            after = after_operation(expected, op)
            if job.get('in_flight') == index and matches(actual, after, touched):
                # The last request succeeded but its response/DB commit was lost.
                pass
            elif matches(actual, expected, touched):
                if op['action'] == 'delete':
                    found = next((c for c in actual[op['position_id']]['codes'] if code_key(c) == code_key(op['code'])), None)
                    if not found or found.get('id') != op['code']['id']:
                        raise ValueError('Идентификатор удаляемой марки изменился в МойСкладе. Требуется сверка.')
                job['in_flight'] = index
                await save_state(db, doc, state)
                url = f'{ms.base_url}/entity/demand/{doc.moysklad_id}/positions/{op["position_id"]}'
                if op['action'] == 'quantity':
                    response = await ms._request_with_retry(client, 'PUT', url, json={'quantity': op['after']})
                else:
                    suffix = '/trackingCodes/delete' if op['action'] == 'delete' else '/trackingCodes'
                    body = [{'meta': {'href': url + '/trackingCodes/' + op['code']['id'],
                                     'type': 'trackingcode', 'mediaType': 'application/json'}}] if op['action'] == 'delete' else [op['code']]
                    response = await ms._request_with_retry(client, 'POST', url + suffix, json=body)
                response.raise_for_status()
                remote, actual = await snapshot(ms, doc.moysklad_id)
                assert_scope(doc, remote)
                if not matches(actual, after, touched):
                    raise ValueError('МойСклад не подтвердил ожидаемый состав. Часть исправлений могла сохраниться; повторите сверку.')
            else:
                raise ValueError('Затронутые позиции изменены в МойСкладе. Исправления остановлены: требуется сверка.')
            job['expected'] = actual
            job['completed'] = index + 1
            job.pop('in_flight', None)
            doc.processing_progress = {'sent': index + 1, 'total': len(operations), 'stage': 'correcting'}
            await save_state(db, doc, state)
    remote, actual = await snapshot(ms, doc.moysklad_id)
    assert_scope(doc, remote)
    if not matches(actual, job['expected'], touched):
        raise ValueError('Состав отгрузки изменился после исправления. Проверьте документ в МойСкладе.')
    state['saved_at'] = datetime.now(timezone.utc).isoformat()
    doc.status = DocumentStatus.accepted
    doc.error_message = None
    previous = (await db.execute(select(Document).where(Document.user_id == doc.user_id,
        Document.kind == DocumentKind.demand, Document.moysklad_id == doc.moysklad_id,
        Document.id != doc.id, Document.status == DocumentStatus.accepted).with_for_update())).scalars().all()
    for older in previous:
        older.upd_meta = {**(older.upd_meta or {}), 'superseded_by_document_id': str(doc.id)}
    await save_state(db, doc, state)


async def process(document_id, user_id):
    from app.services.document_guard import processing_lock
    async with processing_lock(f'process:{document_id}') as acquired:
        if acquired:
            await _process_locked(document_id, user_id)


async def _process_locked(document_id, user_id):
    from app.db.session import AsyncSessionLocal
    from app.api.documents import _get_ms_service
    from app.db.models import User
    from app.services.document_guard import processing_lock
    from app.core.logging import logger
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(Document).where(Document.id == document_id, Document.user_id == user_id))).scalar_one_or_none()
        if not doc or doc.status != DocumentStatus.processing or not correction(doc):
            return
        async with processing_lock(f'ms:{user_id}:demand:{doc.moysklad_id}') as acquired:
            if not acquired:
                doc.status = DocumentStatus.draft
                doc.error_message = 'Отгрузка занята другой операцией. Повторите сохранение.'
                await db.commit()
                return
            try:
                await execute(db, doc, await _get_ms_service(await db.get(User, user_id), db))
                logger.info('shipment_correction.saved', document_id=str(doc.id), user_id=str(user_id))
            except Exception as exc:
                await db.rollback()
                await db.refresh(doc)
                doc.status = DocumentStatus.draft
                doc.error_message = str(exc) if isinstance(exc, ValueError) else 'Сохранение прервалось. Часть изменений могла сохраниться; повторите отправку для сверки и продолжения.'
                await db.commit()
                logger.error('shipment_correction.failed', document_id=str(doc.id), error=str(exc))
