import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.worker.celery_app import celery_app
from app.core.logging import logger
from app.core.monitoring import emit as monitoring_emit
from app.services.chestnyznak import (
    ChestnyZnakService,
    cis_compare_forms_for_ms,
    extract_gtin,
    is_sscc,
    normalize_sscc,
    normalize_gtin_key,
)


@celery_app.task(name='prepare_physical_count')
def prepare_physical_count_task(session_id):
    return _run(_prepare_physical_count_async(session_id))


async def _prepare_physical_count_async(session_id):
    from sqlalchemy import select
    from app.db.models import PhysicalCountSession
    from app.db.session import AsyncSessionLocal
    from app.api.tsd import _ms_for_user
    from app.services.physical_counts import prepare_inventory_plan
    async with AsyncSessionLocal() as db:
        session = (await db.execute(select(PhysicalCountSession).where(
            PhysicalCountSession.id == session_id).with_for_update())).scalar_one_or_none()
        if not session or session.status != 'preparing':
            return
        try:
            ms = await _ms_for_user(db, session.user_id)
            config = session.settings
            plan = await prepare_inventory_plan(ms, config['store_id'], config['include_state_ids'])
            session.plan = plan
            session.settings = {**config, 'snapshot_at': datetime.now(timezone.utc).isoformat()}
            session.status = 'active'
            logger.info('physical_count.prepared', session_id=session_id, positions=len(plan))
        except Exception as exc:
            session.status = 'error'
            session.error_message = 'Не удалось выгрузить остатки и отгрузки из МойСклада. Проверьте подключение и создайте новую сессию.'
            logger.warning('physical_count.prepare_failed', session_id=session_id, error_type=type(exc).__name__)
        await db.commit()


async def _expand_aggregate_for_processing(cz, code: str) -> tuple[list[str], Optional[str]]:
    """Раскрыть упаковку перед записью приёмки в МС.

    SSCC нельзя пропускать через ``get_code_info``: нормализатор обычного КИ
    воспринимает 20 цифр как ``GTIN + serial`` и превращает ``00…`` в ``01…21…``.
    True API закономерно отвечает 404 на изменённый код. Для SSCC сразу используем
    ``aggregated/list`` через ``unpack_box``; AI-02/прочие агрегаты по-прежнему
    раскрываем через ``cises/info`` с последующим ``aggregated/list``.
    """
    if is_sscc(code):
        return list(await cz.unpack_box(normalize_sscc(code))), None

    info = await cz.get_code_info(code)
    if not (info and info.is_aggregate and info.children):
        return [], None
    return list(info.children), info.product_name


def _build_moysklad_scans_data(
    scans,
    kind: str,
    gtin_to_product_id: dict[str, str],
) -> list[dict]:
    """Подготовить сканы к записи в позиции документа МойСклад.

    Для отгрузки SSCC, который пользователь выбрал как «короб целиком»
    (``is_box=True``), всегда остаётся одним ``transportpack``. Состав короба может
    быть сохранён в ``child_codes`` после проверки в ЧЗ, но это справочная
    информация и не должно превращать выбранный пользователем короб в пачки.

    При приёмке агрегаты по-прежнему разворачиваются: этот поток импортирует коды
    упаковок из УПД и записывает в МС вложенные марки.
    """
    rows: list[dict] = []
    for scan in scans:
        pid_default = (
            scan.moysklad_product_id
            or (
                gtin_to_product_id.get(normalize_gtin_key(scan.gtin))
                if scan.gtin
                else None
            )
        )
        send_whole_box = kind == "demand" and bool(scan.is_box or getattr(scan, "keep_aggregate", False))
        if scan.child_codes and not send_whole_box:
            # Развёрнутый агрегат: в МС пишем КМ вложенных пачек поштучно.
            for child_code in scan.child_codes:
                child_gtin = (
                    normalize_gtin_key(extract_gtin(child_code))
                    or normalize_gtin_key(scan.gtin)
                )
                rows.append({
                    "code": child_code,
                    "gtin": child_gtin,
                    "product_id": (
                        scan.moysklad_product_id
                        or (gtin_to_product_id.get(child_gtin) if child_gtin else None)
                        or pid_default
                    ),
                    "is_box": False,
                    "quantity": 1,
                })
        else:
            rows.append({
                "code": scan.code,
                "gtin": scan.gtin,
                "product_id": pid_default,
                "is_box": scan.is_box,
                "is_barcode": scan.is_barcode,
                "quantity": int(scan.box_quantity or 0) or 1,
            })
    return rows


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


