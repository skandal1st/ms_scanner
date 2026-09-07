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


# Статусы запроса к НК: found — карточка есть; not_found — 404 (точно нет в каталоге,
# можно кэшировать негатив); error — 429/5xx/сеть (временная ошибка, НЕ негатив).
FOUND = "found"
NOT_FOUND = "not_found"
ERROR = "error"


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Пауза перед повтором: заголовок Retry-After либо экспонента с потолком."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), 10.0)
        except ValueError:
            pass
    return min(0.5 * (2 ** attempt), 8.0)


async def _fetch_one(
    client: httpx.AsyncClient, base: str, key: str
) -> tuple[str, Optional[NkCard]]:
    """Один GTIN → (статус, карточка). Ретраит 429/5xx с бэкоффом. Не бросает.

    ВАЖНО: негативом (not_found) считается ТОЛЬКО HTTP 404. 429 (лимит) и 5xx/сеть —
    это ``error``: их нельзя кэшировать как «нет в каталоге», иначе валидные GTIN
    навсегда осели бы промахами. Такие GTIN просто перепроверятся в следующий раз."""
    for attempt in range(settings.NK_MAX_RETRIES + 1):
        try:
            resp = await client.get(
                f"{base}/v3/product",
                params={"apikey": settings.NK_API_KEY, "gtin": key},
            )
        except httpx.HTTPError as exc:
            if attempt < settings.NK_MAX_RETRIES:
                await asyncio.sleep(min(0.5 * (2 ** attempt), 8.0))
                continue
            logger.warning("nk.lookup_http_error", gtin=key, error=str(exc))
            return ERROR, None

        if resp.status_code == 200:
            try:
                payload = resp.json()
            except ValueError:
                logger.warning("nk.lookup_bad_json", gtin=key)
                return ERROR, None
            for item in payload.get("result") or []:
                card = _parse_card(item)
                if card:
                    return FOUND, card
            return NOT_FOUND, None  # 200 без карточки — трактуем как «нет»
        if resp.status_code == 404:
            return NOT_FOUND, None
        # 429 / 5xx — временная ошибка: ждём и повторяем.
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < settings.NK_MAX_RETRIES:
                await asyncio.sleep(_retry_after_seconds(resp, attempt))
                continue
        logger.warning("nk.lookup_bad_status", gtin=key, status=resp.status_code)
        return ERROR, None
    return ERROR, None


async def lookup_products_detailed(
    gtins: list[str],
) -> tuple[dict[str, NkCard], set[str]]:
    """(найденные карточки, точно_отсутствующие). GTIN с ошибками (429/5xx/сеть) не
    попадают ни туда, ни туда — их нельзя кэшировать как негатив.

    Дедуп по нормализованному ключу (GTIN-14), по одному GTIN за запрос с ограниченной
    конкурентностью. Не бросает."""
    if not settings.nk_enabled or not gtins:
        return {}, set()

    keys = sorted({k for k in (normalize_gtin_key(g) for g in gtins) if k})
    if not keys:
        return {}, set()

    base = settings.NK_API_BASE.rstrip("/")
    sem = asyncio.Semaphore(max(1, settings.NK_CONCURRENCY))
    found: dict[str, NkCard] = {}
    not_found: set[str] = set()
    errors = 0
    try:
        async with httpx.AsyncClient(timeout=settings.NK_TIMEOUT) as client:
            async def _guarded(key: str) -> tuple[str, str, Optional[NkCard]]:
                async with sem:
                    status, card = await _fetch_one(client, base, key)
                    return key, status, card

            for key, status, card in await asyncio.gather(*(_guarded(k) for k in keys)):
                if status == FOUND and card:
                    found[card.gtin] = card
                elif status == NOT_FOUND:
                    not_found.add(key)
                else:
                    errors += 1
    except Exception as exc:  # noqa: BLE001 — резолвер не критичен, не роняем поток
        logger.warning("nk.lookup_failed", error=str(exc))
        return found, not_found

    logger.info(
        "nk.lookup_done",
        requested=len(keys), found=len(found), not_found=len(not_found), errors=errors,
    )
    return found, not_found


async def lookup_products(gtins: list[str]) -> dict[str, NkCard]:
    """Карточки НК для набора GTIN: ``{нормализованный_GTIN: NkCard}`` (без деталей)."""
    found, _ = await lookup_products_detailed(gtins)
    return found


async def lookup_one(gtin: str) -> Optional[NkCard]:
    """Карточка НК для одного GTIN либо None."""
    cards = await lookup_products([gtin])
    key = normalize_gtin_key(gtin)
    return cards.get(key) if key else None
