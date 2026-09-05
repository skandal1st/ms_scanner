import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.worker.celery_app import celery_app
from app.core.logging import logger
from app.core.monitoring import emit as monitoring_emit
from app.services.chestnyznak import cis_compare_forms_for_ms, normalize_gtin_key, extract_gtin


def _extract_moysklad_error(body: str) -> Optional[str]:
    """Достать человекочитаемый текст ошибки из тела ответа МС ({"errors":[{"error":...}]}).

    МС возвращает текст на русском (например «Нельзя отгрузить товар, которого нет
    на складе») — показываем его кладовщику как есть. Возвращает None, если тело
    не разобрать.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    errors = data.get("errors") if isinstance(data, dict) else None
    if not isinstance(errors, list) or not errors:
        return None
    parts = [
        str(e.get("error")).strip()
        for e in errors
        if isinstance(e, dict) and e.get("error")
    ]
    return "; ".join(p for p in parts if p) or None


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
    if loose_a2 == loose_b2 or loose_a2.lower() == loose_b2.lower():
        return True
    # МС обрезает код в тексте ошибки 412 на кавычке ("): сохранённый
    # ...lOS6"G; приходит в ответе как ...lOS6. Считаем совпадением, если один
    # код — префикс другого, и общий префикс не короче головы 01+GTIN(14)+21
    # (18 символов), иначе можно поймать чужой код с тем же GTIN.
    x, y = loose_a2.lower(), loose_b2.lower()
    if x and y and (x.startswith(y) or y.startswith(x)) and min(len(x), len(y)) >= 18:
        return True
    return False


def _run(coro):
    """Запустить корутину из синхронного Celery воркера."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _enrich_scan_product_name_from_ms(db, user_id, scan) -> None:
    """Подтянуть товар по GTIN: сперва локальная база знаний, при промахе — МС.

    Порядок: своя БД (GtinProductMap) → МойСклад. Каждый успешный резолв из МС
    записываем в базу знаний, чтобы следующие сканы этого GTIN резолвились локально
    (быстро) и имя оставалось доступным как фолбэк, даже если позже МС не ответит.
    """
    from sqlalchemy import select
    from app.db.models import Integration
    from app.services.moysklad import MoySkladService
    from app.services.gtin_product_store import get_gtin_product, remember_gtin_product
    from app.core.security import decrypt_token

    # 1. Локальная база знаний GTIN→товар — без похода в МС.
    remembered = await get_gtin_product(db, user_id, scan.gtin)
    if remembered:
        pid, pname = remembered
        if pid and not scan.moysklad_product_id:
            scan.moysklad_product_id = pid
        if pname and not scan.product_name:
            scan.product_name = pname
        if scan.moysklad_product_id:
            return  # товар определён — в МС не идём

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
    # 2. Сверили GTIN с товаром МС — пополняем базу знаний.
    if product.get("id"):
        await remember_gtin_product(
            db, user_id, scan.gtin, product["id"], product.get("name")
        )


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


async def _get_cz_token(db, user_id) -> Optional[str]:
    """Действующий (не просроченный) токен ЧЗ пользователя или None."""
    from sqlalchemy import select
    from app.db.models import Integration
    from app.core.security import decrypt_token

    q = await db.execute(select(Integration).where(Integration.user_id == user_id))
    integ = q.scalar_one_or_none()
    if not integ or not integ.cz_token:
        return None
    if (
        integ.cz_token_expires_at is not None
        and integ.cz_token_expires_at <= datetime.now(timezone.utc)
    ):
        return None
    try:
        return decrypt_token(integ.cz_token)
    except Exception as exc:
        logger.warning("cz_token.decrypt_failed", user_id=str(user_id), error=str(exc))
        return None


async def _get_cz_product_groups(db, user_id) -> list[str]:
    """Товарные группы (pg) клиента для сужения перебора в ЧЗ. [] → глобальный дефолт."""
    from sqlalchemy import select
    from app.db.models import Integration

    q = await db.execute(select(Integration).where(Integration.user_id == user_id))
    integ = q.scalar_one_or_none()
    return list(integ.cz_product_groups or []) if integ else []


async def _count_valid_units_for_gtin(db, document_id, scan_key: str, exclude_id) -> int:
    """Сколько единиц товара GTIN уже набрано валидными сканами документа.
    Короб/блок считается как box_quantity единиц, обычный скан — как 1. Нужно для
    overflow: агрегат из N штук может перевести строку плана за лимит целиком."""
    from sqlalchemy import select
    from app.db.models import Scan, ScanStatus

    rows = await db.execute(
        select(Scan.gtin, Scan.box_quantity).where(
            Scan.document_id == document_id,
            Scan.status == ScanStatus.valid,
            Scan.id != exclude_id,
        )
    )
    total = 0
    for g, bq in rows.all():
        if g and normalize_gtin_key(g) == scan_key:
            total += int(bq) if bq else 1
    return total


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


