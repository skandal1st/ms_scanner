"""Приёмка маркированной продукции через загрузку УПД (ФНС 5.03).

Кладовщик выбирает товарную группу, загружает XML-файл УПД — система распознаёт
позиции и коды маркировки, сопоставляет позиции с товарами МойСклад по GTIN
(сначала по запомненным связкам GtinProductMap, затем по каталогу) и показывает
таблицу. В v1 коды только сохраняются как сканы; запись в МС/ЧЗ — отдельный этап.

Документ ведётся как Document(kind=supply) и НЕ проходит обычный /process-флоу
отгрузки — поэтому отдельный роутер, без _ensure_supported_kind.
"""

from collections import Counter
from hashlib import sha256
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_full_edition, get_active_organization_profile
from app.api.documents import _profile_for_organization, _ref_id
from app.core.config import settings
from app.core.logging import logger
from app.services.saby import INCOMING_PENDING_STATE_CODES
from app.core.security import decrypt_token
from app.db.models import (
    User, Document, DocumentKind, DocumentStatus, Integration, OrganizationProfile, Scan, ScanStatus,
    GtinProductMap, EdoDocument,
)
from app.db.session import get_db
from app.services.moysklad import MoySkladService
from app.services.chestnyznak import (
    ChestnyZnakService,
    normalize_gtin_key,
    parse_gs1_km_gtin_serial,
    extract_gtin,
    is_sscc,
    normalize_sscc,
    CZ_PRODUCT_GROUP_CATALOG,
    CZ_PRODUCT_GROUP_CODES,
    normalize_product_groups,
)
from app.services.upd_parser import parse_upd_503, UpdParseError, ParsedPosition, ParsedUpd

router = APIRouter(prefix="/acceptance", tags=["acceptance"])


# Товарные группы маркировки — единый справочник из сервиса ЧЗ (тот же список, что и
# чекбоксы в настройках клиента). code совпадает с pg-кодами Честного Знака.
PRODUCT_GROUPS = CZ_PRODUCT_GROUP_CATALOG
_PRODUCT_GROUP_CODES = CZ_PRODUCT_GROUP_CODES


class ProductGroup(BaseModel):
    code: str
    label: str


class CreateAcceptanceDocRequest(BaseModel):
    name: str
    product_group: str
    # Привязка к существующему поступлению МС: КМ будут записаны в его позиции.
    moysklad_id: Optional[str] = None


class AcceptanceDocResponse(BaseModel):
    cz_check_enabled: bool = True
    id: UUID
    name: str
    kind: DocumentKind
    status: DocumentStatus
    product_group: Optional[str] = None
    moysklad_id: Optional[str] = None
    scan_count: int = 0
    # Сколько позиций поступления МС подтянуто в план (для UI «привязано к МС»).
    plan_count: int = 0
    # Причина неуспешной отправки в МС (напр. истёк токен ЧЗ) — показывается на
    # странице приёмки вместо ложного «МойСклад ещё обрабатывает».
    error_message: Optional[str] = None
    processing_progress: Optional[dict] = None


class ImportPositionResult(BaseModel):
    name: str
    xml_index: Optional[int] = None
    source_codes: Optional[List[str]] = None
    link_by_line: bool = False
    gtin: Optional[str] = None
    article: Optional[str] = None
    quantity: Optional[float] = None
    codes_count: int = 0
    packages_count: int = 0
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    matched: bool = False
    line_number: Optional[int] = None
    # Цена за единицу С НДС (руб.) и ставка НДС (НалСт, %) из УПД — для предпросмотра.
    price: Optional[float] = None
    vat: Optional[int] = None


class ImportUpdResponse(BaseModel):
    document_id: UUID
    positions: List[ImportPositionResult]
    created_scans: int
    skipped_duplicates: int
    unmatched_gtins: List[str]
    # Документные итоги из <ВсегоОпл> УПД: сумма с НДС и итоговая сумма НДС.
    total_amount: Optional[float] = None
    total_vat: Optional[float] = None


class ImportMarksRequest(BaseModel):
    """Ручная загрузка списка марок в приёмку (альтернатива УПД)."""
    codes: List[str]


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


async def _maybe_cz_service(
    current_user: User, db: AsyncSession, profile_id=None
) -> Optional[ChestnyZnakService]:
    """Сервис ЧЗ для разворота коробов на импорте. None, если токен ЧЗ отсутствует
    или просрочен — тогда короба остаются unknown_product и определятся при отправке
    (воркер развернёт их сам). Разворот на импорте нужен лишь для корректного
    предпросмотра товара в таблице приёмки."""
    if profile_id:
        result = await db.execute(
            select(OrganizationProfile).where(
                OrganizationProfile.id == profile_id,
                OrganizationProfile.user_id == current_user.id,
            )
        )
    else:
        result = await db.execute(
            select(Integration).where(Integration.user_id == current_user.id)
        )
    integ = result.scalar_one_or_none()
    if not integ or getattr(integ, 'acceptance_cz_check_enabled', True) is False or not integ.cz_token:
        return None
    if (
        integ.cz_token_expires_at is not None
        and integ.cz_token_expires_at <= datetime.now(timezone.utc)
    ):
        return None
    try:
        token = decrypt_token(integ.cz_token)
    except Exception as exc:
        logger.warning("acceptance.cz_service_failed", error=str(exc))
        return None
    return ChestnyZnakService(
        token=token,
        mock=settings.CZ_MOCK_MODE,
        product_groups=list(integ.cz_product_groups or []),
    )


