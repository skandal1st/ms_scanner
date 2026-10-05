from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime
import httpx

from app.db.session import get_db
from app.db.models import User, Document, DocumentKind, DocumentStatus, Integration, OrganizationProfile, Scan
from app.api.deps import get_current_user, get_active_organization_profile
from app.services.moysklad import (MoySkladService, SUPPORTED_KINDS, customer_order_links,
    customer_order_empty_message, customer_order_direct_shipment_count, shipment_matches_customer_order)
from app.services.customer_order_filters import resolve_order_filter
from app.core.security import decrypt_token

router = APIRouter(prefix="/documents", tags=["documents"])


class PlanItem(BaseModel):
    gtin: Optional[str]
    gtins: List[str] = []
    pack_gtins: List[str] = []
    pack_quantities: dict[str, int] = {}
    product_id: Optional[str]
    product_name: str
    expected_qty: int
    marked: Optional[bool] = None


class DocumentResponse(BaseModel):
    id: UUID
    moysklad_id: Optional[str]
    name: str
    kind: DocumentKind
    status: DocumentStatus
    scan_count: int = 0
    plan: List[PlanItem] = []
    error_message: Optional[str] = None
    processing_progress: Optional[dict] = None
    cz_doc_ids: Optional[list[dict]] = None
    organization_profile_id: Optional[UUID] = None
    moysklad_organization_id: Optional[str] = None
    moysklad_store_id: Optional[str] = None
    customer_order_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateDocumentRequest(BaseModel):
    name: str
    kind: DocumentKind = DocumentKind.demand
    moysklad_id: Optional[str] = None


class MoySkladDocumentItem(BaseModel):
    state_name: Optional[str] = None
    state_color: Optional[int] = None
    id: str
    name: str
    moment: Optional[str]
    customer_order_name: Optional[str] = None
    agent_name: Optional[str] = None
    shipment_count: Optional[int] = None
    retail_sale_count: int = 0
    empty_shipments_message: Optional[str] = None


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
        error_message=doc.error_message,
        processing_progress=doc.processing_progress,
        cz_doc_ids=doc.cz_doc_ids,
        organization_profile_id=doc.organization_profile_id,
        moysklad_organization_id=doc.moysklad_organization_id,
        moysklad_store_id=doc.moysklad_store_id,
        customer_order_name=getattr(doc, "customer_order_name", None),
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


def _customer_order_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
        return HTTPException(403, "Нет доступа к заказам покупателей. Обновите права решения в МойСкладе.")
    return HTTPException(502, "Не удалось загрузить заказ или его отгрузки из МойСклада. Повторите попытку.")


@router.get("/customer-orders", response_model=List[MoySkladDocumentItem])
async def list_customer_orders(
    search: Optional[str] = None, offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile), db: AsyncSession = Depends(get_db),
    filter_id: Optional[UUID] = None,
):
    selected_filter = resolve_order_filter(profile, filter_id)
    ms = await _get_ms_service(current_user, db)
    try:
        rows = await ms.get_customer_orders(profile.moysklad_organization_id, search, offset=offset,
                                            **({"order_filter": selected_filter} if selected_filter else {}))
    except Exception as exc:
        raise _customer_order_error(exc) from exc
    return [MoySkladDocumentItem(id=row["id"], name=row.get("name") or "Без номера",
        moment=row.get("moment"), agent_name=(row.get("agent") or {}).get("name"),
        shipment_count=customer_order_direct_shipment_count(row),
        state_name=(row.get('state') or {}).get('name'), state_color=(row.get('state') or {}).get('color'),
        retail_sale_count=len(customer_order_links(row, "retaildemand")),
        empty_shipments_message=customer_order_empty_message(row)) for row in rows]


