"""Copy a completed local shipment into a separate, reviewable writeoff draft."""
import copy

from fastapi import HTTPException
from sqlalchemy import select, exists, or_

from app.db.models import Document, DocumentKind, DocumentStatus, Scan, ScanStatus, TsdDocumentSession
from app.services.document_guard import editable_document
from app.services.moysklad import MoySkladService


async def import_shipment_codes(db, document_id, user_id, *, source_id=None):
    target = await editable_document(db, document_id, user_id)
    if target.kind != DocumentKind.loss or not target.moysklad_id:
        raise HTTPException(400, 'Выберите списание, привязанное к отгрузке.')
    if target.cz_doc_ids:
        raise HTTPException(409, 'Списание уже отправлялось в ЧЗ. Изменять его марки нельзя.')

    completed = exists(select(TsdDocumentSession.id).where(
        TsdDocumentSession.document_id == Document.id, TsdDocumentSession.status == 'completed'))
    active = exists(select(TsdDocumentSession.id).where(
        TsdDocumentSession.document_id == Document.id, TsdDocumentSession.status == 'active'))
    source = (await db.execute(select(Document).where(
        Document.user_id == user_id,
        Document.organization_profile_id == target.organization_profile_id,
        Document.moysklad_id == target.moysklad_id,
        Document.kind == DocumentKind.demand,
        *([Document.id == source_id] if source_id is not None else []),
        Document.upd_meta['superseded_by_document_id'].astext.is_(None),
        or_(Document.status == DocumentStatus.accepted,
            (Document.status == DocumentStatus.draft) & completed & ~active
            & Document.upd_meta['shipment_correction'].astext.is_(None)),
    ).order_by(Document.created_at.desc()).limit(1).with_for_update())).scalar_one_or_none()
    if source is None:
        raise HTTPException(409, 'Готовая сборка этой отгрузки не найдена. Сначала завершите сборку на ТСД или отправьте отгрузку.')
    if (target.moysklad_store_id and source.moysklad_store_id
            and target.moysklad_store_id != source.moysklad_store_id):
        raise HTTPException(409, 'Склад списания отличается от склада готовой сборки.')

    eligible = [ScanStatus.valid, ScanStatus.overflow, ScanStatus.scanned]
    scans = (await db.execute(select(Scan).where(
        Scan.document_id == source.id, Scan.is_barcode.is_(False), Scan.status.in_(eligible),
    ).order_by(Scan.scanned_at, Scan.id))).scalars().all()
    if not scans:
        raise HTTPException(409, 'В готовой сборке нет марок для загрузки в списание.')
    existing = (await db.execute(select(Scan.code).where(Scan.document_id == target.id))).scalars().all()
    seen = {MoySkladService._cis_dedup_key(code) for code in existing}
    additions = []
    for scan in scans:
        key = MoySkladService._cis_dedup_key(scan.code)
        if key in seen:
            continue
        seen.add(key)
        additions.append(scan)

    # The source shipment is a different operation; another writeoff is not.
    # Apply the same cross-document conflict rule as manual scanning, atomically.
    if additions:
        conflict = (await db.execute(select(Document.name).join(Scan, Scan.document_id == Document.id).where(
            Document.user_id == user_id, Document.kind == DocumentKind.loss, Document.id != target.id,
            Document.upd_meta['superseded_by_document_id'].astext.is_(None),
            Scan.code.in_([scan.code for scan in additions]),
            Scan.status.in_([*eligible, ScanStatus.pending, ScanStatus.unknown_product]),
        ).limit(1))).scalar_one_or_none()
        if conflict is not None:
            raise HTTPException(409, f'Марки уже используются в списании «{conflict}». Проверьте его перед загрузкой.')

    for scan in additions:
        db.add(Scan(document_id=target.id, code=scan.code, original_code=scan.original_code, gtin=scan.gtin, serial=scan.serial,
            moysklad_product_id=scan.moysklad_product_id, product_name=scan.product_name,
            is_box=scan.is_box, box_expanded=scan.box_expanded,
            box_quantity=scan.box_quantity, package_type=scan.package_type,
            keep_aggregate=scan.keep_aggregate, child_codes=copy.deepcopy(scan.child_codes),
            status=ScanStatus.scanned))
    target.upd_meta = {**(target.upd_meta or {}), 'shipment_codes_import': {'source_id': str(source.id)}}
    await db.commit()
    return {'imported': len(additions), 'skipped': len(scans) - len(additions), 'source_name': source.name}