async def _get_cz_source(db, user_id, document_id=None):
    from sqlalchemy import select
    from app.db.models import Document, Integration, OrganizationProfile

    if document_id is not None:
        profile = (
            await db.execute(
                select(OrganizationProfile)
                .join(Document, Document.organization_profile_id == OrganizationProfile.id)
                .where(Document.id == document_id, OrganizationProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile:
            return profile
    return (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()


async def _get_cz_token(db, user_id, document_id=None) -> Optional[str]:
    """Действующий (не просроченный) токен ЧЗ пользователя или None."""
    from sqlalchemy import select
    from app.core.security import decrypt_token

    integ = await _get_cz_source(db, user_id, document_id)
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


async def _get_cz_product_groups(db, user_id, document_id=None) -> list[str]:
    """Товарные группы (pg) клиента для сужения перебора в ЧЗ. [] → глобальный дефолт."""
    integ = await _get_cz_source(db, user_id, document_id)
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


@celery_app.task(bind=True, max_retries=3, name="unpack_scan")
def unpack_scan_task(self, scan_id: str, user_id: str, old_status: str):
    try:
        _run(_unpack_scan_async(scan_id, user_id, old_status))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _unpack_scan_async(scan_id, user_id, old_status):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Scan, ScanStatus, Document, DocumentStatus
    from app.services.scan_packaging import package_type
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        scan = (await db.execute(select(Scan).join(Document).where(
            Scan.id == scan_id, Document.user_id == user_id))).scalar_one_or_none()
        if not scan or scan.status != ScanStatus.pending:
            return
        children, kind, error = [], None, None
        document_id = scan.document_id
        try:
            token = await _get_cz_token(db, user_id, scan.document_id)
            if not token:
                raise ValueError("Войдите в Честный Знак для получения состава блока")
            groups = await _get_cz_product_groups(db, user_id, scan.document_id)
            info = await ChestnyZnakService(token=token, mock=False, product_groups=groups).get_code_info(scan.code)
            if not info or not info.children:
                raise ValueError("Честный Знак не вернул состав упаковки. Блок сохранён целиком.")
            children = list(dict.fromkeys(info.children))
            kind = package_type(info.package_type)
        except Exception as exc:
            logger.warning('scan.unpack_failed', scan_id=scan_id, error=str(exc))
            error = str(exc) if isinstance(exc, ValueError) else "Не удалось получить состав упаковки. Повторите попытку."
        await db.rollback()
        doc = (await db.execute(select(Document).where(Document.id == document_id).with_for_update())).scalar_one_or_none()
        if not doc or doc.status != DocumentStatus.draft:
            return
        scan = (await db.execute(select(Scan).where(Scan.id == scan_id).with_for_update())).scalar_one_or_none()
        if not scan or scan.status != ScanStatus.pending:
            return
        scan.status = ScanStatus(old_status)
        scan.error_message = error
        if children:
            scan.child_codes = children
            scan.box_quantity = len(children)
            scan.package_type = kind or 'GROUP'
            scan.keep_aggregate = False
        await db.commit()
        await _push_ws_update(user_id, scan_id, scan.status.value, scan.product_name,
                              scan.error_message, document_id=str(scan.document_id), gtin=scan.gtin,
                              moysklad_product_id=scan.moysklad_product_id, is_box=scan.is_box,
                              box_quantity=scan.box_quantity, child_codes=scan.child_codes,
                              package_type=scan.package_type, keep_aggregate=scan.keep_aggregate)


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
            strict = getattr(precheck, 'verification', None)
            if strict is not None:
                previous = getattr(scan, 'verification', None) or {}
                if previous.get('ms_error'):
                    strict = {**strict, 'ms_error': previous['ms_error'], 'ms_error_at': previous.get('ms_error_at')}
                scan.verification = strict
                scan.owner_inn = precheck.owner_inn
                scan.owner_name = precheck.owner_name
                scan.withdrawn = bool(precheck.mark_withdraw)
                scan.withdraw_reason = precheck.withdraw_reason
                scan.error_message = None

            if getattr(precheck, "uncertain", False):
                if strict is not None:
                    scan.status = ScanStatus.scanned
                    scan.verified_at = None
                scan.error_message = (
                    precheck.error or "Не удалось проверить в ЧЗ — повторите"
                )
                await db.commit()
                await _push_ws_update(
                    user_id, scan_id, scan.status, scan.product_name,
                    scan.error_message, document_id=str(scan.document_id), gtin=scan.gtin,
                    moysklad_product_id=scan.moysklad_product_id, is_box=scan.is_box,
                    box_quantity=scan.box_quantity, owner_name=scan.owner_name,
                    producer_name=scan.producer_name, owner_inn=scan.owner_inn,
                    withdrawn=scan.withdrawn, withdraw_reason=scan.withdraw_reason,
                    child_codes=scan.child_codes,
                    verification=getattr(scan, 'verification', None), verified_at=scan.verified_at,
                )
                logger.info("verify_code.uncertain", scan_id=scan_id)
                return

            valid = precheck.found and str(precheck.status or "").upper() == "INTRODUCED" and not precheck.mark_withdraw
            if valid:
                # Снимаем возможную ошибку от прошлого неудачного прохода (повтор).
                scan.error_message = None
            scan.owner_name = precheck.owner_name
            scan.owner_inn = precheck.owner_inn
            scan.producer_name = precheck.producer_name
            scan.withdrawn = bool(precheck.mark_withdraw)
            scan.withdraw_reason = precheck.withdraw_reason
            from app.services.scan_packaging import package_type
            scan.package_type = package_type(getattr(precheck, "package_type", None)) or scan.package_type
            out_gtin = precheck.gtin or scan.gtin
            name_override = precheck.product_name
            # Агрегат (блок/короб): развернуть в листовые КМ — отдельный запрос (редко).
            if getattr(precheck, 'verified_children', None) is not None:
                scan.child_codes = precheck.verified_children
                scan.box_quantity = len(scan.child_codes)
                if scan.child_codes:
                    out_gtin = _extract_gtin(scan.child_codes[0]) or out_gtin
            if strict is None and valid and (precheck.child_count or scan.package_type in {"GROUP", "BOX"}) and not scan.child_codes:
                tok = await _get_cz_token(db, user_id, scan.document_id)
                if tok:
                    grp = await _get_cz_product_groups(db, user_id, scan.document_id)
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
            integration = await _get_cz_source(db, user_id, scan.document_id)
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
            scan.verification = {'source': 'format', 'checked_at': None, 'format_valid': verify_result.valid,
                                 'owner_result': 'unknown', 'owner_reason': 'Проверен только формат, проверка ЧЗ не выполнена'}
            # USB-сканер даёт сырой GS1; официальное приложение ЧЗ ходит в API.
            # При невалидном локальном разборе — запрос в ЧЗ по полной CIS (нужен токен УКЭП).
            if not verify_result.valid:
                integration = await _get_cz_source(db, user_id, scan.document_id)
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
        scan.verified_at = datetime.now(timezone.utc) if (getattr(scan, 'verification', None) or {}).get('source', '').startswith('cz_') else None
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
            cz_token2 = await _get_cz_token(db, user_id, scan.document_id)
            if not cz_token2:
                # Нет/истёк токен ЧЗ — коды не распознаём через ЧЗ (блоки/короба
                # не развернутся). Сообщаем фронту (баннер «войдите в ЧЗ»).
                await _push_cz_token_expired(user_id, str(scan.document_id))
            if cz_token2:
                info = None
                cz_groups = await _get_cz_product_groups(db, user_id, scan.document_id)
                try:
                    info = await ChestnyZnakService(
                        token=cz_token2, mock=False, product_groups=cz_groups
                    ).get_code_info(scan.code)
                except Exception as exc:
                    logger.warning(
                        "verify_code.code_info_failed", scan_id=scan_id, error=str(exc)
                    )
                if info:
                    from app.services.scan_packaging import package_type
                    scan.package_type = package_type(info.package_type) or scan.package_type
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
            document_id=str(scan.document_id),
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
            verification=getattr(scan, 'verification', None), verified_at=scan.verified_at,
        )


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="verify_document")
def verify_document_task(self, document_id: str, user_id: str, recheck_all: bool = False):
    """Пакетная проверка марок документа в ЧЗ по кнопке «Проверить марки».

    Основной флоу: при скане КМ проверяется только локально (формат GS1) и получает
    статус `scanned`. Здесь проверяем все такие сканы в ЧЗ разом — статус/владелец/
    withdrawn/разворот агрегатов/план/сопоставление товара МС."""
    try:
        _run(_verify_document_async(document_id, user_id, recheck_all))
    except Exception as exc:
        logger.error("verify_document.error", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))


async def _verify_document_async(document_id: str, user_id: str, recheck_all: bool = False):
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
            from app.db.models import Document
            from uuid import UUID
            from app.services.shipment_corrections import correction
            doc = await db.get(Document, UUID(document_id))
            if not doc or str(doc.user_id) != user_id or doc.status.value != 'draft':
                return
            baseline_ids = list((correction(doc) or {}).get('baseline_scans', {}))
            filters = [Scan.document_id == document_id, Scan.is_barcode.is_(False),
                       Scan.status.in_([ScanStatus.scanned, ScanStatus.valid, ScanStatus.overflow, ScanStatus.invalid, ScanStatus.unknown_product])
                       if recheck_all else Scan.status == ScanStatus.scanned]
            if baseline_ids:
                filters.append(Scan.id.not_in([UUID(value) for value in baseline_ids]))
            res = await db.execute(
                select(Scan)
                .where(
                    *filters,
                )
                .order_by(Scan.scanned_at.asc())
            )
            scans = res.scalars().all()
            scan_ids = [str(s.id) for s in scans]
            codes = [s.code for s in scans]
            scan_gtins = [s.gtin for s in scans if s.gtin]
            cz_token = await _get_cz_token(db, user_id, document_id)
            cz_groups = await _get_cz_product_groups(db, user_id, document_id)
            integration = await _get_cz_source(db, user_id, document_id)
            signature_inn = integration.cz_inn if integration else None

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
        use_batch = bool(codes)
        checks_by_code: dict[str, CisCheck] = {}
        cz_started = time.monotonic()
        if use_batch:
            try:
                from app.services.scan_verification import check_scans
                cz = ChestnyZnakService(token=cz_token, mock=False, product_groups=cz_groups) if cz_token and not settings.CZ_MOCK_MODE else None
                checks_by_code = await check_scans(cz, scans, signature_inn)
                if cz is None:
                    await _push_cz_token_expired(user_id, document_id)
            except Exception as exc:
                # Весь батч не удался → все коды uncertain (останутся scanned, повтор).
                logger.warning(
                    "verify_document.check_codes_failed",
                    document_id=document_id,
                    error=str(exc),
                )

        logger.info('verify_document.cz_checked', document_id=document_id, count=len(checks_by_code),
                    duration_ms=int((time.monotonic() - cz_started) * 1000))
        apply_started = time.monotonic()
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
                            verification_source='cz_unavailable',
                            verification={'source': 'cz_unavailable', 'checked_at': None,
                                          'owner_result': 'unknown', 'owner_reason': 'Ответ ЧЗ не получен'},
                        )
                    if precheck.uncertain:
                        failed += 1
                try:
                    await _verify_code_async(scan_id, user_id, precheck=precheck)
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "verify_document.scan_failed", scan_id=scan_id, error=str(exc)
                    )

        if scan_ids:
            await asyncio.gather(
                *(_one(sid, code) for sid, code in zip(scan_ids, codes))
            )

        logger.info('verify_document.results_applied', document_id=document_id, count=len(scan_ids),
                    duration_ms=int((time.monotonic() - apply_started) * 1000))
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
            document_id=str(scan.document_id),
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
    document_id: Optional[str] = None,
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
    package_type: Optional[str] = None,
    keep_aggregate: Optional[bool] = None,
    verification: Optional[dict] = None,
    verified_at=None,
):
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    message = json.dumps({
        "type": "scan_update",
        "scan_id": scan_id,
        "document_id": document_id,
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
        "package_type": package_type,
        "keep_aggregate": keep_aggregate,
        "verification": verification,
        "verified_at": verified_at.isoformat() if verified_at else None,
    })
    await r.publish(f"ws:{user_id}", message)
    await r.aclose()


