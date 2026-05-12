import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.worker.celery_app import celery_app
from app.core.logging import logger


def _run(coro):
    """Запустить корутину из синхронного Celery воркера."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _enrich_scan_product_name_from_ms(db, user_id, scan) -> None:
    """Подставить название товара из МойСклад по GTIN (если токен есть и имя ещё пустое)."""
    from sqlalchemy import select
    from app.db.models import Integration
    from app.services.moysklad import MoySkladService
    from app.core.security import decrypt_token

    int_result = await db.execute(select(Integration).where(Integration.user_id == user_id))
    integration = int_result.scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        return
    try:
        token = decrypt_token(integration.moysklad_token)
    except Exception as exc:
        logger.warning("verify_code.ms_decrypt_failed", scan_id=str(scan.id), error=str(exc))
        return
    ms = MoySkladService(token)
    try:
        product = await ms.find_product_by_gtin(scan.gtin)
    except Exception as exc:
        logger.warning("verify_code.ms_product_lookup_failed", scan_id=str(scan.id), error=str(exc))
        return
    if product and product.get("name"):
        scan.product_name = product["name"]


async def _enrich_scan_product_name_from_plan(db, scan) -> None:
    """Название из плана документа (позиции МС) — приоритетнее глобального поиска по GTIN."""
    from sqlalchemy import select
    from app.db.models import Document

    doc_q = await db.execute(select(Document).where(Document.id == scan.document_id))
    doc = doc_q.scalar_one_or_none()
    if not doc or not doc.plan:
        return
    for p in doc.plan:
        if not isinstance(p, dict):
            continue
        if p.get("gtin") != scan.gtin:
            continue
        name = (p.get("product_name") or "").strip()
        if name:
            scan.product_name = name
            return


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, name="verify_code")
def verify_code_task(self, scan_id: str, code: str, user_id: str):
    """Проверить код маркировки и обновить статус скана."""
    try:
        _run(_verify_code_async(scan_id, code, user_id))
    except Exception as exc:
        logger.error("verify_code.error", scan_id=scan_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_code_async(scan_id: str, code: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus, Document, Integration
    from app.services.chestnyznak import ChestnyZnakService, verify_code_local_gs1, canonicalize_marking_scan_code
    from app.core.security import decrypt_token
    from app.core.config import settings
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        scan_result = await db.execute(select(Scan).where(Scan.id == scan_id))
        scan = scan_result.scalar_one_or_none()
        if not scan:
            logger.error("verify_code.scan_not_found", scan_id=scan_id)
            return

        canon = canonicalize_marking_scan_code(scan.code)
        if canon != scan.code:
            scan.code = canon

        code = canon
        # Проверка КМ: в mock-режиме сервера — имитация ЧЗ; иначе только формат GS1
        # (без УКЭП и без API ЧЗ). МойСклад проверит CIS при записи в документ.
        if settings.CZ_MOCK_MODE:
            int_q = await db.execute(
                select(Integration).where(Integration.user_id == user_id)
            )
            integration = int_q.scalar_one_or_none()
            cz_token = None
            if integration and integration.cz_token:
                if (
                    integration.cz_token_expires_at is None
                    or integration.cz_token_expires_at > datetime.now(timezone.utc)
                ):
                    cz_token = decrypt_token(integration.cz_token)
            cz = ChestnyZnakService(token=cz_token)
            verify_result = await cz.verify_code(code)
        else:
            verify_result = verify_code_local_gs1(code)

        if verify_result.valid:
            scan.status = ScanStatus.valid
        else:
            scan.status = ScanStatus.invalid
            scan.error_message = verify_result.error

        scan.gtin = verify_result.gtin or scan.gtin
        scan.serial = verify_result.serial
        scan.product_name = verify_result.product_name
        scan.verified_at = datetime.now(timezone.utc)

        # Проверка плана: если документ имеет план и для GTIN скана уже
        # отсканировано >= expected_qty валидных — текущий скан переводим
        # в overflow (сверхплана: визуально ошибка, но в МС уходит с valid).
        if scan.status == ScanStatus.valid and scan.gtin:
            doc_q = await db.execute(
                select(Document).where(Document.id == scan.document_id)
            )
            doc = doc_q.scalar_one_or_none()
            plan_items = (doc.plan or []) if doc else []
            expected = next(
                (
                    int(p.get("expected_qty") or 0)
                    for p in plan_items
                    if isinstance(p, dict) and p.get("gtin") == scan.gtin
                ),
                None,
            )
            if expected is not None and expected > 0:
                count_q = await db.execute(
                    select(func.count(Scan.id)).where(
                        Scan.document_id == scan.document_id,
                        Scan.gtin == scan.gtin,
                        Scan.status == ScanStatus.valid,
                        Scan.id != scan.id,
                    )
                )
                already = count_q.scalar() or 0
                if already + 1 > expected:
                    # overflow — визуально красный, но при подтверждении
                    # документа всё равно уйдёт в МС (вместе с valid).
                    scan.status = ScanStatus.overflow
                    scan.error_message = (
                        f"Сверх плана: ожидалось {expected}, отсканировано {already + 1}"
                    )
        if scan.status in (ScanStatus.valid, ScanStatus.overflow) and scan.gtin:
            await _enrich_scan_product_name_from_plan(db, scan)
        if (
            scan.status in (ScanStatus.valid, ScanStatus.overflow)
            and scan.gtin
            and not scan.product_name
        ):
            await _enrich_scan_product_name_from_ms(db, user_id, scan)

        await db.commit()

        logger.info(
            "verify_code.done",
            scan_id=scan_id,
            status=scan.status,
            gtin=scan.gtin,
        )

        # Пуш через Redis pub/sub → WebSocket менеджер
        await _push_ws_update(user_id, scan_id, scan.status, scan.product_name, scan.error_message)


async def _push_ws_update(user_id: str, scan_id: str, status: str,
                          product_name: Optional[str], error: Optional[str]):
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    message = json.dumps({
        "type": "scan_update",
        "scan_id": scan_id,
        "status": status,
        "product_name": product_name,
        "error_message": error,
    })
    await r.publish(f"ws:{user_id}", message)
    await r.aclose()


@celery_app.task(name="process_document")
def process_document_task(document_id: str, user_id: str):
    """
    Завершить документ: обновление МС с trackingCodes (supply и отгрузочные типы),
    поиск product_id по GTIN на лету. API Честного Знака не вызывается.
    """
    try:
        _run(_process_document_async(document_id, user_id))
    except Exception as exc:
        logger.error("process_document.error", document_id=document_id, error=str(exc))
        raise


# Backward-совместимый алиас для старого имени задачи.
@celery_app.task(name="accept_document")
def accept_document_task(document_id: str, user_id: str):
    process_document_task(document_id, user_id)


async def _process_document_async(document_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus, Scan, ScanStatus, Integration
    from app.services.moysklad import MoySkladService
    from app.core.security import decrypt_token
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        # Получаем сам документ — нужен kind для разветвления
        doc_result = await db.execute(select(Document).where(Document.id == document_id))
        doc = doc_result.scalar_one_or_none()
        if not doc:
            logger.error("process_document.not_found", document_id=document_id)
            return

        kind = doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind)

        # Сканы для отправки: valid + overflow.
        # overflow — сверхплановые, визуально помечены красным, но идут в МС.
        result = await db.execute(
            select(Scan).where(
                Scan.document_id == document_id,
                Scan.status.in_([ScanStatus.valid, ScanStatus.overflow]),
            )
        )
        valid_scans = result.scalars().all()

        if not valid_scans:
            logger.warning(
                "process_document.no_valid_scans",
                document_id=document_id,
                kind=kind,
            )

        # Интеграции
        int_result = await db.execute(
            select(Integration).where(Integration.user_id == user_id)
        )
        integration = int_result.scalar_one_or_none()

        # Обновление МС-документа (trackingCodes для supply и отгрузочных типов)
        if doc.moysklad_id and integration and integration.moysklad_token and valid_scans:
            ms_token = decrypt_token(integration.moysklad_token)
            ms = MoySkladService(ms_token)

            # product_id ищем на лету по уникальным GTIN'ам — в Scan он не хранится
            unique_gtins = {s.gtin for s in valid_scans if s.gtin}
            gtin_to_product_id: dict[str, str] = {}
            for gtin in unique_gtins:
                product = await ms.find_product_by_gtin(gtin)
                if product:
                    gtin_to_product_id[gtin] = product["id"]
                else:
                    logger.warning(
                        "process_document.product_not_found",
                        gtin=gtin,
                        document_id=document_id,
                    )

            scans_data = [
                {
                    "code": s.code,
                    "gtin": s.gtin,
                    "product_id": gtin_to_product_id.get(s.gtin) if s.gtin else None,
                }
                for s in valid_scans
            ]
            await ms.update_document(kind, doc.moysklad_id, scans_data)

        # Финальный статус документа
        doc.status = DocumentStatus.accepted
        await db.commit()

        logger.info(
            "process_document.done",
            document_id=document_id,
            kind=kind,
            valid_count=len(valid_scans),
        )
