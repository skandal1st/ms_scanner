import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
import redis.asyncio as aioredis

from app.db.session import get_db
from app.db.models import User, Integration, Document, DocumentStatus, Scan, ScanStatus
from app.api.deps import get_current_user
from app.core.security import encrypt_token, decrypt_token
from app.core.config import settings
from app.core.logging import logger
from app.services.chestnyznak import (
    ChestnyZnakService,
    CZApiError,
    WRITEOFF_REASONS,
    CZ_PRODUCT_GROUP_CATALOG,
    normalize_product_groups,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _parse_inn_from_subject(subject: Optional[str]) -> Optional[str]:
    """Извлечь ИНН из субъекта сертификата.

    Формы: «ИНН=7707083893», «ИНН ЮЛ=7707083893», OID «1.2.643.3.131.1.1=007707...»
    (ИНН ФЛ, 12 цифр) или «1.2.643.100.4=7707083893» (ИНН ЮЛ, 10 цифр).
    """
    if not subject:
        return None
    m = re.search(r"ИНН(?:\s*ЮЛ)?[=\s:]*?(\d{10,12})", subject)
    if m:
        return m.group(1)
    m = re.search(r"1\.2\.643\.(?:3\.131\.1\.1|100\.4)=0*(\d{10,12})", subject)
    if m:
        return m.group(1)
    return None


# Реальный срок жизни токена ЧЗ по умолчанию — 10 часов. ЧЗ поле `expire` в ответе
# /auth/cert/ НЕ присылает (проверено на проде 2026-08-05: expire пустой, а сам токен —
# JWT с exp = выдача + 36000с). Раньше дефолт был 3600 → токен «умирал» через час,
# вынуждая переподписываться раз в час при реальном сроке 10 ч.
CZ_TOKEN_DEFAULT_TTL_SECONDS = 36000


def _cz_token_expiry_from_jwt(token: str) -> Optional[datetime]:
    """Срок действия токена ЧЗ из claim `exp` его JWT (True API отдаёт JWT).

    Возвращает aware datetime (UTC) или None, если токен не JWT / без exp.
    """
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


class IntegrationResponse(BaseModel):
    has_moysklad: bool
    moysklad_account_name: Optional[str]
    has_cz: bool
    cz_token_valid_until: Optional[datetime] = None
    cz_cert_subject: Optional[str] = None
    cz_auth_method: str = "mock"
    cz_box_mode_enabled: bool = False
    cz_inn: Optional[str] = None
    cz_product_groups: list[str] = []


class UpdateIntegrationRequest(BaseModel):
    moysklad_token: Optional[str] = None
    cz_token: Optional[str] = None
    cz_box_mode_enabled: Optional[bool] = None
    cz_inn: Optional[str] = None
    cz_product_groups: Optional[list[str]] = None


class ProductGroupItem(BaseModel):
    code: str
    label: str


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
            cz_product_groups=[],
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
        cz_inn=integration.cz_inn,
        cz_product_groups=list(integration.cz_product_groups or []),
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
    if body.cz_inn is not None:
        integration.cz_inn = body.cz_inn.strip() or None
    if body.cz_product_groups is not None:
        # Нормализуем по справочнику: отсекаем неизвестные коды и дубли, порядок каталога.
        integration.cz_product_groups = normalize_product_groups(body.cz_product_groups)

    await db.commit()
    await db.refresh(integration)
    return _to_response(integration)


@router.get("/cz/product-groups", response_model=list[ProductGroupItem])
async def list_cz_product_groups(
    current_user: User = Depends(get_current_user),
):
    """Справочник товарных групп ЧЗ для чекбоксов в настройках."""
    return [ProductGroupItem(**g) for g in CZ_PRODUCT_GROUP_CATALOG]


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
    if not token:
        raise HTTPException(status_code=502, detail="ЧЗ не вернул token")

    integration = await _get_or_create_integration(db, current_user.id)
    integration.cz_token = encrypt_token(token)
    # Срок действия берём из самого JWT-токена ЧЗ (claim exp) — это реальный срок ~10 ч.
    # ЧЗ поле `expire` в ответе не присылает; если вдруг появится — используем его,
    # иначе дефолт 10 ч. Запас 60 сек: считаем «протух» чуть раньше реального expiry.
    jwt_expiry = _cz_token_expiry_from_jwt(token)
    if jwt_expiry is not None:
        integration.cz_token_expires_at = jwt_expiry - timedelta(seconds=60)
    else:
        expire = int(result.get("expire") or CZ_TOKEN_DEFAULT_TTL_SECONDS)
        integration.cz_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expire - 60)
    integration.cz_cert_thumbprint = body.cert_thumbprint
    integration.cz_cert_subject = body.cert_subject
    integration.cz_auth_method = settings.CZ_AUTH_METHOD
    # ИНН участника оборота — для тела документов вывода из оборота. Парсим из субъекта
    # сертификата; если не нашли — оставляем прежнее значение (можно задать вручную в Settings).
    parsed_inn = _parse_inn_from_subject(body.cert_subject)
    if parsed_inn:
        integration.cz_inn = parsed_inn

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