async def _push_cz_token_expired(user_id: str, document_id: Optional[str] = None):
    """Сообщить фронту, что нужен вход в ЧЗ (баннер «войдите для распознавания кодов»)."""
    import redis.asyncio as aioredis
    import json
    from app.core.config import settings

    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(
        f"ws:{user_id}",
        json.dumps({"type": "cz_token_expired", "document_id": document_id}),
    )
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
        raise


@celery_app.task(name='correct_shipment')
def correct_shipment_task(document_id: str, user_id: str):
    from app.services.shipment_corrections import process
    _run(process(document_id, user_id))


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
    from app.db.models import Document, DocumentStatus, DocumentKind
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_PROCESSING_HOURS)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(
                Document.status == DocumentStatus.processing,
                Document.kind != DocumentKind.loss,
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
            from app.core.diagnostics import operation_scope
            from app.services.incidents import record
            for doc in docs:
                with operation_scope(doc.id, doc.user_id, 'process_document.stale'):
                    await record('process_document.stale', doc.error_message)
        logger.info(
            "cleanup_stale_processing.done",
            reset=len(docs),
            cutoff_hours=STALE_PROCESSING_HOURS,
        )
        return len(docs)


async def _process_document_async(document_id: str, user_id: str):
    from app.core.diagnostics import operation_scope, failure_reason
    from app.services.incidents import record
    with operation_scope(document_id, user_id, 'process_document'):
        try:
            return await _process_document_traced_async(document_id, user_id)
        except Exception as exc:
            await record('process_document.error', str(exc), reason=failure_reason(exc))
            raise


async def _process_document_traced_async(document_id: str, user_id: str):
    from app.services.document_guard import processing_lock
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus
    from sqlalchemy import select
    async with processing_lock(f"process:{document_id}") as acquired:
        if not acquired:
            return
        try:
            await _process_document_unlocked_async(document_id, user_id)
        except Exception as exc:
            async with AsyncSessionLocal() as db:
                doc = (await db.execute(select(Document).where(
                    Document.id == document_id, Document.user_id == user_id,
                    Document.status == DocumentStatus.processing,
                ))).scalar_one_or_none()
                if doc:
                    doc.status = DocumentStatus.draft
                    doc.error_message = (str(exc) if isinstance(exc, ValueError) else
                        "Отправка в МойСклад прервалась. Часть марок могла сохраниться; повторная отправка продолжит с оставшихся.")
                    await db.commit()
            raise


async def _process_document_unlocked_async(document_id: str, user_id: str):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus, Scan, ScanStatus, Integration
    from app.services.moysklad import MoySkladService
    from app.services.chestnyznak import ChestnyZnakService
    from app.core.security import decrypt_token
    from app.core.config import settings
    from sqlalchemy import select, or_, and_

    async with AsyncSessionLocal() as db:
        # Получаем сам документ — нужен kind для разветвления
        doc_result = await db.execute(select(Document).where(Document.id == document_id, Document.user_id == user_id))
        doc = doc_result.scalar_one_or_none()
        if not doc:
            logger.error("process_document.not_found", document_id=document_id)
            return

        if doc.status != DocumentStatus.processing:
            return
        if (getattr(doc, 'upd_meta', None) or {}).get('shipment_correction'):
            raise ValueError('Исправление отгрузки отправляется отдельной задачей; полная запись запрещена.')
        t0 = time.monotonic()
        kind = doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind)
        # demand — отгрузка, supply — приёмка по УПД. Обе ветки пишут trackingCodes
        # в позиции МС-документа одним и тем же механизмом (update_document).
        if kind not in ("demand", "supply"):
            raise ValueError("Этот документ нельзя отправить в МойСклад через проведение")
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
            raise ValueError("Нет проверенных марок или товаров для отправки")
        unresolved = (await db.execute(select(Scan.id).where(
            Scan.document_id == document_id,
            Scan.status.in_([ScanStatus.pending, ScanStatus.scanned]),
        ).limit(1))).first()
        if unresolved:
            raise ValueError("Проверка марок ещё не завершена. Дождитесь результата.")
        # Приёмка по УПД: коды упаковок (НомУпак) сохранены как is_box без раскрытия
        # (импорт УПД в API синхронно ЧЗ не дёргает). Перед записью в МС разворачиваем
        # такие агрегаты в листовые КМ пачек через ЧЗ (cises/info + aggregated/list,
        # «короб→блоки→пачки»), иначе в МС уйдёт код блока как одна штука. Сканы
        # отгрузки (demand) уже развёрнуты в verify_code_task — у них child_codes есть.
        # В отгрузке is_box означает явный выбор «Короб: целиком»: такой SSCC
        # отправляем одним transportpack и не раскрываем здесь повторно. Для режима
        # «раскрывать» /scans/box сразу создаёт отдельные сканы пачек (is_box=False).
        # Автоматическое раскрытие перед записью требуется только для приёмки по УПД.
        boxes_to_expand = (
            [s for s in valid_scans if s.is_box and not s.child_codes]
            if kind == "supply"
            else []
        )
        if boxes_to_expand:
            from app.core.diagnostics import current_operation
            operation = current_operation.get()
            if operation:
                operation.stage = 'chestnyznak.expand_boxes'
            # Развернуть агрегаты (НомУпак: блок/короб) в листовые КМ можно только
            # через ЧЗ. Без валидного токена (или в mock-режиме) писать сырые коды
            # упаковок в МС нельзя — это гарантированный 412. Для supply прерываем
            # запись с понятной ошибкой; для demand (обычно уже развёрнуто в
            # verify_code_task) — прежнее поведение: баннер + отправка как есть.
            cz_token = None if settings.CZ_MOCK_MODE else await _get_cz_token(db, user_id, document_id)
            if not cz_token:
                if kind == "supply":
                    doc.error_message = (
                        "Токен Честного Знака истёк. Войдите в ЧЗ заново и повторите "
                        "приёмку — коды упаковок не удалось развернуть в марки маркировки."
                    )
                    doc.status = DocumentStatus.draft
                    logger.warning(
                        "process_document.cz_token_missing",
                        document_id=document_id,
                        boxes=len(boxes_to_expand),
                        message=doc.error_message,
                    )
                    if not settings.CZ_MOCK_MODE:
                        await _push_cz_token_expired(user_id, str(document_id))
                    await db.commit()
                    await monitoring_emit(
                        "process_document.cz_token_missing",
                        level="warning",
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        document_id=document_id,
                        kind=kind,
                        boxes=len(boxes_to_expand),
                        message=doc.error_message,
                    )
                    return
                await _push_cz_token_expired(user_id, str(document_id))
            else:
                cz_groups = await _get_cz_product_groups(db, user_id, document_id)
                cz = ChestnyZnakService(
                    token=cz_token, mock=False, product_groups=cz_groups
                )
                for s in boxes_to_expand:
                    try:
                        children, product_name = await _expand_aggregate_for_processing(
                            cz, s.code
                        )
                    except Exception as exc:
                        logger.warning(
                            "process_document.expand_box_failed",
                            scan_id=str(s.id),
                            error=str(exc),
                        )
                        continue
                    if children:
                        s.child_codes = children
                        s.box_quantity = len(children)
                        # GTIN агрегата ≠ GTIN пачки — берём GTIN вложенной пачки,
                        # чтобы скан матчился с планом и считался как N единиц.
                        child_gtin = extract_gtin(children[0])
                        if child_gtin:
                            gk = normalize_gtin_key(child_gtin)
                            if gk:
                                s.gtin = gk
                        if product_name:
                            s.product_name = product_name
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
                    "Не получен полный состав упаковок из Честного Знака. "
                    "Проверьте подключение ЧЗ и товарные группы; подробности сохранены для диагностики."
                )
                doc.status = DocumentStatus.draft
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
                    message=doc.error_message,
                    codes=[{'scan_id': str(s.id), 'code': s.code, 'gtin': s.gtin} for s in unexpanded],
                )
                return

        # Интеграции
        from app.core.diagnostics import current_operation
        operation = current_operation.get()
        if operation:
            operation.stage = 'moysklad.write_document'
        int_result = await db.execute(
            select(Integration).where(Integration.user_id == user_id)
        )
        integration = int_result.scalar_one_or_none()

        # Обновление МС-документа (trackingCodes для supply и отгрузочных типов)
        if not doc.moysklad_id or not integration or not integration.moysklad_token:
            raise ValueError("МойСклад не подключён или документ не выбран")
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
            from app.services.plan_matching import plan_gtin_keys, unique_plan_product
            for p in doc.plan or []:
                if not isinstance(p, dict):
                    continue
                pid = p.get("product_id")
                for ng in plan_gtin_keys(p):
                    matched = unique_plan_product(doc.plan, ng)
                    if matched:
                        gtin_to_product_id[ng] = matched["product_id"]
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

            scans_data = _build_moysklad_scans_data(valid_scans, kind, gtin_to_product_id)
            if any(not item.get("product_id") for item in scans_data):
                raise ValueError("Не все марки сопоставлены с товарами МойСклад. Сопоставьте товары и повторите.")

            from app.services.document_cis import needs_cis_confirmation, confirm_document_cis
            if any(needs_cis_confirmation(item['code']) and not item.get('is_box')
                   and not item.get('is_barcode') for item in scans_data):
                if operation:
                    operation.stage = 'chestnyznak.confirm_document_cis'
                cis_token = await _get_cz_token(db, user_id, document_id)
                if not cis_token:
                    await _push_cz_token_expired(user_id, str(document_id))
                cis_groups = await _get_cz_product_groups(db, user_id, document_id)
                scans_data = await confirm_document_cis(scans_data, ChestnyZnakService(
                    token=cis_token, mock=False, product_groups=cis_groups,
                ))
                if operation:
                    operation.stage = 'moysklad.write_document'

            async def progress(sent, total):
                doc.processing_progress = {"sent": sent, "total": total, "stage": "sending"}
                await db.commit()

            # Protect even distinct local sessions linked to the same MS document.
            from app.services.document_guard import processing_lock
            async with processing_lock(f"ms:{user_id}:{kind}:{doc.moysklad_id}") as acquired:
                if not acquired:
                    raise ValueError("Этот документ МойСклад уже отправляется из другой сессии. Дождитесь завершения.")
                result = await ms.update_document(
                    kind, doc.moysklad_id, scans_data,
                    position_quantities=product_qty, position_prices=product_price,
                    description=ms_description, on_progress=progress,
                )
            if result.get("__moysklad_412__"):
                reason = _extract_moysklad_error(result.get("body") or "")
                if reason:
                    from app.services.scan_verification import record_ms_errors
                    affected = record_ms_errors(valid_scans, reason)
                    if affected:
                        await db.commit()
                        from app.services.scan_events import publish_event
                        await publish_event(user_id, {'type': 'scans_changed', 'document_id': str(doc.id)})
                raise ValueError("МойСклад отклонил документ: " + (reason or "проверьте марки и повторите отправку"))

        for scan in valid_scans:
            if (getattr(scan, 'verification', None) or {}).get('ms_error'):
                scan.verification = {k: v for k, v in scan.verification.items() if not k.startswith('ms_error')}
                scan.error_message = None

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
@celery_app.task(name='deliver_incidents')
def deliver_incidents_task():
    from app.services.incidents import deliver_pending
    return _run(deliver_pending())


