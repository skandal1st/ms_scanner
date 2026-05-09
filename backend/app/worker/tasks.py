import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.worker.celery_app import celery_app
from app.core.logging import logger


# Типы документов, которые требуют ввода в оборот в ЧЗ при подтверждении.
# Для всех остальных (demand/loss/move/salesreturn) ЧЗ-API не вызывается —
# только обновление МС-документа с записью кодов в positions[].trackingCodes.
CZ_INTRODUCE_KINDS = {"supply"}


def _run(coro):
    """Запустить корутину из синхронного Celery воркера."""
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, name="verify_code")
def verify_code_task(self, scan_id: str, code: str, user_id: str):
    """Проверить код маркировки в ЧЗ и обновить статус скана."""
    try:
        _run(_verify_code_async(scan_id, code, user_id))
    except Exception as exc:
        logger.error("verify_code.error", scan_id=scan_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_code_async(scan_id: str, code: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus, Document, Integration
    from app.services.chestnyznak import ChestnyZnakService
    from app.core.security import decrypt_token
    from app.core.config import settings
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        # Получаем токен ЧЗ пользователя
        result = await db.execute(
            select(Integration).where(Integration.user_id == user_id)
        )
        integration = result.scalar_one_or_none()
        cz_token = None
        token_expired = False
        if integration and integration.cz_token:
            if (
                integration.cz_token_expires_at is not None
                and integration.cz_token_expires_at <= datetime.now(timezone.utc)
            ):
                token_expired = True
            else:
                cz_token = decrypt_token(integration.cz_token)

        # Получаем скан заранее — он нужен и для happy-path, и для expiry-ветки
        scan_result = await db.execute(select(Scan).where(Scan.id == scan_id))
        scan = scan_result.scalar_one_or_none()
        if not scan:
            logger.error("verify_code.scan_not_found", scan_id=scan_id)
            return

        # Если токен ЧЗ протух или отсутствует при не-mock методе — не ходим в ЧЗ.
        # Авто-обновить мы не можем: для нового токена нужна свежая подпись УКЭП
        # из браузера клиента. Просим фронт через WS показать «Войти заново».
        auth_method = (integration.cz_auth_method if integration else settings.CZ_AUTH_METHOD)
        needs_login = auth_method != "mock" and (token_expired or not cz_token)
        if needs_login:
            scan.status = ScanStatus.invalid
            scan.error_message = "Требуется обновить вход в Честный Знак"
            scan.verified_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                "verify_code.cz_token_expired", scan_id=scan_id, user_id=user_id,
            )
            await _push_ws_update(user_id, scan_id, scan.status, None, scan.error_message)
            await _push_cz_token_expired(user_id)
            return

        cz = ChestnyZnakService(token=cz_token)
        verify_result = await cz.verify_code(code)

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
        # в invalid с пометкой «Превышен план». Кладовщик слышит ошибку
        # и видит причину, а склад не уезжает с лишним.
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


async def _push_cz_token_expired(user_id: str):
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    try:
        await r.publish(
            f"ws:{user_id}",
            json.dumps({"type": "cz_token_expired"}),
        )
    finally:
        await r.aclose()


@celery_app.task(name="process_document")
def process_document_task(document_id: str, user_id: str):
    """
    Завершить документ. Поведение зависит от document.kind:
    - supply: ввод в оборот в ЧЗ + обновление МС.
    - demand/loss/move/salesreturn: только обновление МС с trackingCodes
      (поиск product_id по GTIN на лету).
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
    from app.services.chestnyznak import ChestnyZnakService
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

        # Шаг 1 — ЧЗ ввод в оборот (только для приёмки)
        if kind in CZ_INTRODUCE_KINDS and valid_scans:
            cz_token = decrypt_token(integration.cz_token) if integration and integration.cz_token else None
            cz = ChestnyZnakService(token=cz_token)
            codes = [s.code for s in valid_scans]
            await cz.accept_batch(codes, document_id)

        # Шаг 2 — обновление МС-документа
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

        # Шаг 3 — финальный статус документа
        doc.status = DocumentStatus.accepted
        await db.commit()

        logger.info(
            "process_document.done",
            document_id=document_id,
            kind=kind,
            valid_count=len(valid_scans),
        )