# ── Списание: вывод из оборота через ЧЗ (УКЭП round-trip) ───────────────────────

WRITEOFF_TTL_SECONDS = 120


class WriteoffPrepareRequest(BaseModel):
    document_id: UUID
    reason: str
    basis_number: Optional[str] = None
    basis_date: Optional[str] = None  # YYYY-MM-DD


class WriteoffPart(BaseModel):
    pg: str
    product_document_b64: str


class UnresolvedCode(BaseModel):
    cis: str
    reason: str


class WriteoffPrepareResponse(BaseModel):
    # Пустой, если ни одну марку нельзя списать (см. unresolved).
    writeoff_token: str
    parts: list[WriteoffPart]
    # Марки, которые списать нельзя (не найдены в ЧЗ и т.п.) — показываем списком.
    unresolved: list[UnresolvedCode] = []


class WriteoffSignature(BaseModel):
    pg: str
    signature: str


class WriteoffSubmitRequest(BaseModel):
    writeoff_token: str
    signatures: list[WriteoffSignature]


class WriteoffSubmitResponse(BaseModel):
    doc_ids: list[str]


def _scan_cises(scans: list[Scan]) -> list[str]:
    """Развернуть сканы в список КМ: для агрегата (блок) — вложенные КМ, иначе сам код."""
    out: list[str] = []
    for s in scans:
        if s.child_codes:
            out.extend([c for c in s.child_codes if isinstance(c, str)])
        else:
            out.append(s.code)
    # уникализируем, сохраняя порядок
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


async def _load_owned_document(db: AsyncSession, document_id: UUID, user_id) -> Document:
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