@router.get("/customer-orders/{order_id}/shipments", response_model=List[MoySkladDocumentItem])
async def customer_order_shipments(
    order_id: UUID, current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile), db: AsyncSession = Depends(get_db),
):
    ms = await _get_ms_service(current_user, db)
    try:
        order = await ms.get_customer_order(str(order_id))
        if profile.moysklad_organization_id and _ref_id(order, "organization") != profile.moysklad_organization_id:
            raise HTTPException(403, "Заказ покупателя относится к другому юрлицу")
        rows = await ms.get_customer_order_demands(str(order_id), profile.moysklad_organization_id, order=order)
    except HTTPException:
        raise
    except Exception as exc:
        raise _customer_order_error(exc) from exc
    completed = set((await db.execute(select(Document.moysklad_id).where(
        Document.user_id == current_user.id, Document.organization_profile_id == profile.id,
        Document.kind == DocumentKind.demand, Document.status == DocumentStatus.accepted,
        Document.moysklad_id.in_([row["id"] for row in rows]),
    ))).scalars().all())
    return [MoySkladDocumentItem(id=row["id"], name=row.get("name") or "Без номера", moment=row.get("moment"),
        customer_order_name=order.get("name"), agent_name=(row.get("agent") or {}).get("name")) for row in rows
        if row["id"] not in completed and not row.get("deleted")
        and (not profile.moysklad_organization_id or _ref_id(row, "organization") == profile.moysklad_organization_id)]


@router.get("/moysklad/{kind}", response_model=List[MoySkladDocumentItem])
async def list_moysklad_documents(
    kind: str,
    search: Optional[str] = Query(None, description="Поиск по номеру/контрагенту (полнотекстовый поиск МС)"),
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    """Список МС-документов выбранного типа (supply/demand/loss/move/salesreturn)."""
    _ensure_supported_kind(kind)
    ms = await _get_ms_service(current_user, db)
    return await ms.get_documents(
        kind,
        search=search,
        organization_id=profile.moysklad_organization_id,
    )


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
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).where(
        Document.user_id == current_user.id,
        Document.organization_profile_id == profile.id,
    )
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


def _ref_id(payload: dict, field: str) -> Optional[str]:
    ref = payload.get(field) or {}
    meta = ref.get("meta") or {}
    if ref.get("id"):
        return str(ref["id"])
    href = meta.get("href") or ""
    return href.rstrip("/").rsplit("/", 1)[-1] if href else None