_WRITEOFF_OK = {"CHECKED_OK"}
_WRITEOFF_PENDING = {None, "", "IN_PROGRESS", "PENDING", "CHECKED", "NEW", "PROCESSING"}


@celery_app.task(name="poll_writeoff_status")
def poll_writeoff_status_task(document_id: str, user_id: str):
    """Опросить статус поданных в ЧЗ документов вывода из оборота и финализировать."""
    _run(_poll_writeoff_async(document_id, user_id))


async def _poll_writeoff_async(document_id: str, user_id: str):
    from app.services.document_guard import processing_lock
    async with processing_lock(f"writeoff:{document_id}") as acquired:
        if acquired:
            await _poll_writeoff_unlocked(document_id, user_id)


async def _poll_writeoff_unlocked(document_id, user_id):
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus
    from app.services.chestnyznak import ChestnyZnakService, CZApiError
    from app.core.security import decrypt_token
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(Document).where(
            Document.id == document_id, Document.user_id == user_id,
            Document.status == DocumentStatus.processing,
        ))).scalar_one_or_none()
        if not doc or not doc.cz_doc_ids:
            return
        source = await _get_cz_source(db, user_id, document_id)
        if not source or not source.cz_token:
            await _push_cz_token_expired(user_id, document_id)
            return
        cz = ChestnyZnakService(token=decrypt_token(source.cz_token))
        items = [dict(item) for item in doc.cz_doc_ids]
        error = None
        for item in items:
            if not item.get("doc_id") or item.get("status") in _WRITEOFF_OK:
                continue
            try:
                info = await cz.get_document_info(item["pg"], item["doc_id"])
                status = info.get("status") if info else None
                item["status"] = status
                if status not in _WRITEOFF_OK and status not in _WRITEOFF_PENDING:
                    error = cz.format_document_errors(info) or f"ЧЗ отклонил документ: {status}"
            except CZApiError as exc:
                error = str(exc)
        doc.cz_doc_ids = items
        all_ok = all(i.get("doc_id") and i.get("status") in _WRITEOFF_OK for i in items)
        uncertain = any(not i.get("doc_id") for i in items)
        pending = any(i.get("doc_id") and i.get("status") in _WRITEOFF_PENDING for i in items)
        if all_ok:
            doc.status = DocumentStatus.accepted
            doc.error_message = None
            await db.commit()
            await _push_writeoff_status(str(user_id), str(document_id), "done", None)
            await monitoring_emit("writeoff.done", source="worker", document_id=str(document_id), docs=len(items))
        elif pending:
            await db.commit()
            poll_writeoff_status_task.apply_async(args=[str(document_id), str(user_id)], countdown=60)
        else:
            if uncertain:
                error = "Часть списания имеет неизвестный результат отправки. Проверьте документы в ЧЗ; повторная отправка заблокирована."
            doc.error_message = error or doc.error_message or "ЧЗ отклонил часть списания. Проверьте ранее отправленные документы."
            rejected = all(i.get("doc_id") and i.get("status") not in _WRITEOFF_OK and i.get("status") not in _WRITEOFF_PENDING for i in items)
            if rejected:
                doc.status = DocumentStatus.draft
                doc.cz_doc_ids = []
            await db.commit()
            await _push_writeoff_status(str(user_id), str(document_id), "error", doc.error_message)
            await monitoring_emit("writeoff.error", level="error", source="worker", document_id=str(document_id), error=doc.error_message)


