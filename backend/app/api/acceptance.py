"""Приёмка маркированной продукции через загрузку УПД (ФНС 5.03).

Кладовщик выбирает товарную группу, загружает XML-файл УПД — система распознаёт
позиции и коды маркировки, сопоставляет позиции с товарами МойСклад по GTIN
(сначала по запомненным связкам GtinProductMap, затем по каталогу) и показывает
таблицу. В v1 коды только сохраняются как сканы; запись в МС/ЧЗ — отдельный этап.

Документ ведётся как Document(kind=supply) и НЕ проходит обычный /process-флоу
отгрузки — поэтому отдельный роутер, без _ensure_supported_kind.
"""

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import (
    User, Document, DocumentKind, DocumentStatus, Integration, Scan, ScanStatus,
    GtinProductMap,
)
from app.db.session import get_db
from app.services.moysklad import MoySkladService
from app.services.chestnyznak import normalize_gtin_key
from app.services.upd_parser import parse_upd_503, UpdParseError, ParsedPosition

router = APIRouter(prefix="/acceptance", tags=["acceptance"])


# Товарные группы маркировки для выпадающего списка перед загрузкой УПД.
# code совпадает с pg-кодами Честного Знака (используются при будущей обработке КМ).
PRODUCT_GROUPS: list[dict[str, str]] = [
    {"code": "milk", "label": "Молочная продукция"},
    {"code": "water", "label": "Упакованная вода"},
    {"code": "beer", "label": "Пиво и слабоалкогольные напитки"},
    {"code": "softdrinks", "label": "Безалкогольные напитки и соки"},
    {"code": "tobacco", "label": "Табачная продукция"},
    {"code": "otp", "label": "Альтернативная табачная продукция"},
    {"code": "ncp", "label": "Никотиносодержащая продукция"},
    {"code": "shoes", "label": "Обувные товары"},
    {"code": "lp", "label": "Товары лёгкой промышленности"},
    {"code": "perfumery", "label": "Духи и туалетная вода"},
    {"code": "tires", "label": "Шины и покрышки"},
    {"code": "photo", "label": "Фотокамеры и лампы-вспышки"},
    {"code": "bio", "label": "БАД к пище"},
    {"code": "antiseptic", "label": "Антисептики"},
]
_PRODUCT_GROUP_CODES = {g["code"] for g in PRODUCT_GROUPS}


class ProductGroup(BaseModel):
    code: str
    label: str


class CreateAcceptanceDocRequest(BaseModel):
    name: str
    product_group: str
    # Привязка к существующему поступлению МС: КМ будут записаны в его позиции.
    moysklad_id: Optional[str] = None


class AcceptanceDocResponse(BaseModel):
    id: UUID
    name: str
    kind: DocumentKind
    status: DocumentStatus
    product_group: Optional[str] = None
    moysklad_id: Optional[str] = None
    scan_count: int = 0
    # Сколько позиций поступления МС подтянуто в план (для UI «привязано к МС»).
    plan_count: int = 0


class ImportPositionResult(BaseModel):
    name: str
    gtin: Optional[str] = None
    article: Optional[str] = None
    quantity: Optional[float] = None
    codes_count: int = 0
    packages_count: int = 0
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    matched: bool = False


class ImportUpdResponse(BaseModel):
    document_id: UUID
    positions: List[ImportPositionResult]
    created_scans: int
    skipped_duplicates: int
    unmatched_gtins: List[str]


