"""Quantity-only UPD lines and XML ordering for supply writes."""
from collections import defaultdict

from app.services.chestnyznak import normalize_gtin_key
from app.services.moysklad import MoySkladService
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
    """Keep XML quantities and prices; use spare slots only within the same product."""
    by_code = {}
    product_lines = defaultdict(list)
    quantities = {}
    unmarked = set()
    line_products = [[] for _ in lines]
    for index, line in enumerate(lines):
        for item in line['items']:
            pid = item.get('product_id')
            if pid:
                key = (index, pid)
                if key not in quantities:
                    line_products[index].append(pid)
                    product_lines[pid].append(key)
                    quantities[key] = None
                if item.get('quantity') is not None:
                    quantities[key] = (quantities[key] or 0) + item['quantity']
                if not item['codes']:
                    unmarked.add(key)
            for code in item['codes']:
                by_code.setdefault(code, (index, len(by_code)))

    buckets = defaultdict(list)
    overflow = defaultdict(list)
    extra = []

    def source(row):
        return by_code.get(row.get('code')) or by_code.get(row.get('source_code')) or (len(lines), len(by_code))

    seen = set()
    for row in sorted(rows, key=lambda r: (source(r), r.get('code') or '')):
        pid = row.get('product_id')
        index, _ = source(row)
        if index == len(lines) and pid not in product_lines:
            extra.append(row)
            continue
        if row.get('code') and not row.get('is_barcode'):
            mark = MoySkladService._cis_dedup_key(row['code'])
            if mark in seen:
                continue
            seen.add(mark)
        if index == len(lines):
            overflow[pid].append(row)
            continue
        if (index, pid) not in quantities and pid:
            # A previously unmatched box can resolve to a product in the worker.
            line_products[index].append(pid)
            product_lines[pid].append((index, pid))
            quantities[index, pid] = None
        buckets[index, pid].append(row)

    _apply_xml_line_quantities(lines, line_products, buckets, quantities)
    _distribute_product_marks(buckets, quantities, product_lines, overflow)
    for key, quantity in quantities.items():
        if quantity and not buckets[key] and (rows or include_marked or key in unmarked):
            buckets[key].append({'product_id': key[1], 'code': '', 'is_barcode': True,
                                 'is_box': False, 'quantity': quantity})

    result = []
    for (index, pid), group in sorted(buckets.items(), key=lambda pair: pair[0][0]):
        line = lines[index]
        for row in group:
            result.append({**row, 'upd_line': index, 'price': line.get('price'),
                           'vat': line.get('vat'), 'expected_qty': quantities.get((index, pid))})
    remaining_plan = [p for p in plan if p.get('product_id') not in product_lines]
    result.extend(_prepare_product_rows(extra, remaining_plan, meta, include_marked))
    return result


def _apply_xml_line_quantities(lines, line_products, buckets, quantities):
    # КолТов is the financial total even when a box expands into multiple products.
    for index, products in enumerate(line_products):
        total = lines[index].get('expected_qty')
        if not products or total is None:
            continue
        if total == 0 and all(i.get('quantity') is None for i in lines[index]['items']):
            continue  # Older imports also stored a missing КолТов as zero.
        for pid in products[1:]:
            if quantities[index, pid] is None:
                quantities[index, pid] = sum(MoySkladService._scan_units(s) for s in buckets[index, pid])
        remainder = total - sum(quantities[index, pid] for pid in products[1:])
        if remainder < 0:
            raise ValueError('Количество вложенных товаров превышает количество в строке УПД.')
        quantities[index, products[0]] = remainder


def _distribute_product_marks(buckets, quantities, product_lines, overflow):
    remaining = quantities.copy()
    for key, group in list(buckets.items()):
        if remaining.get(key) is None:
            continue
        kept = []
        for row in group:
            if row.get('is_barcode'):
                continue  # Financial quantity already comes from КолТов.
            units = MoySkladService._scan_units(row)
            if units <= remaining[key]:
                kept.append(row)
                remaining[key] -= units
            else:
                overflow[key[1]].append(row)
        buckets[key] = kept

    # Reserve explicitly assigned marks first. Move only the excess, in XML
    # order, so a retry with a different database scan order yields the same CIS.
    for pid, group in overflow.items():
        slots = sorted(key for key in product_lines[pid] if remaining[key] is not None and remaining[key] > 0)
        for row in group:
            if row.get('is_barcode'):
                continue
            units = MoySkladService._scan_units(row)
            key = next((k for k in slots if remaining[k] >= units), None)
            if key is None:
                raise ValueError('Количество марок товара превышает доступное количество в строках УПД. Проверьте марки и количество товара.')
            buckets[key].append(row)
            remaining[key] -= units
            if remaining[key] == 0:
                slots.remove(key)
