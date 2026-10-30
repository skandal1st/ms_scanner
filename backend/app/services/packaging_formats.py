"""Conservative format-specific facts. Aggregate counts always need live composition."""
import re
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import GtinPackagingFormat
from app.services.chestnyznak import _logistic_gs1_cis, _gs1_check_digit_ok, extract_gtin, normalize_km_ai_prefix
from app.services.packaging_catalog import TYPE_MASK


def code_format(code):
    if _logistic_gs1_cis(code):
        return 'gs1_ai02'
    value = normalize_km_ai_prefix(code.strip())
    if re.match(r'^01\d{14}', value):
        return 'gs1_ai01'
    if len(value) == 29 and value[:14].isdigit():
        return 'tobacco_compact'
    if len(value) > 14 and value[:14].isdigit():
        return 'bare_gtin'
    return None


async def confirmed_unit(db, code):
    mask = await type_mask(db, code)
    return None if mask is None else mask == TYPE_MASK['UNIT']


async def known_aggregate(db, code):
    mask = await type_mask(db, code)
    return bool(mask and mask & (TYPE_MASK['GROUP'] | TYPE_MASK['BOX']))


async def type_mask(db, code):
    fmt, gtin = code_format(code), extract_gtin(code)
    if not fmt or not gtin:
        return None
    return (await db.execute(select(GtinPackagingFormat.type_mask).where(
        GtinPackagingFormat.gtin == gtin, GtinPackagingFormat.code_format == fmt))).scalar_one_or_none()


async def learn(db, code, metadata):
    fmt, gtin = code_format(code), extract_gtin(code)
    # A mismatched/unknown identifier is not a reusable GTIN rule.
    if not fmt or not _gs1_check_digit_ok(gtin or '') or (metadata['package_type'] == 'UNIT' and gtin != metadata['gtin']):
        return False
    stmt = insert(GtinPackagingFormat).values(gtin=gtin, code_format=fmt,
        type_mask=TYPE_MASK[metadata['package_type']], observations=1,
        last_quantity=metadata['box_quantity'] or 1, last_seen_at=datetime.now(timezone.utc))
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[GtinPackagingFormat.gtin, GtinPackagingFormat.code_format], set_={
            'type_mask': GtinPackagingFormat.type_mask.op('|')(stmt.excluded.type_mask),
            'observations': GtinPackagingFormat.observations + 1,
            'last_quantity': stmt.excluded.last_quantity, 'last_seen_at': stmt.excluded.last_seen_at}))
    return True
