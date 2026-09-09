"""Обновление снимка остатка ЧЗ (cz_owner_marks) через асинхронную выгрузку dispenser.

Переиспользует CzDispenser (FILTERED_CIS_REPORT) по контрактным товарным группам клиента,
разбирает CSV в канонические CIS и полностью заменяет снимок пользователя в БД.
Долго (генерация у ЧЗ — минуты на группу) — запускать из Celery.
"""
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import CzOwnerMark, Integration
from app.services.chestnyznak import strip_ai_brackets, normalize_gtin_key
from app.services.cz_dispenser import (
    CZ_PG_STRING_TO_CODE,
    CzDispenser,
    CzDispenserError,
    CzDispenserPending,
)

# Свежую готовую выгрузку ждём недолго: если ЧЗ ещё генерит — отдаём «зайдите позже»,
# задача продолжит формироваться и будет переиспользована следующим прогоном.
_WAIT_FRESH_S = 180

_GS = "\x1d"


def _canon(code: str) -> str:
    s = strip_ai_brackets(code or "").strip()
    s = s.split(_GS, 1)[0]
    return re.sub(r"(?i)%c1", "", s).strip()


async def _contracted_groups(token: str, hint: list[str]) -> list[str]:
    """Группы клиента: из настроек (hint) или автоопределение по cises/search (403=нет договора)."""
    if hint:
        return hint
    found: list[str] = []
    url = f"{settings.CZ_API_BASE_URL}/api/v4/true-api/cises/search"
    H = {"Authorization": f"Bearer {token}", "accept": "application/json", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        for pg in CZ_PG_STRING_TO_CODE:
            try:
                r = await c.post(url, headers=H, json={"filter": {"productGroups": [pg]}, "pagination": {"perPage": 1}})
            except Exception:
                continue
            if r.status_code == 200:
                found.append(pg)
    return found


async def refresh_snapshot(db, integ: Integration) -> dict:
    """Полностью обновить cz_owner_marks пользователя из свежей выгрузки ЧЗ."""
    if not integ.cz_token:
        return {"error": "Нет токена ЧЗ"}
    # Истёкший токен даёт вырожденную выгрузку — не трогаем снимок, иначе полная
    # замена ниже затрёт остаток «в ноль». Обновление токена — только из браузера (УКЭП).
    if integ.cz_token_expires_at is not None and integ.cz_token_expires_at <= datetime.now(timezone.utc):
        return {"error": "Токен ЧЗ истёк — войдите в ЧЗ заново, снимок не изменён"}
    try:
        token = decrypt_token(integ.cz_token)
    except Exception:
        return {"error": "Токен ЧЗ повреждён"}
    inn = (integ.cz_inn or "").strip()
    if not inn:
        return {"error": "Не указан ИНН участника (cz_inn)"}

    disp = CzDispenser(token)
    groups = await _contracted_groups(token, list(integ.cz_product_groups or []))
    if not groups:
        return {"error": "Нет контрактных товарных групп"}
    logger.info("cz_snapshot.start", user_id=str(integ.user_id), groups=groups)

    # Собираем все КИ по группам. Сначала пытаемся переиспользовать свежую готовую
    # выгрузку, затем — уже идущую задачу, и только потом создаём новую.
    rows: dict[str, dict] = {}
    pending_groups: list[str] = []
    for pg in groups:
        try:
            result_id = await disp.find_reusable_result(pg, inn)
            if result_id:
                items = await disp.download_result(pg, result_id)
            else:
                tid = await disp.find_inflight_task(pg, inn) or await disp.create_filtered_cis_task(pg, inn)
                if not tid:
                    continue
                items = await disp.wait_and_download(pg, tid, timeout_s=_WAIT_FRESH_S)
        except CzDispenserPending as exc:
            logger.info("cz_snapshot.group_pending", pg=pg, error=str(exc))
            pending_groups.append(pg)
            continue
        except CzDispenserError as exc:
            logger.warning("cz_snapshot.group_failed", pg=pg, error=str(exc))
            continue
        for it in items:
            key = _canon(it.cis)
            if not key:
                continue
            rows[key] = {
                "user_id": integ.user_id,
                "cis_canonical": key,
                "gtin": normalize_gtin_key(it.gtin) if it.gtin else None,
                "product_name": (it.product_name or "")[:500] or None,
                "status": (it.status or "")[:32] or None,
                "package_type": (it.package_type or "")[:16] or None,
                "product_group": (it.product_group or pg)[:32],
            }
        logger.info("cz_snapshot.group_done", pg=pg, total=len(rows))

    if not rows:
        if pending_groups:
            # Не ошибка токена/договора: ЧЗ ещё генерит выгрузку. Не даём кладовщику
            # жать «обновить» без толку — задача досоздана и переиспользуется позже.
            return {
                "pending": True,
                "error": (
                    "ЧЗ ещё формирует выгрузку остатка (это занимает несколько минут). "
                    "Зайдите позже и обновите — данные подтянутся автоматически."
                ),
                "groups": pending_groups,
            }
        return {"error": "ЧЗ не вернул остаток (проверьте токен/договоры)"}

    # Полная замена снимка пользователя.
    await db.execute(delete(CzOwnerMark).where(CzOwnerMark.user_id == integ.user_id))
    await db.commit()
    data = list(rows.values())
    B = 5000
    for i in range(0, len(data), B):
        await db.execute(
            pg_insert(CzOwnerMark).on_conflict_do_nothing(constraint="ix_cz_owner_marks_user_cis"),
            data[i:i + B],
        )
        await db.commit()
    logger.info(
        "cz_snapshot.done",
        user_id=str(integ.user_id),
        total=len(data),
        pending=pending_groups or None,
    )
    result = {"marks": len(data), "groups": groups, "at": datetime.now(timezone.utc).isoformat()}
    if pending_groups:
        # Часть групп ещё генерится в ЧЗ — снимок неполный, дадим знать.
        result["pending"] = True
        result["pending_groups"] = pending_groups
        result["note"] = (
            "Часть товарных групп ЧЗ ещё формирует выгрузку — обновите позже "
            "для полного остатка."
        )
    return result
