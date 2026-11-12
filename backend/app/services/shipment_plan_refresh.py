"""Refresh only the local shipment plan; preserve scans and verified CZ facts."""
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import Scan, ScanStatus
from app.services.document_guard import editable_document
from app.services.plan_matching import unique_plan_product, plan_gtin_keys
from app.services.shipment_guard import ensure_active_shipment
from app.services.moysklad import shipment_order_id
from app.services.shipment_corrections import entity_id
from app.services.shipment_labels import update_shipment_metadata
from app.services.scan_events import publish_event


def index_plan(plan):
    result = {}
    for p in plan or []:
        key = p.get('product_id') or ('gtin:' + (p.get('gtin') or ''))
        row = result.setdefault(key, {**p, 'expected_qty': 0, 'aliases': set()})
        row['expected_qty'] += p.get('expected_qty') or 0
        row['aliases'].update(plan_gtin_keys(p))
    return result


def changes(before, after):
    old, new = index_plan(before), index_plan(after)
    result = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        kind = 'added' if a is None else 'removed' if b is None else (
            'quantity' if a['expected_qty'] != b['expected_qty'] else 'details')
        if a and b and all(a.get(k) == b.get(k) for k in ['expected_qty','product_name','aliases','marked','pack_quantities']):
            continue
        row = b or a
        result.append({'kind': kind, 'product_id': row.get('product_id'), 'product_name': row.get('product_name'),
                       'before': a['expected_qty'] if a else 0, 'after': b['expected_qty'] if b else 0})
    return result


def reclassify(plan, scans):
    indexed, used = index_plan(plan), defaultdict(int)
    for s in sorted(scans, key=lambda item: (item.scanned_at, str(item.id))):
        if s.status not in {ScanStatus.valid, ScanStatus.overflow}:
            continue
        product = s.moysklad_product_id
        if not product:
            match = unique_plan_product(plan, s.gtin)
            product = match.get('product_id') if match else None
        row = indexed.get(product)
        overflow = False
        if row:
            used[product] += int(s.box_quantity or 1)
            overflow = used[product] > row['expected_qty']
        s.status = ScanStatus.overflow if overflow else ScanStatus.valid
        if overflow:
            s.error_message = f"Сверх плана: ожидалось {row['expected_qty']}, отсканировано {used[product]}"
        elif (s.error_message or '').startswith('Сверх плана:'):
            s.error_message = None


async def refresh(db, document_id, user_id, ms):
    started = datetime.now(timezone.utc)
    doc = await editable_document(db, document_id, user_id)
    if doc.kind.value != 'demand' or not doc.moysklad_id:
        raise HTTPException(400, 'Пересверка доступна для отгрузки МойСклад.')
    source_id = doc.moysklad_id
    saved_order = doc.moysklad_customer_order_id
    await db.rollback()  # Scanning on other devices must not wait for remote IO.
    try:
        shipment = await ms.get_document('demand', source_id)
        ensure_active_shipment(shipment)
        order_id = await shipment_order_id(ms, shipment, saved_order)
        fresh = await ms.build_plan('demand', source_id)
        if not fresh:
            raise ValueError('В отгрузке МС больше нет товарных позиций. Марки сохранены, прежний план не изменён. Проверьте отгрузку в МС.')
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, 'Не удалось прочитать актуальную отгрузку МС. Сохранённый план не изменён.') from exc
    doc = await editable_document(db, document_id, user_id)
    if doc.moysklad_id != source_id:
        raise HTTPException(409, 'Привязка отгрузки изменилась. Откройте сборку заново.')
    last = ((doc.upd_meta or {}).get('plan_sync') or {}).get('checked_at')
    if last and datetime.fromisoformat(last) > started:
        raise HTTPException(409, 'План уже обновили на другом устройстве. Обновите экран и при необходимости повторите пересверку.')
    if doc.moysklad_customer_order_id != saved_order or (saved_order and saved_order != order_id):
        raise HTTPException(409, 'Связанный заказ изменился. Марки сохранены; откройте актуальную отгрузку из заказа.')
    for field, remote in [('moysklad_organization_id','organization'),('moysklad_store_id','store')]:
        if getattr(doc, field) and getattr(doc, field) != entity_id(shipment.get(remote)):
            raise HTTPException(409, 'В МС изменились юрлицо, склад или связанный заказ. Марки сохранены; откройте актуальную отгрузку из заказа.')
    summary = {'checked_at': datetime.now(timezone.utc).isoformat(), 'changes': changes(doc.plan, fresh)}
    scans = (await db.execute(select(Scan).where(Scan.document_id == doc.id).with_for_update())).scalars().all()
    reclassify(fresh, scans)
    doc.plan = fresh
    doc.moysklad_customer_order_id = order_id
    doc.upd_meta = {**(doc.upd_meta or {}), 'plan_sync': summary}
    update_shipment_metadata(doc, shipment)
    await db.commit()
    await publish_event(user_id, {'type': 'plan_changed', 'document_id': str(document_id)})
    return doc
