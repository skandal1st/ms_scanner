"""Клиент мониторинга: отправка бизнес-событий и таймингов в ERP (Elements Platform).

Инвариант: НИКОГДА не тормозит и не роняет хост-приложение. Любая ошибка отправки
(таймаут, недоступность ERP, 5xx) — тихий warning в лог, не исключение. Отправка
best-effort с коротким таймаутом; отключается флагом MONITORING_ENABLED.

Транспорт — HTTP POST на {MONITORING_URL}/ingest c заголовками X-Project/X-Api-Key.
Схема события совпадает с ERP monitoring.EventIn.
"""

from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.core.diagnostics import sanitize, current_operation


async def send_payload(payload, *, delivery_id=None):
    headers = {'X-Project': settings.MONITORING_PROJECT, 'X-Api-Key': settings.MONITORING_KEY}
    if delivery_id:
        headers['X-Idempotency-Key'] = delivery_id
        payload = {'events': [{**event, 'attributes': {**(event.get('attributes') or {}),
                    'delivery_id': delivery_id}} for event in payload['events']]}
    async with httpx.AsyncClient(timeout=settings.MONITORING_TIMEOUT) as client:
        response = await client.post(settings.MONITORING_URL.rstrip('/') + '/ingest',
                                     json=payload, headers=headers)
        response.raise_for_status()
        receipt = response.json()
        if not isinstance(receipt, dict) or receipt.get('accepted') != len(payload['events']):
            raise RuntimeError('ERP не подтвердила приём всех событий мониторинга')


def _enabled() -> bool:
    return bool(
        settings.MONITORING_ENABLED
        and settings.MONITORING_URL
        and settings.MONITORING_KEY
    )


async def emit(
    event: str,
    *,
    source: str = "worker",
    level: str = "info",
    duration_ms: Optional[int] = None,
    trace_id: Optional[str] = None,
    **attributes,
) -> Optional[str]:
    """Отправить одно событие. Проглатывает любые ошибки — вызывать без try/except."""
    if current_operation.get() is not None:
        from app.services.incidents import record, resolve_current
        if level in {'warning', 'error'}:
            return await record(event, attributes.get('error') or attributes.get('message') or event,
                                details=attributes)
        if event == 'process_document.done':
            await resolve_current()
    if not _enabled():
        return
    operation = current_operation.get()
    trace_id = trace_id or (operation.trace_id if operation else None)
    clean_attrs = sanitize({k: v for k, v in attributes.items() if v is not None})
    payload = {
        "events": [
            {
                "event": event,
                "source": source,
                "level": level,
                "ts": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "trace_id": trace_id,
                "attributes": clean_attrs or None,
            }
        ]
    }
    try:
        await send_payload(payload)
    except Exception as exc:  # noqa: BLE001 — мониторинг не должен влиять на основной поток
        # ВНИМАНИЕ: у structlog `event` — зарезервированное имя (само сообщение),
        # передавать его kwargʼом нельзя (TypeError). Используем event_name.
        logger.warning("monitoring.emit_failed", event_name=event, error_type=type(exc).__name__)
