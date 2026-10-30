"""Explicit single-scan packaging correction, with durable pending state and recovery."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import array
from app.db.models import Document, DocumentStatus, Scan, ScanStatus
from app.db.session import AsyncSessionLocal
from app.services.document_guard import editable_document
from app.services.scan_events import publish_scan
from app.services.chestnyznak import ChestnyZnakService, _gs1_check_digit_ok
from app.services.scan_packaging_discovery import packaging_metadata
from app.services.scan_packaging import package_type
from app.services.plan_matching import unique_plan_product
from app.core.logging import logger


async def start(db, scan, user_id):
    await editable_document(db, scan.document_id, user_id)
    await db.refresh(scan)
    if scan.is_barcode or scan.status == ScanStatus.used_in_other_doc:
        raise HTTPException(409, 'Уточнение доступно для марки в текущей сборке, кроме повторов из другого документа.')
    if scan.status == ScanStatus.pending:
        raise HTTPException(409, 'Дождитесь завершения текущей проверки марки.')
    previous = scan.verification
    old_status = scan.status
    request_id = str(uuid4())
    scan.verification = {'source': 'packaging_recheck', 'request_id': request_id,
        'previous_status': old_status.value, 'previous_verification': previous,
        'requested_at': datetime.now(timezone.utc).isoformat()}
    scan.status = ScanStatus.pending
    await db.commit()
    await publish_scan(user_id, scan)
    from app.worker.tasks import recheck_scan_packaging_task
    try:
        recheck_scan_packaging_task.delay(str(scan.id), str(user_id), request_id)
    except Exception as exc:
        scan.status, scan.verification = old_status, previous
        await db.commit()
        await publish_scan(user_id, scan)
        raise HTTPException(503, 'Не удалось запустить уточнение в ЧЗ. Повторите попытку.') from exc
    return scan


def checked_metadata(info, original_gtin):
    if not info or (not package_type(info.package_type) and not info.children):
        raise ValueError('ЧЗ не подтвердил тип упаковки. Данные марки сохранены; повторите уточнение позже.')
    metadata = packaging_metadata(info, original_gtin)
    if metadata['package_type'] == 'UNIT' and not info.gtin:
        raise ValueError('ЧЗ не вернул GTIN пачки. Данные марки сохранены.')
    if not _gs1_check_digit_ok(metadata['gtin'] or ''):
        raise ValueError('ЧЗ не вернул корректный GTIN товара. Данные марки сохранены.')
    return metadata


async def run(scan_id, user_id, request_id):
    from app.worker.tasks import _get_cz_token, _get_cz_product_groups
    async with AsyncSessionLocal() as db:
        scan = await db.get(Scan, scan_id)
        if not current(scan, request_id):
            return
        doc = await db.get(Document, scan.document_id)
        if not doc or str(doc.user_id) != str(user_id) or doc.status != DocumentStatus.draft:
            return
        did, code, gtin, is_box = doc.id, scan.code, scan.gtin, scan.is_box
        token = await _get_cz_token(db, user_id, did)
        groups = await _get_cz_product_groups(db, user_id, did)
        await db.rollback()
        metadata, error = None, None
        try:
            if not token:
                raise ValueError('Войдите в ЧЗ по УКЭП и повторите уточнение. Данные марки сохранены.')
            cz = ChestnyZnakService(token=token, mock=False, product_groups=groups)
            if is_box:
                children = await cz.unpack_box(code)
                info = SimpleNamespace(package_type='BOX', children=children, gtin=None)
            else:
                info = await cz.get_code_info(code)
            metadata = checked_metadata(info, gtin)
        except Exception as exc:
            logger.warning('packaging_recheck.failed', scan_id=str(scan_id), error_type=type(exc).__name__)
            error = str(exc) if isinstance(exc, ValueError) else 'Не удалось получить состав из ЧЗ. Данные марки сохранены; повторите уточнение.'
        await db.rollback()
        doc = (await db.execute(select(Document).where(Document.id == did).with_for_update()
            .execution_options(populate_existing=True))).scalar_one_or_none()
        scan = (await db.execute(select(Scan).where(Scan.id == scan_id).with_for_update()
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if not doc or doc.status != DocumentStatus.draft or not current(scan, request_id):
            return
        marker = scan.verification
        if metadata and not error:
            leaves = metadata['child_codes'] or [code]
            conflict = (await db.execute(select(Scan.id).join(Document).where(
                Document.user_id == doc.user_id, Document.kind == doc.kind,
                Document.upd_meta['superseded_by_document_id'].astext.is_(None), Scan.id != scan.id,
                or_(Scan.code.in_(leaves), Scan.child_codes.has_any(array(leaves))),
                Scan.status.in_([ScanStatus.valid, ScanStatus.overflow, ScanStatus.pending,
                    ScanStatus.scanned, ScanStatus.unknown_product]),
            ).limit(1))).scalar_one_or_none()
            if conflict:
                error = 'Состав пересекается с марками в этой или другой сборке. Данные сохранены; проверьте повторы.'
        if error:
            scan.status = ScanStatus(marker['previous_status'])
            scan.verification = {**(marker.get('previous_verification') or {}),
                'source': (marker.get('previous_verification') or {}).get('source', 'packaging'),
                'packaging_request_id': request_id}
            scan.error_message = error
        else:
            # Preserve the user's whole/expanded write mode when correcting quantity.
            keep = scan.keep_aggregate if scan.child_codes else True
            for field, value in metadata.items():
                setattr(scan, field, value)
            if metadata['child_codes']:
                scan.keep_aggregate = keep
            product = unique_plan_product(doc.plan, metadata['gtin'])
            if product:
                scan.moysklad_product_id = product['product_id']
                scan.product_name = product.get('product_name')
            elif gtin != metadata['gtin']:
                scan.moysklad_product_id = None
                scan.product_name = None
            from app.services.packaging_formats import learn
            learned = await learn(db, code, metadata) if not is_box else False
            scan.status = ScanStatus.scanned
            scan.verified_at = None
            scan.error_message = None
            scan.verification = {'source': 'packaging', 'packaging_checked_at': datetime.now(timezone.utc).isoformat(),
                'packaging_request_id': request_id,
                'packaging_rule_saved': learned, 'packaging_message':
                f"По ответу ЧЗ: {'пачка' if metadata['package_type'] == 'UNIT' else 'упаковка'}, {metadata['box_quantity'] or 1} шт."
                + (' Правило сохранено в справочнике.' if learned else '')}
        await db.commit()
        await publish_scan(user_id, scan)


def current(scan, request_id):
    return bool(scan and scan.status == ScanStatus.pending
        and (scan.verification or {}).get('source') == 'packaging_recheck'
        and scan.verification.get('request_id') == request_id)