async def _verify_code_async(scan_id: str, user_id: str, precheck=None):
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

        # Пакетный флоу: precheck — результат батч-проверки в ЧЗ (check_codes) по этому
        # коду. Статус берём из ЧЗ (INTRODUCED = в обороте). uncertain (таймаут/5xx ЧЗ)
        # → НЕ трогаем статус (остаётся scanned), чтобы кнопка «Проверить» повторила код.
        if precheck is not None:
            from app.services.chestnyznak import VerifyResult, extract_gtin as _extract_gtin

            if getattr(precheck, "uncertain", False):
                scan.error_message = (
                    precheck.error or "Не удалось проверить в ЧЗ — повторите"
                )
                await db.commit()
                await _push_ws_update(
                    user_id, scan_id, scan.status, scan.product_name,
                    scan.error_message, gtin=scan.gtin,
                    moysklad_product_id=scan.moysklad_product_id, is_box=scan.is_box,
                    box_quantity=scan.box_quantity, owner_name=scan.owner_name,
                    producer_name=scan.producer_name, owner_inn=scan.owner_inn,
                    withdrawn=scan.withdrawn, withdraw_reason=scan.withdraw_reason,
                    child_codes=scan.child_codes,
                )
                logger.info("verify_code.uncertain", scan_id=scan_id)
                return

            valid = precheck.found and str(precheck.status or "").upper() == "INTRODUCED"
            if valid:
                # Снимаем возможную ошибку от прошлого неудачного прохода (повтор).
                scan.error_message = None
            scan.owner_name = precheck.owner_name
            scan.owner_inn = precheck.owner_inn
            scan.producer_name = precheck.producer_name
            scan.withdrawn = bool(precheck.mark_withdraw)
            scan.withdraw_reason = precheck.withdraw_reason
            out_gtin = precheck.gtin or scan.gtin
            name_override = precheck.product_name
            # Агрегат (блок/короб): развернуть в листовые КМ — отдельный запрос (редко).
            if valid and precheck.child_count and not scan.child_codes:
                tok = await _get_cz_token(db, user_id)
                if tok:
                    grp = await _get_cz_product_groups(db, user_id)
                    agg = None
                    try:
                        agg = await ChestnyZnakService(
                            token=tok, mock=False, product_groups=grp
                        ).get_code_info(scan.code)
                    except Exception as exc:
                        logger.warning(
                            "verify_code.aggregate_failed", scan_id=scan_id, error=str(exc)
                        )
                    if agg and agg.is_aggregate:
                        scan.child_codes = agg.children
                        scan.box_quantity = len(agg.children)
                        cg = _extract_gtin(agg.children[0]) if agg.children else None
                        if cg:
                            out_gtin = cg
                        if agg.product_name:
                            name_override = agg.product_name
            verify_result = VerifyResult(
                valid=valid,
                gtin=out_gtin,
                serial=scan.serial,  # серию не трогаем (задана при локальном скане)
                status="IN_CIRCULATION" if valid else (precheck.status or "NOT_FOUND"),
                error=(
                    None
                    if valid
                    else (
                        precheck.error
                        or (
                            f"Статус в ЧЗ: {precheck.status}"
                            if precheck.found
                            else "Марка не найдена в ЧЗ"
                        )
                    )
                ),
                product_name=name_override,
            )
        # Проверка КМ: в mock-режиме сервера — имитация ЧЗ; иначе только формат GS1
        # (без УКЭП и без API ЧЗ). МойСклад проверит CIS при записи в документ.
        elif settings.CZ_MOCK_MODE:
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

        # Сведения ЧЗ: владелец/производитель + детект агрегата (блок/короб) → разворот.
        # Реальный режим, есть токен ЧЗ, ещё не SSCC-короб и не развёрнут.
        # В пакетном флоу (precheck) владелец/withdrawn/агрегат уже получены из check_codes —
        # per-code get_code_info пропускаем.
        # Короб (02/37) приходит со status=invalid (не КИ) — проверяем независимо от статуса.
        if precheck is None and not settings.CZ_MOCK_MODE and not scan.is_box and not scan.child_codes:
            cz_token2 = await _get_cz_token(db, user_id)
            if not cz_token2:
                # Нет/истёк токен ЧЗ — коды не распознаём через ЧЗ (блоки/короба
                # не развернутся). Сообщаем фронту (баннер «войдите в ЧЗ»).
                await _push_cz_token_expired(user_id)
            if cz_token2:
                info = None
                cz_groups = await _get_cz_product_groups(db, user_id)
                try:
                    info = await ChestnyZnakService(
                        token=cz_token2, mock=False, product_groups=cz_groups
                    ).get_code_info(scan.code)
                except Exception as exc:
                    logger.warning(
                        "verify_code.code_info_failed", scan_id=scan_id, error=str(exc)
                    )
                if info:
                    scan.owner_name = info.owner_name
                    scan.producer_name = info.producer_name
                    scan.owner_inn = info.owner_inn
                    # Марка выведена из оборота / заблокирована — флаг для подсветки
                    # (статус скана не трогаем: отгрузку не блокируем, только предупреждаем).
                    scan.withdrawn = info.mark_withdraw
                    scan.withdraw_reason = info.withdraw_reason
                    if info.is_aggregate:
                        # Блок/короб: разворачиваем в листовые КМ пачек.
                        scan.status = ScanStatus.valid
                        scan.error_message = None
                        scan.child_codes = info.children
                        scan.box_quantity = len(info.children)
                        # GTIN агрегата ≠ GTIN пачки: берём GTIN вложенной пачки, чтобы
                        # скан матчился с планом и считался как N единиц.
                        child_gtin = (
                            extract_gtin(info.children[0]) if info.children else None
                        )
                        if child_gtin:
                            gk2 = normalize_gtin_key(child_gtin)
                            if gk2:
                                scan.gtin = gk2
                        if info.product_name:
                            scan.product_name = info.product_name
                        logger.info(
                            "verify_code.aggregate",
                            scan_id=scan_id,
                            package_type=info.package_type,
                            units=scan.box_quantity,
                            gtin=scan.gtin,
                        )

        # Единиц в скане: агрегат = box_quantity, обычный КМ = 1.
        units = int(scan.box_quantity) if scan.box_quantity else 1

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
                already = await _count_valid_units_for_gtin(
                    db, scan.document_id, scan_key, scan.id
                )
                if already + units > expected:
                    # overflow — визуально красный, но при подтверждении
                    # документа всё равно уйдёт в МС (вместе с valid).
                    scan.status = ScanStatus.overflow
                    scan.error_message = (
                        f"Сверх плана: ожидалось {expected}, отсканировано {already + units}"
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
            is_box=scan.is_box,
            box_quantity=scan.box_quantity,
            owner_name=scan.owner_name,
            producer_name=scan.producer_name,
            owner_inn=scan.owner_inn,
            withdrawn=scan.withdrawn,
            withdraw_reason=scan.withdraw_reason,
            child_codes=scan.child_codes,
        )


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="verify_document")
def verify_document_task(self, document_id: str, user_id: str):
    """Пакетная проверка марок документа в ЧЗ по кнопке «Проверить марки».

    Основной флоу: при скане КМ проверяется только локально (формат GS1) и получает
    статус `scanned`. Здесь проверяем все такие сканы в ЧЗ разом — статус/владелец/
    withdrawn/разворот агрегатов/план/сопоставление товара МС."""
    try:
        _run(_verify_document_async(document_id, user_id))
    except Exception as exc:
        logger.error("verify_document.error", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_document_async(document_id: str, user_id: str):
    import asyncio
    import redis.asyncio as aioredis

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus
    from app.services.chestnyznak import ChestnyZnakService, CisCheck
    from sqlalchemy import select

    # #1 Идемпотентность: один активный verify на документ. Redis SET NX — второй
    # вызов (двойной клик / вторая вкладка / ретрай) не плодит параллельную проверку.
    lock_key = f"verify:lock:{document_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    got_lock = await r.set(lock_key, user_id, nx=True, ex=900)
    if not got_lock:
        await r.aclose()
        logger.info("verify_document.already_running", document_id=document_id)
        return

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Scan)
                .where(
                    Scan.document_id == document_id,
                    Scan.status == ScanStatus.scanned,
                    Scan.is_box.is_(False),
                )
                .order_by(Scan.scanned_at.asc())
            )
            scans = res.scalars().all()
            scan_ids = [str(s.id) for s in scans]
            codes = [s.code for s in scans]
            scan_gtins = [s.gtin for s in scans if s.gtin]
            cz_token = await _get_cz_token(db, user_id)
            cz_groups = await _get_cz_product_groups(db, user_id)

            # Товарные группы по GTIN (своя БД → МС → запись в БД): добавляем в перебор
            # ЧЗ первыми, чтобы товар из «невключённой» галочкой группы не давал
            # «КМ/КИ не найден» на всех сканах. Промах резолва — обычный перебор.
            try:
                from app.services.gtin_cz_group import resolve_pgs_for_gtins

                ms_pgs = await resolve_pgs_for_gtins(db, user_id, scan_gtins)
            except Exception as exc:
                logger.warning(
                    "verify_document.pg_resolve_failed",
                    document_id=document_id,
                    error=str(exc),
                )
                ms_pgs = set()
            if ms_pgs:
                cz_groups = list(ms_pgs) + [g for g in (cz_groups or []) if g not in ms_pgs]

        logger.info(
            "verify_document.start",
            document_id=document_id,
            count=len(scan_ids),
            ms_pgs=sorted(ms_pgs) if ms_pgs else [],
        )

        # #2 Батч-проверка статуса в ЧЗ: один запрос на товарную группу на всю пачку
        # (check_codes). В mock/без токена — прежний per-scan путь (precheck=None).
        use_batch = bool(codes) and bool(cz_token) and not settings.CZ_MOCK_MODE
        checks_by_code: dict[str, CisCheck] = {}
        if use_batch:
            try:
                results = await ChestnyZnakService(
                    token=cz_token, mock=False, product_groups=cz_groups
                ).check_codes(codes)
                checks_by_code = {c.code: c for c in results}
            except Exception as exc:
                # Весь батч не удался → все коды uncertain (останутся scanned, повтор).
                logger.warning(
                    "verify_document.check_codes_failed",
                    document_id=document_id,
                    error=str(exc),
                )

        sem = asyncio.Semaphore(6)
        failed = 0

        async def _one(scan_id: str, code: str) -> None:
            nonlocal failed
            async with sem:
                precheck = None
                if use_batch:
                    precheck = checks_by_code.get(code)
                    if precheck is None:
                        # check_codes упал целиком / код не вернулся — не терминируем.
                        precheck = CisCheck(
                            code=code,
                            found=False,
                            uncertain=True,
                            error="Не удалось проверить в ЧЗ — повторите",
                        )
                    if precheck.uncertain:
                        failed += 1
                try:
                    await _verify_code_async(scan_id, user_id, precheck=precheck)
                except Exception as exc:
                    logger.warning(
                        "verify_document.scan_failed", scan_id=scan_id, error=str(exc)
                    )

        if scan_ids:
            await asyncio.gather(
                *(_one(sid, code) for sid, code in zip(scan_ids, codes))
            )

        await _push_verify_done(
            user_id, document_id, checked=len(scan_ids) - failed, failed=failed
        )
        logger.info(
            "verify_document.done",
            document_id=document_id,
            count=len(scan_ids),
            failed=failed,
        )
        await monitoring_emit(
            "verify_document.done",
            level="warning" if failed else "info",
            document_id=document_id,
            count=len(scan_ids),
            checked=len(scan_ids) - failed,
            failed=failed,
        )
    finally:
        try:
            await r.delete(lock_key)
            await r.aclose()
        except Exception:
            pass


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, name="verify_box")
def verify_box_task(self, scan_id: str, user_id: str):
    """Подтвердить скан-короб (SSCC «целиком»): агрегат уже проверен через sscc_check,
    здесь только проставляем статус, считаем overflow по box_quantity, тянем имя товара."""
    try:
        _run(_verify_box_async(scan_id, user_id))
    except Exception as exc:
        logger.error("verify_box.error", scan_id=scan_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_box_async(scan_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus, Document
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        scan_result = await db.execute(select(Scan).where(Scan.id == scan_id))
        scan = scan_result.scalar_one_or_none()
        if not scan:
            logger.error("verify_box.scan_not_found", scan_id=scan_id)
            return

        scan.status = ScanStatus.valid
        scan.verified_at = datetime.now(timezone.utc)
        if scan.gtin:
            gk = normalize_gtin_key(scan.gtin)
            if gk:
                scan.gtin = gk

        qty = int(scan.box_quantity or 0) or 1
        scan_key = normalize_gtin_key(scan.gtin) if scan.gtin else None

        doc_q = await db.execute(
            select(Document).where(Document.id == scan.document_id)
        )
        doc = doc_q.scalar_one_or_none()
        plan_items = (doc.plan or []) if doc else []

        # overflow: короб целиком может вывести строку плана за лимит.
        if scan_key:
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
                already = await _count_valid_units_for_gtin(
                    db, scan.document_id, scan_key, scan.id
                )
                if already + qty > expected:
                    scan.status = ScanStatus.overflow
                    scan.error_message = (
                        f"Сверх плана: ожидалось {expected}, "
                        f"в коробе {qty} (уже {already})"
                    )

        if scan.gtin or scan.moysklad_product_id:
            await _enrich_scan_product_name_from_plan(db, scan)
        if scan.gtin and not scan.moysklad_product_id:
            await _enrich_scan_product_name_from_ms(db, user_id, scan)

        # unknown_product: GTIN короба не в плане и не нашёлся в каталоге МС.
        if scan.gtin and not scan.moysklad_product_id:
            in_plan = any(
                isinstance(p, dict)
                and normalize_gtin_key(p.get("gtin")) == scan_key
                for p in plan_items
            )
            if not in_plan:
                scan.status = ScanStatus.unknown_product
                scan.error_message = "Товар короба не найден в МС — сопоставьте вручную"

        await db.commit()

        logger.info(
            "verify_box.done",
            scan_id=scan_id,
            status=scan.status,
            gtin=scan.gtin,
            box_quantity=scan.box_quantity,
        )

        await _push_ws_update(
            user_id,
            scan_id,
            scan.status,
            scan.product_name,
            scan.error_message,
            gtin=scan.gtin,
            moysklad_product_id=scan.moysklad_product_id,
            is_box=scan.is_box,
            box_quantity=scan.box_quantity,
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
    is_box: Optional[bool] = None,
    box_quantity: Optional[int] = None,
    owner_name: Optional[str] = None,
    producer_name: Optional[str] = None,
    owner_inn: Optional[str] = None,
    withdrawn: Optional[bool] = None,
    withdraw_reason: Optional[str] = None,
    child_codes: Optional[list] = None,
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
        "is_box": is_box,
        "box_quantity": box_quantity,
        "owner_name": owner_name,
        "producer_name": producer_name,
        "owner_inn": owner_inn,
        "withdrawn": withdrawn,
        "withdraw_reason": withdraw_reason,
        "child_codes": child_codes,
    })
    await r.publish(f"ws:{user_id}", message)
    await r.aclose()


async def _push_cz_token_expired(user_id: str):
    """Сообщить фронту, что нужен вход в ЧЗ (баннер «войдите для распознавания кодов»)."""
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(f"ws:{user_id}", json.dumps({"type": "cz_token_expired"}))
    await r.aclose()


async def _push_verify_done(
    user_id: str, document_id: str, checked: int, failed: int = 0
):
    """Сообщить фронту, что пакетная проверка марок завершена (+ сколько не удалось)."""
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(
        f"ws:{user_id}",
        json.dumps(
            {
                "type": "verify_done",
                "document_id": document_id,
                "checked": checked,
                "failed": failed,
            }
        ),
    )
    await r.aclose()


async def _push_writeoff_status(
    user_id: str, document_id: str, status: str, error: Optional[str]
):
    """Сообщить фронту результат списания: status='done'|'error'."""
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(
        f"ws:{user_id}",
        json.dumps(
            {
                "type": "writeoff_status",
                "document_id": document_id,
                "status": status,
                "error_message": error,
            }
        ),
    )
    await r.aclose()


@celery_app.task(name="process_document")
def process_document_task(document_id: str, user_id: str):
    """
    Завершить документ: обновление МС с trackingCodes (demand — отгрузка,
    supply — приёмка по УПД), поиск product_id по GTIN на лету.
    API Честного Знака не вызывается.
    """
    try:
        _run(_process_document_async(document_id, user_id))
    except Exception as exc:
        logger.error("process_document.error", document_id=document_id, error=str(exc))
        _run(
            monitoring_emit(
                "process_document.error",
                level="error",
                document_id=document_id,
                error=str(exc),
            )
        )
        raise


# Backward-совместимый алиас для старого имени задачи.
@celery_app.task(name="accept_document")
def accept_document_task(document_id: str, user_id: str):
    process_document_task(document_id, user_id)


# Сколько документ может провисеть в processing до авто-сброса.
STALE_PROCESSING_HOURS = 24


@celery_app.task(name="cleanup_stale_processing")
def cleanup_stale_processing_task():
    """Сбросить в draft документы, зависшие в processing дольше суток.

    Отправка в МС могла не завершиться (краш воркера, ранний выход по истёкшему
    токену ЧЗ и т.п.) — «Обрабатывается» не должно висеть вечно. Возвращаем в
    draft, чтобы кладовщик мог повторить. Запускается Celery Beat раз в час.
    """
    return _run(_cleanup_stale_processing_async())


async def _cleanup_stale_processing_async() -> int:
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_PROCESSING_HOURS)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(
                Document.status == DocumentStatus.processing,
                Document.updated_at < cutoff,
            )
        )
        docs = result.scalars().all()
        for doc in docs:
            doc.status = DocumentStatus.draft
            # Не затираем информативную причину (напр. истёк токен ЧЗ), если она есть.
            if not doc.error_message:
                doc.error_message = (
                    "Отправка в МойСклад не завершилась за 24 ч — документ сброшен "
                    "в черновик. Проверьте и повторите отправку."
                )
        if docs:
            await db.commit()
        logger.info(
            "cleanup_stale_processing.done",
            reset=len(docs),
            cutoff_hours=STALE_PROCESSING_HOURS,
        )
        return len(docs)


