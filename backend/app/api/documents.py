from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from app.db.session import get_db
from app.db.models import User, Document, DocumentKind, DocumentStatus, Integration, Scan
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
    kind: DocumentKind = DocumentKind.demand
    moysklad_id: Optional[str] = None


class MoySkladDocumentItem(BaseModel):
    id: str
    name: str
    moment: Optional[str]
    customer_order_name: Optional[str] = None
    agent_name: Optional[str] = None


def _doc_to_response(doc: Document, scan_count: int = 0) -> DocumentResponse:
    # `doc.scans` НЕЛЬЗЯ дёргать из sync-кода — это lazy-relationship,
    # а сессия async (asyncpg). MissingGreenlet → 500.
    # Вызывающий должен передать scan_count явно (либо посчитать SELECT count,
    # либо использовать selectinload, либо знать что документ только что создан).
    return DocumentResponse(
        id=doc.id,
        moysklad_id=doc.moysklad_id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        scan_count=scan_count,
        plan=doc.plan or [],
        created_at=doc.created_at,
    )


async def _scan_count(db: AsyncSession, document_id: UUID) -> int:
    result = await db.execute(
        select(func.count(Scan.id)).where(Scan.document_id == document_id)
    )
    return int(result.scalar() or 0)


async def _scan_counts(db: AsyncSession, document_ids: List[UUID]) -> dict[UUID, int]:
    if not document_ids:
        return {}
    result = await db.execute(
        select(Scan.document_id, func.count(Scan.id))
        .where(Scan.document_id.in_(document_ids))
        .group_by(Scan.document_id)
    )
    return {doc_id: int(cnt) for doc_id, cnt in result.all()}


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


def _ensure_supported_kind(kind: str) -> None:
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Поддерживаются только отгрузочные документы: "
                f"{', '.join(sorted(SUPPORTED_KINDS))}"
            ),
        )


