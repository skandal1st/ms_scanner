"""Дожать/скачать уже созданные задачи выгрузки ЧЗ по task_id (генерация у ЧЗ долгая).

Не создаёт новых задач — ждёт COMPLETED по существующим и качает результат (хранится 30 дней).
INV_TASKS="otp:<task_id>,ncp:<task_id>"  INV_USER_ID=<uuid>
Пишет тот же снимок, что и inventory_export.py: /app/_inventory/cz_<inn>_<ts>.csv
"""
import asyncio
import csv
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.db.models import Integration
from app.core.security import decrypt_token
from app.services.cz_dispenser import CzDispenser, CzDispenserError

OUT_DIR = "/app/_inventory"


async def main():
    user_id = os.environ.get("INV_USER_ID")
    pairs = [p for p in os.environ.get("INV_TASKS", "").split(",") if p.strip()]
    tasks = [(x.split(":", 1)[0].strip(), x.split(":", 1)[1].strip()) for x in pairs]
    if not tasks:
        raise SystemExit("Задайте INV_TASKS='otp:<id>,ncp:<id>'")

    async with AsyncSessionLocal() as db:
        q = select(Integration).where(Integration.cz_token.isnot(None))
        if user_id:
            q = q.where(Integration.user_id == user_id)
        integ = (await db.execute(q)).scalars().first()
    token = decrypt_token(integ.cz_token)
    inn = (integ.cz_inn or "").strip()
    disp = CzDispenser(token)

    rows: dict[str, dict] = {}
    for pg, tid in tasks:
        print(f"{pg}: жду задачу {tid} …", flush=True)
        try:
            items = await disp.wait_and_download(pg, tid, timeout_s=10800, poll_s=60)
        except CzDispenserError as e:
            print(f"  {pg}: {e}", flush=True)
            continue
        for it in items:
            rows[it.cis] = {
                "cis": it.cis, "gtin": it.gtin or "", "status": it.status or "",
                "package_type": it.package_type or "", "product_group": it.product_group or pg,
                "owner_inn": it.owner_inn or "", "product_name": it.product_name or "",
            }
        print(f"  {pg}: КИ {len(items)} (всего уникальных {len(rows)})", flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = f"{OUT_DIR}/cz_{inn}_{stamp}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cis", "gtin", "status", "package_type", "product_group", "owner_inn", "product_name"])
        w.writeheader()
        for r in rows.values():
            w.writerow(r)
    print(f"→ снимок: {path} | марок: {len(rows)}", flush=True)


asyncio.run(main())
