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

# Агрегат остатка ЧЗ по GTIN (только штучные марки; GTIN пустой — из cis, 14 цифр после AI 01)
# FULL OUTER JOIN снимка МС по GTIN.
_RECONCILE_SQL = text("""
WITH cz AS (
    SELECT gtin_key AS gtin, count(*) AS qty_cz
    FROM (
        SELECT CASE
                 WHEN gtin IS NOT NULL AND gtin <> '' THEN gtin
                 WHEN cis_canonical LIKE '01%' AND length(cis_canonical) >= 16
                   THEN substring(cis_canonical FROM 3 FOR 14)
                 ELSE NULL END AS gtin_key
        FROM cz_owner_marks
        WHERE user_id = CAST(:uid AS uuid) AND (package_type IS NULL OR package_type = 'UNIT')
    ) x
    WHERE gtin_key IS NOT NULL
    GROUP BY gtin_key
),
ms AS (
    SELECT gtin, qty AS qty_ms, product_name, folder_id, folder_name
    FROM ms_stock_snapshot WHERE user_id = CAST(:uid AS uuid)
)
SELECT coalesce(cz.gtin, ms.gtin) AS gtin,
       coalesce(cz.qty_cz, 0)     AS qty_cz,
       coalesce(ms.qty_ms, 0)     AS qty_ms,
       ms.product_name, ms.folder_id, ms.folder_name
FROM cz FULL OUTER JOIN ms ON cz.gtin = ms.gtin
""")


async def _compute_reconcile(db: AsyncSession, user_id, brand: Optional[str], diff: str) -> dict:
    """Сводка расхождений ЧЗ↔МС. Единый источник для JSON и XLSX.

    diff: cz_gt_ms (фантомы ЧЗ>МС) | ms_gt_cz | mismatch | all."""
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
        qty_ms = int(r.qty_ms or 0)
        all_rows.append({
            "gtin": r.gtin,
            "product_name": r.product_name,
            "folder_id": r.folder_id,
            "folder_name": r.folder_name or "Без группы",
            "qty_cz": qty_cz,
            "qty_ms": qty_ms,
            "diff": qty_cz - qty_ms,
        })

    # Бренды (для фильтра) — из полного набора, до среза.
    brands: dict[str, dict] = {}
    for row in all_rows:
        key = row["folder_id"] or row["folder_name"]
        b = brands.get(key)
        if not b:
            b = {"folder_id": row["folder_id"], "folder_name": row["folder_name"],
                 "positions": 0, "qty_cz": 0, "qty_ms": 0, "diff": 0}
            brands[key] = b
        b["positions"] += 1
        b["qty_cz"] += row["qty_cz"]
        b["qty_ms"] += row["qty_ms"]
        b["diff"] += row["diff"]
    brand_list = sorted(brands.values(), key=lambda x: (x["folder_name"] or "").lower())

    rows = all_rows
    if brand:
        rows = [x for x in rows if (x["folder_id"] == brand or x["folder_name"] == brand)]
    if diff == "cz_gt_ms":
        rows = [x for x in rows if x["diff"] > 0]
    elif diff == "ms_gt_cz":
        rows = [x for x in rows if x["diff"] < 0]
    elif diff == "mismatch":
        rows = [x for x in rows if x["diff"] != 0]
    rows = sorted(rows, key=lambda x: abs(x["diff"]), reverse=True)

    return {
        "has_ms_snapshot": ms_size > 0,
        "ms_size": int(ms_size),
        "cz_size": int(cz_size),
        "brands": brand_list,
        "rows": rows,
        "totals": {
            "positions": len(rows),
            "qty_cz": sum(x["qty_cz"] for x in rows),
            "qty_ms": sum(x["qty_ms"] for x in rows),
            "diff": sum(x["diff"] for x in rows),
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
    ws1.append(["Бренд (группа)", "Позиций", "ЧЗ", "МС", "Δ (ЧЗ−МС)"])
    for b in data["brands"]:
        ws1.append([b["folder_name"], b["positions"], b["qty_cz"], b["qty_ms"], b["diff"]])
    ws1.column_dimensions["A"].width = 46
    for col in ("B", "C", "D", "E"):
        ws1.column_dimensions[col].width = 14
    ws1.freeze_panes = "A2"

    ws2 = wb.create_sheet("Позиции")
    ws2.append(["Бренд", "Товар", "GTIN", "ЧЗ", "МС", "Δ (ЧЗ−МС)"])
    for x in data["rows"]:
        ws2.append([x["folder_name"], x["product_name"] or "—", x["gtin"] or "",
                    x["qty_cz"], x["qty_ms"], x["diff"]])
    ws2.column_dimensions["A"].width = 32
    ws2.column_dimensions["B"].width = 46
    ws2.column_dimensions["C"].width = 20
    for col in ("D", "E", "F"):
        ws2.column_dimensions[col].width = 12
    ws2.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    fname = f"Сверка ЧЗ-МС {datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=reconcile.xlsx; filename*=UTF-8''{quote(fname)}"},
    )
