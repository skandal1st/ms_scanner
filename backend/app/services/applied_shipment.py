"""An APPLIED unit may be included explicitly in one MoySklad demand."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services.cz_status import STATUS_ERRORS
from app.services.mark_search import mark_key


# Older CZ checks stored the status only in these exact server-generated messages.
APPLIED_ERRORS = (STATUS_ERRORS['APPLIED'], 'Статус в ЧЗ: APPLIED', 'Статус: APPLIED')
SHIPMENT_STATUSES = (ScanStatus.valid, ScanStatus.overflow, ScanStatus.ms_approved)


def can_include_applied(scan):
    if scan.status != ScanStatus.invalid:
        return False
    check = scan.verification or {}
    return bool(check.get('cz_status', 'APPLIED') == 'APPLIED' and scan.error_message in APPLIED_ERRORS and scan.verified_at
                and check.get('checked_at') and check.get('source') == 'cz_info'
                and not (check.get('ms_error') or check.get('child_issues') or check.get('cz_child_count'))
                and not (scan.withdrawn or scan.is_box or scan.is_barcode or scan.child_codes
                         or scan.keep_aggregate or (scan.box_quantity or 1) != 1)
                and scan.package_type == 'UNIT' and (scan.gtin or scan.moysklad_product_id))


def reserves_mark():
    # A checked APPLIED unit in an open draft can be approved at any moment.
    # Once the document closes, only explicitly included units stay reserved.
    check = Scan.verification
    applied_draft = and_(Document.kind == DocumentKind.demand, Document.status == DocumentStatus.draft,
                         Scan.status == ScanStatus.invalid, Scan.package_type == 'UNIT',
                         Scan.verified_at.is_not(None), Scan.error_message.in_(APPLIED_ERRORS), Scan.withdrawn.is_(False),
                         check['source'].astext == 'cz_info', check['checked_at'].astext.is_not(None),
                         check['ms_error'].astext.is_(None),
                         or_(check['cz_status'].astext == 'APPLIED',
                             ~check.has_key('cz_status')))
    return or_(Scan.status.in_((*SHIPMENT_STATUSES, ScanStatus.scanned, ScanStatus.pending,
                                ScanStatus.unknown_product)), applied_draft)


async def include_applied(db, doc, scan_ids):
    from app.services.mixed_import import conflicts_elsewhere
    if doc.kind != DocumentKind.demand or not doc.moysklad_id or (doc.upd_meta or {}).get('shipment_correction'):
        raise HTTPException(409, 'Выберите черновик отгрузки МойСклад.')
    scans = (await db.scalars(select(Scan).where(Scan.document_id == doc.id)
                              .order_by(Scan.id).with_for_update())).all()
    selected = [s for s in scans if s.id in scan_ids]
    if not selected or len(selected) != len(scan_ids):
        raise HTTPException(409, 'Список марок изменился. Обновите сборку.')
    if any(s.status != ScanStatus.ms_approved and not can_include_applied(s) for s in selected):
        raise HTTPException(409, 'Состояние марки изменилось. Проверьте результаты ЧЗ.')
    newly_approved = [s for s in selected if s.status != ScanStatus.ms_approved]
    if not newly_approved:
        return selected
    keys = {mark_key(s.code) for s in newly_approved}
    occupied = {mark_key(code) for s in scans if s.status in SHIPMENT_STATUSES
                for code in (s.code, *(s.child_codes or []))}
    if len(keys) != len(newly_approved) or keys & occupied:
        raise HTTPException(409, 'Марка уже включена в эту отгрузку.')
    elsewhere = await conflicts_elsewhere(db, doc, [s.code for s in newly_approved], include_children=True)
    if keys & elsewhere.keys():
        raise HTTPException(409, 'Марка уже используется в другой отгрузке. Проверьте сборки.')
    now = datetime.now(timezone.utc).isoformat()
    for scan in newly_approved:
        scan.status = ScanStatus.ms_approved
        scan.verification = {**scan.verification, 'cz_status': 'APPLIED', 'ms_approved_at': now,
                             'ms_approved_by': str(doc.user_id)}
    await db.commit()
    return selected
