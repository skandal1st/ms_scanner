from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone

from app.db.session import get_db
from app.db.models import User, Scan, Document, ScanStatus, Integration
from app.api.deps import get_current_user
from cryptography.fernet import InvalidToken

from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token
from app.services.chestnyznak import (
    ChestnyZnakService,
    CZApiError,
    extract_gtin,
    is_sscc,
)

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanResponse(BaseModel):
    id: UUID
    document_id: UUID
    code: str
    gtin: Optional[str] = None
    status: ScanStatus
    product_name: Optional[str] = None
    moysklad_product_id: Optional[str] = None
    error_message: Optional[str] = None
    scanned_at: datetime
    is_box: bool = False
    box_quantity: Optional[int] = None
    owner_name: Optional[str] = None
    producer_name: Optional[str] = None
    child_codes: Optional[List[str]] = None

    model_config = {"from_attributes": True}


class CreateScanRequest(BaseModel):
    document_id: UUID
    code: str
    moysklad_product_id: Optional[str] = None


class PatchScanBody(BaseModel):
    moysklad_product_id: Optional[str] = None


class CreateBoxRequest(BaseModel):
    document_id: UUID
    sscc: str
    # True — раскрыть короб на штучные КМ (real → 501, пока работает только mock).
    # False — сохранить короб целиком (transportpack): один скан, quantity из ЧЗ.
    unpack: bool = True


