from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from app.db.session import get_db
from app.db.models import User, Document, DocumentKind, DocumentStatus, Integration
from app.api.deps import get_current_user
from app.services.moysklad import MoySkladService, SUPPORTED_KINDS
from app.core.security import decrypt_token

router = APIRouter(prefix="/documents", tags=["documents"])


class PlanItem(BaseModel):
    gtin: Optional[str]
    product_id: Optional[str]
    product_name: str
    expected_qty: int


class DocumentResponse(BaseModel):
    id: UUID
    moysklad_id: Optional[str]
    name: str
    kind: DocumentKind
    status: DocumentStatus
    scan_count: int = 0
    plan: List[PlanItem] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateDocumentRequest(BaseModel):
    name: str
    kind: DocumentKind = DocumentKind.supply
    moysklad_id: Optional[str] = None


class MoySkladDocumentItem(BaseModel):
    id: str
    name: str
    moment: Optional[str]


def _doc_to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        moysklad_id=doc.moysklad_id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        scan_count=len(doc.scans) if doc.scans else 0,
        plan=doc.plan or [],
        created_at=doc.created_at,
    )


async def _get_ms_service(
    current_user: User, db: AsyncSession
) -> MoySkladService:
    result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(status_code=400, detail="МойСклад не подключён")
    token = decrypt_token(integration.moysklad_token)
    return MoySkladService(token)


@router.get("/moysklad/{kind}", response_model=List[MoySkladDocumentItem])
async def list_moysklad_documents(
    kind: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список МС-документов выбранного типа (supply/demand/loss/move/salesreturn)."""
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(
            status_code=400, detail=f"Неподдерживаемый тип документа: {kind}"
        )
    ms = await _get_ms_service(current_user, db)
    return await ms.get_documents(kind)


@router.get("/moysklad-supplies", response_model=List[MoySkladDocumentItem])
async def list_moysklad_supplies_alias(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-совместимый алиас → /moysklad/supply."""
    return await list_moysklad_documents("supply", current_user, db)


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    kind: Optional[DocumentKind] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).where(Document.user_id == current_user.id)
    if kind is not None:
        query = query.where(Document.kind == kind)
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    documents = result.scalars().all()
    return [_doc_to_response(d) for d in documents]


@router.post("/", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: CreateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan: list = []
    if body.moysklad_id:
        # Подгружаем план сборки из МС-документа: positions → expected_qty по товарам.
        # Если МС не подключён или запрос упал — план остаётся пустым (произвольная сборка).
        try:
            ms = await _get_ms_service(current_user, db)
            plan = await ms.build_plan(body.kind.value, body.moysklad_id)
        except HTTPException:
            pass
        except Exception as exc:
            # build_plan может упасть на любом этапе; не блокируем создание документа
            from app.core.logging import logger as _lg
            _lg.warning(
                "create_document.build_plan_failed",
                kind=body.kind.value,
                moysklad_id=body.moysklad_id,
                error=str(exc),
            )

    doc = Document(
        user_id=current_user.id,
        name=body.name,
        kind=body.kind,
        moysklad_id=body.moysklad_id,
        plan=plan,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _doc_to_response(doc)


@router.post("/{document_id}/refresh-plan", response_model=DocumentResponse)
async def refresh_plan(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Перетянуть план сборки из МС-документа (если он привязан)."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.moysklad_id:
        raise HTTPException(400, "Документ не привязан к МойСклад")

    ms = await _get_ms_service(current_user, db)
    doc.plan = await ms.build_plan(doc.kind.value, doc.moysklad_id)
    await db.commit()
    await db.refresh(doc)
    return _doc_to_response(doc)


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
    return _doc_to_response(doc)


@router.post("/{document_id}/process")
async def process_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Завершить документ: для supply — ввод в оборот в ЧЗ + обновление МС;
    для demand/loss/move/salesreturn — только обновление МС с записью кодов
    маркировки в positions[].trackingCodes.
    """
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.worker.tasks import process_document_task
    process_document_task.delay(str(document_id), str(current_user.id))

    doc.status = DocumentStatus.processing
    await db.commit()
    return {"status": "processing", "document_id": str(document_id)}


@router.post("/{document_id}/accept")
async def accept_document_alias(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-совместимый алиас → /process."""
    return await process_document(document_id, current_user, db)
