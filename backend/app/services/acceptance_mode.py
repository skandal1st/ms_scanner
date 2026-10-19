"""Acceptance policy, frozen when a document is queued for processing."""
from sqlalchemy import select
from app.db.models import DocumentStatus, OrganizationProfile


async def acceptance_cz_check_enabled(db, doc):
    saved = (getattr(doc, 'upd_meta', None) or {}).get('acceptance_cz_check_enabled')
    if doc.status != DocumentStatus.draft:
        return saved if isinstance(saved, bool) else True
    profile_id = getattr(doc, 'organization_profile_id', None)
    if profile_id is None:
        return True  # Legacy documents retain the previous behavior.
    enabled = (await db.execute(select(OrganizationProfile.acceptance_cz_check_enabled).where(
        OrganizationProfile.id == profile_id, OrganizationProfile.user_id == doc.user_id,
    ))).scalar_one_or_none()
    return enabled is not False
