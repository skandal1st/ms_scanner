"""Apply configured MS statuses once when a shipment is opened for collection."""
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Document, DocumentKind, DocumentStatus, OrganizationProfile
from app.services.document_guard import processing_lock
from app.services.shipment_guard import ensure_active_shipment


async def start_collection(db, doc, ms):
    if doc.kind != DocumentKind.demand or not doc.moysklad_id:
        return
    async with processing_lock(f'ms:{doc.user_id}:demand:{doc.moysklad_id}', wait=True) as acquired:
        if not acquired:
            raise HTTPException(409, 'Отгрузка сейчас открывается или отправляется другим пользователем. Повторите открытие.')
        await db.refresh(doc)
        if doc.status != DocumentStatus.draft:
            raise HTTPException(409, 'Отгрузка уже отправлена или обрабатывается.')
        if (doc.upd_meta or {}).get('superseded_by_document_id'):
            raise HTTPException(409, 'Марки перенесены в действующую отгрузку. Откройте её из заказа покупателя.')
        # Also covers old duplicate local documents for the same MS shipment.
        siblings = (await db.execute(select(Document).where(
            Document.user_id == doc.user_id, Document.kind == DocumentKind.demand,
            Document.moysklad_id == doc.moysklad_id,
        ))).scalars().all()
        if any(((d.upd_meta or {}).get('collection_start') or {}).get('done') for d in siblings):
            return
        owner = next((d for d in siblings if (d.upd_meta or {}).get('collection_start')), doc)
        marker = (owner.upd_meta or {}).get('collection_start')
        if not marker:
            profile = (await db.execute(select(OrganizationProfile).where(
                OrganizationProfile.id == doc.organization_profile_id,
                OrganizationProfile.user_id == doc.user_id,
            ))).scalar_one_or_none()
            marker = {'started_at': datetime.now(timezone.utc).isoformat(),
                      'shipment_state': str(profile.shipment_start_state_id) if profile and profile.shipment_start_state_id else None,
                      'order_state': str(profile.customer_order_start_state_id) if profile and profile.customer_order_start_state_id else None}

        async def save():
            owner.upd_meta = {**(owner.upd_meta or {}), 'collection_start': dict(marker)}
            await db.commit()

        try:
            if marker['shipment_state'] or marker['order_state']:
                shipment = await ms.get_document('demand', doc.moysklad_id)
                ensure_active_shipment(shipment)
                if marker['shipment_state'] and not marker.get('shipment_done'):
                    if marker['shipment_state'] not in {s['id'] for s in await ms.get_shipment_states()}:
                        raise ValueError('Статус начала сборки отгрузки больше недоступен. Проверьте настройки.')
                if marker['order_state'] and not marker.get('order_done'):
                    if marker['order_state'] not in {s['id'] for s in await ms.get_customer_order_states()}:
                        raise ValueError('Статус начала сборки заказа больше недоступен. Проверьте настройки.')
                    marker['order_id'] = await ms.resolve_shipment_order(shipment, doc.moysklad_customer_order_id)
            await save()
            for key, kind, entity_id in [('shipment', 'demand', doc.moysklad_id),
                                         ('order', 'customerorder', marker.get('order_id'))]:
                target = marker[f'{key}_state']
                if target and entity_id and not marker.get(f'{key}_done'):
                    await ms.change_collection_state(kind, entity_id, target)
                    marker[f'{key}_done'] = True
                    await save()
            marker['done'] = True
            await save()
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(502, 'Не удалось изменить статусы при начале сборки. Проверьте настройки и права решения в МойСкладе, затем откройте отгрузку повторно. ' + (str(exc) if isinstance(exc, ValueError) else '')) from exc
