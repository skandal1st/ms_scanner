import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from app.worker.celery_app import celery_app
from app.core.logging import logger
from app.services.chestnyznak import cis_compare_forms_for_ms, normalize_gtin_key


def _cis_matches_ms_error_message(scan_code: str, ms_snippet: str) -> bool:
    """Совпадение Scan.code с фрагментом из ошибки МС (412): КИ 24 символа, FNC1, регистр."""
    a = (scan_code or "").strip()
    b = (ms_snippet or "").strip()
    if a == b:
        return True

    def _cross(forms_x: list[str], forms_y: list[str]) -> bool:
        for ca in forms_x:
            for cb in forms_y:
                if ca == cb or ca.lower() == cb.lower():
                    return True
        return False

    forms_a = cis_compare_forms_for_ms(a)
    forms_b = cis_compare_forms_for_ms(b)
    if _cross(forms_a, forms_b):
        return True
    for ca in forms_a:
        if ca == b:
            return True
    for cb in forms_b:
        if a == cb:
            return True
    b_alt = re.sub(r"(?i)%c1", "\x1d", b)
    if _cross(forms_a, cis_compare_forms_for_ms(b_alt)):
        return True

    loose_a = "".join(c for c in a if c not in "\x1d\x1e")
    loose_b = "".join(c for c in b if c not in "\x1d\x1e")
    if loose_a == loose_b or loose_a.lower() == loose_b.lower():
        return True
    loose_b2 = re.sub(r"(?i)%c1", "", loose_b)
    loose_a2 = re.sub(r"(?i)%c1", "", loose_a)
    return loose_a2 == loose_b2 or loose_a2.lower() == loose_b2.lower()


