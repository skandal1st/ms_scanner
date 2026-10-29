"""Short-lived, document-scoped UNIT evidence from successful CZ lookups.

This skips repeated packaging discovery, never the explicit mark verification.
Aggregates still require their own composition request for every scanned code.
"""
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import logger
from app.services.chestnyznak import normalize_gtin_key

_TTL_SECONDS = 600


def _key(user_id, document_id, gtin):
    return f"scan_unit:{user_id}:{document_id}:{normalize_gtin_key(gtin)}"


async def has_confirmed_unit(user_id, document_id, gtin):
    if not normalize_gtin_key(gtin):
        return False
    redis = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=0.1, socket_timeout=0.1)
    try:
        return await redis.get(_key(user_id, document_id, gtin)) == b'UNIT'
    except Exception:
        logger.warning('scan.unit_cache_read_failed', document_id=str(document_id))
        return False
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def remember_unit_evidence(user_id, document_id, original_gtin, info):
    key = normalize_gtin_key(original_gtin)
    if not key or info is None:
        return
    # Do not cache a guessed default, an alias, or any composition as UNIT.
    confirmed = (str(info.package_type or '').upper() == 'UNIT' and not info.children
                 and normalize_gtin_key(info.gtin) == key)
    redis = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=0.1, socket_timeout=0.1)
    try:
        if confirmed:
            await redis.set(_key(user_id, document_id, key), 'UNIT', ex=_TTL_SECONDS)
        else:
            await redis.delete(_key(user_id, document_id, key))
    except Exception:
        logger.warning('scan.unit_cache_write_failed', document_id=str(document_id))
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass
