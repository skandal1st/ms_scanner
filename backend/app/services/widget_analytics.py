"""Publish scanner facts in the same transaction as the successful operation."""
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings
from app.db.models import MonitoringDelivery


def enqueue(db, integration, kind, event_id=None):
    if not settings.MONITORING_ENABLED or not integration.moysklad_account_id or not integration.moysklad_app_id:
        return
    now = datetime.now(timezone.utc)
    delivery_id = uuid4()
    attrs = {"appId": integration.moysklad_app_id, "accountId": integration.moysklad_account_id,
             "eventId": str(event_id or delivery_id)}
    event = {"event": "widget." + kind, "source": "scanner", "ts": now.isoformat(), "attributes": attrs}
    db.add(MonitoringDelivery(id=delivery_id, payload={"events": [event]}, next_attempt_at=now))