@celery_app.task(name="resume_writeoff_polling")
def resume_writeoff_polling_task():
    return _run(_resume_writeoff_polling_async())


async def _resume_writeoff_polling_async():
    from app.db.session import AsyncSessionLocal
    from app.db.models import Document, DocumentStatus, DocumentKind
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        docs = (await db.execute(select(Document.id, Document.user_id).where(
            Document.kind == DocumentKind.loss, Document.status == DocumentStatus.processing,
            Document.cz_doc_ids.is_not(None),
        ))).all()
    for doc_id, user_id in docs:
        poll_writeoff_status_task.delay(str(doc_id), str(user_id))
    return len(docs)


@celery_app.task(name="edo_sync")
def edo_sync_task(user_id: str, date_from: str, date_to: str = None, use_cursor: bool = True,
                  backfill_names: bool = False):
    """Синхронизация ЭДО Saby (лента изменений) в EdoDocument/EdoMark для контроля марок."""
    _run(_edo_sync_async(user_id, date_from, date_to, use_cursor, backfill_names))


async def _edo_sync_async(user_id: str, date_from: str, date_to, use_cursor: bool,
                          backfill_names: bool = False):
    import redis.asyncio as aioredis
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration
    from app.services.edo_sync import sync_user
    from sqlalchemy import select

    import json as _json

    # Идемпотентность: один активный синк на пользователя.
    lock_key = f"edo_sync:lock:{user_id}"
    progress_key = f"edo_sync:progress:{user_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    got = await r.set(lock_key, "1", nx=True, ex=1800)
    if not got:
        await r.aclose()
        logger.info("edo_sync.already_running", user_id=user_id)
        return

    async def _progress_cb(p: dict) -> None:
        await r.set(progress_key, _json.dumps(p), ex=1800)

    try:
        await r.delete(progress_key)  # свежий прогон — старый прогресс убираем
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
            if not integ:
                return
            res = await sync_user(db, integ, date_from=date_from, date_to=date_to,
                                  use_cursor=use_cursor, backfill_names=backfill_names,
                                  progress_cb=_progress_cb)
            await db.commit()
        # результат в Redis для опроса фронтом
        r2 = aioredis.from_url(settings.REDIS_URL)
        try:
            await r2.set(f"edo_sync:result:{user_id}", _json.dumps(res), ex=3600)
        finally:
            await r2.aclose()
    except Exception as exc:
        logger.error("edo_sync.error", user_id=user_id, error=str(exc))
    finally:
        try:
            await r.delete(lock_key); await r.delete(progress_key); await r.aclose()
        except Exception:
            pass


