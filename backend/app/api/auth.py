import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr

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
