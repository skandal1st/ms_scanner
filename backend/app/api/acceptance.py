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
from app.services.chestnyznak import (
    normalize_gtin_key,
    parse_gs1_km_gtin_serial,
    is_sscc,
)
from app.services.upd_parser import parse_upd_503, UpdParseError, ParsedPosition, ParsedUpd

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
    # Причина неуспешной отправки в МС (напр. истёк токен ЧЗ) — показывается на
    # странице приёмки вместо ложного «МойСклад ещё обрабатывает».
    error_message: Optional[str] = None


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
    line_number: Optional[int] = None


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


def _code_gtin(code: str, fallback: Optional[str]) -> Optional[str]:
    """GTIN, зашитый в сам код (``01``+GTIN). Фолбэк — GTIN позиции.

    Блочные/агрегатные коды (НомУпак) несут СВОЙ GTIN, отличный от GTIN единиц
    в той же позиции. Поэтому товар резолвим по GTIN каждого кода, а не по одному
    GTIN на всю позицию. Для кодов без GTIN (SSCC ``00…``) — фолбэк на GTIN позиции.
    """
    gtin, _ = parse_gs1_km_gtin_serial(code)
    if gtin:
        return normalize_gtin_key(gtin)
    return fallback


def _package_gtin(code: str, fallback: Optional[str]) -> Optional[str]:
    """GTIN агрегатной упаковки. Фолбэк — GTIN позиции.

    Блок (``НомУпак``) несёт свой ``01``+GTIN — выделяем его. Транспортная
    упаковка (``ИдентТрансУпак``, SSCC с AI ``00``) GTIN НЕ несёт: её первые 14
    цифр — это префикс компании + серийный номер короба, **одинаковый у разных
    товаров** одной поставки. Извлекать из неё «GTIN» нельзя (иначе все позиции
    схлопнутся в один фантомный GTIN) — относим короб к товару позиции (fallback).
    """
    if code.startswith("01"):
        gtin, _ = parse_gs1_km_gtin_serial(code)
        if gtin:
            return normalize_gtin_key(gtin)
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
    cache_key = gtin or (f"art:{_norm_article(article)}" if article else None)
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
            return _done((map_row.product_id, map_row.product_name))

        # 2. Поиск в каталоге МС по штрихкоду.
        if ms is not None:
            try:
                product = await ms.find_product_by_gtin(gtin)
            except Exception as exc:
                logger.warning("acceptance.find_product_failed", gtin=gtin, error=str(exc))
                product = None
            if product:
                return _done(
                    (product.get("id"), (product.get("name") or "").strip() or None)
                )

    # 3. По КодТов/артикулу против позиций привязанного поступления МС. Для коробных
    #    позиций (только SSCC, без штучных КМ) это единственный путь к товару.
    art = _norm_article(article)
    if art and art in article_map:
        return _done(article_map[art])

    return _done((None, None))


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
        parsed: ParsedUpd = parse_upd_503(raw)
    except UpdParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    positions: list[ParsedPosition] = parsed.positions

    # Цена/НДС/кол-во из УПД по GTIN позиции — сохраняем НЕЗАВИСИМО от того,
    # сопоставился ли товар. Нужны при ручной привязке GTIN (link-gtin), чтобы
    # перенести их в план и записать в поступление МС: для пустых поступлений
    # GTIN'ы часто резолвятся вручную, а на этом этапе цена/НДС из плана уже нет.
    upd_positions: dict[str, dict] = {}

    def _record_upd_price(pos: ParsedPosition, gkey: Optional[str], *, with_qty: bool):
        if not gkey:
            return
        info: dict = {}
        if pos.price is not None:
            info["price"] = pos.price
        if pos.vat is not None:
            info["vat"] = pos.vat
        # Кол-во (КолТов) относится к строке позиции в целом — сохраняем только
        # под GTIN строки, не под GTIN отдельных упаковок набора.
        if with_qty and pos.quantity is not None:
            try:
                info["quantity"] = int(pos.quantity)
            except (TypeError, ValueError):
                pass
        if info:
            upd_positions.setdefault(gkey, info)

    for pos in positions:
        # GTIN строки позиции — с кол-вом.
        _record_upd_price(pos, normalize_gtin_key(pos.gtin), with_qty=True)
        # GTIN каждого кода/упаковки набора — та же цена/НДС (без кол-ва). Нужно,
        # чтобы ручная привязка НомУпак-GTIN к товару подхватила закупочную цену.
        for code in pos.codes:
            _record_upd_price(pos, _code_gtin(code, None), with_qty=False)
        for pkg in pos.packages:
            _record_upd_price(pos, _package_gtin(pkg, None), with_qty=False)

    # Реквизиты счёта-фактуры из шапки УПД → для комментария поступления МС.
    meta: dict = dict(doc.upd_meta or {})
    if parsed.invoice_number or parsed.invoice_date:
        meta["invoice_number"] = parsed.invoice_number
        meta["invoice_date"] = parsed.invoice_date
    if upd_positions:
        meta["positions"] = {**(meta.get("positions") or {}), **upd_positions}
    if meta:
        doc.upd_meta = meta

    ms = await _maybe_ms_service(current_user, db)

    # План привязанного поступления МС: gtin → (product_id, product_name) и
    # КодТов/артикул → (product_id, product_name). Позиции УПД сопоставляются с
    # этими товарами по GTIN, а коробные (SSCC) позиции без GTIN — по артикулу.
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
    # Накопление товарных позиций для плана: product_id → {gtin, product_name,
    # expected_qty}. expected_qty = сумма КолТов (УПД) по «основной» группе позиции.
    # При «Отправить в МС» из плана создаются позиции поступления с этими кол-вами.
    plan_acc: dict[str, dict] = {}

    for pos in positions:
        # Группируем коды позиции по их СОБСТВЕННОМУ GTIN (01+GTIN внутри кода).
        # Блочные/смешанные позиции содержат коды с разными GTIN — каждый код
        # резолвится в свой товар, а не в один GTIN на всю позицию (вариант B).
        groups: dict[Optional[str], dict[str, list[str]]] = {}
        order: list[Optional[str]] = []

        def _bucket(g: Optional[str]) -> dict[str, list[str]]:
            if g not in groups:
                groups[g] = {"codes": [], "packages": []}
                order.append(g)
            return groups[g]

        for code in pos.codes:
            code = code.strip()
            if code:
                _bucket(_code_gtin(code, pos.gtin))["codes"].append(code)
        # Агрегаты/упаковки — сохраняем целиком (без разворота). Блок (НомУпак)
        # несёт свой 01+GTIN; транспортная упаковка (ИдентТрансУпак, SSCC) GTIN не
        # несёт → её первые 14 цифр НЕ GTIN, относим к товару позиции.
        for pkg in pos.packages:
            pkg = pkg.strip()
            if pkg:
                _bucket(_package_gtin(pkg, pos.gtin))["packages"].append(pkg)

        # Позиция без кодов — показываем строку «без марок» (как раньше).
        if not order:
            results.append(
                ImportPositionResult(
                    name=pos.name,
                    gtin=pos.gtin,
                    article=pos.article,
                    quantity=pos.quantity,
                    codes_count=0,
                    packages_count=0,
                    product_id=None,
                    product_name=None,
                    matched=False,
                    line_number=pos.line_number,
                )
            )
            continue

        for g in order:
            bucket = groups[g]
            product_id, product_name = await _resolve_product(
                g, pos.article, current_user.id, ms, db, resolve_cache, plan_map, article_map
            )
            matched = product_id is not None
            if g and not matched:
                unmatched.add(g)
            status = ScanStatus.valid if matched else ScanStatus.unknown_product

            # КолТов позиции относим к товару «основной» (первой) группы — она же
            # несёт GTIN единиц/строки УПД. Накапливаем по товару для плана.
            if product_id:
                entry = plan_acc.setdefault(
                    product_id,
                    {
                        "gtin": g,
                        "product_id": product_id,
                        "product_name": product_name,
                        "expected_qty": 0,
                    },
                )
                # Цена/НДС из атрибутов СведТов относятся ко всему «набору» —
                # проставляем их КАЖДОЙ товарной позиции (каждому НомУпак/GTIN
                # внутри позиции), а не только «основной» группе. Иначе у всех
                # товаров набора, кроме первого, закупочная цена в поступлении
                # МС остаётся пустой.
                if pos.price is not None:
                    entry["price"] = pos.price
                if pos.vat is not None:
                    entry["vat"] = pos.vat
                # КолТов относим к «основной» (первой) группе — она несёт GTIN
                # строки/единиц УПД; для остальных групп кол-во неоднозначно.
                if g == order[0] and pos.quantity:
                    try:
                        entry["expected_qty"] += int(pos.quantity)
                    except (TypeError, ValueError):
                        pass

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

            for sscc in bucket["packages"]:
                if sscc in existing_codes:
                    skipped += 1
                    continue
                existing_codes.add(sscc)
                db.add(
                    Scan(
                        document_id=document_id,
                        code=sscc,
                        gtin=g,
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
                    gtin=g,
                    article=pos.article,
                    # Кол-во (КолТов) — на «основную» строку позиции (GTIN единиц);
                    # для отделившихся блочных GTIN кол-во неоднозначно → не показываем.
                    quantity=pos.quantity if g == pos.gtin else None,
                    codes_count=len(bucket["codes"]),
                    packages_count=len(bucket["packages"]),
                    product_id=product_id,
                    product_name=product_name,
                    matched=matched,
                    line_number=pos.line_number,
                )
            )

    # Обновляем план документа позициями из УПД (товар + КолТов). Сохраняем
    # pack_gtins из прежнего плана (если поступление МС уже имело позиции), КолТов
    # УПД имеет приоритет по количеству. Из этого плана при «Отправить в МС»
    # создаются позиции поступления.
    existing_by_pid = {
        p["product_id"]: p
        for p in (doc.plan or [])
        if isinstance(p, dict) and p.get("product_id")
    }
    new_plan: list[dict] = []
    for pid, entry in plan_acc.items():
        old = existing_by_pid.get(pid, {})
        new_plan.append({**old, **entry, "pack_gtins": old.get("pack_gtins", [])})
    # Сохраняем позиции прежнего плана, для которых в этом УПД не было кодов.
    for pid, old in existing_by_pid.items():
        if pid not in plan_acc:
            new_plan.append(old)
    doc.plan = new_plan

    await db.commit()
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
    doc = await _get_acceptance_doc(document_id, current_user, db)

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

    for code in codes:
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

        for sscc in bucket["packages"]:
            if sscc in existing_codes:
                skipped += 1
                continue
            existing_codes.add(sscc)
            db.add(
                Scan(
                    document_id=document_id,
                    code=sscc,
                    gtin=g,
                    moysklad_product_id=product_id,
                    product_name=product_name,
                    status=status,
                    is_box=True,
                )
            )
            created += 1

        results.append(
            ImportPositionResult(
                name=product_name or "Без товара",
                gtin=g,
                article=None,
                quantity=None,
                codes_count=len(bucket["codes"]),
                packages_count=len(bucket["packages"]),
                product_id=product_id,
                product_name=product_name,
                matched=matched,
            )
        )

    await db.commit()
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
        error_message=doc.error_message,
    )
