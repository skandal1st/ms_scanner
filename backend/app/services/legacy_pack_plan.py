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
    from app.db.models import Document, Scan, ScanStatus
    doc = (await db.execute(select(Document).where(Document.id == doc.id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not doc or getattr(doc.status, 'value', doc.status) != 'draft':
        return
    doc.plan = fresh
    from app.services.plan_matching import unique_plan_product, plan_pack_quantity
    scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id,
        Scan.status.in_([ScanStatus.scanned, ScanStatus.valid, ScanStatus.overflow]),
        Scan.is_box.is_(False), Scan.is_barcode.is_(False)).with_for_update())).scalars().all()
    for scan in scans:
        quantity = plan_pack_quantity(fresh, scan.gtin)
        planned = unique_plan_product(fresh, scan.gtin)
        if quantity and not scan.child_codes and (scan.box_quantity or 1) == 1 and planned and scan.moysklad_product_id in (None, planned['product_id']):
            scan.box_quantity = quantity
            scan.package_type = 'GROUP'
            scan.keep_aggregate = True
    await db.commit()
