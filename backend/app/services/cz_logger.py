"""Логирование всех запросов в ЧЗ — критично для отладки инцидентов."""
from typing import Optional, Any
from app.db.session import AsyncSessionLocal
from app.db.models import CzLog


def _redact(url: str, body: Optional[Any]) -> Optional[Any]:
    """Скрывает signed_data при логировании challenge-обмена.

    Подпись — длинный base64 CAdES-BES блоб. Хранить его в БД незачем,
    при компрометации логов он даёт лишний материал для атаки.
    """
    if body is None or "/auth/cert/" not in url:
        return body
    if isinstance(body, dict) and "data" in body:
        return {**body, "data": "<redacted>"}
    return body


async def log_cz_request(
    method: str,
    url: str,
    request_body: Optional[Any],
    response_status: Optional[int],
    response_body: Optional[Any],
    duration_ms: Optional[int],
    user_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        log = CzLog(
            user_id=user_id,
            request_method=method,
            request_url=url,
            request_body=_redact(url, request_body),
            response_status=response_status,
            response_body=response_body,
            duration_ms=duration_ms,
            error=error,
        )
        db.add(log)
        await db.commit()