async def _get_acceptance_doc(
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
        raise HTTPException(status_code=404, detail="Документ не найден")
    if doc.kind != DocumentKind.supply:
        raise HTTPException(status_code=400, detail="Документ не является приёмкой")
    return doc


async def _maybe_ms_service(
    current_user: User, db: AsyncSession
) -> Optional[MoySkladService]:
    """Сервис МС для резолва товаров по каталогу. None, если МС не подключён —
    тогда резолвим только по запомненным связкам GtinProductMap."""
    result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        return None
    try:
        return MoySkladService(decrypt_token(integration.moysklad_token))
    except Exception as exc:
        logger.warning("acceptance.ms_service_failed", error=str(exc))
        return None


@router.get("/product-groups", response_model=List[ProductGroup])
async def list_product_groups(current_user: User = Depends(get_current_user)):
    """Список товарных групп маркировки для выбора перед загрузкой УПД."""
    return PRODUCT_GROUPS


@router.post("/documents", response_model=AcceptanceDocResponse, status_code=201)
async def create_acceptance_document(
    body: CreateAcceptanceDocRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Создать документ приёмки (kind=supply) с выбранной товарной группой.

    Если передан moysklad_id — документ привязывается к существующему поступлению
    МС, его позиции подтягиваются в план (gtin→товар), и при загрузке УПД коды
    сопоставляются именно с этими позициями. Иначе — автономная приёмка без записи
    в МС (только сохранение кодов как сканов)."""
    pg = (body.product_group or "").strip()
    if pg not in _PRODUCT_GROUP_CODES:
        raise HTTPException(status_code=400, detail="Неизвестная товарная группа")
    name = (body.name or "").strip() or "Приёмка"

    moysklad_id = (body.moysklad_id or "").strip() or None
    plan: list[dict] = []
    if moysklad_id:
        # План из позиций поступления МС: gtin/product_id/expected_qty. Best-effort —
        # при сбое МС оставляем план пустым (резолв уйдёт в GtinProductMap/каталог).
        ms = await _maybe_ms_service(current_user, db)
        if ms is not None:
            try:
                plan = await ms.build_plan("supply", moysklad_id)
            except Exception as exc:
                logger.warning(
                    "acceptance.build_plan_failed",
                    moysklad_id=moysklad_id,
                    error=str(exc),
                )

    doc = Document(
        user_id=current_user.id,
        name=name,
        kind=DocumentKind.supply,
        status=DocumentStatus.draft,
        product_group=pg,
        moysklad_id=moysklad_id,
        plan=plan,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(
        "acceptance.doc_created",
        document_id=str(doc.id),
        product_group=pg,
        moysklad_id=moysklad_id,
        plan_count=len(plan),
        user_id=str(current_user.id),
    )
    return AcceptanceDocResponse(
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        product_group=doc.product_group,
        moysklad_id=doc.moysklad_id,
        scan_count=0,
        plan_count=len(plan),
    )


async def _resolve_product(
    gtin: Optional[str],
    user_id: UUID,
    ms: Optional[MoySkladService],
    db: AsyncSession,
    cache: dict[str, tuple[Optional[str], Optional[str]]],
    plan_map: dict[str, tuple[str, Optional[str]]],
) -> tuple[Optional[str], Optional[str]]:
    """(product_id, product_name) по GTIN: сначала позиции привязанного поступления
    МС (plan_map), затем запомненная связка, затем каталог МС."""
    if not gtin:
        return (None, None)
    if gtin in cache:
        return cache[gtin]

    # 0. Позиция привязанного поступления МС — приоритетнее всего: КМ должны лечь
    #    именно в товар из этого документа.
    gk = normalize_gtin_key(gtin)
    if gk and gk in plan_map:
        result = plan_map[gk]
        cache[gtin] = result
        return result

    # 1. Запомненное ручное соответствие.
    map_row = (
        await db.execute(
            select(GtinProductMap).where(
                GtinProductMap.user_id == user_id,
                GtinProductMap.gtin == gtin,
            )
        )
    ).scalar_one_or_none()
    if map_row:
        result = (map_row.product_id, map_row.product_name)
        cache[gtin] = result
        return result

    # 2. Поиск в каталоге МС по штрихкоду.
    if ms is not None:
        try:
            product = await ms.find_product_by_gtin(gtin)
        except Exception as exc:
            logger.warning("acceptance.find_product_failed", gtin=gtin, error=str(exc))
            product = None
        if product:
            result = (product.get("id"), (product.get("name") or "").strip() or None)
            cache[gtin] = result
            return result

    cache[gtin] = (None, None)
    return (None, None)


@router.post(
    "/documents/{document_id}/import-upd", response_model=ImportUpdResponse
)
async def import_upd(
    document_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Загрузить УПД 5.03: распарсить позиции, сопоставить с товарами МС по GTIN,
    сохранить коды маркировки как сканы документа."""
    doc = await _get_acceptance_doc(document_id, current_user, db)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        positions: list[ParsedPosition] = parse_upd_503(raw)
    except UpdParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ms = await _maybe_ms_service(current_user, db)

    # План привязанного поступления МС: gtin → (product_id, product_name).
    # Позиции УПД сопоставляются по GTIN именно с этими товарами.
    plan_map: dict[str, tuple[str, Optional[str]]] = {}
    for p in doc.plan or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("product_id")
        if not (pid and isinstance(pid, str)):
            continue
        name = (p.get("product_name") or "").strip() or None
        g = normalize_gtin_key(p.get("gtin"))
        if g:
            plan_map.setdefault(g, (pid, name))
        # GTIN упаковок (блок/короб) → тот же товар: КМ блока резолвятся в позицию
        # поступления, иначе помечаются «нужен товар».
        for pg in p.get("pack_gtins") or []:
            pgk = normalize_gtin_key(pg)
            if pgk:
                plan_map.setdefault(pgk, (pid, name))

    # Уже имеющиеся коды документа — чтобы не плодить дубли при повторной загрузке.
    existing_codes = set(
        (
            await db.execute(
                select(Scan.code).where(Scan.document_id == document_id)
            )
        ).scalars().all()
    )

    resolve_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
    results: list[ImportPositionResult] = []
    unmatched: set[str] = set()
    created = 0
    skipped = 0

    for pos in positions:
        product_id, product_name = await _resolve_product(
            pos.gtin, current_user.id, ms, db, resolve_cache, plan_map
        )
        matched = product_id is not None
        if pos.gtin and not matched:
            unmatched.add(pos.gtin)

        status = ScanStatus.valid if matched else ScanStatus.unknown_product

        # КИЗ — штучные КМ.
        for code in pos.codes:
            code = code.strip()
            if not code or code in existing_codes:
                skipped += 1
                continue
            existing_codes.add(code)
            db.add(
                Scan(
                    document_id=document_id,
                    code=code,
                    gtin=pos.gtin,
                    moysklad_product_id=product_id,
                    product_name=product_name,
                    status=status,
                )
            )
            created += 1

        # НомУпак — агрегаты/SSCC: сохраняем целиком (без разворота в v1).
        for sscc in pos.packages:
            sscc = sscc.strip()
            if not sscc or sscc in existing_codes:
                skipped += 1
                continue
            existing_codes.add(sscc)
            db.add(
                Scan(
                    document_id=document_id,
                    code=sscc,
                    gtin=pos.gtin,
                    moysklad_product_id=product_id,
                    product_name=product_name,
                    status=status,
                    is_box=True,
                )
            )
            created += 1

        results.append(
            ImportPositionResult(
                name=pos.name,
                gtin=pos.gtin,
                article=pos.article,
                quantity=pos.quantity,
                codes_count=len(pos.codes),
                packages_count=len(pos.packages),
                product_id=product_id,
                product_name=product_name,
                matched=matched,
            )
        )

    await db.commit()
    logger.info(
        "acceptance.upd_imported",
        document_id=str(document_id),
        positions=len(positions),
        created_scans=created,
        skipped=skipped,
        unmatched=len(unmatched),
        user_id=str(current_user.id),
    )

    return ImportUpdResponse(
        document_id=document_id,
        positions=results,
        created_scans=created,
        skipped_duplicates=skipped,
        unmatched_gtins=sorted(unmatched),
    )


@router.get("/documents/{document_id}", response_model=AcceptanceDocResponse)
async def get_acceptance_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_acceptance_doc(document_id, current_user, db)
    count = int(
        (
            await db.execute(
                select(func.count(Scan.id)).where(Scan.document_id == document_id)
            )
        ).scalar()
        or 0
    )
    return AcceptanceDocResponse(
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        product_group=doc.product_group,
        moysklad_id=doc.moysklad_id,
        scan_count=count,
        plan_count=len(doc.plan or []),
    )
