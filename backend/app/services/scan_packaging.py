"""Packaging classification independent of the SSCC transport-pack write mode."""

def package_type(value):
    value = str(value or '').upper()
    if value in {'GROUP', 'LEVEL1', 'SET'}:
        return 'GROUP'
    if value in {'BOX', 'LEVEL2', 'TRANSPORT'}:
        return 'BOX'
    if value == 'UNIT':
        return 'UNIT'
    return None


def scan_package_type(scan):
    known = package_type(getattr(scan, 'package_type', None))
    if known:
        return known
    if scan.is_box:
        return 'BOX'
    if not scan.is_barcode and ((scan.box_quantity or 0) > 1 or scan.child_codes):
        return 'GROUP'
    return 'UNIT'


def needs_packaging_discovery(plan, gtin, product_id, quantity):
    """Resolve unknown GTINs and pack aliases without guessing a pack's unit count."""
    from app.services.chestnyznak import normalize_gtin_key
    from app.services.plan_matching import unique_plan_product
    key = normalize_gtin_key(gtin)
    if not key:
        return False
    pack_alias = any(key in {normalize_gtin_key(v) for v in p.get('pack_gtins', [])}
                     for p in plan or [] if isinstance(p, dict))
    return (not product_id or (not plan and not quantity)
            or (bool(plan) and unique_plan_product(plan, key) is None)
            or (pack_alias and not quantity))
