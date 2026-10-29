"""Background packaging metadata discovery; does not certify marks or send documents."""
from sqlalchemy import select

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import Document, DocumentStatus, Integration, Scan, ScanStatus
from app.db.session import AsyncSessionLocal
from app.services.chestnyznak import ChestnyZnakService, extract_gtin
from app.services.moysklad import MoySkladService
from app.services.plan_matching import unique_plan_product
from app.services.scan_events import publish_scan
from app.services.scan_packaging import package_type


def packaging_metadata(info, original_gtin):
    if info is None:
        raise ValueError('Не удалось определить упаковку в ЧЗ. Нажмите «Проверить марки».')
    kind = package_type(info.package_type)
    children = list(dict.fromkeys(info.children or []))
    if kind in {'GROUP', 'BOX'} or children:
        gtins = {extract_gtin(code) for code in children}
        if not children or None in gtins or len(gtins) != 1:
            raise ValueError('Не удалось определить однородный состав упаковки. Нажмите «Проверить марки».')
        return {'gtin': next(iter(gtins)), 'package_type': kind if kind in {'GROUP', 'BOX'} else 'GROUP',
                'box_quantity': len(children), 'child_codes': children, 'keep_aggregate': True}
    return {'gtin': info.gtin or original_gtin, 'package_type': kind or 'UNIT',
            'box_quantity': None, 'child_codes': None, 'keep_aggregate': False}


async def discover_scan_packaging(scan_id, user_id):
    from app.worker.tasks import _get_cz_token, _get_cz_product_groups
    async with AsyncSessionLocal() as db:
        scan = await db.get(Scan, scan_id)
        if not scan or scan.status != ScanStatus.pending or (scan.verification or {}).get('source') != 'packaging_pending':
            return
        doc = await db.get(Document, scan.document_id)
        if not doc or str(doc.user_id) != str(user_id) or doc.status != DocumentStatus.draft:
            return
        document_id = doc.id
        metadata, product, error = {}, None, None
        try:
            token = await _get_cz_token(db, user_id, document_id)
            if not token:
                raise ValueError('Войдите в ЧЗ для определения состава блока, затем нажмите «Проверить марки».')
            groups = await _get_cz_product_groups(db, user_id, document_id)
            info = await ChestnyZnakService(token=token, mock=False, product_groups=groups).get_code_info(scan.code)
            metadata = packaging_metadata(info, scan.gtin)
            product = unique_plan_product(doc.plan, metadata['gtin'])
            if product is None:
                integration = (await db.execute(select(Integration).where(Integration.user_id == doc.user_id))).scalar_one_or_none()
                if integration and integration.moysklad_token:
                    found = await MoySkladService(decrypt_token(integration.moysklad_token)).find_product_by_gtin(metadata['gtin'])
                    if found:
                        product = {'product_id': found['id'], 'product_name': found.get('name')}
        except Exception as exc:
            logger.warning('scan.packaging_failed', scan_id=scan_id, error_type=type(exc).__name__)
            error = str(exc) if isinstance(exc, ValueError) else 'Не удалось определить упаковку. Нажмите «Проверить марки».'
        # Re-read under the same document lock as scans/deletion; never resurrect a removed mark.
        await db.rollback()
        doc = (await db.execute(select(Document).where(Document.id == document_id).with_for_update()
                                .execution_options(populate_existing=True))).scalar_one_or_none()
        scan = (await db.execute(select(Scan).where(Scan.id == scan_id).with_for_update()
                                 .execution_options(populate_existing=True))).scalar_one_or_none()
        if not doc or doc.status != DocumentStatus.draft or not scan or scan.status != ScanStatus.pending or (scan.verification or {}).get('source') != 'packaging_pending':
            return
        for field, value in metadata.items():
            setattr(scan, field, value)
        # An explicit scanner target always takes precedence over inferred packaging ownership.
        if not scan.moysklad_product_id and product:
            scan.moysklad_product_id = product['product_id']
            scan.product_name = product.get('product_name')
        scan.status = ScanStatus.scanned
        scan.verification = None
        scan.error_message = error
        await db.commit()
        await publish_scan(user_id, scan)
        logger.info('scan.packaging_resolved', scan_id=scan_id, package_type=scan.package_type,
                    units=scan.box_quantity, resolved=bool(scan.moysklad_product_id), error=bool(error))
