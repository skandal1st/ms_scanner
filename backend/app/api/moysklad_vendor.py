import json
import time
from typing import Any, List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.security import encrypt_token
from app.db.models import Integration, User
from app.db.session import get_db


router = APIRouter(prefix="/moysklad/vendor/1.0/apps", tags=["moysklad-vendor"])


class AccessItem(BaseModel):
    resource: str
    scope: List[str] = []
    access_token: Optional[str] = None
    permissions: Optional[dict] = None


class Subscription(BaseModel):
    tariffId: Optional[str] = None
    trial: Optional[bool] = None
    tariffName: Optional[str] = None
    expiryMoment: Optional[str] = None
    notForResale: Optional[bool] = None
    partner: Optional[bool] = None


class ActivateRequest(BaseModel):
    appUid: str
    accountName: Optional[str] = None
    cause: str
    access: Optional[List[AccessItem]] = None
    subscription: Optional[Subscription] = None
    additional: Optional[dict] = None


class StatusResponse(BaseModel):
    status: str  # Activating | SettingsRequired | Activated


async def _verify_vendor_jwt(authorization: Optional[str]) -> dict[str, Any]:
    """
    Проверяет JWT подписанный secretKey решения. См.
    dev.moysklad.ru/doc/api/vendor/1.0 — раздел "Аутентификация".
    """
    if not settings.MOYSKLAD_VENDOR_SECRET_KEY:
        # Решение ещё не зарегистрировано — отдаём 503, чтобы МойСклад
        # ушёл в Retry, а не в ActivationFailed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vendor secret key not configured",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        logger.warning("vendor.jwt.no_bearer", got=authorization[:20] if authorization else None)
        raise HTTPException(401, "missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            settings.MOYSKLAD_VENDOR_SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_iss": False, "verify_exp": False},
        )
    except JWTError as exc:
        logger.warning(
            "vendor.jwt.decode_failed",
            error=str(exc),
            token_prefix=token[:32],
            secret_prefix=settings.MOYSKLAD_VENDOR_SECRET_KEY[:6],
            secret_len=len(settings.MOYSKLAD_VENDOR_SECRET_KEY),
        )
        raise HTTPException(401, f"invalid jwt: {exc}")

    # JWT МойСклад→Разработчик содержит только iat/exp/jti (без sub).
    # См. dev.moysklad.ru/doc/api/vendor/1.0 — раздел "Исходящие запросы МойСклад → Разработчик".
    iat = payload.get("iat")
    jti = payload.get("jti")

    if not isinstance(iat, int) or not jti:
        raise HTTPException(401, "jwt payload missing required fields")

    now = int(time.time())
    max_lifetime = settings.MOYSKLAD_VENDOR_JWT_MAX_LIFETIME
    if now - iat > max_lifetime:
        raise HTTPException(401, f"jwt iat too old: now={now} iat={iat}")

    exp = payload.get("exp")
    if isinstance(exp, int) and exp < now:
        raise HTTPException(401, "jwt expired")

    # Replay protection: jti должен быть уникален в окне maxLifetime.
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        ok = await redis.set(
            f"ms_vendor:jti:{jti}", "1", nx=True, ex=max_lifetime + 60
        )
    finally:
        await redis.aclose()
    if not ok:
        raise HTTPException(401, "jwt replay detected")

    return payload


async def _idempotent_response(
    request_id: Optional[str], cache_key: str
) -> Optional[dict]:
    """Возвращает закэшированный ответ для повторного X-Lognex-RequestId."""
    if not request_id:
        return None
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        cached = await redis.get(f"ms_vendor:req:{cache_key}:{request_id}")
        if cached:
            return json.loads(cached)
    finally:
        await redis.aclose()
    return None


async def _save_idempotent(
    request_id: Optional[str], cache_key: str, payload: dict, ttl: int = 600
) -> None:
    if not request_id:
        return
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        await redis.set(
            f"ms_vendor:req:{cache_key}:{request_id}",
            json.dumps(payload),
            ex=ttl,
        )
    finally:
        await redis.aclose()


def _extract_jsonapi_token(access: Optional[List[AccessItem]]) -> Optional[str]:
    if not access:
        return None
    for item in access:
        if item.resource.startswith(
            "https://api.moysklad.ru/api/remap/1.2"
        ) or item.resource.startswith(
            "https://online.moysklad.ru/api/remap/1.2"
        ):
            return item.access_token
    return None


@router.put("/{app_id}/{account_id}", response_model=StatusResponse)
async def activate(
    app_id: str,
    account_id: str,
    body: ActivateRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_lognex_request_id: Optional[str] = Header(
        default=None, alias="X-Lognex-RequestId"
    ),
):
    await _verify_vendor_jwt(authorization)

    cached = await _idempotent_response(x_lognex_request_id, f"put:{account_id}")
    if cached is not None:
        return cached

    logger.info(
        "vendor.activate",
        app_id=app_id,
        account_id=account_id,
        cause=body.cause,
        request_id=x_lognex_request_id,
    )

    access_token = _extract_jsonapi_token(body.access)

    result = await db.execute(
        select(Integration).where(Integration.moysklad_account_id == account_id)
    )
    integration = result.scalar_one_or_none()

    if integration is None:
        user = User(email=f"ms_{account_id}@moysklad.ru", password_hash="")
        db.add(user)
        await db.flush()
        integration = Integration(
            user_id=user.id,
            moysklad_account_id=account_id,
            moysklad_account_name=body.accountName,
            moysklad_token=encrypt_token(access_token) if access_token else None,
        )
        db.add(integration)
    else:
        if body.accountName:
            integration.moysklad_account_name = body.accountName
        if access_token:
            integration.moysklad_token = encrypt_token(access_token)

    await db.commit()

    payload = {"status": "Activated"}
    await _save_idempotent(x_lognex_request_id, f"put:{account_id}", payload)
    return payload


@router.delete("/{app_id}/{account_id}")
async def deactivate(
    app_id: str,
    account_id: str,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_lognex_request_id: Optional[str] = Header(
        default=None, alias="X-Lognex-RequestId"
    ),
):
    await _verify_vendor_jwt(authorization)

    cached = await _idempotent_response(x_lognex_request_id, f"del:{account_id}")
    if cached is not None:
        return cached

    logger.info(
        "vendor.deactivate",
        app_id=app_id,
        account_id=account_id,
        request_id=x_lognex_request_id,
    )

    result = await db.execute(
        select(Integration).where(Integration.moysklad_account_id == account_id)
    )
    integration = result.scalar_one_or_none()
    if integration:
        integration.moysklad_token = None
        await db.commit()

    payload = {"status": "ok"}
    await _save_idempotent(x_lognex_request_id, f"del:{account_id}", payload)
    return payload


@router.get("/{app_id}/{account_id}", response_model=StatusResponse)
async def get_status(
    app_id: str,
    account_id: str,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    await _verify_vendor_jwt(authorization)

    result = await db.execute(
        select(Integration).where(Integration.moysklad_account_id == account_id)
    )
    integration = result.scalar_one_or_none()
    if integration and integration.moysklad_token:
        return {"status": "Activated"}
    return {"status": "Activating"}
