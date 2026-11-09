"""Document-scoped, read-only packaging observations from CZ."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, or_, func
from sqlalchemy.dialects.postgresql import insert

from app.db.models import DocumentPackaging, Scan
from app.core.logging import logger
from app.services.chestnyznak import ChestnyZnakService, is_sscc, normalize_sscc
from app.services.mark_search import mark_key
from app.services.mixed_import import owned_document
from app.services.scan_packaging import package_type


def normalize_tree(code, raw, kind=None, kinds=None):
    known = {mark_key(k): package_type(v) for k, v in (kinds or {}).items()}
    if kind:
        known[mark_key(code)] = package_type(kind)
    remaining = 25000

    def node(value, children, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16 or not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError('Слишком большая или некорректная структура упаковки.')
        nested = entries(children, depth + 1)
        label = 'BOX' if is_sscc(value) else known.get(mark_key(value))
        return {'code': value, 'kind': label or ('UNKNOWN' if nested else 'UNIT'), 'children': nested}

    def entries(value, depth):
        result = []
        if isinstance(value, dict):
            result = [node(k, v, depth) for k, v in value.items()]
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, str):
                    result.append(node(child, None, depth))
                elif isinstance(child, (dict, list)):
                    result.extend(entries(child, depth))
                else:
                    raise ValueError('ЧЗ вернул некорректную структуру упаковки.')
        elif value is not None:
            raise ValueError('ЧЗ вернул некорректную структуру упаковки.')
        unique = {}
        for child in result:
            key = mark_key(child['code'])
            if key in unique and unique[key] != child:
                raise ValueError('Состав упаковки содержит противоречивые связи.')
            unique[key] = child
        return list(unique.values())

    root = node(code, raw, 0)
    if not root['children']:
        raise ValueError('ЧЗ не вернул структуру вложенности упаковки.')
    return root


def walk(node):
    yield node
    for child in node.get('children', []):
        yield from walk(child)


def compose_forest(roots):
    by_key = {mark_key(root['code']): root for root in sorted(roots, key=lambda r: r.get('fetched_at') or '')}
    def hydrate(node, path, observed_at=''):
        key = mark_key(node['code'])
        if key in path or len(path) > 16:
            return {**node, 'children': [], 'error': 'Сохранённые связи упаковок противоречат друг другу. Обновите структуру из ЧЗ.'}
        observed = by_key.get(key)
        if observed and (not node['children'] or (observed.get('fetched_at') or '') > observed_at):
            node = observed
        children = [hydrate(child, path | {key}, node.get('fetched_at') or observed_at) for child in node['children']]
        return {**node, 'children': children}
    merged = [hydrate(root, set(), root.get('fetched_at') or '') for root in by_key.values()]
    nested = {mark_key(node['code']) for root in merged for node in walk(root) if node is not root}
    return [root for root in merged if mark_key(root['code']) not in nested] or merged


async def capture(db, document_id, cz):
    if cz is None:
        return
    for observation in getattr(cz, 'aggregate_trees', {}).values():
        code = observation['code']
        try:
            tree = normalize_tree(code, observation['raw'], observation.get('kind'),
                                  getattr(cz, 'aggregate_kinds', {}))
        except ValueError:
            logger.warning('packaging_composition.invalid_tree', document_id=str(document_id))
            continue
        values = {'id': uuid4(), 'document_id': document_id, 'root_key': mark_key(code),
                  'code': code, 'tree': tree, 'error': None, 'fetched_at': datetime.now(timezone.utc)}
        await db.execute(insert(DocumentPackaging).values(**values).on_conflict_do_update(
            constraint='uq_document_packaging_root',
            set_={k: v for k, v in values.items() if k not in {'id', 'document_id', 'root_key'}}))


def active_job(job):
    return bool(job.get('status') in {'queued', 'running'}
                and datetime.fromisoformat(job['deadline']) > datetime.now(timezone.utc))


async def view(db, document_id, user_id):
    doc = await owned_document(db, document_id, user_id)
    rows = (await db.execute(select(DocumentPackaging).where(
        DocumentPackaging.document_id == document_id).order_by(DocumentPackaging.code))).scalars().all()
    roots = []
    represented = set()
    for row in rows:
        root = dict(row.tree or {'code': row.code, 'kind': 'UNKNOWN', 'children': [], 'incomplete': True})
        root.update(fetched_at=row.fetched_at.isoformat() if row.fetched_at else None, error=row.error)
        roots.append(root)
        represented.add(row.root_key)
    scans = (await db.execute(select(Scan).where(
        Scan.document_id == document_id,
        Scan.is_barcode.is_(False),
        or_(Scan.is_box.is_(True), Scan.box_quantity > 1,
            Scan.package_type.in_(['GROUP', 'BOX']), func.jsonb_typeof(Scan.child_codes) == 'array'),
    ))).scalars().all()
    for scan in scans:
        if (not scan.is_barcode and (scan.is_box or scan.child_codes or (scan.box_quantity or 0) > 1
                                   or package_type(scan.package_type) in {'GROUP', 'BOX'})
                and mark_key(scan.code) not in represented):
            roots.append({'code': scan.code, 'kind': 'BOX' if scan.is_box else package_type(scan.package_type) or 'UNKNOWN',
                          'children': [], 'incomplete': True, 'expected_units': scan.box_quantity})
            represented.add(mark_key(scan.code))
    forest = compose_forest(roots)
    job = dict((doc.upd_meta or {}).get('packaging_refresh') or {})
    if job.get('status') in {'queued', 'running'} and not active_job(job):
        job.update(status='failed', error='Время получения структуры истекло. Повторите запрос.')
        doc.upd_meta = {**(doc.upd_meta or {}), 'packaging_refresh': job}
        await db.commit()
    return {'roots': forest or roots, 'job': {k: v for k, v in job.items() if k != 'codes'}}


async def refresh(db, document_id, user_id, code=None):
    doc = await owned_document(db, document_id, user_id)
    previous = (doc.upd_meta or {}).get('packaging_refresh') or {}
    if active_job(previous):
        if code and mark_key(code) not in {mark_key(c) for c in previous.get('codes', [])}:
            raise HTTPException(409, 'Для документа уже запрашивается другая структура. Дождитесь завершения и повторите запрос.')
        return {'id': previous['id'], 'status': previous['status']}
    data = await view(db, document_id, user_id)
    doc = await owned_document(db, document_id, user_id)
    previous = (doc.upd_meta or {}).get('packaging_refresh') or {}
    if active_job(previous):
        if code and mark_key(code) not in {mark_key(c) for c in previous.get('codes', [])}:
            raise HTTPException(409, 'Для документа уже запрашивается другая структура. Дождитесь завершения и повторите запрос.')
        return {'id': previous['id'], 'status': previous['status']}
    codes = [root['code'] for root in data['roots']]
    if code and code.strip():
        code = normalize_sscc(code.strip())
        if len(code) > 2048:
            raise HTTPException(400, 'Код упаковки слишком длинный.')
        codes = [code]
    codes = list({mark_key(c): c for c in codes}.values())
    if not codes:
        raise HTTPException(400, 'Укажите код короба или блока, чтобы получить его структуру.')
    if len(codes) > 100:
        raise HTTPException(400, 'Слишком много отдельных упаковок. Запрашивайте их структуру по коду.')
    job = {'id': str(uuid4()), 'status': 'queued', 'codes': codes,
           'deadline': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    doc.upd_meta = {**(doc.upd_meta or {}), 'packaging_refresh': job}
    await db.commit()
    from app.worker.tasks import packaging_composition_task
    try:
        packaging_composition_task.delay(str(document_id), str(user_id), job['id'])
    except Exception:
        doc = await owned_document(db, document_id, user_id)
        job.update(status='failed', error='Не удалось запустить получение структуры. Повторите запрос.')
        job.pop('codes', None)
        doc.upd_meta = {**(doc.upd_meta or {}), 'packaging_refresh': job}
        await db.commit()
    return {k: v for k, v in job.items() if k != 'codes'}


async def run_refresh(document_id, user_id, request_id):
    from app.db.session import AsyncSessionLocal
    from app.worker.tasks import _get_cz_token, _get_cz_product_groups
    from app.services.document_guard import processing_lock
    async with processing_lock(f'packaging-view:{document_id}', wait=True):
        async with AsyncSessionLocal() as db:
            doc = await owned_document(db, document_id, user_id)
            job = dict((doc.upd_meta or {}).get('packaging_refresh') or {})
            if job.get('id') != request_id or not active_job(job):
                return
            token = await _get_cz_token(db, user_id, document_id)
            groups = await _get_cz_product_groups(db, user_id, document_id)
            job['status'] = 'running'
            doc.upd_meta = {**(doc.upd_meta or {}), 'packaging_refresh': job}
            await db.commit()
            cz = ChestnyZnakService(token=token, mock=False, product_groups=groups)
            errors = {}
            async def load():
                for code in job['codes']:
                    try:
                        if not token:
                            raise ValueError('Войдите в ЧЗ для получения структуры упаковок.')
                        if is_sscc(code):
                            await cz.unpack_box(code)
                        else:
                            await cz.get_code_info(code)
                        observation = cz.aggregate_trees.get(code)
                        if not observation:
                            raise ValueError('ЧЗ не вернул структуру этой упаковки.')
                        tree = normalize_tree(code, observation['raw'], observation.get('kind'), cz.aggregate_kinds)
                        for node in walk(tree):
                            if node['children'] and node['kind'] == 'UNKNOWN':
                                await cz.get_code_info(node['code'])
                    except Exception as exc:
                        errors[code] = str(exc) if isinstance(exc, ValueError) else 'Не удалось получить структуру в ЧЗ. Повторите запрос.'
            try:
                remaining = max(0, (datetime.fromisoformat(job['deadline']) - datetime.now(timezone.utc)).total_seconds())
                await asyncio.wait_for(load(), timeout=remaining)
            except asyncio.TimeoutError:
                errors[''] = 'Время запроса в ЧЗ истекло. Показана последняя сохранённая структура.'
            doc = await owned_document(db, document_id, user_id)
            current = (doc.upd_meta or {}).get('packaging_refresh') or {}
            if current.get('id') != request_id or current.get('status') not in {'queued', 'running'}:
                return
            await capture(db, document_id, cz)
            for code, error in errors.items():
                if not code:
                    continue
                existing = (await db.execute(select(DocumentPackaging).where(
                    DocumentPackaging.document_id == document_id, DocumentPackaging.root_key == mark_key(code)))).scalar_one_or_none()
                if existing:
                    existing.error = error
                else:
                    db.add(DocumentPackaging(document_id=document_id, root_key=mark_key(code), code=code, error=error))
            job.update(status='failed' if errors else 'completed', error=next(iter(errors.values()), None))
            job.pop('codes', None)
            doc.upd_meta = {**(doc.upd_meta or {}), 'packaging_refresh': job}
            await db.commit()
