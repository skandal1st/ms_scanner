from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from app.db.session import get_db
from app.db.models import User, Document, DocumentStatus, Integration
from app.api.deps import get_current_user
from app.services.moysklad import MoySkladService
from app.core.security import decrypt_token

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentResponse(BaseModel):
    id: UUID
    moysklad_id: Optional[str]
    name: str
    status: DocumentStatus
    scan_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateDocumentRequest(BaseModel):
    name: str
    moysklad_id: Optional[str] = None


class MoySkladSupplyItem(BaseModel):
    id: str
    name: str
    moment: Optional[str]


@router.get("/moysklad-supplies", response_model=List[MoySkladSupplyItem])
async def list_moysklad_supplies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список поступлений из МойСклад для выбора документа."""
    result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(status_code=400, detail="МойСклад не подключён")

    token = decrypt_token(integration.moysklad_token)
    ms = MoySkladService(token)
    supplies = await ms.get_supplies()
    return supplies


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
    )
    documents = result.scalars().all()
    return [
        DocumentResponse(
            id=d.id,
            moysklad_id=d.moysklad_id,
            name=d.name,
            status=d.status,
            scan_count=len(d.scans) if d.scans else 0,
            created_at=d.created_at,
        )
        for d in documents
    ]


@router.post("/", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: CreateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = Document(
        user_id=current_user.id,
        name=body.name,
        moysklad_id=body.moysklad_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return DocumentResponse(
        id=doc.id,
        moysklad_id=doc.moysklad_id,
        name=doc.name,
        status=doc.status,
        scan_count=0,
        created_at=doc.created_at,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentResponse(
        id=doc.id,
        moysklad_id=doc.moysklad_id,
        name=doc.name,
        status=doc.status,
        scan_count=len(doc.scans) if doc.scans else 0,
        created_at=doc.created_at,
    )


@router.post("/{document_id}/accept")
async def accept_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Подтвердить приёмку: отправить в ЧЗ + обновить МойСклад."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.worker.tasks import accept_document_task
    accept_document_task.delay(str(document_id), str(current_user.id))

    doc.status = DocumentStatus.processing
    await db.commit()
    return {"status": "processing", "document_id": str(document_id)}
