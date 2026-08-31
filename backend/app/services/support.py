"""Клиент техподдержки: создание тикета (обращения) в AXIMA ERP.

В отличие от мониторинга (`app.core.monitoring`, fire-and-forget) — это синхронный
пользовательский флоу: кладовщик жмёт «Написать в поддержку», ждёт результат и должен
понять, создано обращение или нет. Поэтому здесь мы НЕ проглатываем ошибки, а поднимаем
`SupportError` с человекочитаемым русским текстом — его показывает фронт.

Транспорт — HTTP POST на внешний endpoint AXIMA `POST /api/v1/it/tickets/external`
(`SUPPORT_ERP_URL`) с заголовками X-Project / X-Api-Key (тот же механизм ключей проекта,
что и у мониторинга — MONITORING_INGEST_KEYS на стороне ERP). На той стороне создаётся
IT-заявка (source=api); ответы по смене статуса уходят на reporter_email кладовщика.
Схема тела совпадает с `ExternalTicketIn` в модуле it/routes/tickets.py ERP.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger


class SupportError(Exception):
    """Ошибка создания тикета — сообщение уже на русском, годится для показа юзеру."""


def is_enabled() -> bool:
    return bool(
        settings.SUPPORT_ERP_ENABLED
        and settings.SUPPORT_ERP_URL
        and settings.SUPPORT_ERP_KEY
    )


def _build_payload(
    *,
    subject: str,
    message: str,
    category: str,
    category_label: Optional[str],
    reporter_email: Optional[str],
    reporter_user_id: Optional[str],
    ms_account: Optional[str],
    page: Optional[str],
    app_version: str,
) -> dict:
    """Тело обращения — схема `ExternalTicketIn` внешнего endpoint AXIMA ERP."""
    return {
        "subject": subject,
        "message": message,
        "category": category,
        "category_label": category_label,
        "priority": "medium",
        "reporter_email": reporter_email,
        "reporter_name": ms_account,
        "reporter_user_id": reporter_user_id,
        "page": page,
        "app_version": app_version,
    }


def _extract_ref(data: object) -> Optional[str]:
    """Достать ID тикета из ответа AXIMA (`ExternalTicketOut`: {ok, ticket_id})."""
    if not isinstance(data, dict):
        return None
    for key in ("ticket_id", "id", "number", "ref"):
        val = data.get(key)
        if val:
            return str(val)
    return None


async def create_ticket(
    *,
    subject: str,
    message: str,
    category: str = "general",
    category_label: Optional[str] = None,
    reporter_email: Optional[str] = None,
    reporter_user_id: Optional[str] = None,
    ms_account: Optional[str] = None,
    page: Optional[str] = None,
    app_version: str = "1.0.0",
) -> Optional[str]:
    """Создать тикет в AXIMA ERP. Возвращает номер/ID тикета (если ERP его отдал).

    Бросает `SupportError` при выключенной интеграции или сбое отправки.
    """
    if not is_enabled():
        raise SupportError(
            "Отправка в техподдержку временно недоступна. "
            f"Напишите нам на {settings.SUPPORT_EMAIL}."
        )

    payload = _build_payload(
        subject=subject,
        message=message,
        category=category,
        category_label=category_label,
        reporter_email=reporter_email,
        reporter_user_id=reporter_user_id,
        ms_account=ms_account,
        page=page,
        app_version=app_version,
    )
    try:
        async with httpx.AsyncClient(timeout=settings.SUPPORT_TIMEOUT) as client:
            resp = await client.post(
                settings.SUPPORT_ERP_URL,
                json=payload,
                headers={
                    "X-Project": settings.MONITORING_PROJECT,
                    "X-Api-Key": settings.SUPPORT_ERP_KEY,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "support.ticket_failed",
            status=exc.response.status_code,
            error=str(exc),
        )
        raise SupportError(
            "Не удалось создать обращение (ошибка ERP). "
            f"Попробуйте позже или напишите на {settings.SUPPORT_EMAIL}."
        )
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: единый понятный текст юзеру
        logger.warning("support.ticket_failed", error=str(exc))
        raise SupportError(
            "Не удалось связаться с техподдержкой (сеть недоступна). "
            f"Напишите нам на {settings.SUPPORT_EMAIL}."
        )

    ref: Optional[str] = None
    try:
        ref = _extract_ref(resp.json())
    except Exception:  # noqa: BLE001 — ответ не JSON: тикет создан, номер неизвестен
        ref = None
    logger.info("support.ticket_created", ref=ref, user_id=reporter_user_id)
    return ref
