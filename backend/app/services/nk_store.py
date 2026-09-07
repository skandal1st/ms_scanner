"""Cache-first резолвер карточек Национального каталога (НК): БД-кэш → НК → апсерт.

Публичный метод НК отдаёт карточку по одному GTIN за запрос, поэтому между вызовами
кэшируем результат в глобальной таблице ``nk_product`` — включая негативы (GTIN нет в
каталоге), чтобы несопоставленные GTIN не вызывали 404-шторм. Негативы перепроверяются
по TTL (``NK_NEG_TTL_DAYS``).

Единая точка входа для приёмки и инвентаризации. Best-effort: НК недоступен/выключен →
отдаём то, что есть в кэше, поток не роняем.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.logging import logger
from app.db.models import NkProduct
from app.services.chestnyznak import normalize_gtin_key
from app.services.national_catalog import NkCard, lookup_products_detailed


def _card_from_row(row: NkProduct) -> Optional[NkCard]:
    if not row.found:
        return None
    return NkCard(
        gtin=row.gtin,
        good_name=row.good_name,
        brand_name=row.brand_name,
        category=row.category,
    )


async def resolve_cards(db, gtins: list[str]) -> dict[str, NkCard]:
    """``{нормализованный_GTIN: NkCard}`` для GTIN, найденных в НК (через кэш).

    Читает кэш, добирает из НК только промахи и просроченные негативы, апсертит
    результат (позитив и негатив). Коммитит собственные записи в кэш. Не бросает.
    """
    keys = sorted({k for k in (normalize_gtin_key(g) for g in gtins) if k})
    if not keys:
        return {}

    rows = (
        await db.execute(select(NkProduct).where(NkProduct.gtin.in_(keys)))
    ).scalars().all()
    cached = {r.gtin: r for r in rows}

    neg_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.NK_NEG_TTL_DAYS)

    def _is_fresh_neg(r: NkProduct) -> bool:
        at = r.fetched_at
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at is not None and at > neg_cutoff

    result: dict[str, NkCard] = {}
    missing: list[str] = []
    for k in keys:
        r = cached.get(k)
        if r is None:
            missing.append(k)
        elif r.found:
            card = _card_from_row(r)
            if card:
                result[k] = card
        elif not _is_fresh_neg(r):
            missing.append(k)  # негатив протух — перепроверяем

    if missing and settings.nk_enabled:
        fresh, not_found = await lookup_products_detailed(missing)
        result.update(fresh)
        # Кэшируем ТОЛЬКО определённые ответы: позитивы (found) и точные негативы
        # (not_found = HTTP 404). GTIN с ошибкой (429/5xx/сеть) не апсертим — иначе
        # осели бы промахами и не перепроверялись; они добираются в следующий раз.
        to_upsert = [(k, fresh.get(k)) for k in missing if (k in fresh or k in not_found)]
        for k, card in to_upsert:
            values = dict(
                gtin=k,
                found=card is not None,
                good_name=(card.good_name if card else None),
                brand_name=(card.brand_name if card else None),
                category=(card.category if card else None),
                fetched_at=datetime.now(timezone.utc),
            )
            stmt = pg_insert(NkProduct).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["gtin"],
                set_={
                    "found": stmt.excluded.found,
                    "good_name": stmt.excluded.good_name,
                    "brand_name": stmt.excluded.brand_name,
                    "category": stmt.excluded.category,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            try:
                await db.execute(stmt)
            except Exception as exc:  # кэш не критичен
                logger.warning("nk_store.upsert_failed", gtin=k, error=str(exc))
        try:
            await db.commit()
        except Exception as exc:
            logger.warning("nk_store.commit_failed", error=str(exc))

    return result


def display_name(card: NkCard) -> Optional[str]:
    """Читаемое имя из карточки НК: «Бренд · Наименование» либо что есть."""
    name = (card.good_name or "").strip()
    brand = (card.brand_name or "").strip()
    if name and brand and brand.lower() not in name.lower():
        return f"{brand} · {name}"[:500]
    return (name or brand or None) and (name or brand)[:500]
