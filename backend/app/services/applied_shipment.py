"""Explicit, status-specific consent to include checked units in one MoySklad demand."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus
from app.services.cz_status import STATUS_ERRORS, WITHDRAWN_STATUSES, WITHDRAW_FLAG_MESSAGE
from app.services.mark_search import mark_key


ShipmentInclusionReason = Literal['applied', 'withdrawn']
# Older CZ checks stored the status only in these exact server-generated messages.
STATUS_MESSAGES = {status: (STATUS_ERRORS[status], f'Статус в ЧЗ: {status}', f'Статус: {status}')
                   for status in ('APPLIED', *WITHDRAWN_STATUSES)}
LEGACY_STATUS = {message: status for status, messages in STATUS_MESSAGES.items() for message in messages}
SHIPMENT_STATUSES = (ScanStatus.valid, ScanStatus.overflow, ScanStatus.ms_approved)


def checked_status(scan):
    check = scan.verification or {}
    return check.get('cz_status', LEGACY_STATUS.get(scan.error_message))


def shipment_inclusion_reason(scan) -> ShipmentInclusionReason | None:
    if scan.status not in (ScanStatus.invalid, ScanStatus.ms_approved):
        return None
    check = scan.verification or {}
    status = checked_status(scan)
    if status not in STATUS_MESSAGES:
        return None
    reason = 'applied' if status == 'APPLIED' else 'withdrawn'
    if scan.status == ScanStatus.ms_approved:
        return reason
    if (not scan.verified_at or not check.get('checked_at') or check.get('source') != 'cz_info'
            or check.get('ms_error') or check.get('child_issues') or check.get('cz_child_count')
            or scan.is_box or scan.is_barcode or scan.child_codes or scan.keep_aggregate
            or (scan.box_quantity or 1) != 1 or scan.package_type != 'UNIT'
            or not (scan.gtin or scan.moysklad_product_id)):
        return None
    if scan.withdrawn:
        if reason != 'withdrawn':
            return None
        # markWithdraw also covers blocked codes. Only the original CZ status proves withdrawal.
        flagged_error = WITHDRAW_FLAG_MESSAGE + (f': {scan.withdraw_reason}' if scan.withdraw_reason else '')
        if scan.error_message == flagged_error:
            return reason
    return reason if scan.error_message in STATUS_MESSAGES[status] else None


def can_include_applied(scan):
    return scan.status == ScanStatus.invalid and shipment_inclusion_reason(scan) == 'applied'


def approved_cz_statuses(scans):
    return {s.code: checked_status(s) for s in scans if s.status == ScanStatus.ms_approved}


def reserves_mark():
    # A checked unit in an open draft can be approved at any moment.
    # Once the document closes, only explicitly included units stay reserved.
    check = Scan.verification
    status = check['cz_status'].astext
    matches = []
    for value, messages in STATUS_MESSAGES.items():
        match = and_(Scan.error_message.in_(messages), or_(status == value, ~check.has_key('cz_status')))
        if value == 'APPLIED':
            match = and_(match, Scan.withdrawn.is_(False))
        matches.append(match)
    flagged_withdrawal = and_(Scan.withdrawn.is_(True), status.in_(WITHDRAWN_STATUSES),
                              or_(Scan.error_message == WITHDRAW_FLAG_MESSAGE,
                                  Scan.error_message == WITHDRAW_FLAG_MESSAGE + ': ' + Scan.withdraw_reason))
    approvable_draft = and_(
        Document.kind == DocumentKind.demand, Document.status == DocumentStatus.draft,
        Scan.status == ScanStatus.invalid, Scan.package_type == 'UNIT', Scan.verified_at.is_not(None),
        check['source'].astext == 'cz_info', check['checked_at'].astext.is_not(None),
        check['ms_error'].astext.is_(None),
        or_(flagged_withdrawal, *matches),
    )
    return or_(Scan.status.in_((*SHIPMENT_STATUSES, ScanStatus.scanned, ScanStatus.pending,
                                ScanStatus.unknown_product)), approvable_draft)


async def include_shipment_marks(db, doc, scan_ids, reason: ShipmentInclusionReason):
    from app.services.mixed_import import conflicts_elsewhere
    if doc.kind != DocumentKind.demand or not doc.moysklad_id or (doc.upd_meta or {}).get('shipment_correction'):
        raise HTTPException(409, 'Выберите черновик отгрузки МойСклад.')
    scans = (await db.scalars(select(Scan).where(Scan.document_id == doc.id)
                              .order_by(Scan.id).with_for_update())).all()
    selected = [s for s in scans if s.id in scan_ids]
    if not selected or len(selected) != len(scan_ids):
        raise HTTPException(409, 'Список марок изменился. Обновите сборку.')
    if any(shipment_inclusion_reason(s) != reason for s in selected):
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
        scan.verification = {**scan.verification, 'cz_status': checked_status(scan), 'ms_approved_at': now,
                             'ms_approved_by': str(doc.user_id)}
        scan.status = ScanStatus.ms_approved
    await db.commit()
    return selected
