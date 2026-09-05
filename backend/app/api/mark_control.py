"""Раздел «Контроль марок»: подключение ЭДО Saby (СБИС) по клиенту + вытягивание
исходящих УПД со статусами (для отлова марок, зависших на продавце: отгрузили, но
покупатель не подписал УПД → право в ЧЗ не перешло).

Два способа авторизации Saby (по клиенту): сервисная (ключи приложения → X-SBISAccessToken,
рекомендованный) и логин/пароль (X-SBISSessionID). Токен кэшируется в Redis
(saby_auth:<user_id>), TTL 20 мин; на истёкшем — переавторизация.
"""
import json
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
from app.db.models import EdoDocument, EdoMark, Integration, User
from app.db.session import get_db
from app.services.saby import (
    SabyAuthError,
    SabyClient,
    SabyError,
    _extract_doc_list,
    parse_document,
)

router = APIRouter(prefix="/mark-control", tags=["mark-control"])

_AUTH_TTL = 1200


class SabyConnectRequest(BaseModel):
    # Сервисная авторизация (приоритет): id подключения + ключи приложения.
    app_client_id: Optional[str] = None
    app_secret: Optional[str] = None
    secret_key: Optional[str] = None
    # Либо логин/пароль.
    login: Optional[str] = None
    password: Optional[str] = None
    account: Optional[str] = None


class SabyStatusResponse(BaseModel):
    connected: bool
    mode: Optional[str] = None          # "service" | "login"
    login: Optional[str] = None
    account: Optional[str] = None
    app_client_id: Optional[str] = None


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
    state_desc: Optional[str] = None
    incomplete: Optional[bool] = None
    unsigned: Optional[bool] = None
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
    unsigned_count: int


