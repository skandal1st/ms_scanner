"""Canonical product GTINs used by desktop barcode confirmations."""
from app.services.chestnyznak import normalize_gtin_key


def product_gtins(product):
    return sorted({normalize_gtin_key(str(value)) for barcode in product.get('barcodes') or []
        if isinstance(barcode, dict) for value in barcode.values()
        if str(value).isdigit() and len(str(value)) in {8, 12, 13, 14}} - {None})
