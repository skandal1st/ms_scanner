"""СКРИПТ 1: выгрузка всех марок участника из ЧЗ (остатки ГИС МТ) → снимок CSV.

Асинхронная выгрузка dispenser (FILTERED_CIS_REPORT) по каждой контрактной товарной
группе пользователя. Результат — файл-снимок /app/_inventory/cz_<inn>_<YYYYMMDD>.csv
с колонками cis,gtin,status,package_type,product_group,owner_inn,product_name.

Запуск в контейнере:
  docker compose -f docker-compose.prod.yml exec -T backend python /app/inventory_export.py
  (опц.) переменные окружения: INV_USER_ID=<uuid>  INV_GROUPS=otp,ncp

Генерация у ЧЗ занимает минуты — скрипт терпеливо опрашивает статус.
"""
import asyncio
import csv
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.db.models import Integration
from app.core.security import decrypt_token
from app.core.config import settings
from app.services.cz_dispenser import CzDispenser, CzDispenserError, CZ_PG_STRING_TO_CODE

OUT_DIR = "/app/_inventory"


async def _contracted_groups(dispenser: CzDispenser, token: str, hint: list[str]) -> list[str]:
    """Определить группы с договором. Если hint задан — берём его; иначе пробуем каждую
    группу лёгким cises/search (200 = есть договор, 403 = нет)."""
    if hint:
        return hint
    import httpx

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


async def export_user(db, integ: Integration, groups_override: list[str]) -> dict:
    token = decrypt_token(integ.cz_token)
    inn = (integ.cz_inn or "").strip()
    if not inn:
        return {"skipped": "нет ИНН участника (cz_inn)"}
    disp = CzDispenser(token)

    hint = groups_override or list(integ.cz_product_groups or [])
    groups = await _contracted_groups(disp, token, hint)
    print(f"  ИНН {inn}: группы для выгрузки: {groups}", flush=True)
    if not groups:
        return {"skipped": "нет контрактных групп"}

    # 1) создать задачи
    tasks: list[tuple[str, str]] = []
    for pg in groups:
        try:
            tid = await disp.create_filtered_cis_task(pg, inn)
        except CzDispenserError as e:
            print(f"    {pg}: ошибка создания задачи: {e}", flush=True)
            continue
        if tid:
            tasks.append((pg, tid))
            print(f"    {pg}: задача {tid}", flush=True)
        else:
            print(f"    {pg}: нет договора/не поддерживается — пропуск", flush=True)

    # 2) дождаться и скачать
    rows: dict[str, dict] = {}
    for pg, tid in tasks:
        try:
            items = await disp.wait_and_download(pg, tid)
        except CzDispenserError as e:
            print(f"    {pg}: {e}", flush=True)
            continue
        for it in items:
            rows[it.cis] = {
                "cis": it.cis,
                "gtin": it.gtin or "",
                "status": it.status or "",
                "package_type": it.package_type or "",
                "product_group": it.product_group or pg,
                "owner_inn": it.owner_inn or "",
                "product_name": it.product_name or "",
            }
        print(f"    {pg}: получено {len(items)} КИ (всего уникальных: {len(rows)})", flush=True)

    # 3) снимок в CSV
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = f"{OUT_DIR}/cz_{inn}_{stamp}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["cis", "gtin", "status", "package_type", "product_group", "owner_inn", "product_name"],
        )
        w.writeheader()
        for r in rows.values():
            w.writerow(r)
    return {"file": path, "count": len(rows), "groups": groups}


async def main():
    user_id = os.environ.get("INV_USER_ID")
    groups_override = [g.strip() for g in os.environ.get("INV_GROUPS", "").split(",") if g.strip()]

    async with AsyncSessionLocal() as db:
        q = select(Integration).where(Integration.cz_token.isnot(None))
        if user_id:
            q = q.where(Integration.user_id == user_id)
        integs = (await db.execute(q)).scalars().all()
        print(f"Пользователей с токеном ЧЗ: {len(integs)}", flush=True)
        for integ in integs:
            print(f"Пользователь {integ.user_id}:", flush=True)
            try:
                res = await export_user(db, integ, groups_override)
            except Exception as e:
                print(f"  ОШИБКА: {type(e).__name__}: {e}", flush=True)
                continue
            print(f"  → {res}", flush=True)


asyncio.run(main())
