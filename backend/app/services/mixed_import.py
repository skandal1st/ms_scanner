"""Atomic mixed imports. External packaging lookup runs only in Celery."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, or_

from app.core.logging import logger
from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services.chestnyznak import (
    ChestnyZnakService, extract_gtin, is_sscc, normalize_sscc, verify_code_local_gs1,
)
from app.services.mark_search import mark_key
from app.services.scan_input import decode_base64_mark
from app.services.scan_packaging import package_type, scan_package_type

MAX_UNITS = 20000
ACTIVE = {'queued', 'running'}


def active_import(doc):
    job = (getattr(doc, 'upd_meta', None) or {}).get('mixed_import') or {}
    return (job.get('status') in ACTIVE
            and datetime.fromisoformat(job['deadline']) > datetime.now(timezone.utc))


def public_job(job):
    return {k: v for k, v in job.items() if k != 'codes'}


def normalize_codes(codes):
    if not codes or len(codes) > 15000:
        raise HTTPException(400, 'Загрузите от 1 до 15000 кодов за раз.')
    result = []
    for raw in codes:
        if len(raw) > 2048:
            raise HTTPException(400, 'Слишком длинная строка в списке марок.')
        code = normalize_sscc(decode_base64_mark(raw) or raw.strip())
        if code:
            result.append(code)
    if not result:
        raise HTTPException(400, 'Список марок пуст.')
    return result


def store_job(doc, job):
    doc.upd_meta = {**(doc.upd_meta or {}), 'mixed_import': dict(job)}


async def owned_document(db, document_id, user_id):
    doc = (await db.execute(select(Document).where(
        Document.id == document_id, Document.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, 'Документ не найден.')
    return doc


async def start(db, document_id, user_id, codes, target='units'):
    from app.services.collection_start import require_collection_started
    from app.services.document_guard import editable_document
    if target not in {'units', 'blocks', 'blocks_loose', 'boxes'}:
        raise HTTPException(400, 'Выберите пачки, блоки, блоки с россыпью или короба.')
    codes = normalize_codes(codes)
    doc = await owned_document(db, document_id, user_id)
    if target == 'boxes' and doc.kind != DocumentKind.demand:
        raise HTTPException(400, 'Передача коробов целиком доступна только для отгрузки.')
    previous = (doc.upd_meta or {}).get('mixed_import') or {}
    if active_import(doc):
        if previous.get('codes') == codes and previous.get('target', 'units') == target:
            return public_job(previous)
        raise HTTPException(409, 'Смешанная загрузка уже выполняется. Дождитесь её завершения.')
    doc = await editable_document(db, document_id, user_id)
    require_collection_started(doc)
    if (doc.upd_meta or {}).get('shipment_correction'):
        raise HTTPException(409, 'Для исправления отправленной отгрузки используйте обычную загрузку марок: исходные марки должны сохраняться.')
    pending = (await db.execute(select(Scan.id).where(
        Scan.document_id == document_id, Scan.status == ScanStatus.pending,
    ).limit(1))).scalar_one_or_none()
    if pending:
        raise HTTPException(409, 'Дождитесь определения упаковок и проверки уже добавленных марок.')
    if doc.cz_doc_ids:
        raise HTTPException(409, 'Документ уже отправлялся в Честный Знак. Изменять марки нельзя.')
    now = datetime.now(timezone.utc)
    job = {'id': str(uuid4()), 'status': 'queued', 'codes': codes, 'target': target,
           'deadline': (now + timedelta(minutes=10)).isoformat(), 'conflicts': []}
    store_job(doc, job)
    await db.commit()
    from app.worker.tasks import mixed_import_task
    try:
        mixed_import_task.delay(str(document_id), str(user_id), job['id'])
    except Exception:
        doc = await owned_document(db, document_id, user_id)
        job.update(status='failed', error='Не удалось запустить загрузку. Повторите попытку.')
        job.pop('codes', None)
        store_job(doc, job)
        await db.commit()
    return public_job(job)


async def status(db, document_id, user_id):
    doc = await owned_document(db, document_id, user_id)
    job = dict((doc.upd_meta or {}).get('mixed_import') or {})
    if job.get('status') in ACTIVE and not active_import(doc):
        job.update(status='failed', error='Время загрузки истекло. Документ не изменён. Повторите загрузку.')
        job.pop('codes', None)
        store_job(doc, job)
        await db.commit()
    return public_job(job)


class CompositionConflict(ValueError):
    pass


class Resolver:
    def __init__(self, cz, cached, kinds=None):
        self.cz, self.cached, self.units = cz, cached, {}
        self.kinds = dict(kinds or {})
        self.gate = asyncio.Semaphore(4)

    def leaves(self, children):
        result = {}
        if not children:
            raise CompositionConflict('Состав упаковки неизвестен.')
        for raw in children:
            code = decode_base64_mark(raw) or raw.strip()
            key = mark_key(code)
            # Aggregate endpoints return leaf marks. Reject incomplete/logistics IDs.
            if is_sscc(code) or not verify_code_local_gs1(code).valid:
                raise CompositionConflict('Получен неполный состав упаковки: есть нераскрытые или некорректные коды.')
            result.setdefault(key, code)
        if len(self.units.keys() | result.keys()) > MAX_UNITS:
            raise CompositionConflict('В упаковке слишком много марок. Разбейте загрузку на части.')
        self.units.update(result)
        return list(result.values())

    async def resolve(self, code):
        key = mark_key(code)
        if key in self.cached:
            leaves = self.leaves(self.cached[key])
            if any(mark_key(child) == key for child in leaves):
                raise CompositionConflict('Упаковка ссылается на себя; полный состав неизвестен.')
            return leaves
        if key in self.units:
            self.kinds.setdefault(key, 'UNIT')
            return [self.units[key]]
        if self.cz is None:
            raise CompositionConflict('Неизвестен состав или тип упаковки. Войдите в ЧЗ и повторите загрузку.')
        async with self.gate:
            if is_sscc(code):
                self.kinds[key] = 'BOX'
                return self.leaves(await self.cz.unpack_box(normalize_sscc(code)))
            info = await self.cz.get_code_info(code)
        if info is None:
            raise CompositionConflict('ЧЗ не вернул сведения о коде; состав упаковки неизвестен.')
        self.kinds[key] = package_type(info.package_type)
        if info.children:
            leaves = self.leaves(info.children)
            if any(mark_key(child) == key for child in leaves):
                raise CompositionConflict('Упаковка ссылается на себя; полный состав неизвестен.')
            return leaves
        if package_type(info.package_type) != 'UNIT':
            raise CompositionConflict('Состав упаковки неизвестен. Блок не добавлен целиком.')
        return self.leaves([code])


async def resolve_import(codes, scans, cz, target='units'):
    parents = [s for s in scans if not s.is_barcode and (
        s.is_box or (s.box_quantity or 0) > 1
        or scan_package_type(s) in {'GROUP', 'BOX'} or s.child_codes)]
    cached = {mark_key(s.code): s.child_codes for s in parents if s.child_codes
              and (not s.box_quantity or s.box_quantity == len({mark_key(c) for c in s.child_codes}))}
    kinds = {mark_key(s.code): ('BOX' if s.is_box else package_type(s.package_type)) for s in parents}
    resolver = Resolver(cz, cached, kinds)
    resolved, conflicts = {}, []
    # Resolve existing parents first, so their leaf codes can be reused below.
    async def resolve_one(code):
        key = mark_key(code)
        if key in resolved:
            return
        try:
            resolved[key] = await resolver.resolve(code)
        except Exception as exc:
            message = str(exc) if isinstance(exc, CompositionConflict) else 'Не удалось получить состав в ЧЗ. Повторите загрузку.'
            conflicts.append({'code': code, 'message': message})
    await asyncio.gather(*(resolve_one(s.code) for s in parents))
    existing = {mark_key(s.code) for s in scans if s not in parents and not s.is_barcode}
    # Bounded batches also bound queued work for large files.
    unique = list({mark_key(c): c for c in codes}.values())
    for offset in range(0, len(unique), 100):
        await asyncio.gather(*(resolve_one(code) for code in unique[offset:offset + 100]
                              if mark_key(code) not in existing))
    units = {}
    occurrences = 0
    for code in codes:
        leaves = resolved.get(mark_key(code), [code] if mark_key(code) in existing else [])
        occurrences += len(leaves)
        for leaf in leaves:
            units.setdefault(mark_key(leaf), leaf)
    incoming_keys = set(units)
    for parent in parents:
        for leaf in resolved.get(mark_key(parent.code), []):
            units.setdefault(mark_key(leaf), leaf)
            existing.add(mark_key(leaf))
    if len(units) > MAX_UNITS:
        conflicts.append({'code': '', 'message': 'После раскрытия больше 20 000 марок. Разбейте файл на части.'})
    parent_units = {str(p.id): resolved.get(mark_key(p.code), []) for p in parents}
    if target in {'blocks', 'blocks_loose', 'boxes'}:
        blocks = {}
        boxes = target == 'boxes'
        label = 'короба' if boxes else 'блока'
        for code in list({mark_key(c): c for c in [*(s.code for s in parents), *codes]}.values()):
            key = mark_key(code)
            kind = resolver.kinds.get(key, 'UNIT' if key in existing else None)
            if kind == ('BOX' if boxes else 'GROUP'):
                children = resolved.get(key, [])
                if not (is_sscc(code) if boxes else verify_code_local_gs1(code).valid) or not children:
                    conflicts.append({'code': code, 'message': f'Не подтверждён код {label} или его полный состав. Для короба нужен SSCC.' if boxes else 'Не подтверждён код блока или его полный состав.'})
                elif len({extract_gtin(c) for c in children}) != 1:
                    conflicts.append({'code': code, 'message': 'Короб содержит разные товары; выберите уровень пачек.' if boxes else 'Блок содержит разные товары; выберите уровень пачек.'})
                else:
                    blocks[key] = {'code': code, 'children': children}
            elif kind not in ({'UNIT', 'GROUP'} if boxes else {'UNIT', 'BOX'}):
                conflicts.append({'code': code, 'message': 'Не удалось отличить блок от короба. Выберите пачки или повторите получение состава.'})
        all_units = dict(units)
        for scan in scans:
            if scan not in parents and not scan.is_barcode:
                all_units.setdefault(mark_key(scan.code), scan.code)
        coverage = {}
        for block_key, block in blocks.items():
            children_keys = {mark_key(c) for c in block['children']}
            assignments = {s.moysklad_product_id for s in scans
                           if getattr(s, 'moysklad_product_id', None) and (mark_key(s.code) in children_keys
                           or (boxes and s.gtin == extract_gtin(block['children'][0])
                               and any(mark_key(c) in children_keys for c in parent_units.get(str(s.id), []))))}
            if len(assignments) > 1:
                conflicts.append({'code': block['code'], 'message': 'Пачки короба привязаны к разным товарам. Исправьте привязки или выберите пачки.' if boxes else 'Пачки блока привязаны к разным товарам. Исправьте привязки или выберите пачки.'})
            for child in block['children']:
                child_key = mark_key(child)
                if child_key in coverage and coverage[child_key] != block_key:
                    conflicts.append({'code': block['code'], 'message': 'Два разных короба содержат одну и ту же пачку. Нельзя выбрать короб автоматически.' if boxes else 'Два разных блока содержат одну и ту же пачку. Нельзя выбрать блок автоматически.'})
                    break
                coverage[child_key] = block_key
        if boxes:
            # An unrelated earlier batch stays intact. A partially covered earlier
            # aggregate cannot be removed or retained without losing/duplicating marks.
            for parent in parents:
                keys = {mark_key(c) for c in parent_units.get(str(parent.id), [])}
                if keys & coverage.keys() and not keys.issubset(coverage):
                    conflicts.append({'code': parent.code, 'message': 'Ранее загруженная упаковка лишь частично входит в выбранные короба. Проверьте состав упаковок; документ не изменён.'})
        if target in {'blocks', 'boxes'}:
            required = {key: code for key, code in all_units.items() if not boxes or key in incoming_keys}
            for key, code in required.items():
                if key not in coverage:
                    conflicts.append({'code': code, 'message': 'Для марки нет подтверждённого короба в списке или документе. Добавьте SSCC короба либо выберите пачки.' if boxes else 'Для пачки нет подтверждённого блока в списке или документе. Добавьте код блока либо выберите пачки.'})
        return parents, all_units, occurrences, conflicts, len(incoming_keys - existing), parent_units, blocks
    return parents, units, occurrences, conflicts, len(incoming_keys - existing), parent_units


async def conflicts_elsewhere(db, doc, wanted, *, include_children=False):
    from app.services.mark_search import candidate_condition, mark_prefixes
    statement = select(Scan.code, Document.name).join(Document).where(
        Document.user_id == doc.user_id, Document.id != doc.id, Document.kind == doc.kind,
        Document.upd_meta['superseded_by_document_id'].astext.is_(None),
        Scan.status.in_([ScanStatus.valid, ScanStatus.overflow, ScanStatus.pending,
                        ScanStatus.scanned, ScanStatus.unknown_product]))
    if (doc.upd_meta or {}).get('shipment_correction'):
        statement = statement.where(or_(Document.moysklad_id.is_(None), Document.moysklad_id != doc.moysklad_id))
    result = {}
    for offset in range(0, len(wanted), 100):
        condition = or_(*(candidate_condition(Scan.code, mark_prefixes(code))
                          for code in wanted[offset:offset + 100]))
        for code, name in (await db.execute(statement.where(condition))).all():
            result[mark_key(code)] = name
    if include_children and wanted:
        # Whole boxes must also be checked against marks inside other aggregates.
        from sqlalchemy import case, cast, func, true
        from sqlalchemy.dialects.postgresql import JSONB
        children = func.jsonb_array_elements_text(case(
            (func.jsonb_typeof(Scan.child_codes) == 'array', Scan.child_codes),
            else_=cast([], JSONB))).table_valued('value').lateral()
        nested = statement.with_only_columns(children.c.value, Document.name).join(children, true())
        for offset in range(0, len(wanted), 100):
            condition = or_(*(candidate_condition(children.c.value, mark_prefixes(code))
                              for code in wanted[offset:offset + 100]))
            for code, name in (await db.execute(nested.where(condition))).all():
                result[mark_key(code)] = name
    return result


async def apply_units(db, doc, scans, parents, units, parent_units):
    from app.services.gtin_product_store import get_gtin_product
    from app.services.plan_matching import unique_plan_product
    retained, removed = {}, []
    for scan in scans:
        if scan in parents:
            removed.append(scan)
        elif not scan.is_barcode:
            key = mark_key(scan.code)
            if key in retained:
                removed.append(scan)
            else:
                retained[key] = scan
    added = []
    products = {}
    # Preserve existing cross-document safeguards for newly materialized units.
    wanted = [code for key, code in units.items() if key not in retained]
    elsewhere = await conflicts_elsewhere(db, doc, wanted)
    explicit_units = {}
    blocked_units = {}
    for parent in parents:
        for child in parent_units.get(str(parent.id), []):
            if parent.status == ScanStatus.used_in_other_doc:
                blocked_units[mark_key(child)] = parent.error_message
            if parent.moysklad_product_id and parent.gtin == extract_gtin(child):
                explicit_units.setdefault(mark_key(child), parent)
    for key, code in units.items():
        if key in retained:
            continue
        gtin = extract_gtin(code)
        if gtin not in products:
            planned = unique_plan_product(doc.plan, gtin)
            known = None if planned else await get_gtin_product(db, doc.user_id, gtin)
            products[gtin] = (planned['product_id'], planned.get('product_name')) if planned else (known or (None, None))
        pid, name = products[gtin]
        # A confirmed manual parent assignment applies only to that same GTIN.
        explicit = explicit_units.get(key)
        if explicit:
            pid, name = explicit.moysklad_product_id, explicit.product_name
        conflict = elsewhere.get(key)
        blocked = key in blocked_units
        scan = Scan(document_id=doc.id, code=code, gtin=gtin,
                    serial=verify_code_local_gs1(code).serial,
                    status=ScanStatus.used_in_other_doc if conflict or blocked else ScanStatus.scanned,
                    error_message=(blocked_units[key] or 'Упаковка используется в другом документе.') if blocked else
                                  (f'Код уже используется в документе «{conflict}»' if conflict else None),
                    moysklad_product_id=pid, product_name=name, package_type='UNIT',
                    verification={'source': 'mixed_import_composition', 'checked_at': None})
        db.add(scan)
        added.append(scan)
    for scan in removed:
        await db.delete(scan)
    return added, removed


async def apply_blocks(db, doc, scans, blocks, parent_units, keep_units=None, *, boxes=False):
    from app.services.plan_matching import unique_plan_product
    from app.services.gtin_product_store import get_gtin_product
    existing, removed, added = {}, [], []
    retained_units = set()
    keep_units = set(keep_units or ())
    if boxes:
        covered = {mark_key(c) for box in blocks.values() for c in box['children']}
        keep_units.update(mark_key(scan.code) for scan in scans
                          if mark_key(scan.code) not in blocks
                          and not covered.intersection(mark_key(c) for c in parent_units.get(str(scan.id), [scan.code])))
    for scan in scans:
        if scan.is_barcode:
            continue
        key = mark_key(scan.code)
        if key in blocks and key not in existing:
            existing[key] = scan
        elif key in keep_units and key not in retained_units:
            retained_units.add(key)
        else:
            removed.append(scan)
    elsewhere = await conflicts_elsewhere(db, doc, [
        c for block in blocks.values() for c in [block['code'], *block['children']]], include_children=boxes)
    blocked_units = {}
    for scan in scans:
        if scan.status == ScanStatus.used_in_other_doc:
            for code in parent_units.get(str(scan.id), [scan.code]):
                blocked_units[mark_key(code)] = scan.error_message or 'Марка используется в другом документе.'
    for key, block in blocks.items():
        scan = existing.get(key)
        children = block['children']
        gtin = extract_gtin(children[0])
        if scan is None:
            planned = unique_plan_product(doc.plan, gtin)
            known = None if planned else await get_gtin_product(db, doc.user_id, gtin)
            pid, name = (planned['product_id'], planned.get('product_name')) if planned else (known or (None, None))
            child_keys = {mark_key(c) for c in children}
            assigned = next((s for s in scans if s.moysklad_product_id and (mark_key(s.code) in child_keys
                             or (boxes and s.gtin == gtin
                                 and any(mark_key(c) in child_keys for c in parent_units.get(str(s.id), []))))), None)
            if assigned:
                pid, name = assigned.moysklad_product_id, assigned.product_name
            scan = Scan(document_id=doc.id, code=block['code'], gtin=gtin,
                        serial=verify_code_local_gs1(block['code']).serial,
                        moysklad_product_id=pid, product_name=name, status=ScanStatus.scanned,
                        verification={'source': 'mixed_import_composition', 'checked_at': None})
            db.add(scan)
            added.append(scan)
        changed = (scan.is_box != boxes or scan.box_expanded is True
                   or scan.keep_aggregate is not True or scan.box_quantity != len(children)
                   or {mark_key(c) for c in (scan.child_codes or [])} != {mark_key(c) for c in children})
        scan.is_box, scan.box_expanded = boxes, False
        scan.package_type, scan.keep_aggregate = ('BOX' if boxes else 'GROUP'), True
        scan.child_codes, scan.box_quantity, scan.gtin = list(children), len(children), gtin
        conflict = next((elsewhere[k] for k in [key, *(mark_key(c) for c in children)] if k in elsewhere), None)
        blocked = next((blocked_units[mark_key(c)] for c in children if mark_key(c) in blocked_units), None)
        if blocked or conflict is not None:
            scan.status = ScanStatus.used_in_other_doc
            scan.error_message = blocked or f'Код уже используется в документе «{conflict}»'
        elif changed and key in existing:
            scan.status, scan.verified_at = ScanStatus.scanned, None
            scan.verification = {'source': 'mixed_import_composition', 'checked_at': None}
            scan.error_message = None
    for scan in removed:
        await db.delete(scan)
    return added, removed


async def apply_blocks_and_loose(db, doc, scans, parents, units, blocks, parent_units):
    covered = {mark_key(code) for block in blocks.values() for code in block['children']}
    loose = {key: code for key, code in units.items() if key not in covered}
    added, removed = await apply_blocks(db, doc, scans, blocks, parent_units, keep_units=set(loose))
    # Only pass surviving loose rows to the unit writer. Parent metadata still
    # supplies manual assignments and cross-document restrictions to new units.
    retained = []
    seen = set()
    for scan in scans:
        key = mark_key(scan.code)
        if not scan.is_barcode and key in loose and key not in seen:
            retained.append(scan)
            seen.add(key)
    unit_added, unit_removed = await apply_units(db, doc, retained, parents, loose, parent_units)
    return added + unit_added, removed + unit_removed, len(loose)


def scan_signature(scans):
    fields = ('id', 'code', 'gtin', 'moysklad_product_id', 'status', 'is_box',
              'box_expanded', 'box_quantity', 'package_type', 'child_codes',
              'keep_aggregate', 'verification', 'verified_at', 'error_message')
    return json.dumps([[str(getattr(s, field)) for field in fields] for s in scans])


async def fail_import(document_id, user_id, job_id):
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        doc = await owned_document(db, document_id, user_id)
        job = dict((doc.upd_meta or {}).get('mixed_import') or {})
        if job.get('id') == job_id and job.get('status') in ACTIVE:
            job.update(status='failed', error='Не удалось завершить загрузку. Документ не изменён; повторите попытку.')
            job.pop('codes', None)
            store_job(doc, job)
            await db.commit()


async def run_import(document_id, user_id, job_id):
    from app.db.session import AsyncSessionLocal
    from app.services.document_guard import processing_lock
    from app.services.scan_events import publish_event
    from app.worker.tasks import _get_cz_token, _get_cz_product_groups
    async with processing_lock(f'mixed-import:{document_id}', wait=True):
        async with AsyncSessionLocal() as db:
            doc = await owned_document(db, document_id, user_id)
            job = dict((doc.upd_meta or {}).get('mixed_import') or {})
            if job.get('id') != job_id or not active_import(doc) or doc.status != DocumentStatus.draft:
                return
            job['status'] = 'running'
            store_job(doc, job)
            await db.commit()
            scans = (await db.execute(select(Scan).where(Scan.document_id == document_id)
                                     .order_by(Scan.scanned_at, Scan.id))).scalars().all()
            snapshot = scan_signature(scans)
            token = await _get_cz_token(db, user_id, document_id)
            groups = await _get_cz_product_groups(db, user_id, document_id)
            cz = ChestnyZnakService(token=token, mock=False, product_groups=groups) if token else None
            # No transaction/row lock is held while waiting on external systems.
            await db.commit()
            try:
                remaining = max(0, (datetime.fromisoformat(job['deadline']) - datetime.now(timezone.utc)).total_seconds())
                result = await asyncio.wait_for(
                    resolve_import(job['codes'], scans, cz, job.get('target', 'units')), timeout=remaining)
                parents, units, occurrences, conflicts, new_units, parent_units = result[:6]
                blocks = result[6] if len(result) > 6 else None
            except asyncio.TimeoutError:
                parents, units, occurrences, new_units, parent_units = [], {}, 0, 0, {}
                conflicts = [{'code': '', 'message': 'Время получения состава истекло. Документ не изменён.'}]
                blocks = None
            doc = await owned_document(db, document_id, user_id)
            current = (doc.upd_meta or {}).get('mixed_import') or {}
            if current.get('id') != job_id or current.get('status') not in ACTIVE:
                return
            if (doc.status != DocumentStatus.draft or not active_import(doc)
                    or (doc.upd_meta or {}).get('superseded_by_document_id')
                    or (doc.upd_meta or {}).get('shipment_correction') or doc.cz_doc_ids):
                conflicts.append({'code': '', 'message': 'Документ изменился или время загрузки истекло. Повторите загрузку.'})
            fresh = (await db.execute(select(Scan).where(Scan.document_id == document_id)
                                     .order_by(Scan.scanned_at, Scan.id).with_for_update()
                                     .execution_options(populate_existing=True))).scalars().all()
            if scan_signature(fresh) != snapshot:
                conflicts.append({'code': '', 'message': 'Марки изменились во время определения состава. Документ не изменён; повторите загрузку.'})
            if conflicts:
                job.update(status='conflict', conflicts=conflicts)
                job.pop('codes', None)
                store_job(doc, job)
                await db.commit()
                return
            from app.services.packaging_composition import capture
            await capture(db, document_id, cz)
            loose_count = 0
            if job.get('target') == 'blocks_loose':
                added, removed, loose_count = await apply_blocks_and_loose(
                    db, doc, fresh, parents, units, blocks, parent_units)
            elif blocks is not None:
                added, removed = await apply_blocks(db, doc, fresh, blocks, parent_units, boxes=job.get('target') == 'boxes')
            else:
                added, removed = await apply_units(db, doc, fresh, parents, units, parent_units)
            job.update(status='completed', conflicts=[], added=len(added) if blocks is not None else new_units, removed=len(removed),
                       blocks=len(blocks) if blocks is not None and job.get('target') != 'boxes' else 0,
                       boxes=len(blocks) if blocks is not None and job.get('target') == 'boxes' else 0,
                       loose_units=loose_count,
                       duplicates=max(0, occurrences - new_units),
                       units=(len({mark_key(c) for box in blocks.values() for c in box['children']})
                              if job.get('target') == 'boxes' else len(units)))
            job.pop('codes', None)
            store_job(doc, job)
            await db.commit()
            # Publishing is best-effort; polling/list refresh remains authoritative.
            try:
                await publish_event(user_id, {'type': 'scans_changed', 'document_id': str(document_id)})
            except Exception as exc:
                logger.warning('mixed_import.publish_failed', error_type=type(exc).__name__)
            logger.info('mixed_import.completed', document_id=str(document_id), added=len(added), removed=len(removed))
