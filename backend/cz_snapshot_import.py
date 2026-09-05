"""Импорт снимка остатка ЧЗ (CSV от inventory_export) → таблица cz_owner_marks.

Запуск в контейнере:
  env INV_CZ=/app/_inventory/cz_<inn>_ready.csv INV_USER_ID=<uuid> python /app/cz_snapshot_import.py
Канонизируем CIS так же, как edo_marks (strip_ai_brackets + обрезка после GS + %c1),
чтобы сверка сходилась. Полная замена снимка пользователя (delete + insert).
"""
import asyncio
import csv
import os
import re

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import AsyncSessionLocal
from app.db.models import CzOwnerMark
from app.services.chestnyznak import strip_ai_brackets, normalize_gtin_key

_GS = "\x1d"


def canon(code: str) -> str:
    s = strip_ai_brackets(code or "").strip()
    s = s.split(_GS, 1)[0]
    return re.sub(r"(?i)%c1", "", s).strip()


async def main():
    path = os.environ["INV_CZ"]
    user_id = os.environ["INV_USER_ID"]
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = canon(r.get("cis") or "")
            if not key:
                continue
            rows.append({
                "user_id": user_id,
                "cis_canonical": key,
                "gtin": normalize_gtin_key(r.get("gtin")) if r.get("gtin") else None,
                "status": (r.get("status") or "")[:32] or None,
                "package_type": (r.get("package_type") or "")[:16] or None,
                "product_group": (r.get("product_group") or "")[:32] or None,
            })
    # дедуп по cis_canonical
    seen = {}
    for x in rows:
        seen[x["cis_canonical"]] = x
    rows = list(seen.values())
    print(f"строк к импорту: {len(rows)}")

    async with AsyncSessionLocal() as db:
        await db.execute(delete(CzOwnerMark).where(CzOwnerMark.user_id == user_id))
        await db.commit()
        B = 5000
        for i in range(0, len(rows), B):
            await db.execute(pg_insert(CzOwnerMark).on_conflict_do_nothing(
                constraint="ix_cz_owner_marks_user_cis"), rows[i:i + B])
            await db.commit()
            print(f"  {min(i + B, len(rows))}/{len(rows)}")
        cnt = (await db.execute(text(
            "SELECT count(*) FROM cz_owner_marks WHERE user_id=:u").bindparams(u=user_id))).scalar_one()
    print(f"в БД cz_owner_marks: {cnt}")


asyncio.run(main())
