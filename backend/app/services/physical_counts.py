"""Read-only stock preparation and local physical counting, never writes stock to MS."""
import hashlib
from decimal import Decimal
import httpx
from fastapi import HTTPException
from app.services.chestnyznak import normalize_gtin_key, parse_gs1_km_gtin_serial, is_sscc


def acceptance_plan(plan):
    result = []
    for index, value in enumerate(plan or []):
        row = dict(value)
        row['key'] = str(value.get('key') or value.get('product_id') or value.get('gtin') or index)
        row['expected_qty'] = float(value.get('expected_qty') or 0)
        row['base_qty'] = row['expected_qty']
        row['shipment_qty'] = 0
        row['folder_name'] = 'Приёмка'
        # Duplicate positions of one product form one physical count row.
        old = next((v for v in result if v['key'] == row['key']), None)
        if old:
            old['expected_qty'] = float(Decimal(str(old['expected_qty'])) + Decimal(str(row['expected_qty'])))
            old['base_qty'] = old['expected_qty']
            old['gtins'] = sorted(set([*(old.get('gtins') or []), *(row.get('gtins') or []), *([row['gtin']] if row.get('gtin') else [])]))
        else:
            result.append(row)
    return result


def identify_count_scan(plan, code, product_key=None, quantity=1):
    raw = code.strip()
    manual = raw.startswith('MANUAL:')
    if is_sscc(raw):
        raise HTTPException(400, 'Сверьте содержимое короба по маркам или введите количество выбранной позиции. SSCC сам по себе не задаёт количество.')
    gtin, serial = parse_gs1_km_gtin_serial(raw)
    marked = bool(gtin and serial)
    gtin = gtin if marked else normalize_gtin_key(raw) if raw.isdigit() and 6 <= len(raw) <= 14 else None
    matched = [row for row in plan if gtin and gtin in {
        normalize_gtin_key(v) for v in [row.get('gtin'), *(row.get('gtins') or []), *(row.get('pack_gtins') or [])]
    }]
    if product_key:
        selected = next((row for row in plan if row['key'] == product_key), None)
        if not selected:
            raise HTTPException(400, 'Выбранная позиция отсутствует в плане')
        if matched and selected not in matched:
            raise HTTPException(400, 'Код относится к другой позиции')
        matched = [selected]
    if manual and not product_key:
        raise HTTPException(400, 'Выберите позицию для ввода количества')
    if not manual and not gtin:
        raise HTTPException(400, 'Код не распознан. Выберите позицию и введите количество вручную.')
    if len(matched) != 1:
        raise HTTPException(400, 'Товар отсутствует в плане или GTIN неоднозначен. Выберите позицию явно.')
    row = matched[0]
    pack_qty = (row.get('pack_quantities') or {}).get(gtin, 1)
    if marked and any(parse_gs1_km_gtin_serial(value) == (gtin, serial) for value in row.get('package_codes', [])):
        pack_qty = (row.get('pack_quantities') or {}).get(gtin, 0)
    if pack_qty == 0:
        raise HTTPException(400, 'Количество в упаковке неоднозначно. Сверьте поштучно.')
    units = Decimal(str(pack_qty if marked or not manual else 1)) * Decimal(str(quantity))
    if marked and Decimal(str(quantity)) != 1:
        raise HTTPException(400, 'Для уникальной марки количество изменять нельзя')
    identity = f'01{gtin}21{serial}' if marked else raw
    return row, units, identity, marked, hashlib.sha256(identity.encode()).hexdigest()