@celery_app.task(name="cz_snapshot_refresh")
def cz_snapshot_refresh_task(user_id: str):
    """Обновить снимок остатка ЧЗ (cz_owner_marks) через выгрузку dispenser."""
    _run(_cz_snapshot_refresh_async(user_id))


async def _cz_snapshot_refresh_async(user_id: str):
    import json as _json
    import redis.asyncio as aioredis
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration
    from app.services.cz_snapshot import refresh_snapshot
    from sqlalchemy import select

    lock_key = f"cz_snapshot:lock:{user_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    if not await r.set(lock_key, "1", nx=True, ex=3600):
        await r.aclose()
        logger.info("cz_snapshot.already_running", user_id=user_id)
        return
    try:
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
            if not integ:
                return
            res = await refresh_snapshot(db, integ)
            await db.commit()
        r2 = aioredis.from_url(settings.REDIS_URL)
        try:
            await r2.set(f"cz_snapshot:result:{user_id}", _json.dumps(res), ex=3600)
        finally:
            await r2.aclose()
    except Exception as exc:
        logger.error("cz_snapshot.error", user_id=user_id, error=str(exc))
    finally:
        try:
            await r.delete(lock_key); await r.aclose()
        except Exception:
            pass


@celery_app.task(name="ms_stock_refresh")
def ms_stock_refresh_task(user_id: str):
    """Обновить снимок учётного остатка МС (ms_stock_snapshot) по «нашим складам»."""
    _run(_ms_stock_refresh_async(user_id))