async def _process_document_async(document_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus, Scan, ScanStatus, Integration
    from app.services.moysklad import MoySkladService
    from app.services.chestnyznak import ChestnyZnakService
    from app.core.security import decrypt_token
    from app.core.config import settings
    from sqlalchemy import select, or_, and_

    async with AsyncSessionLocal() as db:
        # Получаем сам документ — нужен kind для разветвления
        doc_result = await db.execute(select(Document).where(Document.id == document_id))
        doc = doc_result.scalar_one_or_none()
        if not doc:
            logger.error("process_document.not_found", document_id=document_id)
            return

        t0 = time.monotonic()
        kind = doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind)
        # demand — отгрузка, supply — приёмка по УПД. Обе ветки пишут trackingCodes
        # в позиции МС-документа одним и тем же механизмом (update_document).
        if kind not in ("demand", "supply"):
            logger.warning(
                "process_document.unsupported_kind",
                document_id=document_id,
                kind=kind,
            )
            return

        # Сканы для отправки: valid + overflow.
        # overflow — сверхплановые, визуально помечены красным, но идут в МС.
        # Плюс несопоставленные КОРОБА (is_box, unknown_product): у агрегата с AI 02
        # (GTIN вложенных товаров) собственный GTIN не извлекается парсером УПД, и на
        # импорте он остаётся unknown_product. Но его товар восстановим — при развороте
        # через ЧЗ (ниже) s.gtin становится unit-GTIN пачки и резолвится в каталоге МС.
        # Без этого исключения такие короба тихо выпадали из приёмки. Единичные КМ со
        # статусом unknown_product НЕ рескьюим — их GTIN действительно неизвестен.
        result = await db.execute(
            select(Scan).where(
                Scan.document_id == document_id,
                or_(
                    Scan.status.in_([ScanStatus.valid, ScanStatus.overflow]),
                    and_(
                        Scan.is_box.is_(True),
                        Scan.status == ScanStatus.unknown_product,
                    ),
                ),
            )
        )
        valid_scans = result.scalars().all()

        if not valid_scans:
            logger.warning(
                "process_document.no_valid_scans",
                document_id=document_id,
                kind=kind,
            )

        # Приёмка по УПД: коды упаковок (НомУпак) сохранены как is_box без раскрытия
        # (импорт УПД в API синхронно ЧЗ не дёргает). Перед записью в МС разворачиваем
        # такие агрегаты в листовые КМ пачек через ЧЗ (cises/info + aggregated/list,
        # «короб→блоки→пачки»), иначе в МС уйдёт код блока как одна штука. Сканы
        # отгрузки (demand) уже развёрнуты в verify_code_task — у них child_codes есть.
        boxes_to_expand = [s for s in valid_scans if s.is_box and not s.child_codes]
        if boxes_to_expand:
            # Развернуть агрегаты (НомУпак: блок/короб) в листовые КМ можно только
            # через ЧЗ. Без валидного токена (или в mock-режиме) писать сырые коды
            # упаковок в МС нельзя — это гарантированный 412. Для supply прерываем
            # запись с понятной ошибкой; для demand (обычно уже развёрнуто в
            # verify_code_task) — прежнее поведение: баннер + отправка как есть.
            cz_token = None if settings.CZ_MOCK_MODE else await _get_cz_token(db, user_id)
            if not cz_token:
                if kind == "supply":
                    doc.error_message = (
                        "Токен Честного Знака истёк. Войдите в ЧЗ заново и повторите "
                        "приёмку — коды упаковок не удалось развернуть в марки маркировки."
                    )
                    logger.warning(
                        "process_document.cz_token_missing",
                        document_id=document_id,
                        boxes=len(boxes_to_expand),
                    )
                    if not settings.CZ_MOCK_MODE:
                        await _push_cz_token_expired(user_id)
                    await db.commit()
                    await monitoring_emit(
                        "process_document.cz_token_missing",
                        level="warning",
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        document_id=document_id,
                        kind=kind,
                        boxes=len(boxes_to_expand),
                    )
                    return
                await _push_cz_token_expired(user_id)
            else:
                cz_groups = await _get_cz_product_groups(db, user_id)
                cz = ChestnyZnakService(
                    token=cz_token, mock=False, product_groups=cz_groups
                )
                for s in boxes_to_expand:
                    try:
                        info = await cz.get_code_info(s.code)
                    except Exception as exc:
                        logger.warning(
                            "process_document.expand_box_failed",
                            scan_id=str(s.id),
                            error=str(exc),
                        )
                        continue
                    if info and info.is_aggregate and info.children:
                        s.child_codes = info.children
                        s.box_quantity = len(info.children)
                        # GTIN агрегата ≠ GTIN пачки — берём GTIN вложенной пачки,
                        # чтобы скан матчился с планом и считался как N единиц.
                        child_gtin = extract_gtin(info.children[0])
                        if child_gtin:
                            gk = normalize_gtin_key(child_gtin)
                            if gk:
                                s.gtin = gk
                        if info.product_name:
                            s.product_name = info.product_name
                        logger.info(
                            "process_document.box_expanded",
                            scan_id=str(s.id),
                            units=s.box_quantity,
                            gtin=s.gtin,
                        )
                await db.flush()

            # Развернуть удалось не все агрегаты (ЧЗ 404 по всем товарным группам —
            # напр. группа товара не включена в настройках, либо код не зарегистрирован
            # как агрегат). Слать сырой код короба в МС нельзя: в поступление уйдёт код
            # упаковки вместо марок пачек. Для supply прерываем с понятной ошибкой,
            # не помечая документ accepted (см. guard на отсутствие токена выше).
            unexpanded = [s for s in boxes_to_expand if not s.child_codes]
            if kind == "supply" and unexpanded:
                doc.error_message = (
                    f"Не удалось развернуть {len(unexpanded)} коробов в марки маркировки: "
                    "Честный Знак не нашёл эти коды ни в одной из включённых товарных групп. "
                    "Проверьте, что нужная товарная группа включена в Настройках, и повторите приёмку."
                )
                logger.warning(
                    "process_document.boxes_unexpanded",
                    document_id=document_id,
                    unexpanded=len(unexpanded),
                    boxes=len(boxes_to_expand),
                )
                await db.commit()
                await monitoring_emit(
                    "process_document.boxes_unexpanded",
                    level="warning",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    document_id=document_id,
                    kind=kind,
                    unexpanded=len(unexpanded),
                )
                return

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
            # Лукап в каталоге нужен только для GTIN сканов, у которых нет прямого
            # moysklad_product_id (при приёмке по УПД он проставлен из позиций
            # поступления) — иначе на каждую приёмку летят десятки лишних GET в МС,
            # что упирает в rate limit (429) и роняет всю запись.
            unique_gtins = {
                normalize_gtin_key(s.gtin)
                for s in valid_scans
                if s.gtin and not s.moysklad_product_id
            }
            unique_gtins.discard(None)
            gtin_to_product_id: dict[str, str] = {}
            # Кол-во/цена/НДС позиции из плана (для supply — из УПД): product_id → …
            # Используется при создании новых позиций поступления в МС.
            product_qty: dict[str, int] = {}
            product_price: dict[str, dict] = {}
            for p in doc.plan or []:
                if not isinstance(p, dict):
                    continue
                g, pid = p.get("gtin"), p.get("product_id")
                ng = normalize_gtin_key(g)
                if ng and pid and isinstance(pid, str):
                    gtin_to_product_id.setdefault(ng, pid)
                if pid and isinstance(pid, str):
                    try:
                        q = int(p.get("expected_qty") or 0)
                    except (TypeError, ValueError):
                        q = 0
                    if q > 0:
                        product_qty[pid] = q
                    pr: dict = {}
                    if p.get("price") is not None:
                        pr["price"] = p.get("price")
                    if p.get("vat") is not None:
                        pr["vat"] = p.get("vat")
                    if pr:
                        product_price[pid] = pr

            # Комментарий поступления: «Импорт с ЭДО» + реквизиты счёта-фактуры из УПД.
            ms_description: Optional[str] = None
            if kind == "supply":
                meta = doc.upd_meta or {}
                inv_no = (meta.get("invoice_number") or "").strip()
                inv_dt = (meta.get("invoice_date") or "").strip()
                ms_description = "Импорт с ЭДО"
                if inv_no:
                    ms_description += f". Счёт-фактура № {inv_no}"
                    if inv_dt:
                        ms_description += f" от {inv_dt}"

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
            max_iterations = len(valid_scans) + 1
            iterations = 0
            while remaining_scans:
                iterations += 1
                if iterations > max_iterations:
                    logger.error(
                        "process_document.retry_limit",
                        document_id=document_id,
                        remaining=len(remaining_scans),
                    )
                    break
                scans_data = []
                for s in remaining_scans:
                    pid_default = (
                        s.moysklad_product_id
                        or (
                            gtin_to_product_id.get(normalize_gtin_key(s.gtin))
                            if s.gtin
                            else None
                        )
                    )
                    if s.child_codes:
                        # Блок/агрегат: в МС пишем КМ вложенных пачек поштучно,
                        # код блока не отправляем.
                        for cc in s.child_codes:
                            cg = normalize_gtin_key(extract_gtin(cc)) or normalize_gtin_key(s.gtin)
                            scans_data.append({
                                "code": cc,
                                "gtin": cg,
                                "product_id": (
                                    s.moysklad_product_id
                                    or (gtin_to_product_id.get(cg) if cg else None)
                                    or pid_default
                                ),
                                "is_box": False,
                                "quantity": 1,
                            })
                    else:
                        scans_data.append({
                            "code": s.code,
                            "gtin": s.gtin,
                            "product_id": pid_default,
                            "is_box": s.is_box,
                            # Штрихкод немаркированного товара: quantity без trackingCode.
                            "is_barcode": s.is_barcode,
                            # Короб «целиком»/штрихкод = box_quantity единиц; обычный скан = 1.
                            "quantity": (
                                (int(s.box_quantity or 0) or 1)
                                if (s.is_box or s.is_barcode)
                                else 1
                            ),
                        })
                # В update_document попадут только строки с product_id.
                if not any(s.get("product_id") for s in scans_data):
                    logger.warning(
                        "process_document.no_product_ids",
                        document_id=document_id,
                        kind=kind,
                        scans=len(scans_data),
                    )
                    break
                result = await ms.update_document(
                    kind,
                    doc.moysklad_id,
                    scans_data,
                    position_quantities=product_qty,
                    position_prices=product_price,
                    description=ms_description,
                )
                if isinstance(result, dict) and result.get("__moysklad_412__") is True:
                    body = result.get("body") or ""
                    m = re.search(
                        r"неверный формат кода маркировки\s+([^\s\",}]+)",
                        body,
                        flags=re.IGNORECASE,
                    )
                    bad_code = m.group(1) if m else None
                    if not bad_code:
                        # 412 не про формат кода (напр. error_3007 «Нельзя отгрузить
                        # товар, которого нет на складе»). Это бизнес-ошибка МС, а не
                        # битый КМ — показываем кладовщику текст МС и не роняем задачу
                        # необработанным исключением (иначе документ навсегда виснет
                        # в «Обрабатывается»).
                        ms_reason = _extract_moysklad_error(body)
                        logger.error(
                            "process_document.moysklad_412_business",
                            document_id=document_id,
                            reason=ms_reason,
                            body=body[:800],
                        )
                        doc.error_message = (
                            f"МойСклад отклонил документ: {ms_reason}"
                            if ms_reason
                            else "МойСклад отклонил сохранение документа (412). "
                            "Проверьте остатки и позиции документа в МойСклад."
                        )
                        # Возвращаем в draft — «Обрабатывается» не должно врать;
                        # кладовщик видит причину и может повторить после исправления.
                        doc.status = DocumentStatus.draft
                        await db.commit()
                        await monitoring_emit(
                            "process_document.rejected",
                            level="error",
                            duration_ms=int((time.monotonic() - t0) * 1000),
                            document_id=document_id,
                            kind=kind,
                            reason=ms_reason,
                        )
                        return
                    bad_scan = next(
                        (
                            s
                            for s in remaining_scans
                            if _cis_matches_ms_error_message(s.code, bad_code)
                            or any(
                                _cis_matches_ms_error_message(cc, bad_code)
                                for cc in (s.child_codes or [])
                            )
                        ),
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
                        # Не роняем весь батч из-за одного несопоставимого кода:
                        # прекращаем ретраи; коды, принятые в прошлых итерациях,
                        # сохранены, документ финализируется как accepted.
                        break

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
        # Успешный прогон (в т.ч. повторный после входа в ЧЗ) — снимаем прошлую ошибку.
        doc.error_message = None
        await db.commit()

        logger.info(
            "process_document.done",
            document_id=document_id,
            kind=kind,
            valid_count=len(valid_scans),
        )
        await monitoring_emit(
            "process_document.done",
            duration_ms=int((time.monotonic() - t0) * 1000),
            document_id=document_id,
            kind=kind,
            valid_count=len(valid_scans),
        )


# Терминальные статусы документа ГИС МТ (см. справочник «Статусы документов»).
_WRITEOFF_OK = {"CHECKED_OK"}
_WRITEOFF_PENDING = {None, "", "IN_PROGRESS", "PENDING", "CHECKED", "NEW", "PROCESSING"}


@celery_app.task(name="poll_writeoff_status")
def poll_writeoff_status_task(document_id: str, user_id: str):
    """Опросить статус поданных в ЧЗ документов вывода из оборота и финализировать."""
    _run(_poll_writeoff_async(document_id, user_id))


async def _poll_writeoff_async(document_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus, Integration
    from app.services.chestnyznak import ChestnyZnakService, CZApiError
    from app.core.security import decrypt_token
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        doc_result = await db.execute(select(Document).where(Document.id == document_id))
        doc = doc_result.scalar_one_or_none()
        if not doc or not doc.cz_doc_ids:
            logger.warning("writeoff.poll.no_doc_ids", document_id=document_id)
            return

        int_result = await db.execute(
            select(Integration).where(Integration.user_id == user_id)
        )
        integration = int_result.scalar_one_or_none()
        if not integration or not integration.cz_token:
            logger.warning("writeoff.poll.no_token", document_id=document_id)
            return

        cz = ChestnyZnakService(token=decrypt_token(integration.cz_token))
        items = list(doc.cz_doc_ids)
        statuses: dict[str, Optional[str]] = {}
        error: Optional[str] = None

        # Несколько попыток с задержкой: ждём перехода всех документов в терминальный статус.
        for _attempt in range(15):
            all_terminal = True
            for item in items:
                doc_id = item["doc_id"]
                if statuses.get(doc_id) in _WRITEOFF_OK:
                    continue
                try:
                    status = await cz.get_document_status(item["pg"], doc_id)
                except CZApiError as e:
                    error = str(e)
                    all_terminal = False
                    continue
                statuses[doc_id] = status
                if status in _WRITEOFF_PENDING:
                    all_terminal = False
                elif status not in _WRITEOFF_OK:
                    error = f"Документ {doc_id}: статус {status}"
            if all_terminal:
                break
            await asyncio.sleep(4)

        all_ok = bool(statuses) and all(
            statuses.get(i["doc_id"]) in _WRITEOFF_OK for i in items
        )
        if all_ok:
            doc.status = DocumentStatus.accepted
            await db.commit()
            logger.info("writeoff.poll.done", document_id=document_id)
            await _push_writeoff_status(str(user_id), str(document_id), "done", None)
            await monitoring_emit(
                "writeoff.done",
                source="worker",
                document_id=str(document_id),
                docs=len(items),
            )
        else:
            # Не финализируем как accepted; возвращаем в draft, чтобы можно было повторить.
            doc.status = DocumentStatus.draft
            await db.commit()
            logger.warning(
                "writeoff.poll.error",
                document_id=document_id,
                statuses={i["doc_id"]: statuses.get(i["doc_id"]) for i in items},
            )
            await _push_writeoff_status(
                str(user_id),
                str(document_id),
                "error",
                error or "Не все документы обработаны Честным Знаком",
            )
            await monitoring_emit(
                "writeoff.error",
                level="error",
                source="worker",
                document_id=str(document_id),
                error=error or "Не все документы обработаны Честным Знаком",
            )


@celery_app.task(name="edo_sync")
def edo_sync_task(user_id: str, date_from: str, date_to: str = None, use_cursor: bool = True):
    """Синхронизация ЭДО Saby (лента изменений) в EdoDocument/EdoMark для контроля марок."""
    _run(_edo_sync_async(user_id, date_from, date_to, use_cursor))


async def _edo_sync_async(user_id: str, date_from: str, date_to, use_cursor: bool):
    import redis.asyncio as aioredis
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration
    from app.services.edo_sync import sync_user
    from sqlalchemy import select

    # Идемпотентность: один активный синк на пользователя.
    lock_key = f"edo_sync:lock:{user_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    got = await r.set(lock_key, "1", nx=True, ex=1800)
    if not got:
        await r.aclose()
        logger.info("edo_sync.already_running", user_id=user_id)
        return
    try:
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
            if not integ:
                return
            res = await sync_user(db, integ, date_from=date_from, date_to=date_to, use_cursor=use_cursor)
            await db.commit()
        # результат в Redis для опроса фронтом
        import json as _json
        r2 = aioredis.from_url(settings.REDIS_URL)
        try:
            await r2.set(f"edo_sync:result:{user_id}", _json.dumps(res), ex=3600)
        finally:
            await r2.aclose()
    except Exception as exc:
        logger.error("edo_sync.error", user_id=user_id, error=str(exc))
    finally:
        try:
            await r.delete(lock_key); await r.aclose()
        except Exception:
            pass
