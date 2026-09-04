"""Раздел «Контроль марок»: подключение ЭДО Saby (СБИС) по клиенту + вытягивание
исходящих УПД со статусами (для отлова марок, зависших на продавце: отгрузили, но
покупатель не подписал УПД → право в ЧЗ не перешло).

Сессия Saby (sid) кэшируется в Redis (saby_sid:<user_id>), TTL 20 мин; на истёкшей —
переавторизация по сохранённым (Fernet) кредам.
"""
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token, encrypt_token
from app.db.models import Integration, User
from app.db.session import get_db
from app.services.saby import (
    SabyAuthError,
    SabyClient,
    SabyError,
    _extract_doc_list,
    parse_document,
)

router = APIRouter(prefix="/mark-control", tags=["mark-control"])

_SID_TTL = 1200


class SabyConnectRequest(BaseModel):
    login: str
    password: str
    account: Optional[str] = None


class SabyStatusResponse(BaseModel):
    connected: bool
    login: Optional[str] = None
    account: Optional[str] = None


class DocRow(BaseModel):
    id: Optional[str] = None
    number: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None
    direction: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_inn: Optional[str] = None
    state_code: Optional[int] = None
    state_name: Optional[str] = None
    incomplete: Optional[bool] = None
    note: Optional[str] = None


class DocumentsRequest(BaseModel):
    direction: str = "Исходящий"
    doc_type: Optional[str] = None
    date_from: Optional[str] = None  # ДД.ММ.ГГГГ
    date_to: Optional[str] = None
    page: int = 0
    page_size: int = 100


class DocumentsResponse(BaseModel):
    documents: list[DocRow]
    # Незавершённые исходящие (покупатель не подписал/отклонил) — кандидаты в «зависшие».
    unsigned_count: int


async def _get_integration(db: AsyncSession, user_id) -> Optional[Integration]:
    return (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()


def _client_from_integration(integ: Integration) -> SabyClient:
    if not integ or not integ.saby_login or not integ.saby_password:
        raise HTTPException(status_code=403, detail="ЭДО Saby не подключён. Укажите доступ в разделе «Контроль марок».")
    try:
        pwd = decrypt_token(integ.saby_password)
    except Exception:
        raise HTTPException(status_code=502, detail="Пароль Saby повреждён — подключите заново.")
    return SabyClient(login=integ.saby_login, password=pwd, account=integ.saby_account)


async def _get_sid(user_id, client: SabyClient) -> str:
    """Сессия Saby из Redis или свежая авторизация (с кэшированием)."""
    r = aioredis.from_url(settings.REDIS_URL)
    key = f"saby_sid:{user_id}"
    try:
        cached = await r.get(key)
        if cached:
            return cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
        sid = await client.authenticate()
        await r.set(key, sid, ex=_SID_TTL)
        return sid
    finally:
        await r.aclose()


async def _invalidate_sid(user_id) -> None:
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        await r.delete(f"saby_sid:{user_id}")
    finally:
        await r.aclose()


@router.post("/saby/connect", response_model=SabyStatusResponse)
async def saby_connect(
    body: SabyConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить доступ к Saby и проверить авторизацию (одна проба сразу)."""
    login = (body.login or "").strip()
    if not login or not body.password:
        raise HTTPException(status_code=400, detail="Укажите логин и пароль Saby")
    client = SabyClient(login=login, password=body.password, account=(body.account or "").strip() or None)
    try:
        await client.authenticate()
    except SabyAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Saby не авторизовал: {exc}")
    except SabyError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    integ = await _get_integration(db, current_user.id)
    if not integ:
        integ = Integration(user_id=current_user.id)
        db.add(integ)
    integ.saby_login = login
    integ.saby_password = encrypt_token(body.password)
    integ.saby_account = (body.account or "").strip() or None
    await db.commit()
    await _invalidate_sid(current_user.id)
    logger.info("saby.connected", user_id=str(current_user.id), login=login)
    return SabyStatusResponse(connected=True, login=login, account=integ.saby_account)


@router.get("/saby/status", response_model=SabyStatusResponse)
async def saby_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integ = await _get_integration(db, current_user.id)
    if not integ or not integ.saby_login or not integ.saby_password:
        return SabyStatusResponse(connected=False)
    return SabyStatusResponse(connected=True, login=integ.saby_login, account=integ.saby_account)


@router.post("/saby/documents", response_model=DocumentsResponse)
async def saby_documents(
    body: DocumentsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список документов Saby выбранного направления/типа за период.

    Для контроля «зависших» марок берём Направление=Исходящий: незавершённые
    (НеполнаяОбработка=Да либо статус «ожидает/отклонён») — кандидаты, где право в ЧЗ
    не перешло. Разбор полей best-effort; сырой ответ логируется (saby.list_documents.raw).
    """
    integ = await _get_integration(db, current_user.id)
    client = _client_from_integration(integ)

    async def _fetch() -> list:
        sid = await _get_sid(current_user.id, client)
        try:
            return await client.list_documents(
                sid,
                direction=body.direction,
                doc_type=body.doc_type,
                date_from=body.date_from,
                date_to=body.date_to,
                page=body.page,
                page_size=body.page_size,
            )
        except SabyAuthError:
            # сессия истекла → сбросить кэш и переавторизоваться разово
            await _invalidate_sid(current_user.id)
            sid = await _get_sid(current_user.id, client)
            return await client.list_documents(
                sid,
                direction=body.direction,
                doc_type=body.doc_type,
                date_from=body.date_from,
                date_to=body.date_to,
                page=body.page,
                page_size=body.page_size,
            )

    try:
        result = await _fetch()
    except SabyAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Saby: {exc}")
    except SabyError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    rows = [DocRow(**parse_document(d)) for d in _extract_doc_list(result) if isinstance(d, dict)]
    unsigned = sum(1 for r in rows if r.incomplete or (r.state_code == 23))
    return DocumentsResponse(documents=rows, unsigned_count=unsigned)