async def _profile_for_organization(
    db: AsyncSession,
    user_id,
    organization_id: Optional[str],
    fallback: OrganizationProfile,
) -> OrganizationProfile:
    if not organization_id or fallback.moysklad_organization_id == organization_id:
        return fallback
    found = (
        await db.execute(
            select(OrganizationProfile).where(
                OrganizationProfile.user_id == user_id,
                OrganizationProfile.moysklad_organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if found:
        return found
    if fallback.moysklad_organization_id is None:
        fallback.moysklad_organization_id = organization_id
        return fallback
    profile = OrganizationProfile(
        user_id=user_id,
        moysklad_organization_id=organization_id,
        name=f"Юрлицо {organization_id[:8]}",
        is_default=False,
    )
    db.add(profile)
    await db.flush()
    return profile


@router.post("/", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: CreateDocumentRequest,
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    _ensure_supported_kind(body.kind.value)
    verified_ms_doc = None
    if body.moysklad_id and body.kind == DocumentKind.demand:
        from app.services.document_guard import lock_ms_document
        await lock_ms_document(db, current_user.id, body.kind, body.moysklad_id)
        ms = await _get_ms_service(current_user, db)
        try:
            verified_ms_doc = await ms.get_document('demand', body.moysklad_id)
        except Exception as exc:
            raise HTTPException(502, 'Не удалось проверить отгрузку в МойСкладе. Повторите попытку.') from exc
        from app.services.shipment_guard import ensure_active_shipment, ensure_no_stranded_scans
        try:
            ensure_active_shipment(verified_ms_doc)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        existing = (await db.execute(select(Document).where(
            Document.user_id == current_user.id, Document.moysklad_id == body.moysklad_id,
            Document.kind == DocumentKind.demand, Document.status != DocumentStatus.accepted,
        ).order_by(Document.created_at.desc()))).scalars().first()
        if existing is not None:
            await ensure_no_stranded_scans(db, ms, current_user.id, existing.organization_profile_id,
                                          existing.moysklad_customer_order_id, body.moysklad_id)
            return _doc_to_response(existing, await _scan_count(db, existing.id))
    plan: list = []
    ms_organization_id: Optional[str] = None
    ms_store_id: Optional[str] = None
    if body.moysklad_id:
        # Подгружаем план сборки из МС-документа: positions → expected_qty по товарам.
        # Если МС не подключён или запрос упал — план остаётся пустым (произвольная сборка).
        try:
            ms = await _get_ms_service(current_user, db)
            ms_doc = verified_ms_doc or await ms.get_document(_plan_source_kind(body.kind.value), body.moysklad_id)
            ms_organization_id = _ref_id(ms_doc, "organization")
            ms_store_id = _ref_id(ms_doc, "store")
            profile = await _profile_for_organization(
                db, current_user.id, ms_organization_id, profile
            )
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
        organization_profile_id=profile.id,
        moysklad_organization_id=ms_organization_id,
        moysklad_store_id=ms_store_id,
        plan=plan,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    # Документ только что создан — сканов заведомо нет.
    return _doc_to_response(doc, scan_count=0)


class ResolveDocRequest(BaseModel):
    moysklad_id: str
    kind: DocumentKind = DocumentKind.demand
    customer_order_id: Optional[UUID] = None


@router.post("/resolve", response_model=DocumentResponse)
async def resolve_document(
    body: ResolveDocRequest,
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    """Найти-или-создать наш Document по документу МС (kind + moysklad_id).

    Для попапа, открытого из кнопки МС: повторное открытие той же отгрузки не
    должно плодить дубли. Берём самый свежий незавершённый документ с этим
    moysklad_id; если нет — создаём с именем и планом из МС."""
    _ensure_supported_kind(body.kind.value)

    order_name = None
    from app.services.document_guard import lock_ms_document
    await lock_ms_document(db, current_user.id, body.kind, body.moysklad_id)
    verified_ms_doc = None
    if body.customer_order_id:
        if body.kind != DocumentKind.demand:
            raise HTTPException(400, "Из заказа покупателя можно открыть только отгрузку")
        ms = await _get_ms_service(current_user, db)
        try:
            verified_ms_doc = await ms.get_document("demand", body.moysklad_id)
            order = await ms.get_customer_order(str(body.customer_order_id))
        except Exception as exc:
            raise _customer_order_error(exc) from exc
        if not shipment_matches_customer_order(verified_ms_doc, order, str(body.customer_order_id)):
            raise HTTPException(409, "Отгрузка больше не связана с выбранным заказом. Обновите список отгрузок.")
        if profile.moysklad_organization_id and any(_ref_id(item, "organization") != profile.moysklad_organization_id
                                                 for item in (order, verified_ms_doc)):
            raise HTTPException(403, "Заказ или отгрузка относятся к другому юрлицу")
        order_name = order.get("name")

    from app.services.shipment_guard import ensure_active_shipment, ensure_no_stranded_scans
    if body.kind == DocumentKind.demand:
        if verified_ms_doc is None:
            ms = await _get_ms_service(current_user, db)
            try:
                verified_ms_doc = await ms.get_document('demand', body.moysklad_id)
            except Exception as exc:
                raise HTTPException(502, 'Не удалось проверить отгрузку в МойСкладе. Повторите попытку.') from exc
        try:
            ensure_active_shipment(verified_ms_doc)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    existing = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.user_id == current_user.id,
                    *([Document.organization_profile_id == profile.id] if body.customer_order_id else []),
                    Document.moysklad_id == body.moysklad_id,
                    Document.kind == body.kind,
                    *([] if body.customer_order_id else [Document.status != DocumentStatus.accepted]),
                )
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        if body.customer_order_id and existing.status == DocumentStatus.accepted:
            raise HTTPException(409, 'Отгрузка уже собрана. Обновите список отгрузок заказа.')
        if body.kind == DocumentKind.demand:
            await ensure_no_stranded_scans(db, ms, current_user.id, existing.organization_profile_id,
                                          body.customer_order_id or existing.moysklad_customer_order_id,
                                          body.moysklad_id)
        from app.services.legacy_pack_plan import refresh_legacy_pack_plan
        if existing.status == DocumentStatus.draft and any('pack_quantities' not in p for p in existing.plan or []):
            await refresh_legacy_pack_plan(db, existing, await _get_ms_service(current_user, db), body.kind.value)
        if body.customer_order_id:
            existing.moysklad_customer_order_id = str(body.customer_order_id)
            existing.customer_order_name = order_name
            await db.commit()
        return _doc_to_response(existing, await _scan_count(db, existing.id))

    ms = await _get_ms_service(current_user, db)
    name: Optional[str] = None
    ms_doc: dict = {}
    try:
        ms_doc = verified_ms_doc or await ms.get_document(body.kind.value, body.moysklad_id)
        name = ms_doc.get("name")
    except Exception:
        pass  # имя не критично — подставим дефолт
    ms_organization_id = _ref_id(ms_doc, "organization")
    ms_store_id = _ref_id(ms_doc, "store")
    profile = await _profile_for_organization(
        db, current_user.id, ms_organization_id, profile
    )
    if body.kind == DocumentKind.demand:
        await ensure_no_stranded_scans(db, ms, current_user.id, profile.id,
                                      body.customer_order_id, body.moysklad_id)
    plan: list = []
    try:
        # objectId из кнопки МС — документ самого этого типа, поэтому план строим
        # напрямую по kind (в отличие от create_document, где loss сеется из demand).
        plan = await ms.build_plan(body.kind.value, body.moysklad_id)
    except Exception as exc:
        if body.customer_order_id:
            raise HTTPException(502, "Не удалось загрузить план отгрузки из МойСклада. Повторите попытку.") from exc
        from app.core.logging import logger as _lg
        _lg.warning(
            "resolve_document.build_plan_failed",
            kind=body.kind.value,
            moysklad_id=body.moysklad_id,
            error=str(exc),
        )

    doc = Document(
        user_id=current_user.id,
        name=name or f"Документ {body.moysklad_id[:8]}",
        kind=body.kind,
        moysklad_id=body.moysklad_id,
        organization_profile_id=profile.id,
        moysklad_organization_id=ms_organization_id,
        moysklad_store_id=ms_store_id,
        moysklad_customer_order_id=str(body.customer_order_id) if body.customer_order_id else None,
        customer_order_name=order_name,
        plan=plan,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _doc_to_response(doc, scan_count=0)


@router.post("/{document_id}/refresh-plan", response_model=DocumentResponse)
async def refresh_plan(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Перетянуть план сборки из МС-документа (если он привязан)."""
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, current_user.id)

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
    from app.services.legacy_pack_plan import refresh_legacy_pack_plan
    if doc.moysklad_id and doc.status == DocumentStatus.draft and any('pack_quantities' not in p for p in doc.plan or []):
        await refresh_legacy_pack_plan(db, doc, await _get_ms_service(current_user, db), _plan_source_kind(doc.kind.value))
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
        if s.child_codes and not (s.is_box or s.keep_aggregate):
            for cc in s.child_codes:
                rows.append((name, cc, 1))
        elif s.is_barcode:  # немаркированный товар — марка пустая, кол-во из скана
            rows.append((name, "", int(s.box_quantity or 1)))
        elif s.is_box:  # SSCC «целиком» — код короба + кол-во внутри
            rows.append((name, s.code, int(s.box_quantity or 1)))
        else:
            rows.append((name, s.code, int(s.box_quantity or 1)))
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


@router.post("/{document_id}/verify")
async def verify_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Пакетно проверить в ЧЗ все локально отсканированные марки (status=scanned).

    Основной флоу: скан ставит `scanned` (только формат GS1, без ЧЗ), а проверка
    в ЧЗ идёт здесь одной операцией. Прогресс — через WS scan_update по каждому скану,
    завершение — событие verify_done.
    """
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, current_user.id)

    from sqlalchemy import func
    from app.db.models import Scan, ScanStatus

    pending_q = await db.execute(
        select(func.count(Scan.id)).where(
            Scan.document_id == document_id,
            Scan.status == ScanStatus.scanned,
            Scan.is_box.is_(False),
        )
    )
    to_check = pending_q.scalar_one()

    from app.worker.tasks import verify_document_task
    verify_document_task.delay(str(document_id), str(current_user.id))
    return {"status": "verifying", "document_id": str(document_id), "count": to_check}


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
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, current_user.id)
    if doc.kind not in (DocumentKind.demand, DocumentKind.supply):
        raise HTTPException(400, "Для списания используйте отправку в Честный Знак")
    if not doc.moysklad_id:
        raise HTTPException(409, "Сначала выберите документ МойСклад")
    ms = await _get_ms_service(current_user, db)
    if doc.kind == DocumentKind.demand:
        from app.services.shipment_guard import ensure_active_shipment
        try:
            shipment = await ms.get_document('demand', doc.moysklad_id)
        except Exception as exc:
            raise HTTPException(502, 'Не удалось проверить отгрузку в МойСкладе. Повторите попытку.') from exc
        try:
            ensure_active_shipment(shipment)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    from sqlalchemy import func
    from app.db.models import Scan, ScanStatus

    # Не отправляем в МС непроверенные марки: сначала «Проверить марки».
    scanned_q = await db.execute(
        select(func.count(Scan.id)).where(
            Scan.document_id == document_id,
            Scan.status.in_([ScanStatus.scanned, ScanStatus.pending]),
        )
    )
    scanned_count = scanned_q.scalar_one()
    if scanned_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Сначала проверьте марки ({scanned_count} не проверено) — "
                f"нажмите «Проверить марки»"
            ),
        )

    # Блокируем процесс, если есть сканы со статусом unknown_product —
    # их нельзя отправить в МС без сопоставления товара.
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
    count = (await db.execute(select(func.count(Scan.id)).where(
        Scan.document_id == document_id, Scan.status.in_([ScanStatus.valid, ScanStatus.overflow])
    ))).scalar_one()
    if not count:
        raise HTTPException(409, "Нет проверенных марок или товаров для отправки")
    doc.status = DocumentStatus.processing
    doc.error_message = None
    doc.processing_progress = {"sent": 0, "total": count, "stage": "preparing"}
    await db.commit()

    try:
        process_document_task.delay(str(document_id), str(current_user.id))
    except Exception:
        doc.status = DocumentStatus.draft
        doc.error_message = "Не удалось поставить отправку в очередь. Повторите попытку."
        await db.commit()
        raise HTTPException(503, doc.error_message)
    return {"status": "processing", "document_id": str(document_id)}


@router.post("/{document_id}/accept")
async def accept_document_alias(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-совместимый алиас → /process."""
    return await process_document(document_id, current_user, db)


@router.post('/{document_id}/start-collection')
async def begin_collection(document_id: UUID, current_user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(select(Document).where(
        Document.id == document_id, Document.user_id == current_user.id,
    ))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, 'Документ не найден')
    if doc.kind != DocumentKind.demand or not doc.moysklad_id:
        return {'status': 'started'}
    from app.services.collection_start import start_collection
    await start_collection(db, doc, await _get_ms_service(current_user, db))
    return {'status': 'started'}
