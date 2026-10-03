"""Best-effort committed changes for desktop and document-scoped terminal subscribers."""
import json
import redis.asyncio as aioredis
from app.core.config import settings
from app.core.logging import logger


async def publish_event(user_id, payload):
    redis = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=0.3, socket_timeout=0.3)
    try:
        await redis.publish(f"ws:{user_id}", json.dumps(payload))
    except Exception:
        # A committed scan must remain successful even when live delivery is unavailable.
        logger.warning("scan.live_delivery_failed", document_id=payload.get("document_id"))
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def publish_scan(user_id, scan):
    from app.api.scans import ScanResponse
    await publish_event(user_id, {"type": "scan_upsert", "document_id": str(scan.document_id),
                                 "scan": ScanResponse.model_validate(scan).model_dump(mode="json")})


async def publish_removed(user_id, document_id, scan_id):
    await publish_event(user_id, {"type": "scan_removed", "document_id": str(document_id), "scan_id": str(scan_id)})
