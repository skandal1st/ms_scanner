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