@router.post("/cz/writeoff/prepare", response_model=WriteoffPrepareResponse)
async def cz_writeoff_prepare(
    body: WriteoffPrepareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Собрать документы вывода из оборота и отдать их на подпись УКЭП в браузере.

    Группируем КМ по товарной группе ЧЗ, на каждую строим тело LK_RECEIPT, кодируем в
    base64 и кладём в Redis под одноразовым writeoff_token (TTL). Подпись фронт делает сам.
    """
    if body.reason not in WRITEOFF_REASONS:
        raise HTTPException(status_code=400, detail="Неизвестная причина списания")
    reason = WRITEOFF_REASONS[body.reason]

    doc = await _load_owned_document(db, body.document_id, current_user.id)

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()
    if not integration or not integration.cz_token:
        raise HTTPException(status_code=400, detail="Нет токена Честного Знака — войдите по УКЭП")
    if (
        integration.cz_token_expires_at is not None
        and integration.cz_token_expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=400, detail="Токен Честного Знака истёк — войдите заново")
    if not integration.cz_inn:
        raise HTTPException(
            status_code=400,
            detail="Не задан ИНН участника оборота — укажите его в настройках",
        )

    scan_result = await db.execute(
        select(Scan).where(
            Scan.document_id == doc.id,
            Scan.status.in_([ScanStatus.valid, ScanStatus.overflow]),
        )
    )
    scans = scan_result.scalars().all()
    cises = _scan_cises(list(scans))
    if not cises:
        raise HTTPException(status_code=400, detail="Нет валидных марок для списания")

    cz = ChestnyZnakService(
        token=decrypt_token(integration.cz_token),
        product_groups=integration.cz_product_groups,
    )

    # Группируем КМ по товарной группе (pg). Марки, которые ЧЗ не находит (нельзя
    # списать физически), не роняют весь документ — собираем их отдельным списком.
    groups: dict[str, list[str]] = {}
    unresolved: list[UnresolvedCode] = []
    for cis in cises:
        pg, cz_reason = await cz.resolve_product_group(cis)
        if not pg:
            unresolved.append(
                UnresolvedCode(cis=cis, reason=cz_reason or "Марка не найдена в ЧЗ")
            )
            continue
        groups.setdefault(pg, []).append(cis)

    action_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts: list[WriteoffPart] = []
    stored_parts: dict[str, dict] = {}
    token = ""
    if groups:
        for pg, pg_cises in groups.items():
            document = cz.build_writeoff_document(
                inn=integration.cz_inn,
                action=reason["action"],
                action_date=action_date,
                cises=pg_cises,
                custom_name=reason["label"],
                basis_number=body.basis_number,
                basis_date=body.basis_date,
            )
            b64 = cz.encode_product_document(document)
            parts.append(WriteoffPart(pg=pg, product_document_b64=b64))
            stored_parts[pg] = {"product_document_b64": b64, "cises": pg_cises}

        token = secrets.token_urlsafe(32)
        payload = {
            "document_id": str(doc.id),
            "reason": body.reason,
            "parts": stored_parts,
        }
        r = aioredis.from_url(settings.REDIS_URL)
        try:
            await r.set(
                f"cz:writeoff:{current_user.id}:{token}",
                json.dumps(payload),
                ex=WRITEOFF_TTL_SECONDS,
            )
        finally:
            await r.aclose()

        doc.writeoff_reason = body.reason
        await db.commit()

    logger.info(
        "writeoff.prepared",
        document_id=str(doc.id),
        reason=body.reason,
        groups=list(groups.keys()),
        cises=len(cises),
        unresolved=len(unresolved),
    )
    return WriteoffPrepareResponse(
        writeoff_token=token, parts=parts, unresolved=unresolved
    )


@router.post("/cz/writeoff/submit", response_model=WriteoffSubmitResponse)
async def cz_writeoff_submit(
    body: WriteoffSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Подать подписанные документы вывода из оборота в ЧЗ и запустить опрос статуса."""
    redis_key = f"cz:writeoff:{current_user.id}:{body.writeoff_token}"
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        raw = await r.getdel(redis_key)
    finally:
        await r.aclose()
    if not raw:
        raise HTTPException(status_code=400, detail="Сессия списания истекла — повторите")

    payload = json.loads(raw)
    document_id = UUID(payload["document_id"])
    stored_parts: dict[str, dict] = payload["parts"]

    doc = await _load_owned_document(db, document_id, current_user.id)

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()
    if not integration or not integration.cz_token:
        raise HTTPException(status_code=400, detail="Нет токена Честного Знака")

    sig_by_pg = {s.pg: s.signature for s in body.signatures}
    cz = ChestnyZnakService(token=decrypt_token(integration.cz_token))

    doc_ids: list[dict] = []
    for pg, part in stored_parts.items():
        signature = sig_by_pg.get(pg)
        if not signature:
            raise HTTPException(status_code=400, detail=f"Нет подписи для группы {pg}")
        try:
            cz_doc_id = await cz.submit_document(
                pg=pg,
                product_document_b64=part["product_document_b64"],
                signature_b64=signature,
            )
        except CZApiError as e:
            # Не 401 — иначе axios-интерцептор выкинет на /login.
            raise HTTPException(status_code=502, detail=str(e))
        doc_ids.append({"doc_id": cz_doc_id, "pg": pg})

    doc.cz_doc_ids = doc_ids
    doc.status = DocumentStatus.processing
    await db.commit()

    from app.worker.tasks import poll_writeoff_status_task

    poll_writeoff_status_task.delay(str(doc.id), str(current_user.id))

    logger.info("writeoff.submitted", document_id=str(doc.id), docs=len(doc_ids))
    return WriteoffSubmitResponse(doc_ids=[d["doc_id"] for d in doc_ids])


# ── Проверка марок через ГИС МТ (только чтение, без отгрузки/списания) ───────────

CZ_CHECK_MAX = 500


class CzCheckRequest(BaseModel):
    codes: list[str]


class CzCheckDocRef(BaseModel):
    document_id: UUID
    name: str
    kind: str
    scan_status: str


class CzCheckItem(BaseModel):
    code: str
    found: bool
    product_name: Optional[str] = None
    gtin: Optional[str] = None
    owner_name: Optional[str] = None
    owner_inn: Optional[str] = None
    producer_name: Optional[str] = None
    product_group: Optional[str] = None
    status: Optional[str] = None
    package_type: Optional[str] = None
    child_count: int = 0
    error: Optional[str] = None
    documents: list[CzCheckDocRef] = []


class CzCheckResponse(BaseModel):
    items: list[CzCheckItem]


def _cis_key(code: str) -> str:
    """КИ для сопоставления с нашими сканами: часть кода до GS (01+GTIN+21+serial)."""
    return (code or "").split("\x1d", 1)[0].strip()


@router.post("/cz/check", response_model=CzCheckResponse)
async def cz_check(
    body: CzCheckRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Пробить марки через ГИС МТ (только чтение): товар, владелец, статус + в каких
    документах приложения они фигурируют. Ничего не пишет ни в ЧЗ, ни в МС."""
    codes = list(dict.fromkeys(c.strip() for c in body.codes if c and c.strip()))
    if not codes:
        return CzCheckResponse(items=[])
    if len(codes) > CZ_CHECK_MAX:
        raise HTTPException(
            status_code=400, detail=f"Слишком много кодов за раз (максимум {CZ_CHECK_MAX})"
        )

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()
    token_ok = bool(integration and integration.cz_token) and (
        integration.cz_token_expires_at is None
        or integration.cz_token_expires_at > datetime.now(timezone.utc)
    )
    if not token_ok:
        raise HTTPException(
            status_code=400,
            detail="Войдите в Честный Знак по УКЭП (раздел «Настройки»)",
        )

    cz = ChestnyZnakService(
        token=decrypt_token(integration.cz_token),
        product_groups=integration.cz_product_groups,
    )
    checks = await cz.check_codes(codes)

    # В каких наших документах фигурируют эти марки — сопоставляем по КИ (код до GS).
    keys = list({_cis_key(c) for c in codes})
    docmap: dict[str, list[CzCheckDocRef]] = {}
    if keys:
        rows = (
            await db.execute(
                select(Scan.code, Scan.status, Document.id, Document.name, Document.kind)
                .join(Document, Document.id == Scan.document_id)
                .where(Document.user_id == current_user.id)
                .where(func.split_part(Scan.code, "\x1d", 1).in_(keys))
            )
        ).all()
        seen: set[tuple[str, str]] = set()
        for scan_code, scan_status, doc_id, doc_name, doc_kind in rows:
            k = _cis_key(scan_code)
            dk = (k, str(doc_id))
            if dk in seen:
                continue
            seen.add(dk)
            docmap.setdefault(k, []).append(
                CzCheckDocRef(
                    document_id=doc_id,
                    name=doc_name,
                    kind=getattr(doc_kind, "value", str(doc_kind)),
                    scan_status=getattr(scan_status, "value", str(scan_status)),
                )
            )

    items = [
        CzCheckItem(
            code=r.code,
            found=r.found,
            product_name=r.product_name,
            gtin=r.gtin,
            owner_name=r.owner_name,
            owner_inn=r.owner_inn,
            producer_name=r.producer_name,
            product_group=r.product_group,
            status=r.status,
            package_type=r.package_type,
            child_count=r.child_count,
            error=r.error,
            documents=docmap.get(_cis_key(r.code), []),
        )
        for r in checks
    ]
    return CzCheckResponse(items=items)
