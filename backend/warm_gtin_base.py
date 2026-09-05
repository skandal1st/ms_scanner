"""Разовый прогрев базы знаний GtinProductMap по существующим поступлениям и отгрузкам МС.

Для каждого пользователя с токеном МС: перебирает документы supply+demand, из позиций
(expand=assortment через build_plan) достаёт GTIN→(product_id, product_name) и pack_gtins,
дедуплицирует и заливает в GtinProductMap (upsert). Идемпотентно — можно гонять повторно.

Устойчив к нестабильной сети (ConnectTimeout к api.moysklad.ru): ретрай на сетевые
ошибки, обработка чанками с инкрементальным коммитом — при обрыве прогресс не теряется.
"""
import asyncio

import httpx

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models import Integration
from app.core.security import decrypt_token
from app.core.logging import logger
from app.services.moysklad import MoySkladService
from app.services.gtin_product_store import remember_gtin_product

KINDS = ("supply", "demand")
PAGE = 1000
CHUNK = 40          # документов в чанке (concurrency + коммит после каждого чанка)
NET_TRIES = 5       # попыток на сетевую ошибку
TIMEOUT = httpx.Timeout(90, connect=30)


async def _retry(factory, what: str):
    """Выполнить корутину с ретраем на сетевые ошибки МС (ConnectTimeout и т.п.)."""
    delay = 2
    for attempt in range(NET_TRIES):
        try:
            return await factory()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if attempt == NET_TRIES - 1:
                raise
            logger.warning("warm.net_retry", what=what, attempt=attempt, error=type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)


async def list_doc_ids(ms: MoySkladService, kind: str) -> list[str]:
    ids: list[str] = []
    offset = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while True:
            async def _page():
                r = await client.get(
                    f"{ms.base_url}/entity/{kind}",
                    headers=ms.headers,
                    params={"limit": PAGE, "offset": offset, "order": "moment,desc"},
                )
                r.raise_for_status()
                return r.json()
            data = await _retry(_page, f"list {kind}@{offset}")
            rows = data.get("rows", []) or []
            ids.extend(r["id"] for r in rows if r.get("id"))
            size = (data.get("meta") or {}).get("size", 0)
            offset += PAGE
            if not rows or offset >= size:
                break
    return ids


async def warm_user(db, integ: Integration) -> dict:
    token = decrypt_token(integ.moysklad_token)
    ms = MoySkladService(token)
    written = 0
    docs_done = 0
    docs_total = 0

    for kind in KINDS:
        try:
            ids = await list_doc_ids(ms, kind)
        except Exception as exc:
            print(f"  {kind}: не удалось получить список ({type(exc).__name__}) — пропуск")
            logger.warning("warm.list_failed", kind=kind, error=str(exc))
            continue
        docs_total += len(ids)
        print(f"  {kind}: {len(ids)} документов", flush=True)

        for start in range(0, len(ids), CHUNK):
            chunk = ids[start:start + CHUNK]

            async def plan_of(doc_id: str):
                try:
                    return await _retry(lambda: ms.build_plan(kind, doc_id), f"plan {doc_id}")
                except Exception as exc:
                    logger.warning("warm.build_plan_failed", kind=kind, doc_id=doc_id, error=str(exc))
                    return []

            plans = await asyncio.gather(*(plan_of(i) for i in chunk))
            # Запись в базу знаний — последовательно по одной сессии, затем коммит чанка.
            for plan in plans:
                for item in plan:
                    pid = item.get("product_id")
                    if not pid:
                        continue
                    name = item.get("product_name") or None
                    keys = [item.get("gtin")] + list(item.get("pack_gtins") or [])
                    for g in keys:
                        if g:
                            await remember_gtin_product(db, integ.user_id, g, pid, name)
                            written += 1
            await db.commit()
            docs_done += len(chunk)
            print(f"    {kind}: {docs_done}/{len(ids)} док., записей GTIN всего: {written}", flush=True)

    return {"docs_total": docs_total, "docs_done": docs_done, "gtins": written}


async def main():
    async with AsyncSessionLocal() as db:
        integs = (
            await db.execute(
                select(Integration).where(Integration.moysklad_token.isnot(None))
            )
        ).scalars().all()
        print(f"Пользователей с токеном МС: {len(integs)}", flush=True)
        grand = 0
        for integ in integs:
            print(f"Пользователь {integ.user_id}:", flush=True)
            try:
                res = await warm_user(db, integ)
            except Exception as exc:
                print(f"  ОШИБКА: {type(exc).__name__}: {exc}", flush=True)
                logger.warning("warm.user_failed", user_id=str(integ.user_id), error=str(exc))
                continue
            print(f"  → GTIN записано/обновлено: {res['gtins']} (документов {res['docs_done']}/{res['docs_total']})", flush=True)
            grand += res["gtins"]
        print(f"ИТОГО GTIN записано/обновлено: {grand}", flush=True)


asyncio.run(main())
