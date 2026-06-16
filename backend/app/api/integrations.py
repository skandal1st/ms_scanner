from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import redis.asyncio as aioredis

from app.db.session import get_db
from app.db.models import User, Integration
from app.api.deps import get_current_user
from app.core.security import encrypt_token
from app.core.config import settings
from app.services.chestnyznak import ChestnyZnakService, CZApiError

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationResponse(BaseModel):
    has_moysklad: bool
    moysklad_account_name: Optional[str]
    has_cz: bool
    cz_token_valid_until: Optional[datetime] = None
    cz_cert_subject: Optional[str] = None
    cz_auth_method: str = "mock"
    cz_box_mode_enabled: bool = False


class UpdateIntegrationRequest(BaseModel):
    moysklad_token: Optional[str] = None
    cz_token: Optional[str] = None
    cz_box_mode_enabled: Optional[bool] = None


class CzChallengeResponse(BaseModel):
    uuid: str
    data: str


class CzLoginRequest(BaseModel):
    uuid: str
    signed_data: str
    cert_thumbprint: Optional[str] = None
    cert_subject: Optional[str] = None


class CzLoginResponse(BaseModel):
    cz_token_valid_until: datetime
    cz_cert_subject: Optional[str]


def _to_response(integration: Optional[Integration]) -> IntegrationResponse:
    if not integration:
        return IntegrationResponse(
            has_moysklad=False,
            moysklad_account_name=None,
            has_cz=False,
            cz_auth_method=settings.CZ_AUTH_METHOD,
            cz_box_mode_enabled=False,
        )
    has_cz = bool(integration.cz_token) and (
        integration.cz_token_expires_at is None
        or integration.cz_token_expires_at > datetime.now(timezone.utc)
    )
    return IntegrationResponse(
        has_moysklad=bool(integration.moysklad_token),
        moysklad_account_name=integration.moysklad_account_name,
        has_cz=has_cz,
        cz_token_valid_until=integration.cz_token_expires_at,
        cz_cert_subject=integration.cz_cert_subject,
        cz_auth_method=integration.cz_auth_method or settings.CZ_AUTH_METHOD,
        cz_box_mode_enabled=bool(integration.cz_box_mode_enabled),
    )


async def _get_or_create_integration(db: AsyncSession, user_id) -> Integration:
    result = await db.execute(select(Integration).where(Integration.user_id == user_id))
    integration = result.scalar_one_or_none()
    if not integration:
        integration = Integration(user_id=user_id)
        db.add(integration)
    return integration


@router.get("/", response_model=IntegrationResponse)
async def get_integration(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    return _to_response(result.scalar_one_or_none())


@router.put("/", response_model=IntegrationResponse)
async def update_integration(
    body: UpdateIntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = await _get_or_create_integration(db, current_user.id)

    if body.moysklad_token is not None:
        integration.moysklad_token = encrypt_token(body.moysklad_token) if body.moysklad_token else None
    if body.cz_token is not None:
        integration.cz_token = encrypt_token(body.cz_token) if body.cz_token else None
    if body.cz_box_mode_enabled is not None:
        integration.cz_box_mode_enabled = body.cz_box_mode_enabled

    await db.commit()
    await db.refresh(integration)
    return _to_response(integration)


@router.post("/cz/challenge", response_model=CzChallengeResponse)
async def cz_challenge(current_user: User = Depends(get_current_user)):
    """Получить challenge от ЧЗ для подписи УКЭПом в браузере.

    uuid кладётся в Redis с TTL — single-use, защита от replay.
    """
    cz = ChestnyZnakService()
    try:
        challenge = await cz.request_cert_key()
    except CZApiError as e:
        raise HTTPException(status_code=502, detail=str(e))

    r = aioredis.from_url(settings.REDIS_URL)
    try:
        await r.set(
            f"cz:challenge:{current_user.id}:{challenge['uuid']}",
            "1",
            ex=settings.CZ_CHALLENGE_TTL_SECONDS,
        )
    finally:
        await r.aclose()

    return CzChallengeResponse(uuid=challenge["uuid"], data=challenge["data"])


@router.post("/cz/login", response_model=CzLoginResponse)
async def cz_login(
    body: CzLoginRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Обменять подписанный challenge на access_token ЧЗ и сохранить.

    Приватный ключ остаётся в КриптоПро CSP клиента — сюда приходит только
    готовая CAdES-BES подпись.
    """
    redis_key = f"cz:challenge:{current_user.id}:{body.uuid}"
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        deleted = await r.delete(redis_key)
    finally:
        await r.aclose()
    if not deleted:
        raise HTTPException(status_code=400, detail="Challenge истёк или не найден")

    cz = ChestnyZnakService()
    try:
        result = await cz.exchange_cert_signature(body.uuid, body.signed_data)
    except CZApiError as e:
        # НЕ 401: фронтовый axios-интерцептор трактует любой 401 как протухшую
        # сессию приложения и выкидывает пользователя на /login. Здесь же отказ
        # касается обмена подписи в Честном Знаке, а не сессии в нашем приложении.
        raise HTTPException(
            status_code=502, detail=f"Честный Знак отклонил вход: {e}"
        )

    token = result.get("token")
    expire = int(result.get("expire", 3600))
    if not token:
        raise HTTPException(status_code=502, detail="ЧЗ не вернул token")

    integration = await _get_or_create_integration(db, current_user.id)
    integration.cz_token = encrypt_token(token)
    # запас 60 секунд: на стороне celery считаем «протух» чуть раньше реального expiry
    integration.cz_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expire - 60)
    integration.cz_cert_thumbprint = body.cert_thumbprint
    integration.cz_cert_subject = body.cert_subject
    integration.cz_auth_method = settings.CZ_AUTH_METHOD

    await db.commit()
    await db.refresh(integration)

    return CzLoginResponse(
        cz_token_valid_until=integration.cz_token_expires_at,
        cz_cert_subject=integration.cz_cert_subject,
    )


@router.delete("/cz", response_model=IntegrationResponse)
async def cz_logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Выход из ЧЗ — обнуляем токен и метаданные сертификата."""
    result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = result.scalar_one_or_none()
    if integration:
        integration.cz_token = None
        integration.cz_token_expires_at = None
        integration.cz_cert_thumbprint = None
        integration.cz_cert_subject = None
        await db.commit()
        await db.refresh(integration)
    return _to_response(integration)
