"""Quantity-only UPD lines and XML ordering for supply writes."""
from collections import defaultdict

from app.services.chestnyznak import normalize_gtin_key
from app.services.moysklad import MoySkladService
from app.services.plan_matching import unique_plan_product


def scan_code_filter(codes):
    from sqlalchemy import Text, any_, literal, or_
    from sqlalchemy.dialects.postgresql import ARRAY
    from app.db.models import Scan
    values = literal(list(codes), type_=ARRAY(Text()))
    return or_(Scan.code == any_(values), Scan.original_code == any_(values))


def xml_scan_scope(meta):
    lines = (meta or {}).get('physical_plan') or []
    if not lines or 'xml_index' not in lines[0]:
        return ()
    codes = {code for line in lines for item in line['items'] for code in item['codes']}
    codes.update(meta.get('supplemental_codes') or [])
    return (scan_code_filter(codes),)


def prepare_supply_without_cz(plan, meta):
    """Use document quantities, never aggregate/code counts, in the no-CZ flow."""
    plan = plan or []
    lines = (meta or {}).get('physical_plan') or []
    if lines and 'items' in lines[0]:
        return _prepare_xml_rows([], lines, include_marked=True, preflight=False)
    for line in lines:
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


def prepare_supply_rows(rows, plan, meta, *, include_marked=False, preflight=False):
    plan = [p for p in (plan or []) if isinstance(p, dict)]
    meta = meta or {}
    lines = meta.get('physical_plan') or []
    if lines and 'items' in lines[0]:
        return _prepare_xml_rows(rows, lines, include_marked, preflight, meta.get('supplemental_codes', ()))

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


def _prepare_xml_rows(rows, lines, include_marked, preflight, supplemental_codes=()):
    """One financial position per source line, even when marking GTINs differ."""
    by_code = {}
    for index, line in enumerate(lines):
        for item in line['items']:
            for code in item['codes']:
                by_code.setdefault(code, (index, len(by_code)))

    def source(row):
        return by_code.get(row.get('code')) or by_code.get(row.get('source_code')) or (len(lines), len(by_code))

    by_line = defaultdict(list)
    supplemental = []
    supplemental_codes = set(supplemental_codes)
    seen = set()
    for row in sorted(rows, key=lambda r: (source(r), r.get('code') or '')):
        index, _ = source(row)
        if row.get('code') and not row.get('is_barcode'):
            mark = MoySkladService._cis_dedup_key(row['code'])
            if mark in seen:
                continue
            seen.add(mark)
        if index < len(lines):
            by_line[index].append(row)
        elif ('xml_index' not in lines[0] or row.get('code') in supplemental_codes
              or row.get('source_code') in supplemental_codes):
            supplemental.append(row)

    product_lines = defaultdict(list)
    quantities = {}
    buckets = {}
    for index, line in enumerate(lines):
        items = line['items']
        group = by_line[index]
        marked = any(item['codes'] for item in items)
        if preflight and marked and not include_marked:
            continue  # Only count rows eligible for quantity-only submission here.
        products = {pid for pid in [line.get('product_id'), *(i.get('product_id') for i in items),
                                    *(r.get('product_id') for r in group)] if pid}
        number = line.get('line_number') or index + 1
        if len(products) != 1 or (line.get('link_by_line') and not line.get('product_id')):
            raise ValueError(f'Выберите один товар МойСклад для строки УПД {number}.')
        pid = next(iter(products))
        qty = line.get('expected_qty')
        if qty is None and all(i.get('quantity') is not None for i in items):
            qty = sum(i['quantity'] for i in items)
        if qty is None or qty <= 0:
            raise ValueError(f'Проверьте количество товара в строке УПД {number}.')
        key = index, pid
        quantities[key] = qty
        product_lines[pid].append(key)
        buckets[key] = group

    overflow = defaultdict(list)
    for row in supplemental:
        if row.get('product_id') in product_lines:
            overflow[row['product_id']].append(row)
        elif supplemental_codes:
            raise ValueError('Добавленная марка не соответствует ни одной позиции УПД. Проверьте выбранный товар.')
    _distribute_product_marks(buckets, quantities, product_lines, overflow)
    result = []
    for (index, pid), group in buckets.items():
        qty = quantities[index, pid]
        if not group:
            group = [{'product_id': pid, 'code': '', 'is_barcode': True, 'is_box': False, 'quantity': qty}]
        for row in group:
            result.append({**row, 'product_id': pid, 'upd_line': index, 'expected_qty': qty,
                           'price': lines[index].get('price'), 'vat': lines[index].get('vat')})
    return result

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
