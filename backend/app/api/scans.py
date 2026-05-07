from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from app.db.session import get_db
from app.db.models import User, Scan, Document, ScanStatus
from app.api.deps import get_current_user

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


@router.post("/", response_model=ScanResponse, status_code=201)
async def create_scan(
    body: CreateScanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Проверяем что документ принадлежит пользователю
    doc_result = await db.execute(
        select(Document).where(
            Document.id == body.document_id,
            Document.user_id == current_user.id,
        )
    )
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Document not found")

    # Проверяем дубликат в рамках документа
    dup_result = await db.execute(
        select(Scan).where(
            Scan.document_id == body.document_id,
            Scan.code == body.code,
        )
    )
    if dup_result.scalar_one_or_none():
        # Возвращаем существующий скан со статусом duplicate
        existing = dup_result.scalar_one()
        existing.status = ScanStatus.duplicate
        await db.commit()
        await db.refresh(existing)
        return ScanResponse.model_validate(existing)

    gtin = _extract_gtin(body.code)
    scan = Scan(
        document_id=body.document_id,
        code=body.code,
        gtin=gtin,
        status=ScanStatus.pending,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)

    # Ставим в очередь Celery для проверки
    from app.worker.tasks import verify_code_task
    verify_code_task.delay(str(scan.id), body.code, str(current_user.id))

    return ScanResponse.model_validate(scan)


@router.get("/{document_id}", response_model=List[ScanResponse])
async def list_scans(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc_result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Document not found")

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


def _extract_gtin(code: str) -> Optional[str]:
    """Извлекает GTIN из DataMatrix кода маркировки.
    Формат: 01{gtin14}{serial_prefix}{serial}
    """
    if code.startswith("01") and len(code) >= 16:
        return code[2:16]
    return None
