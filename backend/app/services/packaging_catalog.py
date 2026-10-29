"""Permanent packaging facts learned only from successful, matched CZ responses."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.logging import logger
from app.db.models import GtinPackaging
from app.db.session import AsyncSessionLocal
from app.services.chestnyznak import normalize_gtin_key
from app.services.scan_packaging import package_type

TYPE_MASK = {'UNIT': 1, 'GROUP': 2, 'BOX': 4}
SOURCES = {'cz_info', 'cz_batch', 'cz_history'}


def observations_from_facts(facts):
    """facts: (requested GTIN, returned GTIN, explicit type, has children)."""
    grouped = {}
    for requested, returned, kind, has_children in facts:
        gtin = normalize_gtin_key(requested)
        kind = package_type(kind)
        if not gtin or gtin != normalize_gtin_key(returned) or not kind:
            continue
        mask = TYPE_MASK[kind]
        if kind == 'UNIT' and has_children:
            mask |= TYPE_MASK['GROUP']
        previous = grouped.get(gtin, (0, 0))
        grouped[gtin] = (previous[0] | mask, previous[1] + 1)
    return grouped


def observation_upsert(facts, source):
    if source not in SOURCES:
        raise ValueError('Недопустимый источник сведений об упаковке')
    grouped = observations_from_facts(facts)
    if not grouped:
        return None
    now = datetime.now(timezone.utc)
    # Stable ordering avoids lock inversions between overlapping worker batches.
    stmt = insert(GtinPackaging).values([
        dict(gtin=gtin, type_mask=mask, observations=count, last_source=source,
             first_seen_at=now, last_seen_at=now)
        for gtin, (mask, count) in sorted(grouped.items())])
    return stmt.on_conflict_do_update(index_elements=[GtinPackaging.gtin], set_={
        'type_mask': GtinPackaging.type_mask.op('|')(stmt.excluded.type_mask),
        'observations': GtinPackaging.observations + stmt.excluded.observations,
        'last_source': stmt.excluded.last_source,
        'last_seen_at': stmt.excluded.last_seen_at,
    })


async def record_observations(facts, source='cz_info'):
    stmt = observation_upsert(facts, source)
    if stmt is None:
        return True
    try:
        # Separate short transaction: learning survives rollback of a deleted scan.
        async with AsyncSessionLocal() as db:
            await db.execute(stmt)
            await db.commit()
        return True
    except Exception as exc:
        logger.warning('packaging_catalog.write_failed', error_type=type(exc).__name__)
        return False


async def is_confirmed_unit(db, gtin):
    key = normalize_gtin_key(gtin)
    if not key:
        return False
    # One indexed read in the scan transaction. No Redis/process UNIT cache:
    # every client sees committed conflicts without waiting for a cache TTL.
    mask = (await db.execute(select(GtinPackaging.type_mask).where(GtinPackaging.gtin == key))).scalar_one_or_none()
    return mask == TYPE_MASK['UNIT']
