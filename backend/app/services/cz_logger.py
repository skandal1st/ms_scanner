"""Логирование всех запросов в ЧЗ — критично для отладки инцидентов."""
from typing import Optional, Any
from app.db.session import AsyncSessionLocal
from app.db.models import CzLog
from app.core.diagnostics import sanitize, safe_url, current_operation, capture_request
from app.core.logging import logger
from uuid import UUID


def _redact(url: str, body: Optional[Any]) -> Optional[Any]:
    """Скрывает signed_data при логировании challenge-обмена.

    Подпись — длинный base64 CAdES-BES блоб. Хранить его в БД незачем,
    при компрометации логов он даёт лишний материал для атаки.
    """
    if isinstance(body, dict) and '/auth/cert/' in url and 'data' in body:
        body = {**body, 'data': '<redacted>'}
    return sanitize(body)


async def log_cz_request(
    method: str,
    url: str,
    request_body: Optional[Any],
    response_status: Optional[int],
    response_body: Optional[Any],
    duration_ms: Optional[int],
    user_id: Optional[str] = None,
    error: Optional[str] = None,
    original_code: Optional[str] = None,
) -> None:
    operation = current_operation.get()
    request_body = _redact(url, request_body)
    response_body = _redact(url, response_body)
    capture_request('chestnyznak', method, url, request_body, response_status, response_body, error,
                    original_code=original_code)
    try:
        async with AsyncSessionLocal() as db:
            log = CzLog(
                user_id=UUID(user_id or operation.user_id) if user_id or operation else None,
                request_method=method, request_url=safe_url(url), request_body=request_body,
                response_status=response_status, response_body=response_body, duration_ms=duration_ms,
                error=sanitize(error), trace_id=UUID(operation.trace_id) if operation else None,
                document_id=UUID(operation.document_id) if operation else None,
                original_code=sanitize(original_code))
            db.add(log)
            await db.commit()
    except Exception as exc:
        logger.warning('cz_log.save_failed', error_type=type(exc).__name__)
