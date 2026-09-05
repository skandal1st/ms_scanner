"""Снимок учётного остатка МойСклада по маркированным товарам — для сверки с ЧЗ.

Зеркалит app/services/cz_snapshot.py: берёт маркированную вселенную (distinct GTIN из
cz_owner_marks), резолвит каждый GTIN в товар МС (база знаний GtinProductMap → каталог МС),
берёт остаток по «нашим складам» (report/stock/all) и полностью заменяет снимок в БД.
Долгая операция (резолв сотен GTIN + отчёт остатков) — запускать из Celery.
"""
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import CzOwnerMark, Integration, MsStockSnapshot
from app.services.chestnyznak import normalize_gtin_key
from app.services.gtin_product_store import get_gtin_product, remember_gtin_product
from app.services.moysklad import MoySkladService


def _gtin_from_row(gtin, cis_canonical) -> str | None:
    """GTIN-14 из колонки gtin снимка ЧЗ либо из cis (14 цифр после AI 01)."""
    if gtin:
        return normalize_gtin_key(gtin)
    c = cis_canonical or ""
    if c.startswith("01") and len(c) >= 16 and c[2:16].isdigit():
        return normalize_gtin_key(c[2:16])
    return None


async def refresh_ms_stock(db, integ: Integration) -> dict:
    """Полностью обновить ms_stock_snapshot пользователя по «нашим складам»."""
    if not integ.moysklad_token:
        return {"error": "Не подключён МойСклад"}
    try:
        token = decrypt_token(integ.moysklad_token)
    except Exception:
        return {"error": "Токен МойСклад повреждён"}

    # 1. Маркированная вселенная — distinct GTIN из снимка ЧЗ.
    rows = (
        await db.execute(
            select(CzOwnerMark.gtin, CzOwnerMark.cis_canonical).where(
                CzOwnerMark.user_id == integ.user_id
            )
        )
    ).all()
    gtins: set[str] = set()
    for g, c in rows:
        key = _gtin_from_row(g, c)
        if key:
            gtins.add(key)
    if not gtins:
        return {"error": "Снимок ЧЗ пуст — сначала обновите остаток ЧЗ"}
    logger.info("ms_stock.start", user_id=str(integ.user_id), gtins=len(gtins))

    ms = MoySkladService(token)
    store_hrefs = [
        f"{ms.base_url}/entity/store/{sid}" for sid in (integ.inventory_store_ids or [])
    ]

    # 2. Учётный остаток по «нашим складам» (может упасть 403 без права на отчёт).
    try:
        stock_map = await ms.get_stock_map(store_hrefs)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 403:
            return {"error": "Нет права на отчёт остатков МойСклад — обновите дескриптор приложения."}
        return {"error": f"МойСклад вернул {code} при запросе остатков"}
    except httpx.HTTPError as exc:
        return {"error": f"МойСклад недоступен: {exc}"}

    # 3. Резолв GTIN → товар МС + остаток + группа (бренд).
    out: dict[str, dict] = {}
    resolved = 0
    for gtin in gtins:
        pid = pname = None
        row = None
        cached = await get_gtin_product(db, integ.user_id, gtin)
        if cached:
            pid, pname = cached
        else:
            try:
                row = await ms.find_product_by_gtin(gtin)
            except httpx.HTTPError:
                row = None
            if row:
                pid = row.get("id")
                pname = row.get("name")
                await remember_gtin_product(db, integ.user_id, gtin, pid, pname)
        if not pid:
            # GTIN есть в ЧЗ, но товара в МС нет — в сверке всплывёт со стороны ЧЗ (МС=0).
            continue
        info = stock_map.get(pid) or {}
        folder_id = info.get("folder_id")
        folder_name = info.get("folder_name")
        if not folder_name and row is not None:
            folder_name = row.get("pathName")
            folder_id = ms._id_from_href(
                ((row.get("productFolder") or {}).get("meta") or {}).get("href", "")
            ) or None
        out[gtin] = {
            "user_id": integ.user_id,
            "product_id": pid,
            "gtin": gtin,
            "product_name": pname,
            "folder_id": folder_id,
            "folder_name": folder_name,
            "qty": Decimal(str(info.get("qty") or 0)),
        }
        resolved += 1
    await db.commit()  # зафиксировать upsert'ы базы знаний GtinProductMap

    # 4. Полная замена снимка МС.
    await db.execute(delete(MsStockSnapshot).where(MsStockSnapshot.user_id == integ.user_id))
    await db.commit()
    data = list(out.values())
    B = 5000
    for i in range(0, len(data), B):
        await db.execute(
            pg_insert(MsStockSnapshot).on_conflict_do_nothing(
                constraint="ix_ms_stock_snapshot_user_gtin"
            ),
            data[i:i + B],
        )
        await db.commit()
    logger.info("ms_stock.done", user_id=str(integ.user_id), resolved=resolved, gtins=len(gtins))
    return {
        "products": resolved,
        "gtins": len(gtins),
        "stores": len(integ.inventory_store_ids or []),
        "at": datetime.now(timezone.utc).isoformat(),
    }
