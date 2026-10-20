"""Background snapshot update; successful evidence survives partial failures."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import MarkHistorySnapshot, OrganizationProfile
from app.db.session import AsyncSessionLocal
from app.services.mark_history import fetch_history
from app.services.gtin_cz_group import get_cached_pg
from app.services.chestnyznak import extract_gtin


async def refresh_snapshot(snapshot_id, refresh_id):
    async with AsyncSessionLocal() as db:
        row = await db.get(MarkHistorySnapshot, UUID(snapshot_id))
        if not row or not row.refreshing or row.refresh_id != UUID(refresh_id):
            return
        profile = await db.get(OrganizationProfile, row.organization_profile_id)
        if not profile or profile.user_id != row.user_id:
            return
        code, user_id = row.lookup_code, row.user_id
        now = datetime.now(timezone.utc)
        token = None
        if profile.cz_token and (not profile.cz_token_expires_at or profile.cz_token_expires_at > now):
            try:
                token = decrypt_token(profile.cz_token)
            except Exception:
                pass
        groups = list(profile.cz_product_groups or [])
        preferred = await get_cached_pg(db, extract_gtin(code))
        await db.rollback()  # Release the connection while waiting for external IO.
        try:
            result = await fetch_history(code, token, groups, user_id, preferred)
        except Exception as exc:
            logger.warning('mark_history.refresh_failed', snapshot_id=str(snapshot_id), error_type=type(exc).__name__)
            result = {'current': None, 'events': None, 'info_error': 'Не удалось обновить сведения ЧЗ. Повторите запрос.',
                      'history_error': 'Не удалось обновить историю ЧЗ. Повторите запрос.'}
        saved = (await db.execute(select(MarkHistorySnapshot).where(MarkHistorySnapshot.id == UUID(snapshot_id))
                                 .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if not saved or saved.refresh_id != UUID(refresh_id):
            return
        checked_at = datetime.now(timezone.utc)
        if result['current'] is not None:
            current = dict(result['current'])
            previous = saved.current or {}
            if current.get('owner_inn') or current.get('owner_name'):
                current.update(owner_checked_at=checked_at.isoformat(), owner_is_stale=False)
            elif previous.get('owner_inn') or previous.get('owner_name'):
                # A later privacy-limited answer must not erase the last known
                # owner. Keep its own timestamp; never call this the new owner.
                current.update({name: previous.get(name) for name in (
                    'owner_inn', 'owner_name', 'owner_address', 'region', 'region_source')})
                current.update(owner_checked_at=previous.get('owner_checked_at') or
                    (saved.info_checked_at.isoformat() if saved.info_checked_at else None), owner_is_stale=True)
            saved.current = current
            saved.info_checked_at = checked_at
        if result['events'] == [] and saved.events:
            # A privacy/archival change may turn an earlier nonempty answer into
            # an empty one. Preserve the known evidence with its original date.
            result['history_error'] = 'ЧЗ вернул пустой список событий. Показана ранее сохранённая история.'
        elif result['events'] is not None:
            saved.events = result['events']
            saved.history_checked_at = checked_at
        saved.info_error = result['info_error']
        saved.history_error = result['history_error']
        saved.refreshing = False
        await db.commit()
        logger.info('mark_history.refreshed', snapshot_id=str(snapshot_id),
                    info_received=result['current'] is not None, history_received=result['events'] is not None)
