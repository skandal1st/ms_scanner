"""Клиент Национального каталога (НК ЦРПТ) — публичное чтение карточек товаров по GTIN.

Доступ публичный: `GET {NK_API_BASE}/v3/product?apikey=…&gtin=G` отдаёт карточку любого
GTIN (наименование, товарный знак, категории), не только «своих». apikey лишь
аутентифицирует вызывающего и снимает лимиты. Используется как ещё один резолвер
GTIN→товар (фолбэк к каталогу МойСклад) и для человекочитаемых имён в сверке остатков.

ВАЖНО: пачка `gtins=…` доступна только для собственных карточек участника (иначе 404),
поэтому резолвим по ОДНОМУ GTIN за запрос с ограниченной конкурентностью
(``NK_CONCURRENCY``). НК принимает и GTIN-13, и GTIN-14 — шлём нормализованный ключ.

Best-effort: любая ошибка (нет ключа, сеть, 4xx/5xx НК) деградирует к пустому результату
и не роняет вызывающий поток. НК не трогаем, если `settings.nk_enabled` == False.

Док: https://docs.crpt.ru/gismt/API_НК/ — метод v3/product.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.chestnyznak import normalize_gtin_key


@dataclass
class NkCard:
    """Разобранная карточка товара НК (только нужные нам поля)."""

    gtin: str                       # нормализованный ключ (GTIN-14)
    good_name: Optional[str]
    brand_name: Optional[str]
    category: Optional[str]         # первая категория (cat_name), если есть
    good_id: Optional[int] = None


def _parse_card(item: dict) -> Optional[NkCard]:
    """Один элемент result[] → NkCard. GTIN берём из identified_by (type=gtin)."""
    raw_gtin: Optional[str] = None
    for ident in item.get("identified_by") or []:
        if (ident.get("type") or "").lower() == "gtin" and ident.get("value"):
            raw_gtin = str(ident["value"])
            break
    key = normalize_gtin_key(raw_gtin)
    if not key:
        return None

    categories = item.get("categories") or []
    category = None
    if categories and isinstance(categories[0], dict):
        category = (categories[0].get("cat_name") or "").strip() or None

    name = (item.get("good_name") or "").strip() or None
    brand = (item.get("brand_name") or "").strip() or None
    good_id = item.get("good_id")
    return NkCard(
        gtin=key,
        good_name=name,
        brand_name=brand,
        category=category,
        good_id=good_id if isinstance(good_id, int) and good_id else None,
    )


async def _fetch_one(client: httpx.AsyncClient, base: str, key: str) -> Optional[NkCard]:
    """Один запрос v3/product?gtin=key → NkCard либо None. Не бросает."""
    try:
        resp = await client.get(
            f"{base}/v3/product",
            params={"apikey": settings.NK_API_KEY, "gtin": key},
        )
    except httpx.HTTPError as exc:
        logger.warning("nk.lookup_http_error", gtin=key, error=str(exc))
        return None
    if resp.status_code == 404:  # нет карточки в каталоге — не ошибка
        return None
    if resp.status_code != 200:
        logger.warning("nk.lookup_bad_status", gtin=key, status=resp.status_code)
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("nk.lookup_bad_json", gtin=key)
        return None
    for item in payload.get("result") or []:
        card = _parse_card(item)
        if card:
            return card
    return None


async def lookup_products(gtins: list[str]) -> dict[str, NkCard]:
    """Карточки НК для набора GTIN: ``{нормализованный_GTIN: NkCard}``.

    Дедупим по нормализованному ключу (GTIN-14), запрашиваем по одному GTIN с
    ограниченной конкурентностью (``NK_CONCURRENCY``). Промахи (нет карточки) в словарь
    не попадают. Не бросает.
    """
    if not settings.nk_enabled or not gtins:
        return {}

    keys = sorted({k for k in (normalize_gtin_key(g) for g in gtins) if k})
    if not keys:
        return {}

    base = settings.NK_API_BASE.rstrip("/")
    sem = asyncio.Semaphore(max(1, settings.NK_CONCURRENCY))
    result: dict[str, NkCard] = {}
    try:
        async with httpx.AsyncClient(timeout=settings.NK_TIMEOUT) as client:
            async def _guarded(key: str) -> Optional[NkCard]:
                async with sem:
                    return await _fetch_one(client, base, key)

            cards = await asyncio.gather(*(_guarded(k) for k in keys))
        for card in cards:
            if card:
                result[card.gtin] = card
    except Exception as exc:  # noqa: BLE001 — резолвер не критичен, не роняем поток
        logger.warning("nk.lookup_failed", error=str(exc))
        return result

    logger.info("nk.lookup_done", requested=len(keys), found=len(result))
    return result


async def lookup_one(gtin: str) -> Optional[NkCard]:
    """Карточка НК для одного GTIN либо None."""
    cards = await lookup_products([gtin])
    key = normalize_gtin_key(gtin)
    return cards.get(key) if key else None