async def prepare_inventory_plan(ms, store_id, state_ids):
    """One warehouse snapshot + paginated catalog and applied demands of selected states."""
    stock = await ms.get_stock_map([f'{ms.base_url}/entity/store/{store_id}'], group_by='variant', full_snapshot=True)
    products, demands = {}, []
    async with httpx.AsyncClient(timeout=60) as client:
        async def pages(path, params, limit=1000):
            rows, offset = [], 0
            while True:
                response = await ms._request_with_retry(client, 'GET', f'{ms.base_url}/{path}', params={**params, 'limit': limit, 'offset': offset})
                response.raise_for_status()
                page = response.json().get('rows', [])
                rows.extend(page)
                if len(page) < limit:
                    return rows
                offset += len(page)
        catalog = await pages('entity/product', {'filter': 'archived=false;archived=true'})
        variants = await pages('entity/variant', {'filter': 'archived=false;archived=true'})
        catalog_by_id = {v['id']: v for v in catalog}
        for variant in variants:
            parent = variant.get('product') or {}
            parent = catalog_by_id.get(parent.get('id') or ms._id_from_href(parent.get('meta', {}).get('href', '')), {})
            catalog.append({**variant, 'pathName': parent.get('pathName') or variant.get('pathName'),
                            'productFolder': parent.get('productFolder') or variant.get('productFolder') or {}})
        for item in catalog:
            pid = item['id']
            aliases = [normalize_gtin_key(v) for barcode in item.get('barcodes', []) for v in barcode.values()]
            packs = {}
            for pack in item.get('packs', []):
                for barcode in pack.get('barcodes', []):
                    for value in barcode.values():
                        key = normalize_gtin_key(value)
                        if key:
                            qty = float(pack.get('quantity') or 1)
                            packs[key] = qty if key not in packs or packs[key] == qty else 0
            products[pid] = {'key': pid, 'product_id': pid, 'product_name': item.get('name') or pid,
                'gtins': sorted(set(v for v in aliases if v)), 'pack_gtins': list(packs), 'pack_quantities': packs,
                'folder_name': item.get('pathName') or 'Без бренда',
                'folder_id': ms._id_from_href((item.get('productFolder', {}).get('meta') or {}).get('href', '')),
                'base_qty': float(stock.get(pid, {}).get('qty') or 0), 'shipment_qty': 0.0}
        if state_ids:
            # The stock report covers the entire warehouse, across organizations.
            # Add-backs must use that same scope to avoid understated physical stock.
            filters = ['applied=true', f'store={ms.base_url}/entity/store/{store_id}']
            filters.extend(f'state={ms.base_url}/entity/demand/metadata/states/{sid}' for sid in state_ids)
            demands = await pages('entity/demand', {'filter': ';'.join(filters), 'expand': 'positions.assortment'}, limit=100)
        seen_demands = set()
        for demand in demands:
            if demand['id'] in seen_demands:
                continue
            seen_demands.add(demand['id'])
            # Defense against unintended/different-account rows and unapplied documents.
            ref = lambda value: value.get('id') or ms._id_from_href(value.get('meta', {}).get('href', ''))
            if demand.get('applied') is not True or ref(demand.get('store') or {}) != store_id or ref(demand.get('state') or {}) not in state_ids:
                continue
            positions = demand.get('positions') or {}
            rows = positions.get('rows') or []
            if positions.get('meta', {}).get('size', len(rows)) > len(rows) or not rows:
                rows = await ms._load_positions_rows('demand', demand['id'])
            for position in rows:
                assortment = position.get('assortment') or {}
                pid = ref(assortment)
                if not pid or (assortment.get('meta', {}).get('type') or 'product') == 'service':
                    continue
                if pid not in products:
                    products[pid] = {'key': pid, 'product_id': pid, 'product_name': assortment.get('name') or pid,
                        'gtins': [], 'folder_name': assortment.get('pathName') or 'Без бренда',
                        'base_qty': float(stock.get(pid, {}).get('qty') or 0), 'shipment_qty': 0.0}
                products[pid]['shipment_qty'] = float(Decimal(str(products[pid]['shipment_qty'])) + Decimal(str(position.get('quantity') or 0)))
    for pid, value in stock.items():
        if pid not in products:
            products[pid] = {'key': pid, 'product_id': pid, 'product_name': value.get('product_name') or pid,
                'gtins': [], 'folder_name': value.get('folder_name') or 'Без бренда', 'base_qty': float(value.get('qty') or 0), 'shipment_qty': 0.0}
    for row in products.values():
        row['expected_qty'] = float(Decimal(str(row['base_qty'])) + Decimal(str(row['shipment_qty'])))
    return sorted(products.values(), key=lambda row: (row['folder_name'], row['product_name']))
