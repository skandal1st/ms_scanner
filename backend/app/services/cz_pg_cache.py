"""Кэш «GTIN → товарная группа (pg)» в Redis.

Товарная группа — стабильное свойство самого GTIN, одинаковое для всех клиентов,
поэтому кэш глобальный (общий на всех арендаторов). Сужает перебор групп в True API:
после первого успешного cises/info для GTIN нужная группа пробуется первой (а не после
2-3 промахов). Кэш — только оптимизация порядка: при промахе/устаревании перебор
всё равно проходит по остальным группам, так что неверное значение самоисправляется.
"""
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import logger

# pg меняется у GTIN крайне редко (по сути — свойство товара), но TTL держим конечным
# на случай ошибочной записи. 30 дней.
_TTL_SECONDS = 60 * 60 * 24 * 30


def _key(gtin: str) -> str:
    return f"czpg:{gtin}"


async def get_cached_pg(gtin: Optional[str]) -> Optional[str]:
    if not gtin:
        return None
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        try:
            v = await r.get(_key(gtin))
        finally:
            await r.aclose()
        if v is None:
            return None
        return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
    except Exception as exc:  # кэш не критичен — молча деградируем к полному перебору
        logger.warning("cz_pg_cache.get_failed", gtin=gtin, error=str(exc))
        return None


async def set_cached_pg(gtin: Optional[str], pg: Optional[str]) -> None:
    if not gtin or not pg:
        return
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        try:
            await r.set(_key(gtin), pg, ex=_TTL_SECONDS)
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning("cz_pg_cache.set_failed", gtin=gtin, error=str(exc))
