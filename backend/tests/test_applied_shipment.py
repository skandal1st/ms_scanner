from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.scans import ScanResponse
from app.db.models import DocumentKind, Scan, ScanStatus
from app.services.applied_shipment import can_include_applied, include_applied
from app.services.cz_status import cz_status_error
from app.services.scan_verification import evidence
from app.services.chestnyznak import CisCheck


def applied_scan():
    now = datetime.now(timezone.utc)
    code = '010464027597268621abCD123'
    return Scan(id=uuid4(), document_id=uuid4(), code=code, gtin=code[2:16], package_type='UNIT',
                scanned_at=now, verified_at=now, status=ScanStatus.invalid, is_box=False,
                is_barcode=False, withdrawn=False, keep_aggregate=False, box_expanded=False,
                verification=evidence(CisCheck(code, found=True, status='APPLIED', owner_inn='222'), '111'),
                error_message=cz_status_error('APPLIED'))


def test_checked_applied_with_manufacturer_owner_and_legacy_messages():
    scan = applied_scan()
    assert ScanResponse.model_validate(scan).can_include_in_shipment
    del scan.verification['cz_status']
    for message in (cz_status_error('APPLIED'), 'Статус: APPLIED', 'Статус в ЧЗ: APPLIED'):
        scan.error_message = message
        assert can_include_applied(scan)


@pytest.mark.parametrize('scan_fields,check_fields', [
    ({}, {'cz_status': status}) for status in ('WITHDRAWN', 'RETIRED', 'EMITTED', 'NOT_FOUND', None)
] + [
    ({'withdrawn': True}, {}), ({'status': ScanStatus.used_in_other_doc}, {}),
    ({'is_box': True}, {}), ({'package_type': 'GROUP'}, {}), ({'verified_at': None}, {}),
    ({'error_message': 'Другая ошибка'}, {}), ({}, {'source': 'format'}),
    ({}, {'ms_error': 'МС отклонил марку'}), ({}, {'cz_child_count': 1}),
    ({}, {'child_issues': [{'code': 'child'}]}), ({}, {'checked_at': None}),
])
def test_only_checked_applied_units_qualify(scan_fields, check_fields):
    scan = applied_scan()
    for key, value in scan_fields.items():
        setattr(scan, key, value)
    scan.verification.update(check_fields)
    assert not can_include_applied(scan)


async def test_approval_is_scoped_atomic_and_idempotent(monkeypatch):
    good, bad = applied_scan(), applied_scan()
    bad.withdrawn = True
    doc = SimpleNamespace(id=good.document_id, kind=DocumentKind.demand, moysklad_id='ms', upd_meta={}, user_id=uuid4())
    db = SimpleNamespace(scalars=AsyncMock(return_value=Mock(all=lambda: [good, bad])), commit=AsyncMock())
    monkeypatch.setattr('app.services.mixed_import.conflicts_elsewhere', AsyncMock(return_value={}))
    for ids in ({good.id, bad.id}, {uuid4()}):
        with pytest.raises(HTTPException):
            await include_applied(db, doc, ids)
        assert good.status == ScanStatus.invalid
        db.commit.assert_not_awaited()
    await include_applied(db, doc, {good.id})
    await include_applied(db, doc, {good.id})
    db.commit.assert_awaited_once()
    assert good.status == ScanStatus.ms_approved
    assert good.verification['owner_result'] == 'mismatch'
    assert good.error_message == cz_status_error('APPLIED')
    doc.kind = DocumentKind.supply
    with pytest.raises(HTTPException):
        await include_applied(db, doc, {good.id})
