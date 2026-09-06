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
from app.db.models import CzOwnerMark, EdoDocument, EdoMark, Integration, User
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


def _to_dt(d: str, end: bool = False) -> str:
    """«ДД.ММ.ГГГГ» → «ДД.ММ.ГГГГ ЧЧ.ММ.СС» для СписокИзменений."""
    d = (d or "").strip()
    if len(d) <= 10:
        return f"{d} {'23.59.59' if end else '00.00.00'}"
    return d


def _default_from() -> str:
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=30)).strftime("%d.%m.%Y")


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

    # «Быстрый просмотр» — первая страница ленты изменений (одна пачка ~25 последних
    # событий). Полный охват периода — через синхронизацию в БД (/edo/sync).
    df = _to_dt(body.date_from) if body.date_from else _to_dt(_default_from())
    dt = _to_dt(body.date_to, end=True) if body.date_to else None

    async def _once() -> object:
        auth = await _get_auth(current_user.id, client)
        return await client.changes_page(auth, date_from=df, date_to=dt)

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


class BackfillNamesRequest(BaseModel):
    # За сколько дней назад пройтись по ленте (по умолчанию год). Имена в gtin_name_map.
    days: int = 365


@router.post("/edo/backfill-names")
async def edo_backfill_names(
    body: BackfillNamesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Разово докачать первичные УПД по историческим документам и заполнить наименования
    по GTIN (gtin_name_map) — для имён в «Инвентаризации». Марки не трогает."""
    from datetime import datetime, timedelta

    integ = await _get_integration(db, current_user.id)
    _client_from_integration(integ)
    from app.worker.tasks import edo_sync_task

    days = max(1, min(int(body.days or 365), 1825))
    date_from = _to_dt((datetime.now() - timedelta(days=days)).strftime("%d.%m.%Y"))
    edo_sync_task.delay(str(current_user.id), date_from, None, False, True)
    return {"status": "started", "days": days}


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
            .where(
                EdoDocument.user_id == current_user.id,
                EdoDocument.direction == "Исходящий",
                EdoDocument.codes_total > 0,  # без марок не показываем
            )
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


@router.post("/cz/snapshot/refresh")
async def cz_snapshot_refresh(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Запустить обновление снимка остатка ЧЗ (dispenser → cz_owner_marks)."""
    integ = await _get_integration(db, current_user.id)
    if not integ or not integ.cz_token:
        raise HTTPException(status_code=403, detail="Не подключён Честный Знак (нужен вход по УКЭП).")
    from app.worker.tasks import cz_snapshot_refresh_task

    cz_snapshot_refresh_task.delay(str(current_user.id))
    return {"status": "started"}


@router.get("/cz/snapshot/status")
async def cz_snapshot_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Состояние снимка ЧЗ: идёт ли обновление, размер, дата, результат последнего."""
    from sqlalchemy import func

    r = aioredis.from_url(settings.REDIS_URL)
    try:
        running = await r.get(f"cz_snapshot:lock:{current_user.id}")
        res = await r.get(f"cz_snapshot:result:{current_user.id}")
    finally:
        await r.aclose()
    result = json.loads(res.decode() if isinstance(res, (bytes, bytearray)) else res) if res else None
    size = (
        await db.execute(
            select(func.count()).select_from(CzOwnerMark).where(CzOwnerMark.user_id == current_user.id)
        )
    ).scalar_one()
    at = (
        await db.execute(
            select(func.max(CzOwnerMark.snapshot_at)).where(CzOwnerMark.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    return {
        "running": bool(running),
        "size": int(size),
        "at": at.isoformat() if at else None,
        "result": result,
    }


async def _compute_stuck(db: AsyncSession, user_id) -> dict:
    """Отчёт «не принятые УПД» (по контрагентам + по документам). Единый источник для
    JSON-эндпоинта и XLSX-экспорта.

    «Не принято» = документ НЕ завершён успешно и НЕ отменён/черновик (исключаем коды
    7/19/22/20/0). stuck — сколько марок УПД ещё числится за нами в ЧЗ."""
    from sqlalchemy import func, text

    snap = (
        await db.execute(
            select(func.count()).select_from(CzOwnerMark).where(CzOwnerMark.user_id == user_id)
        )
    ).scalar_one()
    if not snap:
        return {"has_snapshot": False, "snapshot_size": 0, "counterparties": [], "documents": [],
                "stuck_docs": 0, "stuck_marks": 0, "snapshot_at": None}

    q = text("""
        SELECT d.number, d.doc_date, d.counterparty_inn, d.counterparty_name,
               d.state_name, d.codes_total AS total,
               count(o.id) AS stuck
        FROM edo_documents d
        JOIN edo_marks m ON m.document_id = d.id
        LEFT JOIN cz_owner_marks o
               ON o.user_id = d.user_id AND o.cis_canonical = m.cis_canonical
        WHERE d.user_id = :uid AND d.direction = 'Исходящий' AND d.codes_total > 0
          AND coalesce(d.state_code, -1) NOT IN (7, 19, 22, 20, 0)
        GROUP BY d.id, d.number, d.doc_date, d.counterparty_inn, d.counterparty_name,
                 d.state_name, d.codes_total
        ORDER BY d.doc_date DESC
    """)
    res = await db.execute(q, {"uid": str(user_id)})
    docs = [
        {
            "number": r.number,
            "doc_date": r.doc_date,
            "counterparty_inn": r.counterparty_inn,
            "counterparty_name": r.counterparty_name,
            "state_name": r.state_name,
            "total": int(r.total or 0),
            "stuck": int(r.stuck or 0),
        }
        for r in res
    ]

    by_cp: dict[str, dict] = {}
    for d in docs:
        key = d["counterparty_inn"] or (d["counterparty_name"] or "—")
        c = by_cp.get(key)
        if not c:
            c = {"counterparty_inn": d["counterparty_inn"], "counterparty_name": d["counterparty_name"],
                 "not_accepted_upd": 0, "marks_total": 0, "stuck_marks": 0}
            by_cp[key] = c
        c["not_accepted_upd"] += 1
        c["marks_total"] += d["total"]
        c["stuck_marks"] += d["stuck"]
        if not c["counterparty_name"] and d["counterparty_name"]:
            c["counterparty_name"] = d["counterparty_name"]
    counterparties = sorted(by_cp.values(), key=lambda x: (-x["not_accepted_upd"], -x["stuck_marks"]))

    snap_at = (
        await db.execute(
            select(func.max(CzOwnerMark.snapshot_at)).where(CzOwnerMark.user_id == user_id)
        )
    ).scalar_one_or_none()

    return {
        "has_snapshot": True,
        "snapshot_size": int(snap),
        "snapshot_at": snap_at.isoformat() if snap_at else None,
        "counterparties": counterparties,
        "documents": docs,
        "stuck_docs": len(docs),
        "stuck_marks": sum(d["stuck"] for d in docs),
    }


@router.get("/edo/stuck")
async def edo_stuck(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Не принятые УПД: сводка по контрагентам + по документам."""
    return await _compute_stuck(db, current_user.id)


@router.get("/edo/stuck.xlsx")
async def edo_stuck_xlsx(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Выгрузка отчёта «Не принятые УПД» в XLSX: лист по контрагентам + лист по документам."""
    import io
    from datetime import datetime
    from urllib.parse import quote
    from fastapi.responses import Response
    from openpyxl import Workbook

    data = await _compute_stuck(db, current_user.id)
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "По контрагентам"
    ws1.append(["Контрагент", "ИНН", "Не принято УПД", "Марок всего", "Марок за нами"])
    for c in data.get("counterparties", []):
        ws1.append([c["counterparty_name"] or "—", c["counterparty_inn"] or "",
                    c["not_accepted_upd"], c["marks_total"], c["stuck_marks"]])
    ws1.column_dimensions["A"].width = 46
    ws1.column_dimensions["B"].width = 16
    for col in ("C", "D", "E"):
        ws1.column_dimensions[col].width = 15
    ws1.freeze_panes = "A2"

    ws2 = wb.create_sheet("По документам")
    ws2.append(["УПД №", "Дата", "Покупатель", "ИНН", "Статус ЭДО", "Марок за нами", "Марок всего"])
    for d in data.get("documents", []):
        ws2.append([d["number"] or "—", d["doc_date"] or "", d["counterparty_name"] or "—",
                    d["counterparty_inn"] or "", d["state_name"] or "", d["stuck"], d["total"]])
    ws2.column_dimensions["A"].width = 14
    ws2.column_dimensions["B"].width = 12
    ws2.column_dimensions["C"].width = 40
    ws2.column_dimensions["D"].width = 16
    ws2.column_dimensions["E"].width = 26
    ws2.column_dimensions["F"].width = 14
    ws2.column_dimensions["G"].width = 12
    ws2.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    fname = f"Не принятые УПД {datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=stuck.xlsx; filename*=UTF-8''{quote(fname)}"},
    )

