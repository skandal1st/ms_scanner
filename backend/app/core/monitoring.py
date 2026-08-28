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
) -> None:
    """Отправить одно событие. Проглатывает любые ошибки — вызывать без try/except."""
    if not _enabled():
        return
    clean_attrs = {k: v for k, v in attributes.items() if v is not None}
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
    url = settings.MONITORING_URL.rstrip("/") + "/ingest"
    try:
        async with httpx.AsyncClient(timeout=settings.MONITORING_TIMEOUT) as client:
            await client.post(
                url,
                json=payload,
                headers={
                    "X-Project": settings.MONITORING_PROJECT,
                    "X-Api-Key": settings.MONITORING_KEY,
                },
            )
    except Exception as exc:  # noqa: BLE001 — мониторинг не должен влиять на основной поток
        # ВНИМАНИЕ: у structlog `event` — зарезервированное имя (само сообщение),
        # передавать его kwargʼом нельзя (TypeError). Используем event_name.
        logger.warning("monitoring.emit_failed", event_name=event, error=str(exc))