def _run(coro):
    """Запустить корутину из синхронного Celery воркера."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _enrich_scan_product_name_from_ms(db, user_id, scan) -> None:
    """Подтянуть товар из МойСклад по GTIN: имя и UUID (если ещё не заполнены)."""
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
    if not product:
        return
    if product.get("name") and not scan.product_name:
        scan.product_name = product["name"]
    if product.get("id") and not scan.moysklad_product_id:
        scan.moysklad_product_id = product["id"]


async def _enrich_scan_product_name_from_ms_by_product_id(db, user_id, scan) -> None:
    """Имя товара по явному UUID (если не нашли в плане)."""
    if not scan.moysklad_product_id or scan.product_name:
        return
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
        row = await ms.get_product_by_id(scan.moysklad_product_id)
    except Exception as exc:
        logger.warning("verify_code.ms_product_by_id_failed", scan_id=str(scan.id), error=str(exc))
        return
    if row and row.get("name"):
        scan.product_name = row["name"]


async def _enrich_scan_product_name_from_plan(db, scan) -> None:
    """Имя и product_id из плана документа — приоритетнее глобального поиска по GTIN."""
    from sqlalchemy import select
    from app.db.models import Document

    doc_q = await db.execute(select(Document).where(Document.id == scan.document_id))
    doc = doc_q.scalar_one_or_none()
    if not doc or not doc.plan:
        return
    if scan.moysklad_product_id:
        for p in doc.plan:
            if not isinstance(p, dict):
                continue
            if p.get("product_id") != scan.moysklad_product_id:
                continue
            name = (p.get("product_name") or "").strip()
            if name and not scan.product_name:
                scan.product_name = name
            return
    for p in doc.plan:
        if not isinstance(p, dict):
            continue
        if normalize_gtin_key(p.get("gtin")) != normalize_gtin_key(scan.gtin):
            continue
        pid = p.get("product_id")
        if pid and isinstance(pid, str) and not scan.moysklad_product_id:
            scan.moysklad_product_id = pid
        name = (p.get("product_name") or "").strip()
        if name and not scan.product_name:
            scan.product_name = name
        return


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, name="verify_code")
def verify_code_task(self, scan_id: str, _code: str, user_id: str):
    """Проверить код маркировки и обновить статус скана."""
    try:
        _run(_verify_code_async(scan_id, user_id))
    except Exception as exc:
        logger.error("verify_code.error", scan_id=scan_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_code_async(scan_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus, Document, Integration
    from app.services.chestnyznak import ChestnyZnakService, verify_code_local_gs1
    from app.core.security import decrypt_token
    from app.core.config import settings
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        scan_result = await db.execute(select(Scan).where(Scan.id == scan_id))
        scan = scan_result.scalar_one_or_none()
        if not scan:
            logger.error("verify_code.scan_not_found", scan_id=scan_id)
            return

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
            verify_result = await cz.verify_code(scan.code)
        else:
            verify_result = verify_code_local_gs1(scan.code)
            # USB-сканер даёт сырой GS1; официальное приложение ЧЗ ходит в API.
            # При невалидном локальном разборе — запрос в ЧЗ по полной CIS (нужен токен УКЭП).
            if not verify_result.valid:
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
                        try:
                            cz_token = decrypt_token(integration.cz_token)
                        except Exception as exc:
                            logger.warning(
                                "verify_code.cz_decrypt_failed",
                                scan_id=scan_id,
                                error=str(exc),
                            )
                if cz_token:
                    from app.services.chestnyznak import CZApiError, VerifyResult

                    try:
                        cz = ChestnyZnakService(token=cz_token, mock=False)
                        alt = await cz._real_verify(
                            scan.code, verify_result.gtin, verify_result.serial
                        )
                        if alt.valid:
                            verify_result = alt
                            logger.info("verify_code.chz_facade_ok", scan_id=scan_id)
                        elif alt.gtin or alt.product_name:
                            verify_result = VerifyResult(
                                valid=False,
                                gtin=alt.gtin or verify_result.gtin,
                                serial=alt.serial or verify_result.serial,
                                status=alt.status,
                                error=alt.error or verify_result.error,
                                product_name=alt.product_name,
                            )
                    except CZApiError as exc:
                        logger.warning(
                            "verify_code.chz_facade_failed",
                            scan_id=scan_id,
                            error=str(exc),
                        )

        if verify_result.valid:
            scan.status = ScanStatus.valid
        else:
            scan.status = ScanStatus.invalid
            scan.error_message = verify_result.error

        scan.gtin = verify_result.gtin or scan.gtin
        scan.serial = verify_result.serial
        scan.product_name = verify_result.product_name or scan.product_name
        scan.verified_at = datetime.now(timezone.utc)
        if scan.gtin:
            gk = normalize_gtin_key(scan.gtin)
            if gk:
                scan.gtin = gk

        # Проверка плана: если документ имеет план и для GTIN скана уже
        # отсканировано >= expected_qty валидных — текущий скан переводим
        # в overflow (сверхплана: визуально ошибка, но в МС уходит с valid).
        if scan.status == ScanStatus.valid and scan.gtin:
            doc_q = await db.execute(
                select(Document).where(Document.id == scan.document_id)
            )
            doc = doc_q.scalar_one_or_none()
            plan_items = (doc.plan or []) if doc else []
            scan_key = normalize_gtin_key(scan.gtin)
            expected = next(
                (
                    int(p.get("expected_qty") or 0)
                    for p in plan_items
                    if isinstance(p, dict)
                    and normalize_gtin_key(p.get("gtin")) == scan_key
                ),
                None,
            )
            if expected is not None and expected > 0:
                prev_q = await db.execute(
                    select(Scan.gtin).where(
                        Scan.document_id == scan.document_id,
                        Scan.status == ScanStatus.valid,
                        Scan.id != scan.id,
                    )
                )
                already = sum(
                    1
                    for (g,) in prev_q.all()
                    if g and normalize_gtin_key(g) == scan_key
                )
                if already + 1 > expected:
                    # overflow — визуально красный, но при подтверждении
                    # документа всё равно уйдёт в МС (вместе с valid).
                    scan.status = ScanStatus.overflow
                    scan.error_message = (
                        f"Сверх плана: ожидалось {expected}, отсканировано {already + 1}"
                    )
        if scan.status in (ScanStatus.valid, ScanStatus.overflow) and (scan.gtin or scan.moysklad_product_id):
            await _enrich_scan_product_name_from_plan(db, scan)
        # find_product_by_gtin теперь дёргаем и ради product_id, не только ради имени.
        if (
            scan.status in (ScanStatus.valid, ScanStatus.overflow)
            and scan.gtin
            and not scan.moysklad_product_id
        ):
            await _enrich_scan_product_name_from_ms(db, user_id, scan)
        if scan.status in (ScanStatus.valid, ScanStatus.overflow) and not scan.product_name:
            await _enrich_scan_product_name_from_ms_by_product_id(db, user_id, scan)

        # unknown_product — только если GTIN не упомянут в плане документа И не нашёлся
        # в каталоге МС. Если GTIN есть в плане (даже без product_id в plan-item) —
        # код считается валидным для документа: позиция МС уже относится к нему,
        # product_id подтянется при /process через find_product_by_gtin.
        if (
            scan.status in (ScanStatus.valid, ScanStatus.overflow)
            and scan.gtin
            and not scan.moysklad_product_id
        ):
            doc_q2 = await db.execute(
                select(Document).where(Document.id == scan.document_id)
            )
            doc2 = doc_q2.scalar_one_or_none()
            scan_key = normalize_gtin_key(scan.gtin)
            in_plan = False
            for p in (doc2.plan or []) if doc2 else []:
                if isinstance(p, dict) and normalize_gtin_key(p.get("gtin")) == scan_key:
                    in_plan = True
                    break
            if not in_plan:
                scan.status = ScanStatus.unknown_product
                scan.error_message = "Товар не найден в МС — сопоставьте вручную"

        await db.commit()

        logger.info(
            "verify_code.done",
            scan_id=scan_id,
            status=scan.status,
            gtin=scan.gtin,
        )

        # Пуш через Redis pub/sub → WebSocket менеджер
        await _push_ws_update(
            user_id,
            scan_id,
            scan.status,
            scan.product_name,
            scan.error_message,
            gtin=scan.gtin,
            moysklad_product_id=scan.moysklad_product_id,
        )


async def _push_ws_update(
    user_id: str,
    scan_id: str,
    status: str,
    product_name: Optional[str],
    error: Optional[str],
    *,
    gtin: Optional[str] = None,
    moysklad_product_id: Optional[str] = None,
):
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
        "gtin": gtin,
        "moysklad_product_id": moysklad_product_id,
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
        if kind != "demand":
            logger.warning(
                "process_document.unsupported_kind",
                document_id=document_id,
                kind=kind,
            )
            return

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

            # product_id: сначала план документа (позиции этой отгрузки/приёмки в МС),
            # иначе поиск в каталоге по штрихкоду — иначе КМ без баркода в карточке
            # не попадёт в update_document (там отбрасываются строки без product_id).
            unique_gtins = {
                normalize_gtin_key(s.gtin) for s in valid_scans if s.gtin
            }
            unique_gtins.discard(None)
            gtin_to_product_id: dict[str, str] = {}
            for p in doc.plan or []:
                if not isinstance(p, dict):
                    continue
                g, pid = p.get("gtin"), p.get("product_id")
                ng = normalize_gtin_key(g)
                if ng and pid and isinstance(pid, str):
                    gtin_to_product_id.setdefault(ng, pid)

            for gtin in unique_gtins:
                if not gtin or gtin in gtin_to_product_id:
                    continue
                product = await ms.find_product_by_gtin(gtin)
                if product:
                    gtin_to_product_id[gtin] = product["id"]
                else:
                    logger.warning(
                        "process_document.product_not_found",
                        gtin=gtin,
                        document_id=document_id,
                    )

            remaining_scans = list(valid_scans)
            while remaining_scans:
                scans_data = [
                    {
                        "code": s.code,
                        "gtin": s.gtin,
                        "product_id": (
                            s.moysklad_product_id
                            or (
                                gtin_to_product_id.get(normalize_gtin_key(s.gtin))
                                if s.gtin
                                else None
                            )
                        ),
                    }
                    for s in remaining_scans
                ]
                # В update_document попадут только строки с product_id.
                if not any(s.get("product_id") for s in scans_data):
                    logger.warning(
                        "process_document.no_product_ids",
                        document_id=document_id,
                        kind=kind,
                        scans=len(scans_data),
                    )
                    break
                result = await ms.update_document(kind, doc.moysklad_id, scans_data)
                if isinstance(result, dict) and result.get("__moysklad_412__") is True:
                    body = result.get("body") or ""
                    m = re.search(
                        r"неверный формат кода маркировки\s+([^\s\",}]+)",
                        body,
                        flags=re.IGNORECASE,
                    )
                    bad_code = m.group(1) if m else None
                    if not bad_code:
                        logger.error(
                            "process_document.moysklad_412_unparsed",
                            document_id=document_id,
                            body=body[:800],
                        )
                        raise RuntimeError(
                            "МойСклад отклонил сохранение документа (412): не удалось извлечь код из ответа."
                        )
                    bad_scan = next(
                        (s for s in remaining_scans if _cis_matches_ms_error_message(s.code, bad_code)),
                        None,
                    )
                    if not bad_scan:
                        logger.error(
                            "process_document.bad_code_unmatched",
                            document_id=document_id,
                            bad_code=bad_code,
                            scan_codes=[
                                (str(s.id), (s.code or "")[:120]) for s in remaining_scans
                            ],
                        )
                        raise RuntimeError(
                            "МойСклад 412: неверный формат кода маркировки; не удалось сопоставить со сканами."
                        )

                    bad_scan.status = ScanStatus.invalid
                    bad_scan.error_message = (
                        "Код отклонён МойСклад: неверный формат кода маркировки"
                    )
                    await db.commit()
                    await _push_ws_update(
                        str(user_id),
                        str(bad_scan.id),
                        bad_scan.status,
                        bad_scan.product_name,
                        bad_scan.error_message,
                        gtin=bad_scan.gtin,
                        moysklad_product_id=bad_scan.moysklad_product_id,
                    )
                    logger.warning(
                        "process_document.bad_code_filtered",
                        document_id=document_id,
                        code=bad_code,
                    )
                    remaining_scans = [s for s in remaining_scans if s.id != bad_scan.id]
                    if not remaining_scans:
                        logger.warning(
                            "process_document.no_scans_after_filter",
                            document_id=document_id,
                            kind=kind,
                        )
                    continue

                break
            valid_scans = remaining_scans

        # Финальный статус документа
        doc.status = DocumentStatus.accepted
        await db.commit()

        logger.info(
            "process_document.done",
            document_id=document_id,
            kind=kind,
            valid_count=len(valid_scans),
        )
