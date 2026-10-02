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