async def _ms_stock_refresh_async(user_id: str):
    import json as _json
    import redis.asyncio as aioredis
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration
    from app.services.ms_stock import refresh_ms_stock
    from sqlalchemy import select

    lock_key = f"ms_stock:lock:{user_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    if not await r.set(lock_key, "1", nx=True, ex=3600):
        await r.aclose()
        logger.info("ms_stock.already_running", user_id=user_id)
        return
    try:
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
            if not integ:
                return
            res = await refresh_ms_stock(db, integ)
            await db.commit()
        r2 = aioredis.from_url(settings.REDIS_URL)
        try:
            await r2.set(f"ms_stock:result:{user_id}", _json.dumps(res), ex=3600)
        finally:
            await r2.aclose()
    except Exception as exc:
        logger.error("ms_stock.error", user_id=user_id, error=str(exc))
    finally:
        try:
            await r.delete(lock_key); await r.aclose()
        except Exception:
            pass


@celery_app.task(name="nk_enrich_names")
def nk_enrich_names_task(user_id: str, brand: str | None = None):
    """Опознать «не опознанные» позиции инвентаризации через Национальный каталог.

    Тянет карточки НК по GTIN (cache-first, батчами) и пишет найденные имена в
    gtin_name_map (source=nk). Прогресс — в Redis, фронт опрашивает статус."""
    _run(_nk_enrich_names_async(user_id, brand))


