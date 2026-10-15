"""Quantity-only UPD lines and XML ordering for supply writes."""
from app.services.chestnyznak import normalize_gtin_key


def prepare_supply_rows(rows, plan, meta):
    plan = [p for p in (plan or []) if isinstance(p, dict)]
    meta = meta or {}
    linked = {p.get('product_id') for p in (meta.get('manual_position_links') or {}).values()}
    result = list(rows)
    present = {row.get('product_id') for row in rows}
    for p in plan:
        pid = p.get('product_id')
        if not pid or pid in present or (p.get('marked') is not False and pid not in linked):
            continue
        quantity = float(p.get('expected_qty') or 0)
        if quantity > 0:
            result.append({'product_id': pid, 'code': '', 'is_barcode': True,
                           'is_box': False, 'quantity': quantity})
            present.add(pid)

    order = {}
    for line in meta.get('physical_plan') or []:
        pid = line.get('product_id')
        if not pid:
            gtins = {normalize_gtin_key(g) for g in line.get('gtins', [])}
            pid = next((p.get('product_id') for p in plan if normalize_gtin_key(p.get('gtin')) in gtins), None)
        if pid:
            order.setdefault(pid, len(order))
    for p in plan:
        if p.get('product_id'):
            order.setdefault(p['product_id'], len(order))
    return sorted(result, key=lambda row: order.get(row.get('product_id'), len(order)))
