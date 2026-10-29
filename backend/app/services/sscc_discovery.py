"""Resolve a durable SSCC scan without holding up the scanner HTTP request."""
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import array

from app.core.config import settings
from app.core.logging import logger
from app.db.models import Document, DocumentStatus, Scan, ScanStatus
from app.db.session import AsyncSessionLocal
from app.services.chestnyznak import ChestnyZnakService, extract_gtin, normalize_gtin_key
from app.services.plan_matching import plan_gtin_keys, unique_plan_product
from app.services.scan_events import publish_scan


async def discover_sscc(scan_id, user_id):
    from app.worker.tasks import _get_cz_token, _get_cz_product_groups, _verify_box_async

    async with AsyncSessionLocal() as db:
        scan = await db.get(Scan, scan_id)
        if not scan or not scan.is_box:
            return
        doc = await db.get(Document, scan.document_id)
        if not doc or str(doc.user_id) != str(user_id) or doc.status != DocumentStatus.draft:
            return
        if scan.box_quantity:
            if scan.status == ScanStatus.pending and not scan.box_expanded:
                # Recover a delivery interrupted between metadata commit and
                # final classification without fetching CZ composition again.
                await db.rollback()
                await _verify_box_async(str(scan_id), str(user_id))
            return
        if scan.status not in {ScanStatus.pending, ScanStatus.scanned, ScanStatus.invalid}:
            return
        document_id, code, unpack = doc.id, scan.code, bool(scan.box_expanded)
        plan = doc.plan or []
        plan_gtins = list({gtin for p in plan for gtin in plan_gtin_keys(p)})
        # Release the database transaction before waiting for the external API.
        await db.rollback()
        error, children, gtin, quantity = None, None, None, None
        try:
            token = await _get_cz_token(db, user_id, document_id)
            if not token and not settings.CZ_MOCK_MODE:
                raise ValueError('Войдите в ЧЗ по УКЭП для проверки короба.')
            groups = await _get_cz_product_groups(db, user_id, document_id)
            await db.rollback()
            cz = ChestnyZnakService(token=token, mock=settings.CZ_MOCK_MODE, product_groups=groups)
            if unpack:
                children = list(dict.fromkeys(await cz.unpack_box(code, plan_gtins)))
                gtins = {normalize_gtin_key(extract_gtin(child)) for child in children}
                if not children or None in gtins or len(gtins) != 1:
                    raise ValueError('ЧЗ не вернул однородный состав короба.')
                gtin, quantity = next(iter(gtins)), len(children)
            else:
                info = await cz.get_sscc_info(code, plan_gtins)
                gtin, quantity = normalize_gtin_key(info.gtin), info.quantity
                if not gtin or not quantity or quantity <= 0:
                    raise ValueError('ЧЗ не вернул товар или количество в коробе.')
        except Exception as exc:
            logger.warning('scan.sscc_discovery_failed', scan_id=str(scan_id), error_type=type(exc).__name__)
            error = (str(exc) if isinstance(exc, ValueError) else 'Не удалось проверить короб в ЧЗ.')
            error += ' Удалите короб и отсканируйте повторно после восстановления подключения.'

        # Deletion/undo, document submission and duplicate deliveries must win
        # over a late external response. Use the same document-first lock order.
        await db.rollback()
        doc = (await db.execute(select(Document).where(Document.id == document_id).with_for_update())).scalar_one_or_none()
        if not doc or doc.status != DocumentStatus.draft or str(doc.user_id) != str(user_id):
            return
        scan = (await db.execute(select(Scan).where(Scan.id == scan_id).with_for_update())).scalar_one_or_none()
        if not scan or scan.box_quantity or scan.status not in {ScanStatus.pending, ScanStatus.scanned, ScanStatus.invalid}:
            return
        if children and not error:
            conflict = (await db.execute(select(Scan.id).join(Document).where(
                Document.user_id == doc.user_id, Document.kind == doc.kind,
                Document.upd_meta['superseded_by_document_id'].astext.is_(None),
                Scan.id != scan.id,
                or_(Scan.code.in_(children), Scan.child_codes.has_any(array(children))),
                Scan.status.in_([ScanStatus.valid, ScanStatus.overflow, ScanStatus.pending,
                                ScanStatus.scanned, ScanStatus.unknown_product]),
            ).limit(1))).scalar_one_or_none()
            if conflict:
                error = 'Марки из короба уже отсканированы в этой или другой сборке. Удалите короб и проверьте повторные марки.'
        if error:
            scan.status = ScanStatus.invalid
            scan.error_message = error
            scan.verification = {'source': 'sscc_error', 'checked_at': None}
        else:
            scan.gtin, scan.box_quantity, scan.child_codes = gtin, quantity, children
            scan.package_type = 'BOX'
            scan.error_message = None
            scan.verification = None
            planned = unique_plan_product(doc.plan, gtin)
            if planned and not scan.moysklad_product_id:
                scan.moysklad_product_id = planned['product_id']
                scan.product_name = planned.get('product_name')
            # Unpacked marks still need the normal explicit batch CZ check.
            scan.status = ScanStatus.scanned if unpack else ScanStatus.pending
        await db.commit()
        await publish_scan(user_id, scan)
    if not error and not unpack:
        await _verify_box_async(str(scan_id), str(user_id))
