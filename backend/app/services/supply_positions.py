"""Quantity-only UPD lines and XML ordering for supply writes."""
from app.services.chestnyznak import normalize_gtin_key


def prepare_supply_without_cz(plan, meta):
    """Use document quantities, never aggregate/code counts, in the no-CZ flow."""
    from app.services.plan_matching import unique_plan_product
    plan = plan or []
    for line in (meta or {}).get('physical_plan') or []:
        if line.get('product_id'):
            continue
        matches = {p['product_id'] for gtin in line.get('gtins', [])
                   if (p := unique_plan_product(plan, normalize_gtin_key(gtin)))}
        if len(matches) != 1:
            raise ValueError('Сопоставьте все позиции УПД с товарами МойСклад перед приёмкой без ЧЗ.')
    if any(not p.get('product_id') and float(p.get('expected_qty') or 0) > 0 for p in plan):
        raise ValueError('Сопоставьте все товары перед приёмкой без ЧЗ.')
    rows = prepare_supply_rows([], plan, meta, include_marked=True)
    if not rows:
        raise ValueError('Нет товаров с известным количеством. Загрузите УПД или выберите поступление с позициями.')
    return rows


def prepare_supply_rows(rows, plan, meta, *, include_marked=False):
    plan = [p for p in (plan or []) if isinstance(p, dict)]
    meta = meta or {}
    linked = {p.get('product_id') for p in (meta.get('manual_position_links') or {}).values()}
    result = list(rows)
    present = {row.get('product_id') for row in rows}
    for p in plan:
        pid = p.get('product_id')
        if not pid or pid in present or (not include_marked and p.get('marked') is not False and pid not in linked):
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