async def _ensure_document_owner(
    document_id: UUID, current_user: User, db: AsyncSession
) -> Document:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _normalize_moysklad_product_id(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > 64:
        raise HTTPException(
            status_code=400,
            detail="Идентификатор товара в МойСклад слишком длинный",
        )
    return s


async def _create_scan_record(
    db: AsyncSession,
    document_id: UUID,
    code: str,
    current_user_id: UUID,
    moysklad_product_id: Optional[str] = None,
    *,
    is_box: bool = False,
    box_quantity: Optional[int] = None,
    gtin_override: Optional[str] = None,
) -> tuple[Scan, bool]:
    """
    Создаёт скан или возвращает существующий со статусом duplicate.
    Возвращает (scan, is_duplicate).

    Для коробов «целиком» (``is_box=True``): ``code`` — это SSCC, GTIN берётся из
    ``gtin_override`` (из ЧЗ sscc_check, у SSCC своего GTIN в коде нет), а проверку
    делает ``verify_box_task`` вместо ``verify_code_task``.
    """
    code = code.strip()
    gtin = gtin_override if is_box else extract_gtin(code)

    doc_row = await db.execute(select(Document).where(Document.id == document_id))
    doc_obj = doc_row.scalar_one()

    initial_name = None
    if moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == moysklad_product_id:
                initial_name = (p.get("product_name") or "").strip() or None
                break

    scan = Scan(
        document_id=document_id,
        code=code,
        gtin=gtin,
        moysklad_product_id=moysklad_product_id,
        status=ScanStatus.pending,
        product_name=initial_name,
        is_box=is_box,
        box_quantity=box_quantity,
    )
    db.add(scan)
    try:
        await db.commit()
    except IntegrityError:
        # Дубль по unique (document_id, code) — берём существующий, помечаем duplicate.
        await db.rollback()
        existing_q = await db.execute(
            select(Scan).where(
                Scan.document_id == document_id,
                Scan.code == code,
            )
        )
        existing = existing_q.scalar_one()
        existing.status = ScanStatus.duplicate
        existing.error_message = "Дубль: код уже сканировался в этом документе"
        await db.commit()
        await db.refresh(existing)
        logger.info(
            "scan.duplicate",
            document_id=str(document_id),
            scan_id=str(existing.id),
            gtin=extract_gtin(code),
        )
        return existing, True

    await db.refresh(scan)

    # Очередь Celery: проверка формата кода / mock ЧЗ.
    # Короб «целиком» проверяется отдельной задачей (агрегат уже подтверждён sscc_check).
    logger.info(
        "scan.created",
        document_id=str(document_id),
        scan_id=str(scan.id),
        gtin=scan.gtin,
        is_box=is_box,
        user_id=str(current_user_id),
    )
    if is_box:
        from app.worker.tasks import verify_box_task
        verify_box_task.delay(str(scan.id), str(current_user_id))
    else:
        from app.worker.tasks import verify_code_task
        verify_code_task.delay(str(scan.id), code, str(current_user_id))
    return scan, False


@router.post("/", response_model=ScanResponse, status_code=201)
async def create_scan(
    body: CreateScanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Создать скан кода маркировки (KM, не SSCC). Для коробов — POST /scans/box."""
    await _ensure_document_owner(body.document_id, current_user, db)
    if is_sscc(body.code):
        raise HTTPException(
            400,
            "Это код короба (SSCC). Используйте /scans/box.",
        )
    scan, _ = await _create_scan_record(
        db,
        body.document_id,
        body.code,
        current_user.id,
        _normalize_moysklad_product_id(body.moysklad_product_id),
    )
    return ScanResponse.model_validate(scan)


@router.post("/box", response_model=List[ScanResponse], status_code=201)
async def create_box_scans(
    body: CreateBoxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Принять SSCC-код короба.

    ``unpack=True`` — раскрыть на индивидуальные KM (по скану на каждый). Реальное
    раскрытие через ЧЗ пока не реализовано (501); работает только в mock.

    ``unpack=False`` — сохранить короб целиком: один скан с ``is_box=True`` и
    ``box_quantity`` из ЧЗ (``sscc_check``); в МС уйдёт одним ``transportpack``.

    В проде (CZ_MOCK_MODE=false) требуется включённый режим коробов в настройках и
    действующий вход в Честный Знак по УКЭП; в dev/mock — без ограничений.
    Возвращает массив созданных сканов (включая дубли — статус duplicate).
    """
    doc = await _ensure_document_owner(body.document_id, current_user, db)
    sscc = body.sscc.strip()
    if not is_sscc(sscc):
        raise HTTPException(400, "Это не SSCC-код короба")

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()

    if not settings.CZ_MOCK_MODE:
        if not integration or not integration.cz_box_mode_enabled:
            raise HTTPException(
                status_code=403,
                detail="Работа с коробами отключена. Включите режим в настройках и войдите через УКЭП.",
            )
        try:
            cz_token = (
                decrypt_token(integration.cz_token)
                if integration.cz_token
                else None
            )
        except InvalidToken:
            raise HTTPException(
                status_code=502,
                detail="Токен Честного Знака повреждён. Выйдите и войдите снова по УКЭП.",
            )
        expired = (
            integration.cz_token_expires_at is not None
            and integration.cz_token_expires_at <= datetime.now(timezone.utc)
        )
        if not cz_token or expired:
            raise HTTPException(
                status_code=403,
                detail="Нужен действующий вход в Честный Знак (УКЭП) для работы с коробами.",
            )
        cz = ChestnyZnakService(token=cz_token, mock=False)
    else:
        cz = ChestnyZnakService(token=None)

    plan_gtins = [
        item.get("gtin")
        for item in (doc.plan or [])
        if isinstance(item, dict) and item.get("gtin")
    ]

    # Короб целиком: один скан-короб, quantity и GTIN из ЧЗ sscc_check.
    if not body.unpack:
        try:
            info = await cz.get_sscc_info(sscc, plan_gtins)
        except CZApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        scan, _ = await _create_scan_record(
            db,
            body.document_id,
            sscc,
            current_user.id,
            is_box=True,
            box_quantity=info.quantity,
            gtin_override=info.gtin,
        )
        return [ScanResponse.model_validate(scan)]

    # Раскрытие на штучные КМ.
    try:
        member_codes = await cz.unpack_box(sscc, plan_gtins)
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Раскрытие SSCC через API Честного Знака пока не настроено. "
            "Переключите тумблер на «целиком» или сканируйте коды поштучно.",
        )

    scans: List[Scan] = []
    for code in member_codes:
        scan, _ = await _create_scan_record(
            db, body.document_id, code, current_user.id
        )
        scans.append(scan)

    return [ScanResponse.model_validate(s) for s in scans]


@router.patch("/item/{scan_id}", response_model=ScanResponse)
async def patch_scan_product(
    scan_id: UUID,
    body: PatchScanBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Задать или снять явную привязку скана к товару МС (UUID)."""
    result = await db.execute(
        select(Scan)
        .join(Document)
        .where(
            Scan.id == scan_id,
            Document.user_id == current_user.id,
        )
    )
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    data = body.model_dump(exclude_unset=True)
    if "moysklad_product_id" not in data:
        return ScanResponse.model_validate(scan)

    scan.moysklad_product_id = _normalize_moysklad_product_id(body.moysklad_product_id)

    doc_row = await db.execute(select(Document).where(Document.id == scan.document_id))
    doc_obj = doc_row.scalar_one()
    scan.product_name = None
    if scan.moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == scan.moysklad_product_id:
                scan.product_name = (p.get("product_name") or "").strip() or None
                break

    await db.commit()
    await db.refresh(scan)
    return ScanResponse.model_validate(scan)


@router.get("/{document_id}", response_model=List[ScanResponse])
async def list_scans(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_document_owner(document_id, current_user, db)
    result = await db.execute(
        select(Scan)
        .where(Scan.document_id == document_id)
        .order_by(Scan.scanned_at.desc())
    )
    return [ScanResponse.model_validate(s) for s in result.scalars().all()]


@router.delete("/{scan_id}", status_code=204)
async def delete_scan(
    scan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Scan)
        .join(Document)
        .where(
            Scan.id == scan_id,
            Document.user_id == current_user.id,
        )
    )
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    await db.delete(scan)
    await db.commit()
