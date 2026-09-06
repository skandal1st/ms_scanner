"""Раздел «Инвентаризация»: сверка учётного остатка МойСклада с остатком марок в ЧЗ.

Два снимка в БД — ЧЗ (cz_owner_marks, наполняется dispenser'ом) и МС (ms_stock_snapshot,
наполняется по «нашим складам»). Сверка = FULL OUTER JOIN по нормализованному GTIN. Срез по
бренду = группе товаров МС (folder). Расхождение ЧЗ>МС = кандидаты на вывод из оборота.

Мультиюрлицо: в ЧЗ один cz_inn, в МС остаток по складам → карта «наши склады»
(Integration.inventory_store_ids) задаёт периметр учётного остатка.
"""
import json
from typing import Optional

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import CzOwnerMark, Integration, MsStockSnapshot, User
from app.db.session import get_db
from app.services.moysklad import MoySkladService

router = APIRouter(prefix="/inventory", tags=["inventory"])


async def _get_integration(db: AsyncSession, user_id) -> Optional[Integration]:
    return (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()


# ── Карта «наши склады» ───────────────────────────────────────────────────────

class StoresResponse(BaseModel):
    stores: list[dict]
    selected: list[str]
    # У приложения может не быть права на список складов (/entity/store 403). Тогда выбор
    # «наших складов» недоступен, а остаток МС берётся по ВСЕМ складам (report/stock/all 200).
    available: bool = True


class StoresSaveRequest(BaseModel):
    store_ids: list[str]


@router.get("/stores", response_model=StoresResponse)
async def get_stores(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Склады МС + текущая карта «наши склады» (для сверки берётся остаток по ним)."""
    integ = await _get_integration(db, current_user.id)
    if not integ or not integ.moysklad_token:
        raise HTTPException(status_code=403, detail="Не подключён МойСклад.")
    try:
        ms = MoySkladService(decrypt_token(integ.moysklad_token))
        stores = await ms.get_stores()
    except httpx.HTTPStatusError as exc:
        # 403 на списке складов — не ошибка: выбор складов недоступен, сверка идёт по всем.
        if exc.response.status_code == 403:
            logger.info("inventory.stores_forbidden", user_id=str(current_user.id))
            return StoresResponse(stores=[], selected=list(integ.inventory_store_ids or []), available=False)
        logger.warning("inventory.stores_failed", status=exc.response.status_code)
        raise HTTPException(status_code=502, detail="Не удалось получить склады МойСклад.")
    except Exception as exc:
        logger.warning("inventory.stores_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Не удалось получить склады МойСклад.")
    return StoresResponse(stores=stores, selected=list(integ.inventory_store_ids or []))


@router.post("/stores", response_model=StoresResponse)
async def save_stores(
    body: StoresSaveRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integ = await _get_integration(db, current_user.id)
    if not integ:
        raise HTTPException(status_code=403, detail="Не подключён МойСклад.")
    integ.inventory_store_ids = [s for s in body.store_ids if s]
    await db.commit()
    logger.info("inventory.stores_saved", user_id=str(current_user.id), n=len(integ.inventory_store_ids))
    return await get_stores(current_user, db)


# ── Снимки: остаток ЧЗ и остаток МС ───────────────────────────────────────────

async def _snapshot_status(db, user_id, model, lock_key: str, result_key: str) -> dict:
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        running = await r.get(lock_key)
        res = await r.get(result_key)
    finally:
        await r.aclose()
    result = json.loads(res.decode() if isinstance(res, (bytes, bytearray)) else res) if res else None
    size = (
        await db.execute(select(func.count()).select_from(model).where(model.user_id == user_id))
    ).scalar_one()
    at = (
        await db.execute(select(func.max(model.snapshot_at)).where(model.user_id == user_id))
    ).scalar_one_or_none()
    return {"running": bool(running), "size": int(size), "at": at.isoformat() if at else None, "result": result}


@router.post("/cz-stock/refresh")
async def cz_stock_refresh(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Обновить снимок остатка ЧЗ (реюз cz_snapshot_refresh_task)."""
    integ = await _get_integration(db, current_user.id)
    if not integ or not integ.cz_token:
        raise HTTPException(status_code=403, detail="Не подключён Честный Знак (вход по УКЭП).")
    from app.worker.tasks import cz_snapshot_refresh_task

    cz_snapshot_refresh_task.delay(str(current_user.id))
    return {"status": "started"}


@router.get("/cz-stock/status")
async def cz_stock_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _snapshot_status(
        db, current_user.id, CzOwnerMark,
        f"cz_snapshot:lock:{current_user.id}", f"cz_snapshot:result:{current_user.id}",
    )


@router.post("/ms-stock/refresh")
async def ms_stock_refresh(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Обновить снимок учётного остатка МС по «нашим складам»."""
    integ = await _get_integration(db, current_user.id)
    if not integ or not integ.moysklad_token:
        raise HTTPException(status_code=403, detail="Не подключён МойСклад.")
    from app.worker.tasks import ms_stock_refresh_task

    ms_stock_refresh_task.delay(str(current_user.id))
    return {"status": "started"}


@router.get("/ms-stock/status")
async def ms_stock_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _snapshot_status(
        db, current_user.id, MsStockSnapshot,
        f"ms_stock:lock:{current_user.id}", f"ms_stock:result:{current_user.id}",
    )


# ── Сверка ЧЗ ↔ МС ────────────────────────────────────────────────────────────

# Три источника, свёрнутые по GTIN (только штучные марки; GTIN пустой — из cis, 14 цифр после AI 01):
#   cz  — числится за нами в ЧЗ (cz_owner_marks)
#   upd — из ЧЗ-остатка отгружено по УПД, но право ещё не перешло (пересечение с edo_marks по
#         незавершённым исходящим документам) → «в пути к покупателю», НЕ фантом
#   ms  — учётный остаток МойСклада
# Расхождение «сколько искать» = qty_cz − qty_upd − qty_ms (не простое ЧЗ−МС): из числящегося за
# нами в ЧЗ вычитаем и полку (МС), и то, что уже уехало по УПД (зависло на покупателе).
# upd берёт gtin из той же cz_owner_marks (той же формулой) → qty_upd ⊆ qty_cz по каждому GTIN.
_RECONCILE_SQL = text("""
WITH cz AS (
    SELECT gtin_key AS gtin, count(*) AS qty_cz, max(pname) AS product_name
    FROM (
        SELECT CASE
                 WHEN gtin IS NOT NULL AND gtin <> '' THEN gtin
                 WHEN cis_canonical LIKE '01%' AND length(cis_canonical) >= 16
                   THEN substring(cis_canonical FROM 3 FOR 14)
                 ELSE NULL END AS gtin_key,
               product_name AS pname
        FROM cz_owner_marks
        WHERE user_id = CAST(:uid AS uuid) AND (package_type IS NULL OR package_type = 'UNIT')
    ) x
    WHERE gtin_key IS NOT NULL
    GROUP BY gtin_key
),
upd AS (
    SELECT gtin_key AS gtin, count(DISTINCT oid) AS qty_upd
    FROM (
        SELECT o.id AS oid,
               CASE
                 WHEN o.gtin IS NOT NULL AND o.gtin <> '' THEN o.gtin
                 WHEN o.cis_canonical LIKE '01%' AND length(o.cis_canonical) >= 16
                   THEN substring(o.cis_canonical FROM 3 FOR 14)
                 ELSE NULL END AS gtin_key
        FROM cz_owner_marks o
        JOIN edo_marks m ON m.user_id = o.user_id AND m.cis_canonical = o.cis_canonical
        JOIN edo_documents d ON d.id = m.document_id
        WHERE o.user_id = CAST(:uid AS uuid)
          AND (o.package_type IS NULL OR o.package_type = 'UNIT')
          AND d.direction = 'Исходящий'
          AND coalesce(d.state_code, -1) NOT IN (7, 19, 22, 20, 0)
    ) x
    WHERE gtin_key IS NOT NULL
    GROUP BY gtin_key
),
ms AS (
    SELECT gtin, qty AS qty_ms, product_name, folder_id, folder_name
    FROM ms_stock_snapshot WHERE user_id = CAST(:uid AS uuid)
),
nm AS (
    SELECT gtin, product_name FROM gtin_name_map WHERE user_id = CAST(:uid AS uuid)
)
SELECT coalesce(cz.gtin, ms.gtin)              AS gtin,
       coalesce(cz.qty_cz, 0)                  AS qty_cz,
       coalesce(upd.qty_upd, 0)                AS qty_upd,
       coalesce(ms.qty_ms, 0)                  AS qty_ms,
       coalesce(ms.product_name, cz.product_name, nm.product_name) AS product_name,
       ms.folder_id, ms.folder_name,
       (ms.gtin IS NULL)                       AS not_in_ms
FROM cz
FULL OUTER JOIN ms ON cz.gtin = ms.gtin
LEFT JOIN upd ON upd.gtin = cz.gtin
LEFT JOIN nm ON nm.gtin = coalesce(cz.gtin, ms.gtin)
""")


async def _compute_reconcile(db: AsyncSession, user_id, brand: Optional[str], diff: str) -> dict:
    """Сводка расхождений ЧЗ↔МС с учётом отгруженного по УПД. Единый источник для JSON и XLSX.

    to_search = qty_cz − qty_upd − qty_ms (сколько марок реально искать).
    diff: to_search (фантомы после УПД) | cz_gt_ms (сырое ЧЗ>МС) | ms_gt_cz | mismatch | all."""
    ms_size = (
        await db.execute(select(func.count()).select_from(MsStockSnapshot).where(MsStockSnapshot.user_id == user_id))
    ).scalar_one()
    cz_size = (
        await db.execute(select(func.count()).select_from(CzOwnerMark).where(CzOwnerMark.user_id == user_id))
    ).scalar_one()

    res = await db.execute(_RECONCILE_SQL, {"uid": str(user_id)})
    all_rows = []
    for r in res:
        qty_cz = int(r.qty_cz or 0)
        qty_upd = int(r.qty_upd or 0)
        qty_ms = int(r.qty_ms or 0)
        not_in_ms = bool(r.not_in_ms)
        all_rows.append({
            "gtin": r.gtin,
            "product_name": r.product_name,
            "folder_id": r.folder_id,
            "folder_name": r.folder_name or ("Нет в МС" if not_in_ms else "Без группы"),
            "qty_cz": qty_cz,
            "qty_upd": qty_upd,
            "qty_ms": qty_ms,
            "diff": qty_cz - qty_ms,
            "to_search": qty_cz - qty_upd - qty_ms,
            "not_in_ms": not_in_ms,
        })

    # Бренды (для фильтра) — из полного набора, до среза.
    brands: dict[str, dict] = {}
    for row in all_rows:
        key = row["folder_id"] or row["folder_name"]
        b = brands.get(key)
        if not b:
            b = {"folder_id": row["folder_id"], "folder_name": row["folder_name"],
                 "positions": 0, "qty_cz": 0, "qty_upd": 0, "qty_ms": 0, "diff": 0, "to_search": 0}
            brands[key] = b
        b["positions"] += 1
        b["qty_cz"] += row["qty_cz"]
        b["qty_upd"] += row["qty_upd"]
        b["qty_ms"] += row["qty_ms"]
        b["diff"] += row["diff"]
        b["to_search"] += row["to_search"]
    brand_list = sorted(brands.values(), key=lambda x: (x["folder_name"] or "").lower())

    rows = all_rows
    if brand:
        rows = [x for x in rows if (x["folder_id"] == brand or x["folder_name"] == brand)]
    # «Сколько искать» — сумма положительных to_search по срезу бренда, независимо от таба diff.
    search_total = sum(x["to_search"] for x in rows if x["to_search"] > 0)
    if diff == "to_search":
        rows = [x for x in rows if x["to_search"] > 0]
    elif diff == "cz_gt_ms":
        rows = [x for x in rows if x["diff"] > 0]
    elif diff == "ms_gt_cz":
        rows = [x for x in rows if x["diff"] < 0]
    elif diff == "mismatch":
        rows = [x for x in rows if x["diff"] != 0]
    sort_key = (lambda x: x["to_search"]) if diff == "to_search" else (lambda x: abs(x["diff"]))
    rows = sorted(rows, key=sort_key, reverse=True)

    return {
        "has_ms_snapshot": ms_size > 0,
        "ms_size": int(ms_size),
        "cz_size": int(cz_size),
        "search_total": int(search_total),
        "brands": brand_list,
        "rows": rows,
        "totals": {
            "positions": len(rows),
            "qty_cz": sum(x["qty_cz"] for x in rows),
            "qty_upd": sum(x["qty_upd"] for x in rows),
            "qty_ms": sum(x["qty_ms"] for x in rows),
            "diff": sum(x["diff"] for x in rows),
            "to_search": sum(x["to_search"] for x in rows),
        },
    }


@router.get("/reconcile")
async def reconcile(
    brand: Optional[str] = None,
    diff: str = "all",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Сверка остатков ЧЗ↔МС по GTIN, срез по бренду (группе товаров МС)."""
    return await _compute_reconcile(db, current_user.id, brand, diff)


@router.get("/reconcile.xlsx")
async def reconcile_xlsx(
    brand: Optional[str] = None,
    diff: str = "all",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Выгрузка сверки в XLSX: лист по брендам + лист по позициям."""
    import io
    from datetime import datetime
    from urllib.parse import quote
    from fastapi.responses import Response
    from openpyxl import Workbook

    data = await _compute_reconcile(db, current_user.id, brand, diff)
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "По брендам"
    ws1.append(["Бренд (группа)", "Позиций", "ЧЗ", "УПД (в пути)", "МС", "Δ (ЧЗ−МС)", "Искать (ЧЗ−УПД−МС)"])
    for b in data["brands"]:
        ws1.append([b["folder_name"], b["positions"], b["qty_cz"], b["qty_upd"], b["qty_ms"], b["diff"], b["to_search"]])
    ws1.column_dimensions["A"].width = 46
    for col in ("B", "C", "D", "E", "F", "G"):
        ws1.column_dimensions[col].width = 15
    ws1.freeze_panes = "A2"

    ws2 = wb.create_sheet("Позиции")
    ws2.append(["Бренд", "Товар", "GTIN", "ЧЗ", "УПД (в пути)", "МС", "Δ (ЧЗ−МС)", "Искать (ЧЗ−УПД−МС)"])
    for x in data["rows"]:
        ws2.append([x["folder_name"], x["product_name"] or "—", x["gtin"] or "",
                    x["qty_cz"], x["qty_upd"], x["qty_ms"], x["diff"], x["to_search"]])
    ws2.column_dimensions["A"].width = 32
    ws2.column_dimensions["B"].width = 46
    ws2.column_dimensions["C"].width = 20
    for col in ("D", "E", "F", "G", "H"):
        ws2.column_dimensions[col].width = 15
    ws2.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    fname = f"Сверка ЧЗ-МС {datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=reconcile.xlsx; filename*=UTF-8''{quote(fname)}"},
    )
