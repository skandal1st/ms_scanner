"""Apply shipment date and configured MS statuses once on collection start."""
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Document, DocumentKind, DocumentStatus, OrganizationProfile
from app.services.document_guard import processing_lock
from app.services.shipment_guard import ensure_active_shipment
from app.services.collection_time import shipment_moment, resolve_collection_timezone


def collection_started(doc):
    return bool(((getattr(doc, 'upd_meta', None) or {}).get('collection_start') or {}).get('done'))


def require_collection_started(doc):
    if (getattr(doc, 'kind', None) == DocumentKind.demand
            and getattr(doc, 'moysklad_id', None) and not collection_started(doc)):
        raise HTTPException(409, 'Сначала нажмите «Начать сборку».')


async def start_collection(db, doc, ms, *, client_timezone=None, client_utc_offset_minutes=None):
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
        completed = next((d for d in siblings if collection_started(d)), None)
        if completed:
            if not collection_started(doc):
                doc.upd_meta = {**(doc.upd_meta or {}), 'collection_start': dict(completed.upd_meta['collection_start'])}
                await db.commit()
            return
        owner = next((d for d in siblings if (d.upd_meta or {}).get('collection_start')), doc)
        profile = (await db.execute(select(OrganizationProfile).where(
            OrganizationProfile.id == doc.organization_profile_id,
            OrganizationProfile.user_id == doc.user_id,
        ))).scalar_one_or_none()
        date_enabled = bool(profile and profile.shipment_date_on_collection_start)
        marker = (owner.upd_meta or {}).get('collection_start')
        if marker:
            marker = dict(marker)
            # Disabling the setting cancels any pending date write. Never add a
            # date to a collection that already began without one.
            if not date_enabled or not marker.get('shipment_moment'):
                marker['moment_done'] = True
        else:
            started_at = datetime.now(timezone.utc)
            marker = {'started_at': started_at.isoformat(),
                      'shipment_state': str(profile.shipment_start_state_id) if profile and profile.shipment_start_state_id else None,
                      'order_state': str(profile.customer_order_start_state_id) if profile and profile.customer_order_start_state_id else None,
                      'moment_done': not date_enabled}
            if date_enabled:
                timezone_name = profile.shipment_timezone or client_timezone
                try:
                    zone = resolve_collection_timezone(timezone_name, client_utc_offset_minutes)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                marker['shipment_timezone'] = str(zone)
                marker['shipment_moment'] = shipment_moment(started_at, zone)

        async def save():
            owner.upd_meta = {**(owner.upd_meta or {}), 'collection_start': dict(marker)}
            await db.commit()

        try:
            expected_order = getattr(doc, 'moysklad_customer_order_id', None) or marker.get('order_id')
            if expected_order or not marker.get('moment_done') or marker['shipment_state'] or marker['order_state']:
                shipment = await ms.get_document('demand', doc.moysklad_id)
                ensure_active_shipment(shipment)
                if expected_order or marker['order_state']:
                    from app.services.moysklad import shipment_order_id
                    marker['order_id'] = await shipment_order_id(ms, shipment, expected_order,
                                                               require_invoice_order=bool(marker['order_state']))
                    doc.moysklad_customer_order_id = marker['order_id']
                if marker['shipment_state'] and not marker.get('shipment_done'):
                    if marker['shipment_state'] not in {s['id'] for s in await ms.get_shipment_states()}:
                        raise ValueError('Статус начала сборки отгрузки больше недоступен. Проверьте настройки.')
                if marker['order_state'] and not marker.get('order_done'):
                    if marker['order_state'] not in {s['id'] for s in await ms.get_customer_order_states()}:
                        raise ValueError('Статус начала сборки заказа больше недоступен. Проверьте настройки.')
            await save()
            # One PUT applies date and shipment status together. Retried calls use
            # the persisted date even after midnight or from a different terminal.
            if not marker.get('moment_done') or (marker['shipment_state'] and not marker.get('shipment_done')):
                target = marker['shipment_state'] if not marker.get('shipment_done') else None
                date_fields = {'moment': marker['shipment_moment']} if not marker.get('moment_done') else {}
                await ms.change_collection_state('demand', doc.moysklad_id, target, **date_fields)
                marker['shipment_done'] = True
                marker['moment_done'] = True
                await save()
            if marker['order_state'] and marker.get('order_id') and not marker.get('order_done'):
                await ms.change_collection_state('customerorder', marker['order_id'], marker['order_state'])
                marker['order_done'] = True
                await save()
            marker['done'] = True
            await save()
            if owner.id != doc.id:
                doc.upd_meta = {**(doc.upd_meta or {}), 'collection_start': dict(marker)}
                await db.commit()
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(502, 'Не удалось изменить дату или статусы при начале сборки. Проверьте настройки и права решения в МойСкладе, затем нажмите «Начать сборку» повторно. ' + (str(exc) if isinstance(exc, ValueError) else '')) from exc
