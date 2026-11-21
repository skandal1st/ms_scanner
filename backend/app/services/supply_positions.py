"""Quantity-only UPD lines and XML ordering for supply writes."""
from collections import defaultdict

from app.services.chestnyznak import normalize_gtin_key
from app.services.plan_matching import unique_plan_product


def prepare_supply_without_cz(plan, meta):
    """Use document quantities, never aggregate/code counts, in the no-CZ flow."""
    plan = plan or []
    for line in (meta or {}).get('physical_plan') or []:
        if 'items' in line:
            items = line['items']
            products = {item.get('product_id') for item in items}
            if None in products:
                raise ValueError('Сопоставьте все позиции УПД с товарами МойСклад перед приёмкой без ЧЗ.')
            if products - {item['product_id'] for item in items if item.get('quantity') is not None}:
                raise ValueError('В строке УПД не указано количество для каждого товара. Проверьте количества перед приёмкой без ЧЗ.')
            continue
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
    lines = meta.get('physical_plan') or []
    if lines and 'items' in lines[0]:
        return _prepare_xml_rows(rows, plan, meta, lines, include_marked)

    # Older XML imports retained per-line quantities but only one price per product.
    seen_products = set()
    for line in lines:
        pid = line.get('product_id')
        if not pid:
            matches = {p['product_id'] for gtin in line.get('gtins', []) if (p := unique_plan_product(plan, gtin))}
            pid = next(iter(matches)) if len(matches) == 1 else None
        if pid and pid in seen_products:
            raise ValueError('В этой приёмке несколько строк УПД одного товара. Загрузите исходный УПД повторно, чтобы восстановить цены строк.')
        seen_products.add(pid)
    return _prepare_product_rows(rows, plan, meta, include_marked)


def _prepare_product_rows(rows, plan, meta, include_marked):
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


def _prepare_xml_rows(rows, plan, meta, lines, include_marked):
    """Route each mark (including units of an expanded box) to its source XML row."""
    by_code = {}
    product_lines = defaultdict(set)
    quantities = defaultdict(float)
    unmarked = set()
    for index, line in enumerate(lines):
        for item in line['items']:
            pid = item.get('product_id')
            if pid:
                product_lines[pid].add(index)
                quantities[index, pid] += item.get('quantity') or 0
                if not item['codes']:
                    unmarked.add((index, pid))
            for code in item['codes']:
                by_code.setdefault(code, index)

    buckets = defaultdict(list)
    extra = []
    for row in rows:
        pid = row.get('product_id')
        index = by_code.get(row.get('source_code'), by_code.get(row.get('code')))
        if index is None and pid in product_lines:
            candidates = product_lines[pid]
            if len(candidates) > 1:
                raise ValueError('Дополнительная марка относится к товару из нескольких строк УПД. Проверьте исходный УПД и список марок.')
            index = next(iter(candidates))
        if index is None:
            extra.append(row)
        else:
            buckets[index, pid].append(row)

    if include_marked:
        unmarked.update(quantities)
    for key in sorted(unmarked):
        if not buckets[key] and quantities[key] > 0:
            buckets[key].append({'product_id': key[1], 'code': '', 'is_barcode': True,
                                 'is_box': False, 'quantity': quantities[key]})

    result = []
    for (index, pid), group in sorted(buckets.items(), key=lambda pair: pair[0][0]):
        line = lines[index]
        for row in group:
            result.append({**row, 'upd_line': index, 'price': line.get('price'),
                           'vat': line.get('vat'), 'expected_qty': quantities[index, pid]})
    remaining_plan = [p for p in plan if p.get('product_id') not in product_lines]
    result.extend(_prepare_product_rows(extra, remaining_plan, meta, include_marked))
    return result