async def _expand_box_via_cz(
    code: str, cz: ChestnyZnakService
) -> Optional[dict]:
    """Развернуть агрегат (короб/блок) через ЧЗ: unit-GTIN вложенной пачки + листовые
    КМ. None, если код не агрегат / не найден / ошибка (best-effort, не бросает).

    GTIN агрегата (напр. под GS1 AI 02 — «GTIN вложенных товаров») ≠ unit-GTIN пачки,
    поэтому товар резолвим по GTIN первого ребёнка, а не по коду короба."""
    try:
        info = await cz.get_code_info(code)
    except Exception as exc:
        logger.warning("acceptance.expand_box_failed", code=code[:32], error=str(exc))
        return None
    if not (info and info.is_aggregate and info.children):
        return None
    child_gtin = extract_gtin(info.children[0])
    gk = normalize_gtin_key(child_gtin) if child_gtin else None
    return {
        "gtin": gk,
        "children": list(info.children),
        "qty": len(info.children),
        "product_name": info.product_name,
    }


_PG_LABELS = {g["code"]: g["label"] for g in CZ_PRODUCT_GROUP_CATALOG}


@router.get("/product-groups", response_model=List[ProductGroup])
async def list_product_groups(
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    """Список товарных групп маркировки для выбора перед загрузкой УПД.

    Ограничиваем группами, выбранными клиентом в настройках (Integration.cz_product_groups):
    если он отметил конкретные группы — показываем только их; если ничего не выбрано —
    весь справочник.
    """
    selected = normalize_product_groups(profile.cz_product_groups)
    if selected:
        return [ProductGroup(code=c, label=_PG_LABELS[c]) for c in selected]
    return PRODUCT_GROUPS


@router.post("/documents", response_model=AcceptanceDocResponse, status_code=201)
async def create_acceptance_document(
    body: CreateAcceptanceDocRequest,
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
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
    ms_organization_id = profile.moysklad_organization_id
    ms_store_id = None
    if moysklad_id:
        # План из позиций поступления МС: gtin/product_id/expected_qty. Best-effort —
        # при сбое МС оставляем план пустым (резолв уйдёт в GtinProductMap/каталог).
        ms = await _maybe_ms_service(current_user, db)
        if ms is not None:
            try:
                ms_doc = await ms.get_document("supply", moysklad_id)
                ms_organization_id = _ref_id(ms_doc, "organization")
                ms_store_id = _ref_id(ms_doc, "store")
                profile = await _profile_for_organization(
                    db, current_user.id, ms_organization_id, profile
                )
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
        organization_profile_id=profile.id,
        moysklad_organization_id=ms_organization_id,
        moysklad_store_id=ms_store_id,
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
        cz_check_enabled=getattr(profile, 'acceptance_cz_check_enabled', True),
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        product_group=doc.product_group,
        moysklad_id=doc.moysklad_id,
        scan_count=0,
        plan_count=len(plan),
    )


# ── Приёмка из ЭДО (Saby): входящие УПД → документ приёмки ──────────────────────


class EdoIncomingDoc(BaseModel):
    external_id: str
    number: Optional[str] = None
    date: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_inn: Optional[str] = None
    state_name: Optional[str] = None


class EdoImportRequest(BaseModel):
    external_id: str
    product_group: str
    moysklad_id: Optional[str] = None
    name: Optional[str] = None


class EdoImportResponse(BaseModel):
    document: AcceptanceDocResponse
    import_result: ImportUpdResponse


def _edo_incoming_query(user_id):
    """Pending incoming UPDs with confirmed marks, not yet imported locally."""
    return (
        select(EdoDocument)
        .where(
            EdoDocument.user_id == user_id,
            EdoDocument.direction == "Входящий",
            EdoDocument.upd_link.isnot(None),
            EdoDocument.accepted_document_id.is_(None),
            EdoDocument.state_code.in_(INCOMING_PENDING_STATE_CODES),
            EdoDocument.marks_parsed.is_(True),
            EdoDocument.codes_total > 0,
        )
        .order_by(EdoDocument.created_at.desc())
    )


@router.get("/edo/incoming", response_model=List[EdoIncomingDoc])
async def list_edo_incoming(
    refresh: bool = False,
    current_user: User = Depends(require_full_edition),
    db: AsyncSession = Depends(get_db),
):
    """Входящие УПД (Поступление) для приёмки — из БД (наполняется периодическим синком).

    refresh=1 — сначала живой скан ленты Saby (upsert новых входящих), затем чтение из БД.
    Так бейдж/список отражают мониторинг, а кнопка «Обновить» подтягивает свежие сразу."""
    if refresh:
        from app.services.edo_sync import scan_incoming_upds
        from app.services.saby import SabyError, SabyAuthError

        integ = (
            await db.execute(select(Integration).where(Integration.user_id == current_user.id))
        ).scalar_one_or_none()
        if integ is None or (not integ.saby_app_client_id and not integ.saby_login):
            raise HTTPException(
                status_code=400, detail="ЭДО (Saby) не подключён — заполните доступ в настройках"
            )
        try:
            await scan_incoming_upds(db, integ)
        except SabyAuthError as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось авторизоваться в Saby: {exc}")
        except SabyError as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка ЭДО Saby: {exc}")

    rows = (await db.execute(_edo_incoming_query(current_user.id))).scalars().all()
    return [
        EdoIncomingDoc(
            external_id=r.external_id,
            number=r.number,
            date=r.doc_date,
            counterparty_name=r.counterparty_name,
            counterparty_inn=r.counterparty_inn,
            state_name=r.state_name,
        )
        for r in rows
    ]


@router.get("/edo/incoming-count")
async def edo_incoming_count(
    current_user: User = Depends(require_full_edition),
    db: AsyncSession = Depends(get_db),
):
    """Число новых входящих УПД (не принятых) — для бейджа на странице приёмки."""
    n = (
        await db.execute(
            select(func.count()).select_from(_edo_incoming_query(current_user.id).subquery())
        )
    ).scalar() or 0
    return {"count": int(n)}


@router.post("/edo/import", response_model=EdoImportResponse)
async def import_edo_upd(
    body: EdoImportRequest,
    current_user: User = Depends(require_full_edition),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    """Создать приёмку из входящего УПД ЭДО: скачать XML из Saby и импортировать коды."""
    from app.services.edo_sync import _client
    from app.services.saby import SabyError, SabyAuthError

    pg = (body.product_group or "").strip()
    if pg not in _PRODUCT_GROUP_CODES:
        raise HTTPException(status_code=400, detail="Неизвестная товарная группа")

    integ = (
        await db.execute(select(Integration).where(Integration.user_id == current_user.id))
    ).scalar_one_or_none()
    client = _client(integ) if integ else None
    if client is None:
        raise HTTPException(status_code=400, detail="ЭДО (Saby) не подключён")

    edo_row = (
        await db.execute(
            select(EdoDocument).where(
                EdoDocument.user_id == current_user.id,
                EdoDocument.external_id == body.external_id,
            )
        )
    ).scalar_one_or_none()
    if edo_row is None or not edo_row.upd_link:
        raise HTTPException(
            status_code=409, detail="Документ ЭДО не найден — обновите список входящих"
        )
    if edo_row.accepted_document_id is not None:
        raise HTTPException(status_code=409, detail="Этот УПД уже принят")
    if edo_row.state_code not in INCOMING_PENDING_STATE_CODES:
        raise HTTPException(status_code=409, detail="УПД уже обработан в ЭДО или не ожидает приёмки. Обновите список.")
    link = edo_row.upd_link

    try:
        auth = await client.authenticate()
        raw = await client.download(auth, link)
    except SabyAuthError as exc:
        raise HTTPException(status_code=400, detail=f"Авторизация Saby: {exc}")
    except SabyError as exc:
        raise HTTPException(status_code=502, detail=f"Скачивание УПД из Saby: {exc}")
    if not raw:
        raise HTTPException(status_code=502, detail="Пустой файл УПД из ЭДО")
    from app.services.edo_sync import incoming_mark_count
    from app.services.upd_parser import UpdParseError
    try:
        count = incoming_mark_count(raw)
    except UpdParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not count:
        edo_row.marks_parsed = True
        edo_row.codes_total = 0
        await db.commit()
        raise HTTPException(status_code=409, detail="В УПД нет кодов маркировки. Обновите список входящих.")

    # План из поступления МС (если привязано) — как в create_acceptance_document.
    moysklad_id = (body.moysklad_id or "").strip() or None
    plan: list[dict] = []
    ms_organization_id = profile.moysklad_organization_id
    ms_store_id = None
    if moysklad_id:
        ms = await _maybe_ms_service(current_user, db)
        if ms is not None:
            try:
                ms_doc = await ms.get_document("supply", moysklad_id)
                ms_organization_id = _ref_id(ms_doc, "organization")
                ms_store_id = _ref_id(ms_doc, "store")
                profile = await _profile_for_organization(
                    db, current_user.id, ms_organization_id, profile
                )
                plan = await ms.build_plan("supply", moysklad_id)
            except Exception as exc:
                logger.warning(
                    "acceptance.build_plan_failed", moysklad_id=moysklad_id, error=str(exc)
                )

    default_name = "Приёмка из ЭДО"
    if edo_row.number:
        default_name = f"Приёмка ЭДО · УПД {edo_row.number}"
        if edo_row.counterparty_name:
            default_name += f" · {edo_row.counterparty_name}"
    name = (body.name or "").strip() or default_name[:255]
    doc = Document(
        user_id=current_user.id,
        name=name,
        kind=DocumentKind.supply,
        status=DocumentStatus.draft,
        product_group=pg,
        moysklad_id=moysklad_id,
        organization_profile_id=profile.id,
        moysklad_organization_id=ms_organization_id,
        moysklad_store_id=ms_store_id,
        plan=plan,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    import_result = await _import_upd_bytes(doc.id, doc, raw, current_user, db)
    # Помечаем входящий УПД принятым — уходит из списка «новых» и из бейджа.
    edo_row.accepted_document_id = doc.id
    await db.commit()
    scan_count = (
        await db.execute(
            select(func.count()).select_from(Scan).where(Scan.document_id == doc.id)
        )
    ).scalar() or 0
    logger.info(
        "acceptance.edo_imported",
        document_id=str(doc.id),
        external_id=body.external_id,
        created=import_result.created_scans,
        user_id=str(current_user.id),
    )
    return EdoImportResponse(
        document=AcceptanceDocResponse(
            cz_check_enabled=getattr(profile, 'acceptance_cz_check_enabled', True),
            id=doc.id,
            name=doc.name,
            kind=doc.kind,
            status=doc.status,
            product_group=doc.product_group,
            moysklad_id=doc.moysklad_id,
            scan_count=scan_count,
            plan_count=len(doc.plan or []),
        ),
        import_result=import_result,
    )


def _ai02_gtin(code: str) -> Optional[str]:
    """content-GTIN из агрегатного кода GS1 AI 02 (``02`` + 14-значный GTIN «вложенных
    товаров»).

    Такие групповые коротбочные коды приходят в УПД от некоторых поставщиков
    (напр. Chabacco). Честный Знак через ``cises/info`` их НЕ разворачивает (отдаёт
    «не найден»), но МойСклад принимает код как обычную марку и валидирует сам.
    Извлекаем content-GTIN, иначе `parse_gs1_km_gtin_serial` наивно возьмёт первые
    14 цифр вместе с префиксом ``02`` (мусорный GTIN), а короб останется без GTIN —
    несопоставимым и непривязываемым вручную."""
    if code.startswith("02") and len(code) >= 16 and code[2:16].isdigit():
        return normalize_gtin_key(code[2:16])
    return None


def _code_gtin(code: str, fallback: Optional[str]) -> Optional[str]:
    """GTIN, зашитый в сам код (``01``+GTIN). Фолбэк — GTIN позиции.

    Блочные/агрегатные коды (НомУпак) несут СВОЙ GTIN, отличный от GTIN единиц
    в той же позиции. Поэтому товар резолвим по GTIN каждого кода, а не по одному
    GTIN на всю позицию. Для кодов без GTIN (SSCC ``00…``) — фолбэк на GTIN позиции.
    """
    a2 = _ai02_gtin(code)
    if a2:
        return a2
    gtin, _ = parse_gs1_km_gtin_serial(code)
    if gtin:
        return normalize_gtin_key(gtin)
    return fallback


def _package_gtin(code: str, fallback: Optional[str]) -> Optional[str]:
    """GTIN агрегатной упаковки. Фолбэк — GTIN позиции.

    Блок (``НомУпак``) несёт свой ``01``+GTIN — выделяем его. Групповой код с GS1
    AI ``02`` (``02``+GTIN) несёт content-GTIN вложенных товаров — берём его. А
    транспортная упаковка (``ИдентТрансУпак``, SSCC с AI ``00``) GTIN НЕ несёт: её
    первые 14 цифр — это префикс компании + серийный номер короба, **одинаковый у
    разных товаров** одной поставки. Извлекать из неё «GTIN» нельзя (иначе все
    позиции схлопнутся в один фантомный GTIN) — относим короб к товару позиции.
    """
    if code.startswith("01"):
        gtin, _ = parse_gs1_km_gtin_serial(code)
        if gtin:
            return normalize_gtin_key(gtin)
    a2 = _ai02_gtin(code)
    if a2:
        return a2
    return fallback


def _norm_article(v: Optional[str]) -> Optional[str]:
    """Нормализованный ключ артикула/КодТов для сопоставления УПД ↔ позиции МС."""
    if not v:
        return None
    s = str(v).strip().upper()
    return s or None


async def _resolve_product(
    gtin: Optional[str],
    article: Optional[str],
    user_id: UUID,
    ms: Optional[MoySkladService],
    db: AsyncSession,
    cache: dict[str, tuple[Optional[str], Optional[str]]],
    plan_map: dict[str, tuple[str, Optional[str]]],
    article_map: dict[str, tuple[str, Optional[str]]],
) -> tuple[Optional[str], Optional[str]]:
    """(product_id, product_name) для группы кодов: сначала позиции привязанного
    поступления МС по GTIN (plan_map), затем запомненная связка, затем каталог МС,
    и наконец — по КодТов/артикулу против позиций поступления (article_map).

    Резолв по артикулу нужен для коробных (SSCC/ИдентТрансУпак) позиций без штучных
    КМ: собственного GTIN у них нет, единственный надёжный идентификатор — КодТов."""
    cache_key = f"{gtin or ''}|{_norm_article(article) or ''}" if gtin or article else None
    if cache_key and cache_key in cache:
        return cache[cache_key]

    def _done(result: tuple[Optional[str], Optional[str]]) -> tuple[Optional[str], Optional[str]]:
        if cache_key:
            cache[cache_key] = result
        return result

    if gtin:
        # 0. Позиция привязанного поступления МС — приоритетнее всего: КМ должны лечь
        #    именно в товар из этого документа.
        gk = normalize_gtin_key(gtin)
        if gk and gk in plan_map:
            return _done(plan_map[gk])

        # 1. Запомненная связка (ручная привязка ИЛИ авто-резолв прошлых приёмок/отгрузок).
        #    Ключ нормализуем (GTIN-14), т.к. в базу знаний пишем нормализованный.
        map_row = (
            await db.execute(
                select(GtinProductMap).where(
                    GtinProductMap.user_id == user_id,
                    GtinProductMap.gtin == (gk or gtin),
                )
            )
        ).scalar_one_or_none()
        if map_row:
            # product_id стабилен при переименовании товара в МС, а сохранённое
            # имя — снимок на момент привязки. Обычно доверяем снимку: обновление
            # имени по id — это отдельный запрос к МС на КАЖДЫЙ сопоставленный GTIN,
            # что на импорте из десятков позиций упирается в rate limit (429) и
            # тормозит приёмку. Ходим в МС только когда имени вовсе нет (self-heal).
            name = map_row.product_name
            if ms is not None and not name:
                try:
                    card = await ms.get_product_by_id(map_row.product_id)
                except Exception as exc:
                    logger.warning(
                        "acceptance.refresh_name_failed",
                        product_id=map_row.product_id,
                        error=str(exc),
                    )
                    card = None
                if card:
                    fresh = (card.get("name") or "").strip() or None
                    if fresh:
                        map_row.product_name = fresh
                        name = fresh
            return _done((map_row.product_id, name))

        # 2. Поиск в каталоге МС по штрихкоду. Успешный резолв запоминаем в базе
        #    знаний GTIN→товар — следующие приёмки/отгрузки этого GTIN резолвятся
        #    локально, без похода в каталог МС.
        if ms is not None:
            try:
                product = await ms.find_product_by_gtin(gtin)
            except Exception as exc:
                logger.warning("acceptance.find_product_failed", gtin=gtin, error=str(exc))
                product = None
            if product and product.get("id"):
                pid = product.get("id")
                pname = (product.get("name") or "").strip() or None
                from app.services.gtin_product_store import remember_gtin_product

                await remember_gtin_product(db, user_id, gtin, pid, pname)
                return _done((pid, pname))

    # 3. По КодТов/артикулу против позиций привязанного поступления МС. Для коробных
    #    позиций (только SSCC, без штучных КМ) это единственный путь к товару.
    art = _norm_article(article)
    if art and art in article_map:
        return _done(article_map[art])

    return _done((None, None))


async def _enrich_unmatched_names_nk(
    db: AsyncSession,
    document_id: UUID,
    unmatched: set[str],
    results: list[ImportPositionResult],
) -> None:
    """Дозаполнить имя из Национального каталога для несопоставленных GTIN.

    НК не даёт product_id МС (товар остаётся «нужна привязка»), но даёт наименование —
    подставляем его в scan.product_name и строки предпросмотра, чтобы кладовщик видел,
    что за марка, ещё до ручной привязки. Best-effort, cache-first, вне hot-path
    резолва. Не трогает уже сопоставленные позиции.

    НК троттлится (~100/5мин), поэтому в hot-path приёмки ограничиваем ожидание бюджетом
    (``NK_ACCEPTANCE_BUDGET_S``): что успели из кэша/быстрых запросов — применяем, остальное
    доберётся позже (при следующем импорте, когда GTIN уже в кэше, или в инвентаризации)."""
    if not settings.nk_enabled or not unmatched:
        return
    import asyncio
    from sqlalchemy import update
    from app.services.nk_store import resolve_cards, display_name

    try:
        cards = await asyncio.wait_for(
            resolve_cards(db, sorted(unmatched)), timeout=settings.NK_ACCEPTANCE_BUDGET_S
        )
    except asyncio.TimeoutError:
        logger.info("acceptance.nk_enrich_budget", document_id=str(document_id))
        return
    name_map: dict[str, str] = {}
    for gtin, card in cards.items():
        nm = display_name(card)
        if nm:
            name_map[gtin] = nm
    if not name_map:
        return
    for gtin, nm in name_map.items():
        await db.execute(
            update(Scan)
            .where(
                Scan.document_id == document_id,
                Scan.gtin == gtin,
                Scan.product_name.is_(None),
            )
            .values(product_name=nm)
        )
    await db.commit()
    # Отражаем имена в возвращаемых строках предпросмотра (без похода в БД).
    for r in results:
        if not r.matched and r.gtin in name_map and not r.product_name:
            r.product_name = name_map[r.gtin]
            if not r.name or r.name == "Без товара":
                r.name = name_map[r.gtin]
    logger.info("acceptance.nk_enriched", document_id=str(document_id), enriched=len(name_map))


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
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, current_user.id)
    if doc.kind != DocumentKind.supply:
        raise HTTPException(400, "Документ не является приёмкой")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    return await _import_upd_bytes(document_id, doc, raw, current_user, db)


async def _import_upd_bytes(
    document_id: UUID, doc: Document, raw: bytes, current_user: User, db: AsyncSession,
) -> ImportUpdResponse:
    from app.core.diagnostics import operation_scope, failure_reason
    from app.services.incidents import record, resolve_current
    with operation_scope(document_id, current_user.id, 'acceptance.import_upd'):
        try:
            result = await _import_upd_bytes_traced(document_id, doc, raw, current_user, db)
            await resolve_current(import_only=True)
            return result
        except Exception as exc:
            await db.rollback()
            incident_id = await record('acceptance.import_upd.error', str(exc), reason=failure_reason(exc))
            if incident_id and isinstance(exc, HTTPException):
                exc.detail = f'{exc.detail}\nКод ошибки: {incident_id}'
            raise


async def _import_upd_bytes_traced(
    document_id: UUID,
    doc: Document,
    raw: bytes,
    current_user: User,
    db: AsyncSession,
) -> ImportUpdResponse:
    """Разобрать УПД (байты XML) и импортировать позиции/коды в документ приёмки.

    Общее ядро для двух источников УПД: загрузка файлом и приёмка из ЭДО (Saby).
    Здесь всегда ``document_id == doc.id``.
    """
    try:
        parsed: ParsedUpd = parse_upd_503(raw)
    except UpdParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    positions: list[ParsedPosition] = parsed.positions
    from app.api.products import _upd_position_key
    legacy_keys = [_upd_position_key(p.line_number, p.name, p.article) for p in positions]
    key_counts = Counter(legacy_keys)
    position_keys = [key if key_counts[key] == 1 else f'xml:{i}' for i, key in enumerate(legacy_keys)]

    # GTIN index is only for suggesting product names. Financials belong to XML rows.
    upd_positions = {}

    # Реквизиты счёта-фактуры из шапки УПД → для комментария поступления МС.
    meta: dict = dict(doc.upd_meta or {})
    digest = sha256(raw).hexdigest()
    if meta.get('xml_digest') not in (None, digest):
        meta.pop('manual_position_links', None)
        meta.pop('supplemental_codes', None)
    meta.update(xml_digest=digest, invoice_number=parsed.invoice_number,
                invoice_date=parsed.invoice_date, positions=upd_positions)

    ms = await _maybe_ms_service(current_user, db)

    # План привязанного поступления МС: gtin → (product_id, product_name) и
    # КодТов/артикул → (product_id, product_name). Позиции УПД сопоставляются с
    # этими товарами по GTIN, а коробные (SSCC) позиции без GTIN — по артикулу.
    plan_map: dict[str, tuple[str, Optional[str]]] = {}
    article_map: dict[str, tuple[str, Optional[str]]] = {}
    for p in doc.plan or []:
        if not isinstance(p, dict) or p.get('link_by_line'):
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
        # Артикул и код товара МС → тот же товар. КодТов из УПД совпадает с одним
        # из них для поступления, созданного из того же каталога поставщика.
        for a in (_norm_article(p.get("article")), _norm_article(p.get("code"))):
            if a:
                article_map.setdefault(a, (pid, name))

    stored = (await db.execute(select(Scan).where(Scan.document_id == document_id))).scalars().all()
    existing = {code: scan for scan in stored for code in (scan.code, scan.original_code) if code}
    previous_products = {p['product_id']: p for p in doc.plan or [] if p.get('product_id')}
    manual_links = meta.get('manual_position_links') or {}
    resolve_cache = {}
    results = []
    new_plan = []
    physical_plan = []
    unmatched = set()
    imported_codes = set()
    created = skipped = 0
    cz_loaded = False
    cz = None

    for index, pos in enumerate(positions):
        sources = [(code, _code_gtin(code, pos.gtin), False) for code in pos.codes]
        sources.extend((code, pos.gtin if pos.codes else _package_gtin(code, pos.gtin), True)
                       for code in pos.packages)
        manual = manual_links.get(position_keys[index]) or {}
        candidates = {}
        observations = []
        for code, gtin, is_box in sources or [(None, pos.gtin, False)]:
            pid, name = manual.get('product_id'), manual.get('product_name')
            if not pid:
                pid, name = await _resolve_product(gtin, pos.article, current_user.id, ms, db,
                                                   resolve_cache, plan_map, article_map)
            expansion = None
            previous = existing.get(code)
            if is_box and not pid:
                if previous and previous.moysklad_product_id:
                    gtin, pid, name = previous.gtin, previous.moysklad_product_id, previous.product_name
                else:
                    if not cz_loaded:
                        cz = await _maybe_cz_service(current_user, db, doc.organization_profile_id)
                        cz_loaded = True
                    if cz:
                        expansion = await _expand_box_via_cz(code, cz)
                        if expansion and expansion.get('gtin'):
                            gtin = expansion['gtin']
                            pid, name = await _resolve_product(gtin, pos.article, current_user.id, ms, db,
                                                               resolve_cache, plan_map, article_map)
            candidates[pid] = name
            if code:
                observations.append((code, gtin, is_box, expansion))

        # A position in MS has one assortment. Several marking GTINs can be
        # aliases of that assortment; different matches require a line-level choice.
        pid = next(iter(candidates)) if len(candidates) == 1 else None
        name = candidates.get(pid) if pid else None
        aliases = list(dict.fromkeys(g for _, g, _, _ in observations if g))
        for lookup_gtin in aliases or [pos.gtin]:
            if lookup_gtin:
                upd_positions.setdefault(lookup_gtin, {'name': pos.name})
        line_link = bool(manual) or len({g for _, g, _, _ in observations}) > 1 or (bool(sources) and not aliases)
        gtin = aliases[0] if len(aliases) == 1 else pos.gtin
        codes = [code for code, _, _, _ in observations]
        if not pid and codes and gtin and not line_link:
            unmatched.add(gtin)
        for code, g, is_box, expansion in observations:
            if code in imported_codes:
                skipped += 1
                continue
            imported_codes.add(code)
            scan = existing.get(code)
            if scan is None:
                scan = Scan(document_id=document_id, code=code, is_box=is_box)
                db.add(scan)
                created += 1
            else:
                skipped += 1
            scan.gtin, scan.moysklad_product_id, scan.product_name = g, pid, name
            if scan.status in (None, ScanStatus.valid, ScanStatus.unknown_product, ScanStatus.overflow):
                scan.status = ScanStatus.valid if pid else ScanStatus.unknown_product
            if expansion and expansion.get('children'):
                scan.child_codes = expansion['children']
                scan.box_quantity = expansion['qty']

        result = ImportPositionResult(name=pos.name, xml_index=index, line_number=pos.line_number,
            article=pos.article, gtin=gtin, quantity=pos.quantity, price=pos.price, vat=pos.vat,
            product_id=pid, product_name=name, matched=bool(pid), codes_count=len(pos.codes),
            packages_count=len(pos.packages), source_codes=codes, link_by_line=line_link)
        results.append(result)
        old = previous_products.get(pid, {})
        plan_row = {'xml_index': index, 'product_id': pid, 'product_name': name or pos.name,
                    'gtin': gtin, 'gtins': aliases, 'article': pos.article, 'expected_qty': pos.quantity or 0,
                    'price': pos.price, 'vat': pos.vat, 'marked': bool(codes), 'link_by_line': line_link,
                    'pack_gtins': old.get('pack_gtins', []), 'pack_quantities': old.get('pack_quantities', {})}
        new_plan.append(plan_row)
        physical_plan.append({**plan_row, 'key': f'xml:{index}', 'position_key': position_keys[index],
            'product_name': pos.name, 'line_number': pos.line_number,
            'items': [{'product_id': pid, 'gtin': gtin, 'quantity': pos.quantity, 'codes': codes}],
            'package_codes': pos.packages})
    # A supplier can put every mark on one price line. A second line without
    # marks can use the same article even when the selected MS receipt is empty.
    products_by_article = {}
    for row in new_plan:
        article = _norm_article(row['article'])
        if article and row['product_id']:
            products_by_article.setdefault(article, {})[row['product_id']] = row['product_name']
    for row, line, result in zip(new_plan, physical_plan, results):
        matches = products_by_article.get(_norm_article(row['article']), {})
        if not row['marked'] and not row['product_id'] and len(matches) == 1:
            pid, name = next(iter(matches.items()))
            row.update(product_id=pid, product_name=name)
            line['product_id'] = line['items'][0]['product_id'] = pid
            result.product_id, result.product_name, result.matched = pid, name, True
    doc.plan = new_plan
    doc.upd_meta = {**meta, 'physical_plan': physical_plan}

    await db.commit()
    await _enrich_unmatched_names_nk(db, document_id, unmatched, results)
    logger.info(
        "acceptance.upd_imported",
        document_id=str(document_id),
        positions=len(positions),
        created_scans=created,
        skipped=skipped,
        unmatched=len(unmatched),
        plan_products=len(new_plan),
        user_id=str(current_user.id),
    )

    return ImportUpdResponse(
        document_id=document_id,
        positions=results,
        created_scans=created,
        skipped_duplicates=skipped,
        unmatched_gtins=sorted(unmatched),
        total_amount=parsed.total_amount,
        total_vat=parsed.total_vat,
    )


@router.post(
    "/documents/{document_id}/import-marks", response_model=ImportUpdResponse
)
async def import_marks(
    document_id: UUID,
    body: ImportMarksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Загрузить список марок в приёмку вручную (без УПД).

    Дополнительный источник рядом с загрузкой УПД: кладовщик вставляет/загружает
    готовый перечень кодов. Каждый код резолвится в товар по СВОЕМУ GTIN тем же
    порядком, что и при импорте УПД (позиции привязанного поступления → GtinProductMap
    → каталог МС), и сохраняется как скан ``valid``/``unknown_product``. План НЕ
    мутируем: кол-во/цена берутся из привязанного поступления МС. Несопоставленные
    GTIN привязываются вручную панелью подбора (``/products/link-gtin``).
    """
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, current_user.id)
    if doc.kind != DocumentKind.supply:
        raise HTTPException(400, "Документ не является приёмкой")

    # Нормализуем список: strip, отбрасываем пустые, дедуп с сохранением порядка.
    seen: set[str] = set()
    codes: list[str] = []
    for raw in body.codes:
        c = (raw or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        codes.append(c)
    if not codes:
        raise HTTPException(status_code=400, detail="Список марок пуст")

    ms = await _maybe_ms_service(current_user, db)
    # ЧЗ для разворота коробов (AI-02 и пр. агрегаты) — best-effort, как в import_upd.
    cz = await _maybe_cz_service(current_user, db, doc.organization_profile_id)

    # План привязанного поступления МС: gtin/pack_gtin → товар, артикул/КодТов → товар.
    plan_map: dict[str, tuple[str, Optional[str]]] = {}
    article_map: dict[str, tuple[str, Optional[str]]] = {}
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
        for pg in p.get("pack_gtins") or []:
            pgk = normalize_gtin_key(pg)
            if pgk:
                plan_map.setdefault(pgk, (pid, name))
        for a in (_norm_article(p.get("article")), _norm_article(p.get("code"))):
            if a:
                article_map.setdefault(a, (pid, name))

    # Уже имеющиеся коды документа — не плодим дубли при повторной загрузке.
    existing_codes = set(
        (
            await db.execute(
                select(Scan.code).where(Scan.document_id == document_id)
            )
        ).scalars().all()
    )

    # Группируем коды по их СОБСТВЕННОМУ GTIN. SSCC-короб GTIN не несёт — сохраняем
    # его как is_box с gtin=None (резолв только по артикулу, обычно не сматчится →
    # ручная привязка). Блок (НомУпак с 01+GTIN) несёт свой GTIN.
    groups: dict[Optional[str], dict[str, list[str]]] = {}
    order: list[Optional[str]] = []

    def _bucket(g: Optional[str]) -> dict[str, list[str]]:
        if g not in groups:
            groups[g] = {"codes": [], "packages": []}
            order.append(g)
        return groups[g]

    for raw_code in codes:
        code = normalize_sscc(raw_code)
        if is_sscc(code):
            _bucket(_package_gtin(code, None))["packages"].append(code)
        else:
            _bucket(_code_gtin(code, None))["codes"].append(code)

    resolve_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
    results: list[ImportPositionResult] = []
    unmatched: set[str] = set()
    created = 0
    skipped = 0

    for g in order:
        bucket = groups[g]
        product_id, product_name = await _resolve_product(
            g, None, current_user.id, ms, db, resolve_cache, plan_map, article_map
        )
        matched = product_id is not None
        if g and not matched:
            unmatched.add(g)
        status = ScanStatus.valid if matched else ScanStatus.unknown_product

        for code in bucket["codes"]:
            if code in existing_codes:
                skipped += 1
                continue
            existing_codes.add(code)
            db.add(
                Scan(
                    document_id=document_id,
                    code=code,
                    gtin=g,
                    moysklad_product_id=product_id,
                    product_name=product_name,
                    status=status,
                )
            )
            created += 1

        # Короба/агрегаты: если группа не сопоставилась, разворачиваем каждую упаковку
        # через ЧЗ и резолвим товар по unit-GTIN пачки (см. import_upd). План здесь не
        # мутируем — товар проставляется прямо на скан, воркер допишет его в МС.
        box_rows: dict[Optional[str], dict] = {}
        for sscc in bucket["packages"]:
            if sscc in existing_codes:
                skipped += 1
                continue
            existing_codes.add(sscc)
            b_gtin, b_pid, b_name, b_status = g, product_id, product_name, status
            b_children: Optional[list] = None
            b_qty: Optional[int] = None
            if cz is not None and b_pid is None:
                exp = await _expand_box_via_cz(sscc, cz)
                if exp and exp.get("gtin"):
                    u_pid, u_name = await _resolve_product(
                        exp["gtin"], None, current_user.id, ms, db,
                        resolve_cache, plan_map, article_map,
                    )
                    if u_pid:
                        b_gtin = exp["gtin"]
                        b_pid = u_pid
                        b_name = u_name or exp.get("product_name")
                        b_status = ScanStatus.valid
                        b_children = exp["children"]
                        b_qty = exp["qty"]
            db.add(
                Scan(
                    document_id=document_id,
                    code=sscc,
                    gtin=b_gtin,
                    moysklad_product_id=b_pid,
                    product_name=b_name,
                    status=b_status,
                    is_box=True,
                    child_codes=b_children,
                    box_quantity=b_qty,
                )
            )
            created += 1
            key = b_pid if (b_pid and b_pid != product_id) else None
            row = box_rows.setdefault(
                key,
                {
                    "product_id": b_pid if key else product_id,
                    "product_name": b_name if key else product_name,
                    "gtin": b_gtin if key else g,
                    "count": 0,
                },
            )
            row["count"] += 1

        base_pkgs = box_rows.get(None, {}).get("count", 0)
        has_expanded = any(k is not None for k in box_rows)
        if len(bucket["codes"]) or base_pkgs or not has_expanded:
            results.append(
                ImportPositionResult(
                    name=product_name or "Без товара",
                    gtin=g,
                    article=None,
                    quantity=None,
                    codes_count=len(bucket["codes"]),
                    packages_count=base_pkgs,
                    product_id=product_id,
                    product_name=product_name,
                    matched=matched,
                )
            )
        for key, row in box_rows.items():
            if key is None:
                continue
            results.append(
                ImportPositionResult(
                    name=row["product_name"] or "Без товара",
                    gtin=row["gtin"],
                    article=None,
                    quantity=None,
                    codes_count=0,
                    packages_count=row["count"],
                    product_id=row["product_id"],
                    product_name=row["product_name"],
                    matched=True,
                )
            )

    meta = doc.upd_meta or {}
    lines = meta.get('physical_plan') or []
    if lines and 'xml_index' in lines[0]:
        source_codes = {code for line in lines for item in line['items'] for code in item['codes']}
        extra = {normalize_sscc(code) for code in codes} - source_codes
        if extra:
            products = {line['product_id'] for line in lines if line.get('product_id')}
            if any(r.product_id not in products for r in results):
                raise HTTPException(409, 'Добавлять марки можно только для товаров, сопоставленных со строками УПД.')
            doc.upd_meta = {**meta, 'supplemental_codes': sorted(extra.union(meta.get('supplemental_codes') or []))}
    await db.commit()
    await _enrich_unmatched_names_nk(db, document_id, unmatched, results)
    logger.info(
        "acceptance.marks_imported",
        document_id=str(document_id),
        total=len(codes),
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
    from app.services.acceptance_mode import acceptance_cz_check_enabled
    check_enabled = await acceptance_cz_check_enabled(db, doc)
    count = int(
        (
            await db.execute(
                select(func.count(Scan.id)).where(Scan.document_id == document_id)
            )
        ).scalar()
        or 0
    )
    return AcceptanceDocResponse(
        cz_check_enabled=check_enabled,
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        status=doc.status,
        product_group=doc.product_group,
        moysklad_id=doc.moysklad_id,
        scan_count=count,
        plan_count=len(doc.plan or []),
        error_message=doc.error_message,
        processing_progress=doc.processing_progress,
    )
