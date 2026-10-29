"""Upgrade saved pack metadata when opening a document, never while scanning."""
from app.core.logging import logger

async def refresh_legacy_pack_plan(db, doc, ms, kind=None):
    if getattr(doc.status, 'value', doc.status) != 'draft' or not doc.moysklad_id:
        return
    if not any('pack_quantities' not in item for item in doc.plan or []):
        return
    try:
        fresh = await ms.build_plan(kind or doc.kind.value, doc.moysklad_id)
    except Exception as exc:
        logger.warning('document.pack_plan_refresh_failed', document_id=str(doc.id), error=str(exc))
        return
    from sqlalchemy import select
    from app.db.models import Document
    doc = (await db.execute(select(Document).where(Document.id == doc.id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not doc or getattr(doc.status, 'value', doc.status) != 'draft':
        return
    doc.plan = fresh
    # MS pack aliases help match products, but cannot reclassify existing marks.
    await db.commit()
