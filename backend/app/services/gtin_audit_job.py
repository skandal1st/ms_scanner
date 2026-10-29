"""One background catalog traversal per account, with an atomic successful report."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update, delete, insert

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import GtinAuditRun, GtinAuditFinding, GtinProductMap, Integration
from app.db.session import AsyncSessionLocal
from app.services.document_guard import processing_lock
from app.services.gtin_audit import Catalog, analyze
from app.services.moysklad import MoySkladService


async def load_catalog(ms, progress):
    catalog = Catalog()
    async with httpx.AsyncClient(timeout=60) as client:
        for archived in (False, True):
            offset, expected_size = 0, None
            while True:
                response = await ms._request_with_retry(client, 'GET', f'{ms.base_url}/entity/product',
                    params={'limit': 1000, 'offset': offset, 'filter': f'archived={str(archived).lower()}'})
                response.raise_for_status()
                body = response.json()
                rows = body.get('rows')
                if not isinstance(rows, list):
                    raise ValueError('МС не вернул список товаров. Предыдущий отчёт сохранён.')
                size = (body.get('meta') or {}).get('size')
                if expected_size is None:
                    expected_size = size
                elif size != expected_size:
                    raise ValueError('Количество товаров в МС изменилось во время проверки. Запустите её повторно.')
                for row in rows:
                    if bool(row.get('archived')) != archived:
                        raise ValueError('МС вернул товары без учёта фильтра архива. Повторите проверку.')
                    catalog.add(row)
                offset += len(rows)
                await progress('archived_catalog' if archived else 'active_catalog', len(catalog.cards))
                if len(rows) < 1000:
                    if expected_size is not None and expected_size != offset:
                        raise ValueError('Не удалось получить полный каталог МС. Предыдущий отчёт сохранён.')
                    break
                await asyncio.sleep(.2)
    return catalog


async def run_audit(run_id, user_id):
    run_id, user_id = UUID(str(run_id)), UUID(str(user_id))
    async with processing_lock(f'gtin-audit:{user_id}') as acquired:
        if not acquired:
            return
        try:
            async with AsyncSessionLocal() as db:
                run = (await db.execute(select(GtinAuditRun).where(GtinAuditRun.id == run_id,
                    GtinAuditRun.user_id == user_id).with_for_update())).scalar_one_or_none()
                if not run or run.status not in {'queued', 'running'}:
                    return
                run.status, run.phase = 'running', 'active_catalog'
                run.started_at = datetime.now(timezone.utc)
                run.products_checked = 0
                run.error_message = None
                integ = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
                if not integ or not integ.moysklad_token:
                    raise ValueError('МойСклад не подключён. Подключите его в настройках и повторите проверку.')
                token = decrypt_token(integ.moysklad_token)
                mappings = [dict(row) for row in (await db.execute(select(GtinProductMap.gtin,
                    GtinProductMap.product_id, GtinProductMap.product_name).where(GtinProductMap.user_id == user_id))).mappings()]
                await db.commit()

            async def progress(phase, count):
                async with AsyncSessionLocal() as db:
                    await db.execute(update(GtinAuditRun).where(GtinAuditRun.id == run_id,
                        GtinAuditRun.user_id == user_id, GtinAuditRun.status == 'running')
                        .values(phase=phase, products_checked=count, updated_at=datetime.now(timezone.utc)))
                    await db.commit()

            catalog = await load_catalog(MoySkladService(token), progress)
            await progress('analyzing', len(catalog.cards))
            findings, summary = analyze(mappings, catalog)
            async with AsyncSessionLocal() as db:
                run = (await db.execute(select(GtinAuditRun).where(GtinAuditRun.id == run_id,
                    GtinAuditRun.user_id == user_id).with_for_update())).scalar_one()
                if run.status != 'running':
                    return
                await db.execute(delete(GtinAuditFinding).where(GtinAuditFinding.run_id == run_id))
                for start in range(0, len(findings), 250):
                    await db.execute(insert(GtinAuditFinding), [dict(id=uuid4(), run_id=run_id, **row)
                                     for row in findings[start:start + 250]])
                run.summary = summary
                run.status, run.phase = 'done', 'done'
                run.finished_at = datetime.now(timezone.utc)
                # Keep the previous successful report while a new audit runs or fails.
                keep = (await db.execute(select(GtinAuditRun.id).where(GtinAuditRun.user_id == user_id,
                    GtinAuditRun.status == 'done').order_by(GtinAuditRun.created_at.desc()).limit(2))).scalars().all()
                if run_id not in keep:
                    keep = [run_id, *keep[:1]]
                await db.execute(delete(GtinAuditRun).where(GtinAuditRun.user_id == user_id,
                    GtinAuditRun.id.not_in(keep), GtinAuditRun.status.in_(['done', 'error'])))
                await db.commit()
            logger.info('gtin_audit.done', user_id=str(user_id), run_id=str(run_id),
                        products=len(catalog.cards), mappings=len(mappings), findings=len(findings))
        except Exception as exc:
            logger.warning('gtin_audit.failed', user_id=str(user_id), run_id=str(run_id), error_type=type(exc).__name__)
            message = str(exc) if isinstance(exc, ValueError) else 'Не удалось прочитать каталог МойСклад. Проверьте подключение и повторите проверку.'
            async with AsyncSessionLocal() as db:
                await db.execute(update(GtinAuditRun).where(GtinAuditRun.id == run_id,
                    GtinAuditRun.user_id == user_id, GtinAuditRun.status.in_(['queued', 'running']))
                    .values(status='error', phase='error', error_message=message,
                            finished_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
                await db.commit()