async def _get_integration(db: AsyncSession, user_id) -> Optional[Integration]:
    return (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()


def _client_from_integration(integ: Integration) -> SabyClient:
    """SabyClient из сохранённых кред: сервисная (приоритет) или логин/пароль."""
    if integ and integ.saby_app_client_id:
        return SabyClient(
            app_client_id=integ.saby_app_client_id,
            app_secret=decrypt_token(integ.saby_app_secret) if integ.saby_app_secret else None,
            secret_key=decrypt_token(integ.saby_secret_key) if integ.saby_secret_key else None,
        )
    if integ and integ.saby_login and integ.saby_password:
        try:
            pwd = decrypt_token(integ.saby_password)
        except Exception:
            raise HTTPException(status_code=502, detail="Пароль Saby повреждён — подключите заново.")
        return SabyClient(login=integ.saby_login, password=pwd, account=integ.saby_account)
    raise HTTPException(status_code=403, detail="ЭДО Saby не подключён. Укажите доступ в разделе «Контроль марок».")


async def _get_auth(user_id, client: SabyClient) -> tuple[str, str]:
    """(имя_заголовка, токен) из Redis-кэша или свежая авторизация."""
    r = aioredis.from_url(settings.REDIS_URL)
    key = f"saby_auth:{user_id}"
    try:
        cached = await r.get(key)
        if cached:
            d = json.loads(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
            return (d["name"], d["token"])
        name, token = await client.authenticate()
        await r.set(key, json.dumps({"name": name, "token": token}), ex=_AUTH_TTL)
        return (name, token)
    finally:
        await r.aclose()


async def _invalidate_auth(user_id) -> None:
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        await r.delete(f"saby_auth:{user_id}")
    finally:
        await r.aclose()


@router.post("/saby/connect", response_model=SabyStatusResponse)
async def saby_connect(
    body: SabyConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить доступ к Saby (сервисный или логин/пароль) и проверить авторизацию."""
    use_service = bool((body.app_client_id or "").strip())
    if use_service:
        client = SabyClient(
            app_client_id=body.app_client_id.strip(),
            app_secret=(body.app_secret or "").strip() or None,
            secret_key=(body.secret_key or "").strip() or None,
        )
    else:
        login = (body.login or "").strip()
        if not login or not body.password:
            raise HTTPException(status_code=400, detail="Укажите либо ключи сервисной авторизации, либо логин и пароль Saby")
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
    if use_service:
        integ.saby_app_client_id = body.app_client_id.strip()
        integ.saby_app_secret = encrypt_token(body.app_secret) if (body.app_secret or "").strip() else None
        integ.saby_secret_key = encrypt_token(body.secret_key) if (body.secret_key or "").strip() else None
    else:
        integ.saby_login = (body.login or "").strip()
        integ.saby_password = encrypt_token(body.password)
        integ.saby_account = (body.account or "").strip() or None
    await db.commit()
    await _invalidate_auth(current_user.id)
    logger.info("saby.connected", user_id=str(current_user.id), mode="service" if use_service else "login")
    return await saby_status(current_user, db)


@router.get("/saby/status", response_model=SabyStatusResponse)
async def saby_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integ = await _get_integration(db, current_user.id)
    if integ and integ.saby_app_client_id:
        return SabyStatusResponse(connected=True, mode="service", app_client_id=integ.saby_app_client_id)
    if integ and integ.saby_login and integ.saby_password:
        return SabyStatusResponse(connected=True, mode="login", login=integ.saby_login, account=integ.saby_account)
    return SabyStatusResponse(connected=False)


@router.post("/saby/documents", response_model=DocumentsResponse)
async def saby_documents(
    body: DocumentsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список документов Saby выбранного направления/типа за период (best-effort разбор;
    сырой ответ логируется saby.list_documents.raw)."""
    integ = await _get_integration(db, current_user.id)
    client = _client_from_integration(integ)

    async def _once() -> list:
        auth = await _get_auth(current_user.id, client)
        return await client.list_documents(
            auth,
            direction=body.direction,
            date_from=body.date_from,
            date_to=body.date_to,
        )

    try:
        try:
            result = await _once()
        except SabyAuthError:
            await _invalidate_auth(current_user.id)  # токен истёк → переавторизация разово
            result = await _once()
    except SabyAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Saby: {exc}")
    except SabyError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # СписокИзменений отдаёт документ несколькими строками (по событиям) — дедуп по id.
    seen: set[str] = set()
    rows: list[DocRow] = []
    for d in _extract_doc_list(result):
        if not isinstance(d, dict):
            continue
        parsed = parse_document(d)
        key = parsed.get("id") or f"{parsed.get('number')}|{parsed.get('date')}"
        if key in seen:
            continue
        seen.add(key)
        # Только реализации (исходящие УПД) — прочие регламенты отсеиваем.
        if body.direction == "Исходящий" and (parsed.get("type") or "") and "реализац" not in str(parsed["type"]).lower():
            continue
        rows.append(DocRow(**{k: parsed.get(k) for k in DocRow.model_fields}))
    unsigned = sum(1 for r in rows if r.unsigned)
    return DocumentsResponse(documents=rows, unsigned_count=unsigned)


# ── Синхронизация ЭДО + отчёт по маркам ──────────────────────────────────────

class SyncRequest(BaseModel):
    date_from: str          # «ДД.ММ.ГГГГ»
    date_to: Optional[str] = None
    use_cursor: bool = False


def _to_dt(d: str, end: bool = False) -> str:
    d = (d or "").strip()
    if len(d) <= 10:
        return f"{d} {'23.59.59' if end else '00.00.00'}"
    return d


@router.post("/edo/sync")
async def edo_sync(
    body: SyncRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Запустить фоновую синхронизацию ЭДО (лента Saby → БД марок) за период."""
    integ = await _get_integration(db, current_user.id)
    _client_from_integration(integ)
    from app.worker.tasks import edo_sync_task

    edo_sync_task.delay(
        str(current_user.id),
        _to_dt(body.date_from),
        _to_dt(body.date_to, end=True) if body.date_to else None,
        body.use_cursor,
    )
    return {"status": "started"}


@router.get("/edo/sync/status")
async def edo_sync_status(current_user: User = Depends(get_current_user)):
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        running = await r.get(f"edo_sync:lock:{current_user.id}")
        res = await r.get(f"edo_sync:result:{current_user.id}")
    finally:
        await r.aclose()
    result = json.loads(res.decode() if isinstance(res, (bytes, bytearray)) else res) if res else None
    return {"running": bool(running), "result": result}


@router.get("/edo/documents-db")
async def edo_documents_db(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Синхронизированные исходящие реализации из БД (что уже вытянули из ЭДО)."""
    rows = (
        await db.execute(
            select(EdoDocument)
            .where(EdoDocument.user_id == current_user.id, EdoDocument.direction == "Исходящий")
            .order_by(EdoDocument.doc_date.desc())
        )
    ).scalars().all()
    return [
        {
            "number": d.number,
            "doc_date": d.doc_date,
            "counterparty_name": d.counterparty_name,
            "counterparty_inn": d.counterparty_inn,
            "state_name": d.state_name,
            "codes_total": d.codes_total,
            "marks_parsed": d.marks_parsed,
        }
        for d in rows
    ]