@router.get("/moysklad/{kind}", response_model=List[MoySkladDocumentItem])
async def list_moysklad_documents(
    kind: str,
    search: Optional[str] = Query(None, description="Поиск по номеру/контрагенту (полнотекстовый поиск МС)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список МС-документов выбранного типа (supply/demand/loss/move/salesreturn)."""
    _ensure_supported_kind(kind)
    ms = await _get_ms_service(current_user, db)
    return await ms.get_documents(kind, search=search)


@router.get("/moysklad-supplies", response_model=List[MoySkladDocumentItem])
async def list_moysklad_supplies_alias(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Старый алиас приёмки больше не используется."""
    raise HTTPException(status_code=410, detail="Приёмка отключена. Используйте отгрузочные документы.")


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    kind: Optional[DocumentKind] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).where(Document.user_id == current_user.id)
    if kind is not None:
        _ensure_supported_kind(kind.value)
        query = query.where(Document.kind == kind)
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    documents = result.scalars().all()
    counts = await _scan_counts(db, [d.id for d in documents])
    return [_doc_to_response(d, counts.get(d.id, 0)) for d in documents]


def _plan_source_kind(doc_kind: str) -> str:
    """Тип МС-документа, из позиций которого строится план.

    Списание (loss) не имеет собственных позиций в МС — его план берём из
    привязанной отгрузки (demand): «прикрепляем отгрузку как план списания».
    Для остальных типов источник плана совпадает с типом документа.
    """
    return "demand" if doc_kind == "loss" else doc_kind


@router.post("/", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: CreateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ensure_supported_kind(body.kind.value)
    plan: list = []
    if body.moysklad_id:
        # Подгружаем план сборки из МС-документа: positions → expected_qty по товарам.
        # Если МС не подключён или запрос упал — план остаётся пустым (произвольная сборка).
        try:
            ms = await _get_ms_service(current_user, db)
            plan = await ms.build_plan(_plan_source_kind(body.kind.value), body.moysklad_id)
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
    # Документ только что создан — сканов заведомо нет.
    return _doc_to_response(doc, scan_count=0)


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
    _ensure_supported_kind(doc.kind.value)
    if not doc.moysklad_id:
        raise HTTPException(400, "Документ не привязан к МойСклад")

    ms = await _get_ms_service(current_user, db)
    doc.plan = await ms.build_plan(_plan_source_kind(doc.kind.value), doc.moysklad_id)
    await db.commit()
    await db.refresh(doc)
    return _doc_to_response(doc, await _scan_count(db, doc.id))


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
    _ensure_supported_kind(doc.kind.value)
    return _doc_to_response(doc, await _scan_count(db, doc.id))


@router.get("/{document_id}/export.xlsx")
async def export_document_xlsx(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Выгрузить структуру документа в XLSX: наименование позиции + марка + кол-во.

    Агрегаты (короб/блок) разворачиваются в марки пачек (child_codes). Немаркированный
    товар (is_barcode) — строка с пустой маркой и количеством. SSCC «целиком» (is_box без
    child_codes) — строка с кодом SSCC и количеством. Включаются все сканы документа.
    """
    import io
    from urllib.parse import quote
    from fastapi.responses import Response
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    # Коды маркировки содержат управляющий разделитель GS (\x1d) и пр. control-символы —
    # openpyxl их запрещает (IllegalCharacterError). Вырезаем перед записью в ячейку.
    def _xl(v):
        return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    scans_res = await db.execute(
        select(Scan).where(Scan.document_id == document_id).order_by(Scan.scanned_at)
    )
    scans = scans_res.scalars().all()

    rows: List[tuple] = []
    for s in scans:
        name = s.product_name or "—"
        if s.child_codes:  # короб/блок развёрнут — по строке на марку пачки
            for cc in s.child_codes:
                rows.append((name, cc, 1))
        elif s.is_barcode:  # немаркированный товар — марка пустая, кол-во из скана
            rows.append((name, "", int(s.box_quantity or 1)))
        elif s.is_box:  # SSCC «целиком» — код короба + кол-во внутри
            rows.append((name, s.code, int(s.box_quantity or 1)))
        else:
            rows.append((name, s.code, 1))
    rows.sort(key=lambda r: (r[0], r[1]))

    wb = Workbook()
    ws = wb.active
    ws.title = "Отгрузка"
    ws.append(["Наименование позиции", "Марка", "Кол-во"])
    for r in rows:
        ws.append([_xl(r[0]), _xl(r[1]), r[2]])
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 45
    ws.column_dimensions["C"].width = 8
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)

    safe_name = (doc.name or "Документ").replace("/", "-").replace("\\", "-")
    filename = f"{safe_name}.xlsx"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=order.xlsx; filename*=UTF-8''{quote(filename)}"
        )
    }
    return Response(
        content=buf.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers=headers,
    )


@router.post("/{document_id}/process")
async def process_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Завершить документ: Celery обновляет МС (positions, при необходимости
    trackingCodes) и ставит статус accepted. API Честного Знака не вызывается.
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
    _ensure_supported_kind(doc.kind.value)

    # Блокируем процесс, если есть сканы со статусом unknown_product —
    # их нельзя отправить в МС без сопоставления товара.
    from sqlalchemy import func
    from app.db.models import Scan, ScanStatus

    unknown_q = await db.execute(
        select(func.count(Scan.id)).where(
            Scan.document_id == document_id,
            Scan.status == ScanStatus.unknown_product,
        )
    )
    unknown_count = unknown_q.scalar_one()
    if unknown_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Сначала сопоставьте товары для {unknown_count} "
                f"кодов с неизвестным GTIN"
            ),
        )

    from app.worker.tasks import process_document_task
    doc.status = DocumentStatus.processing
    await db.commit()

    process_document_task.delay(str(document_id), str(current_user.id))
    return {"status": "processing", "document_id": str(document_id)}


@router.post("/{document_id}/accept")
async def accept_document_alias(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-совместимый алиас → /process."""
    return await process_document(document_id, current_user, db)
