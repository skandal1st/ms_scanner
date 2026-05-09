import secrets
import time
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
import redis.asyncio as aioredis

from app.db.session import get_db
from app.db.models import User, Integration, OAuthState
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    encrypt_token, decrypt_token,
)
from app.core.config import settings
from app.core.logging import logger
import httpx

LAUNCH_TOKEN_TTL_SECONDS = 60
LAUNCH_TOKEN_PREFIX = "ms_launch:"

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()

    integration = Integration(user_id=user.id)
    db.add(integration)
    await db.commit()

    logger.info("user.registered", email=body.email)
    return _issue_tokens(str(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info("user.login", user_id=str(user.id))
    return _issue_tokens(str(user.id))


@router.get("/moysklad/login")
async def moysklad_login(db: AsyncSession = Depends(get_db)):
    state = secrets.token_urlsafe(32)
    oauth_state = OAuthState(
        state=state,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(oauth_state)
    await db.commit()

    url = (
        f"{settings.MOYSKLAD_OAUTH_URL}"
        f"?client_id={settings.MOYSKLAD_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={settings.MOYSKLAD_REDIRECT_URI}"
        f"&state={state}"
    )
    return RedirectResponse(url)


@router.get("/moysklad/callback")
async def moysklad_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OAuthState).where(
            OAuthState.state == state,
            OAuthState.expires_at > datetime.now(timezone.utc),
        )
    )
    oauth_state = result.scalar_one_or_none()
    if not oauth_state:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.MOYSKLAD_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.MOYSKLAD_REDIRECT_URI,
                "client_id": settings.MOYSKLAD_CLIENT_ID,
                "client_secret": settings.MOYSKLAD_CLIENT_SECRET,
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get МойСклад token")
        token_data = resp.json()

    # Получаем info об аккаунте
    ms_token = token_data["access_token"]
    async with httpx.AsyncClient() as client:
        me_resp = await client.get(
            f"{settings.MOYSKLAD_API_BASE}/context/employee",
            headers={"Authorization": f"Bearer {ms_token}"},
        )

    # Находим или создаём пользователя по МС аккаунту
    account_id = None
    account_name = None
    if me_resp.status_code == 200:
        me = me_resp.json()
        account_id = me.get("accountId")
        account_name = me.get("name")

    result = await db.execute(
        select(Integration).where(Integration.moysklad_account_id == account_id)
    )
    integration = result.scalar_one_or_none()

    if integration:
        user_id = integration.user_id
        integration.moysklad_token = encrypt_token(ms_token)
    else:
        user = User(email=f"ms_{account_id}@moysklad.ru", password_hash="")
        db.add(user)
        await db.flush()
        user_id = user.id
        integration = Integration(
            user_id=user_id,
            moysklad_token=encrypt_token(ms_token),
            moysklad_account_id=account_id,
            moysklad_account_name=account_name,
        )
        db.add(integration)

    await db.delete(oauth_state)
    await db.commit()

    tokens = _issue_tokens(str(user_id))
    redirect_url = (
        f"{settings.MOYSKLAD_REDIRECT_URI.replace('/auth/callback', '')}"
        f"/auth/callback"
        f"?access_token={tokens.access_token}"
        f"&refresh_token={tokens.refresh_token}"
    )
    return RedirectResponse(redirect_url)


def _issue_tokens(user_id: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token({"sub": user_id}),
        refresh_token=create_refresh_token({"sub": user_id}),
    )


class MsLaunchRequest(BaseModel):
    contextKey: str


class MsLaunchResponse(BaseModel):
    launch_token: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    employee_name: str | None = None
    account_name: str | None = None


class LaunchExchangeRequest(BaseModel):
    launch_token: str


def _build_vendor_jwt() -> str:
    """JWT Разработчик→МойСклад: sub=appUid + iat + exp + jti, HS256(secretKey)."""
    now = int(time.time())
    payload = {
        "sub": settings.MOYSKLAD_APP_UID,
        "iat": now,
        "exp": now + 60,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(
        payload, settings.MOYSKLAD_VENDOR_SECRET_KEY, algorithm="HS256"
    )


@router.post("/ms-launch", response_model=MsLaunchResponse)
async def moysklad_launch(
    body: MsLaunchRequest, db: AsyncSession = Depends(get_db)
):
    """
    Лаунчер из iframe МойСклада. По contextKey проверяет, что пользователь имеет
    активированное решение, и выдаёт одноразовый launch_token (TTL 60s в Redis).
    Сам JWT не возвращаем — фронт-лаунчер откроет новую вкладку, та обменяет
    launch_token на JWT через /auth/launch. Это нужно, чтобы JWT не светился
    в URL новой вкладки (история, Referer, логи).
    """
    if not settings.MOYSKLAD_APP_UID or not settings.MOYSKLAD_VENDOR_SECRET_KEY:
        raise HTTPException(503, "Vendor API не настроен на сервере")

    vendor_jwt = _build_vendor_jwt()
    url = f"{settings.MOYSKLAD_VENDOR_BASE}/context/{body.contextKey}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {vendor_jwt}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )

    if resp.status_code != 200:
        logger.warning(
            "ms_launch.context_failed",
            status=resp.status_code,
            body=resp.text[:300],
        )
        raise HTTPException(
            status_code=401, detail="Не удалось получить контекст из МойСклад"
        )

    ctx = resp.json()
    account_id = ctx.get("accountId")
    if not account_id:
        meta = ctx.get("meta") or {}
        href = meta.get("href") or ""
        if "/employee/" in href:
            account_id = href.rsplit("/employee/", 1)[0].rsplit("/", 1)[-1]
    if not account_id:
        logger.warning("ms_launch.no_account_id", ctx_keys=list(ctx.keys()))
        raise HTTPException(401, "В контексте нет accountId")

    result = await db.execute(
        select(Integration).where(Integration.moysklad_account_id == account_id)
    )
    integration = result.scalar_one_or_none()
    if integration is None:
        # Vendor API ещё не активировал решение на этом аккаунте
        logger.warning("ms_launch.no_integration", account_id=account_id)
        raise HTTPException(
            403,
            "Решение не активировано на вашем аккаунте. "
            "Установите приложение через каталог решений МойСклад.",
        )

    employee_name = ctx.get("name") or ctx.get("fullName")
    launch_token = secrets.token_urlsafe(32)
    user_id = str(integration.user_id)

    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        await redis.set(
            f"{LAUNCH_TOKEN_PREFIX}{launch_token}",
            user_id,
            ex=LAUNCH_TOKEN_TTL_SECONDS,
        )
    finally:
        await redis.aclose()

    logger.info(
        "ms_launch.issued",
        account_id=account_id,
        user_id=user_id,
        employee=employee_name,
    )
    iframe_tokens = _issue_tokens(user_id)
    return MsLaunchResponse(
        launch_token=launch_token,
        access_token=iframe_tokens.access_token,
        refresh_token=iframe_tokens.refresh_token,
        employee_name=employee_name,
        account_name=integration.moysklad_account_name,
    )


@router.post("/launch", response_model=TokenResponse)
async def exchange_launch_token(body: LaunchExchangeRequest):
    """
    Обмен одноразового launch_token на JWT-пару. Вызывается новой вкладкой
    после window.open из iframe-лаунчера. Токен удаляется атомарно (GETDEL),
    повторный обмен невозможен.
    """
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        user_id = await redis.getdel(f"{LAUNCH_TOKEN_PREFIX}{body.launch_token}")
    finally:
        await redis.aclose()

    if not user_id:
        raise HTTPException(401, "Ссылка устарела или уже использована")

    if isinstance(user_id, bytes):
        user_id = user_id.decode()

    logger.info("launch.exchanged", user_id=user_id)
    return _issue_tokens(user_id)
