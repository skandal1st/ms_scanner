"""Persistent incident cards and an at-least-once ERP delivery outbox."""
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.diagnostics import current_operation, sanitize, safe_text
from app.core.logging import logger
from app.db import session
from app.db.models import Document, Incident, MonitoringDelivery


def fingerprint(document_id, event, reason):
    return hashlib.sha256(f'{document_id}:{event}:{reason}'.encode()).hexdigest()


def event_payload(incident, event):
    return {'events': [{'event': event, 'source': 'worker', 'level': 'info' if event.endswith('resolved') else 'error',
        'ts': datetime.now(timezone.utc).isoformat(), 'trace_id': str(incident.trace_id),
        'attributes': {'incident_id': str(incident.id), 'document_id': str(incident.document_id),
            'kind': incident.kind, 'stage': incident.stage, 'reason': incident.reason,
            'message': incident.message, 'occurrences': incident.occurrences,
            'app_version': incident.app_version, 'diagnostics': incident.diagnostics}}]}


async def record(event, message, *, reason=None, details=None):
    """Failure to record diagnostics must never change a warehouse operation's outcome."""
    operation = current_operation.get()
    if operation is None:
        return None
    try:
        async with session.AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL statement_timeout = '5s'"))
            doc = (await db.execute(select(Document).where(Document.id == UUID(operation.document_id),
                         Document.user_id == UUID(operation.user_id)))).scalar_one_or_none()
            if doc is None:
                return None
            reason = reason or event
            key = fingerprint(operation.document_id, f'{event}:{operation.stage}', reason)
            now = datetime.now(timezone.utc)
            # Failed requests must survive snapshot limits even after many successful batches.
            evidence = sorted(operation.requests,
                key=lambda item: not (item.get('error') or (item.get('status') or 0) >= 400))
            diagnostics = sanitize({'requests': evidence,
                'dropped_requests': operation.dropped_requests, 'details': details})
            incident_id = uuid4()
            inserted = (await db.execute(insert(Incident).values(id=incident_id, fingerprint=key,
                document_id=doc.id, user_id=doc.user_id, kind=doc.kind.value,
                stage=operation.stage, reason=reason, message=safe_text(message), status='open',
                trace_id=UUID(operation.trace_id), app_version=settings.APP_VERSION,
                diagnostics=diagnostics, occurrences=1, created_at=now, last_seen_at=now)
                .on_conflict_do_nothing(index_elements=['fingerprint']).returning(Incident.id))).scalar_one_or_none()
            incident = (await db.execute(select(Incident).where(Incident.fingerprint == key)
                                         .with_for_update())).scalar_one()
            notify = inserted is not None or incident.status == 'resolved'
            if inserted is None:
                incident.occurrences += 1
                incident.status = 'open'
                incident.resolved_at = None
                incident.last_seen_at = now
                incident.message = safe_text(message)
                incident.trace_id = UUID(operation.trace_id)
                incident.app_version = settings.APP_VERSION
                incident.diagnostics = diagnostics
            if notify:
                db.add(MonitoringDelivery(id=uuid4(), incident_id=incident.id,
                    payload=event_payload(incident, 'incident.opened'), next_attempt_at=now))
            if doc.error_message and doc.status.value != 'accepted':
                base_message = doc.error_message.split('\nКод ошибки:')[0]
                doc.error_message = f'{base_message}\nКод ошибки: {incident.id}'
            await db.commit()
            logger.warning('incident.saved', incident_id=str(incident.id), document_id=operation.document_id,
                           reason=reason, occurrences=incident.occurrences)
            return str(incident.id)
    except Exception as exc:
        logger.warning('incident.save_failed', error_type=type(exc).__name__)
        return None


async def resolve_current(*, import_only=False):
    operation = current_operation.get()
    if operation is None:
        return
    try:
        async with session.AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL statement_timeout = '5s'"))
            query = select(Incident).where(
                Incident.document_id == UUID(operation.document_id), Incident.user_id == UUID(operation.user_id),
                Incident.status == 'open')
            if import_only:
                query = query.where(Incident.stage == 'acceptance.import_upd')
            rows = (await db.execute(query.with_for_update())).scalars().all()
            now = datetime.now(timezone.utc)
            for incident in rows:
                incident.status = 'resolved'
                incident.resolved_at = now
                db.add(MonitoringDelivery(id=uuid4(), incident_id=incident.id,
                    payload=event_payload(incident, 'incident.resolved'), next_attempt_at=now))
            await db.commit()
    except Exception as exc:
        logger.warning('incident.resolve_failed', error_type=type(exc).__name__)


async def deliver_pending(limit=20):
    from app.core.monitoring import _enabled, send_payload
    if not _enabled():
        return 0
    delivered = 0
    for _ in range(limit):
        async with session.AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL statement_timeout = '5s'"))
            now = datetime.now(timezone.utc)
            item = (await db.execute(select(MonitoringDelivery).where(
                MonitoringDelivery.delivered_at.is_(None), MonitoringDelivery.next_attempt_at <= now)
                .order_by(MonitoringDelivery.created_at).limit(1)
                .with_for_update(skip_locked=True))).scalar_one_or_none()
            if item is None:
                break
            item.attempts += 1
            try:
                await send_payload(item.payload, delivery_id=str(item.id))
                item.delivered_at = now
                item.last_error = None
                delivered += 1
            except Exception as exc:
                # Do not retain transport exception strings: they can contain credentials in URLs.
                item.last_error = type(exc).__name__
                item.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(item.attempts, 7)))
                logger.warning('incident.delivery_failed', delivery_id=str(item.id), attempts=item.attempts,
                               error_type=type(exc).__name__)
            await db.commit()
    return delivered