async def _nk_enrich_names_async(user_id: str, brand: str | None):
    import json as _json
    import redis.asyncio as aioredis
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.db.models import GtinNameMap
    from app.services.nk_store import resolve_cards, display_name
    from app.services.national_catalog import cooldown_remaining

    lock_key = f"nk_enrich:lock:{user_id}"
    prog_key = f"nk_enrich:progress:{user_id}"
    r = aioredis.from_url(settings.REDIS_URL)
    if not await r.set(lock_key, "1", nx=True, ex=3600):
        await r.aclose()
        logger.info("nk_enrich.already_running", user_id=user_id)
        return

    async def _progress(**kw):
        try:
            await r.set(prog_key, _json.dumps(kw), ex=3600)
        except Exception:
            pass

    enriched = 0
    processed = 0
    try:
        from app.api.inventory import _compute_reconcile

        await _progress(phase="collecting", total=0, processed=0, enriched=0, done=False)
        async with AsyncSessionLocal() as db:
            data = await _compute_reconcile(db, user_id, brand or None, "all", "unmatched")
            gtins = [row["gtin"] for row in data["rows"] if row.get("gtin")]
            total = len(gtins)
            await _progress(phase="enriching", total=total, processed=0, enriched=0, done=False)
            if not total:
                await _progress(phase="enriching", total=0, processed=0, enriched=0, done=True)
                return

            # Батчами по одному GTIN (НК ~100/5мин, троттлинг внутри сервиса). Малый
            # батч → прогресс двигается чаще. Оценка ETA — по интервалу троттлинга.
            B = 10
            interval = max(0.0, settings.NK_MIN_INTERVAL_MS / 1000.0)
            for i in range(0, total, B):
                # Если активен кулдаун 429 — честно показываем «ждём снятия лимита».
                cd = cooldown_remaining()
                eta = int((total - processed) * interval + cd)
                if cd > 3:
                    await _progress(
                        phase="waiting", total=total, processed=processed,
                        enriched=enriched, done=False, wait_s=int(cd), eta_s=eta,
                    )
                chunk = gtins[i : i + B]
                cards = await resolve_cards(db, chunk)
                for gtin, card in cards.items():
                    name = display_name(card)
                    if not name:
                        continue
                    stmt = pg_insert(GtinNameMap).values(
                        user_id=user_id, gtin=gtin, product_name=name[:500], source="nk"
                    )
                    stmt = stmt.on_conflict_do_nothing(constraint="ix_gtin_name_map_user_gtin")
                    res = await db.execute(stmt)
                    enriched += res.rowcount or 0
                await db.commit()
                processed = min(i + B, total)
                await _progress(
                    phase="enriching", total=total, processed=processed,
                    enriched=enriched, done=False, eta_s=int((total - processed) * interval),
                )
        await _progress(
            phase="done", total=total, processed=processed, enriched=enriched, done=True,
        )
        logger.info(
            "nk_enrich.done", user_id=user_id, total=total, enriched=enriched,
        )
    except Exception as exc:
        logger.error("nk_enrich.error", user_id=user_id, error=str(exc))
        await _progress(
            phase="error", processed=processed, enriched=enriched, done=True,
            error="Ошибка опознания через Национальный каталог",
        )
    finally:
        try:
            await r.delete(lock_key); await r.aclose()
        except Exception:
            pass


@celery_app.task(name="edo_auto_sync_all")
def edo_auto_sync_all_task():
    """Beat: инкрементальный добор ЭДО для всех клиентов с подключённым Saby (по курсору)."""
    _run(_edo_auto_sync_all_async())


async def _edo_auto_sync_all_async():
    from sqlalchemy import select, or_
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration

    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(Integration.user_id).where(
                or_(
                    Integration.saby_app_client_id.isnot(None),
                    Integration.saby_login.isnot(None),
                )
            )
        )
        user_ids = [str(u) for (u,) in q.all()]
    from datetime import datetime, timedelta, timezone
    # Дефолтная нижняя граница на случай пустого курсора — последние 40 дней.
    since = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%d.%m.%Y 00.00.00")
    for uid in user_ids:
        # use_cursor=True → продолжаем с сохранённого курсора (инкремент); дата — фолбэк.
        edo_sync_task.delay(uid, since, None, True)
    logger.info("edo_auto_sync_all.dispatched", users=len(user_ids))


@celery_app.task(name="cz_snapshot_refresh_all")
def cz_snapshot_refresh_all_task():
    """Beat: ежедневное обновление снимка остатка ЧЗ для всех клиентов с токеном ЧЗ."""
    _run(_cz_snapshot_refresh_all_async())


async def _cz_snapshot_refresh_all_async():
    from sqlalchemy import and_, or_, select
    from app.db.session import AsyncSessionLocal
    from app.db.models import Integration

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        # Только валидные токены: истёкший токен дал бы вырожденную выгрузку, а
        # refresh_snapshot полностью заменяет снимок — можно затереть остаток «в ноль».
        # Воркер токен ЧЗ сам не обновляет (нужна свежая подпись из браузера) — просто пропускаем.
        q = await db.execute(
            select(Integration.user_id).where(
                and_(
                    Integration.cz_token.isnot(None),
                    or_(
                        Integration.cz_token_expires_at.is_(None),
                        Integration.cz_token_expires_at > now,
                    ),
                )
            )
        )
        user_ids = [str(u) for (u,) in q.all()]
    for uid in user_ids:
        cz_snapshot_refresh_task.delay(uid)
    logger.info("cz_snapshot_refresh_all.dispatched", users=len(user_ids))
