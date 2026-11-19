"""Short-lived COM frame and HTTP payload pairs for document scans."""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import logger


class SerialCapture(BaseModel):
    serial_raw_hex: Annotated[str, Field(max_length=8192, pattern=r'^(?:[0-9a-fA-F]{2})+$')] | None = None


class SerialAttempt(BaseModel):
    received_at: datetime
    raw_hex: str
    received_code: str


def _key(document_id: UUID, code: str) -> str:
    digest = hashlib.sha256(code.encode()).hexdigest()
    return f'scan:serial:{document_id}:{digest}'


def _redis():
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)


async def record(document_id: UUID, code: str, raw_hex: str | None, lookup_code: str):
    if not raw_hex or len(code) > 8192:
        return
    key = _key(document_id, lookup_code)
    attempt = SerialAttempt(received_at=datetime.now(timezone.utc), raw_hex=raw_hex, received_code=code)
    try:
        async with _redis() as client, client.pipeline(transaction=True) as pipe:
            pipe.lpush(key, attempt.model_dump_json())
            pipe.ltrim(key, 0, 9)
            pipe.expire(key, 7 * 24 * 3600)
            await pipe.execute()
    except (RedisError, OSError):
        logger.warning('scan.serial_diagnostics.unavailable', document_id=str(document_id))


async def attempts(document_id: UUID, code: str):
    async with _redis() as client:
        rows = await client.lrange(_key(document_id, code), 0, 9)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = (SerialAttempt.model_validate_json(row) for row in rows)
    return [attempt for attempt in recent if attempt.received_at >= cutoff]
