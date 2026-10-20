"""Cached mark history, scoped to the active organization; CZ runs in Celery."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_active_organization_profile
from app.core.config import settings
from app.db.models import MarkHistorySnapshot, OrganizationProfile, User
from app.db.session import get_db
from app.services.mark_history import AUTH_ERROR, identity

router = APIRouter(prefix='/marks/history', tags=['marks'])


class HistoryRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    force: bool = False


class HistoryResponse(BaseModel):
    organization_profile_id: UUID
    organization_name: str
    current: dict | None = None
    events: list[dict] | None = None
    info_checked_at: datetime | None = None
    history_checked_at: datetime | None = None
    requested_at: datetime | None = None
    refreshing: bool = False
    info_error: str | None = None
    history_error: str | None = None


def response(row, profile):
    data = {field: getattr(row, field) for field in ('current', 'events', 'info_checked_at',
            'history_checked_at', 'requested_at', 'refreshing', 'info_error', 'history_error')} if row else {}
    if data.get('refreshing') and (not data['requested_at'] or data['requested_at'] < datetime.now(timezone.utc)-timedelta(minutes=2)):
        data.update(refreshing=False, info_error='Обновление не завершилось. Повторите запрос.',
                    history_error='Обновление истории не завершилось. Повторите запрос.')
    return HistoryResponse(organization_profile_id=profile.id, organization_name=profile.name, **data)


def code_identity(code):
    try:
        return identity(code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def query(user, profile, key):
    return select(MarkHistorySnapshot).where(MarkHistorySnapshot.user_id == user.id,
        MarkHistorySnapshot.organization_profile_id == profile.id, MarkHistorySnapshot.mark_key == key)


@router.get('', response_model=HistoryResponse)
async def read_history(code: str = Query(..., min_length=1, max_length=4096),
                       user: User = Depends(get_current_user),
                       profile: OrganizationProfile = Depends(get_active_organization_profile),
                       db: AsyncSession = Depends(get_db)):
    _, key = code_identity(code)
    row = (await db.execute(query(user, profile, key))).scalar_one_or_none()
    return response(row, profile)


@router.post('', response_model=HistoryResponse)
async def refresh_history(body: HistoryRequest, user: User = Depends(get_current_user),
                          profile: OrganizationProfile = Depends(get_active_organization_profile),
                          db: AsyncSession = Depends(get_db)):
    code, key = code_identity(body.code)
    # Serialize only the short claim/dispatch step, including first insertion.
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                     {'key': f'mark-history:{user.id}:{profile.id}:{key}'})
    row = (await db.execute(query(user, profile, key).with_for_update())).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row and row.refreshing and row.requested_at and row.requested_at > now-timedelta(minutes=2):
        return response(row, profile)
    if row and not body.force and not row.info_error and not row.history_error and row.info_checked_at and row.history_checked_at:
        if min(row.info_checked_at, row.history_checked_at) > now-timedelta(minutes=5):
            return response(row, profile)
    if row is None:
        row = MarkHistorySnapshot(user_id=user.id, organization_profile_id=profile.id, mark_key=key, lookup_code=code)
        db.add(row)
    row.lookup_code = code
    row.requested_at = now
    row.refresh_id = uuid4()
    row.info_error = row.history_error = None
    row.refreshing = False
    if not profile.cz_token or (profile.cz_token_expires_at and profile.cz_token_expires_at <= now):
        row.info_error = row.history_error = AUTH_ERROR
    elif settings.CZ_MOCK_MODE:
        row.info_error = row.history_error = 'История ЧЗ недоступна в демонстрационном режиме.'
    else:
        row.refreshing = True
    await db.commit()
    await db.refresh(row)
    if row.refreshing:
        job_id = row.refresh_id
        try:
            from app.worker.tasks import refresh_mark_history_task
            refresh_mark_history_task.delay(str(row.id), str(job_id))
        except Exception:
            # Do not clobber another retry that already owns this snapshot.
            from sqlalchemy import update
            await db.execute(update(MarkHistorySnapshot).where(MarkHistorySnapshot.id == row.id,
                MarkHistorySnapshot.refresh_id == job_id).values(refreshing=False,
                info_error='Не удалось запустить обновление ЧЗ. Повторите запрос.',
                history_error='Не удалось запустить обновление истории. Повторите запрос.'))
            await db.commit()
            await db.refresh(row)
    return response(row, profile)
