"""Exact GTIN aliases from a document plan; never match products by display name."""
from app.services.chestnyznak import normalize_gtin_key


def plan_gtin_keys(item):
    if not isinstance(item, dict):
        return set()
    return {key for value in [item.get("gtin"), *(item.get("gtins") or []),
                              *(item.get("pack_gtins") or [])]
            if (key := normalize_gtin_key(value))}


def unique_plan_product(plan, gtin):
    key = normalize_gtin_key(gtin)
    matches = {item["product_id"]: item for item in plan or []
               if isinstance(item, dict) and item.get("product_id") and key in plan_gtin_keys(item)}
    return next(iter(matches.values())) if len(matches) == 1 else None


def plan_pack_quantity(plan, gtin):
    """Only unambiguous packaging metadata can increase a mark's unit count."""
    key = normalize_gtin_key(gtin)
    matches = [item for item in plan or [] if key in plan_gtin_keys(item)]
    if not matches or unique_plan_product(plan, gtin) is None:
        return None
    quantities = set()
    for item in matches:
        if key in {normalize_gtin_key(v) for v in [item.get("gtin"), *(item.get("gtins") or [])]}:
            return None
        quantity = (item.get("pack_quantities") or {}).get(key)
        if not isinstance(quantity, int) or quantity <= 1:
            return None
        quantities.add(quantity)
    return next(iter(quantities)) if len(quantities) == 1 else None
