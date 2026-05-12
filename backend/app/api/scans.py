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
from app.core.security import decrypt_token
from app.services.chestnyznak import (
    ChestnyZnakService,
    canonicalize_marking_scan_code,
    is_sscc,
    extract_gtin,
)

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanResponse(BaseModel):
    id: UUID
    document_id: UUID
    code: str
    gtin: Optional[str]
    status: ScanStatus
    product_name: Optional[str]
    error_message: Optional[str]
    scanned_at: datetime

    model_config = {"from_attributes": True}


class CreateScanRequest(BaseModel):
    document_id: UUID
    code: str


class CreateBoxRequest(BaseModel):
    document_id: UUID
    sscc: str


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


async def _create_scan_record(
    db: AsyncSession, document_id: UUID, code: str, current_user_id: UUID
) -> tuple[Scan, bool]:
    """
    Создаёт скан или возвращает существующий со статусом duplicate.
    Возвращает (scan, is_duplicate).
    """
    code = canonicalize_marking_scan_code(code.strip())
    gtin = extract_gtin(code)
    scan = Scan(
        document_id=document_id,
        code=code,
        gtin=gtin,
        status=ScanStatus.pending,
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
        return existing, True

    await db.refresh(scan)

    # Очередь Celery: проверка формата кода / mock ЧЗ
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
    scan, _ = await _create_scan_record(db, body.document_id, body.code, current_user.id)
    return ScanResponse.model_validate(scan)


@router.post("/box", response_model=List[ScanResponse], status_code=201)
async def create_box_scans(
    body: CreateBoxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Принять SSCC-код короба: распаковать на индивидуальные KM и создать по скану на каждый.

    В проде (CZ_MOCK_MODE=false) требуется включённый режим коробов в настройках и
    действующий вход в Честный Знак по УКЭП; в dev/mock — без ограничений.
    Возвращает массив созданных сканов (включая дубли — статус duplicate).
    """
    doc = await _ensure_document_owner(body.document_id, current_user, db)
    if not is_sscc(body.sscc):
        raise HTTPException(400, "Это не SSCC-код короба")

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()

    if not settings.CZ_MOCK_MODE:
        if not integration or not integration.cz_box_mode_enabled:
            raise HTTPException(
                status_code=403,
                detail="Распаковка коробов отключена. Включите режим в настройках и войдите через УКЭП.",
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
                detail="Нужен действующий вход в Честный Знак (УКЭП) для распаковки коробов.",
            )
        cz = ChestnyZnakService(token=cz_token, mock=False)
    else:
        cz = ChestnyZnakService(token=None)

    plan_gtins = [
        item.get("gtin")
        for item in (doc.plan or [])
        if isinstance(item, dict) and item.get("gtin")
    ]

    try:
        member_codes = await cz.unpack_box(body.sscc, plan_gtins)
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Распаковка SSCC через API Честного Знака на сервере пока не настроена.",
        )

    scans: List[Scan] = []
    for code in member_codes:
        scan, _ = await _create_scan_record(
            db, body.document_id, code, current_user.id
        )
        scans.append(scan)

    return [ScanResponse.model_validate(s) for s in scans]


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
