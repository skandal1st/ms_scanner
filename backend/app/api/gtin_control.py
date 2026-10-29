"""Account-scoped, desktop-only access to persisted GTIN audits."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User, Integration, GtinAuditRun, GtinAuditFinding
from app.db.session import get_db
from app.services.document_guard import processing_lock
from app.services.gtin_audit import ACTIVE, CATEGORIES

router = APIRouter(prefix='/gtin-control', tags=['gtin-control'])


def run_view(row):
    return {key: getattr(row, key) for key in ('id', 'status', 'phase', 'products_checked', 'summary',
        'error_message', 'created_at', 'started_at', 'finished_at', 'updated_at')} if row else None


async def active_run(db, user_id):
    return (await db.execute(select(GtinAuditRun).where(GtinAuditRun.user_id == user_id,
        GtinAuditRun.status.in_(ACTIVE)).order_by(GtinAuditRun.created_at.desc()).limit(1))).scalar_one_or_none()


@router.get('')
async def status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    latest = (await db.execute(select(GtinAuditRun).where(GtinAuditRun.user_id == current_user.id)
        .order_by(GtinAuditRun.created_at.desc()).limit(1))).scalar_one_or_none()
    report = (await db.execute(select(GtinAuditRun).where(GtinAuditRun.user_id == current_user.id,
        GtinAuditRun.status == 'done').order_by(GtinAuditRun.created_at.desc()).limit(1))).scalar_one_or_none()
    return {'job': run_view(latest) if latest and (not report or latest.id != report.id) else None,
            'report': run_view(report)}


@router.post('/refresh', status_code=202)
async def refresh(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    async with processing_lock(f'gtin-audit:{current_user.id}') as acquired:
        existing = await active_run(db, current_user.id)
        if not acquired:
            if existing:
                return run_view(existing)
            raise HTTPException(409, 'Проверка уже запускается. Обновите страницу через несколько секунд.')
        if existing:
            updated = existing.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if updated > datetime.now(timezone.utc) - timedelta(minutes=30):
                return run_view(existing)
            existing.status, existing.phase = 'error', 'error'
            existing.error_message = 'Предыдущая проверка прервалась. Запущена новая.'
            existing.finished_at = datetime.now(timezone.utc)
            await db.flush()
        integ = (await db.execute(select(Integration.id).where(Integration.user_id == current_user.id,
            Integration.moysklad_token.is_not(None)))).scalar_one_or_none()
        if not integ:
            raise HTTPException(409, 'МойСклад не подключён. Подключите его в настройках.')
        run = GtinAuditRun(user_id=current_user.id)
        db.add(run)
        await db.commit()
        await db.refresh(run)
    # Release the shared lock before dispatch: a fast worker must not consume
    # the job while the API still owns its processing lock.
    try:
        from app.worker.tasks import gtin_audit_task
        gtin_audit_task.delay(str(run.id), str(current_user.id))
    except Exception as exc:
        run.status, run.phase = 'error', 'error'
        run.error_message = 'Не удалось запустить проверку. Повторите попытку.'
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(503, run.error_message) from exc
    return run_view(run)


@router.get('/findings')
async def findings(run_id: UUID, kind: Literal['mapping', 'duplicate'] = 'mapping',
                   category: str = '', q: str = Query('', max_length=200),
                   include_archived: bool = False, page: int = Query(1, ge=1),
                   page_size: int = Query(50, ge=1, le=100),
                   current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    owned = (await db.execute(select(GtinAuditRun.id).where(GtinAuditRun.id == run_id,
        GtinAuditRun.user_id == current_user.id, GtinAuditRun.status == 'done'))).scalar_one_or_none()
    if not owned:
        raise HTTPException(404, 'Отчёт проверки GTIN не найден. Запустите проверку.')
    if category and category not in CATEGORIES:
        raise HTTPException(400, 'Неизвестный тип расхождения GTIN.')
    filters = [GtinAuditFinding.run_id == run_id, GtinAuditFinding.kind == kind]
    if category:
        filters.append(GtinAuditFinding.category == category)
    if kind == 'duplicate' and not include_archived:
        filters.append(GtinAuditFinding.category == 'duplicate_active')
    if q.strip():
        filters.append(GtinAuditFinding.search_text.contains(q.strip().casefold(), autoescape=True))
    total = (await db.execute(select(func.count()).select_from(GtinAuditFinding).where(*filters))).scalar_one()
    rows = (await db.execute(select(GtinAuditFinding).where(*filters)
        .order_by(GtinAuditFinding.priority, GtinAuditFinding.gtin, GtinAuditFinding.id)
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {'total': total, 'page': page, 'page_size': page_size, 'items': [
        {key: getattr(row, key) for key in ('id', 'kind', 'category', 'gtin', 'saved_product_id',
            'saved_product_name', 'saved_card', 'candidates')} for row in rows]}
