from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.db.models import User, Scan, Document, ScanStatus, Integration, GtinProductMap
from app.api.deps import get_current_user
from app.core.logging import logger
from app.core.security import decrypt_token
from app.services.moysklad import MoySkladService
from app.services.chestnyznak import normalize_gtin_key


router = APIRouter(prefix="/products", tags=["products"])


class ProductSearchItem(BaseModel):
    id: str
    name: str
    article: str = ""
    code: str = ""
    barcodes: List[str] = []


class LinkGtinRequest(BaseModel):
    document_id: UUID
    gtin: str
    moysklad_product_id: str
    product_name: Optional[str] = None


class LinkGtinResponse(BaseModel):
    updated_count: int


class BulkLinkItem(BaseModel):
    gtin: str
    moysklad_product_id: str
    product_name: Optional[str] = None


class BulkLinkRequest(BaseModel):
    document_id: UUID
    links: List[BulkLinkItem]


class BulkLinkResult(BaseModel):
    gtin: str
    updated: int


class BulkLinkResponse(BaseModel):
    updated_total: int
    results: List[BulkLinkResult]


class MatchSuggestion(BaseModel):
    """Подсказка сопоставления для одного несопоставленного GTIN."""
    gtin: str
    gtin_key: str
    name: Optional[str] = None
    count: int
    confidence: str  # high | low | none
    best: Optional[ProductSearchItem] = None
    suggestions: List[ProductSearchItem] = []


async def _get_ms_service(user: User, db: AsyncSession) -> MoySkladService:
    int_q = await db.execute(select(Integration).where(Integration.user_id == user.id))
    integration = int_q.scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(
            status_code=400,
            detail="Сначала подключите МойСклад в настройках",
        )
    try:
        token = decrypt_token(integration.moysklad_token)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Не удалось расшифровать токен МойСклад. Переподключите интеграцию.",
        )
    return MoySkladService(token)


@router.get("/search", response_model=List[ProductSearchItem])
async def search_products(
    q: str = Query(..., min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Поиск товаров в каталоге МС по строке (name/article/code)."""
    ms = await _get_ms_service(current_user, db)
    rows = await ms.search_products(q, limit=20)
    return [ProductSearchItem.model_validate(r) for r in rows]


def _norm_name(s: Optional[str]) -> str:
    """Нормализация имени товара для сравнения: lower + сжатие пробелов."""
    return " ".join((s or "").lower().split())


# Стоп-слова/единицы — мешают полнотекстовому поиску МС (он ищет по И-логике) и не
# различают товар. Выкидываем из запроса и из проверки вхождения.
_NAME_NOISE = {
    "с", "и", "для", "гр", "г", "мл", "шт", "кг", "ml", "g", "kg",
    "x", "аромат", "ароматом", "вкус", "вкусом",
}


def _significant_tokens(name: str) -> List[str]:
    """Значимые слова имени (без стоп-слов, единиц и чистых чисел) — для поиска и оценки."""
    import re

    toks = [t for t in re.split(r"[\s\-,.()]+", (name or "").lower()) if t]
    # Отбрасываем стоп-слова и токены с цифрами (фасовка/упаковка: «25», «10x», «40г») —
    # их нет в именах товаров МС, они ломают поиск и проверку вхождения.
    return [
        t for t in toks
        if len(t) >= 2 and not any(ch.isdigit() for ch in t) and t not in _NAME_NOISE
    ]


async def _ms_search_progressive(ms, name: str) -> tuple:
    """Поиск товара в МС с деградацией запроса: полное имя → всё меньше значимых слов.

    Полнотекстовый ``search`` МС требует ВСЕ слова, поэтому длинное имя из ЧЗ
    («…SEBERO Black с ароматом Watermelon 25 гр 10X») часто даёт 0. Отступаем к
    первым N значимым словам, пока не найдём. Возвращает ``(rows, significant_tokens)``.
    """
    sig = _significant_tokens(name)
    queries = [name.strip()]
    for n in (6, 5, 4, 3, 2):
        q = " ".join(sig[:n])
        if q and q not in queries:
            queries.append(q)
    for q in queries:
        rows = await ms.search_products(q, limit=8)
        if rows:
            return rows, sig
    return [], sig


async def _link_gtin_core(
    db: AsyncSession,
    doc: Document,
    user_id: UUID,
    gtin: str,
    pid: str,
    name: Optional[str],
) -> List[Scan]:
    """Привязать сканы одного GTIN к товару: статусы + GtinProductMap + план.

    БЕЗ commit / WS-пуша / записи штрихкода в МС — это делает вызывающий (одиночный
    ``link_gtin_to_product`` или пакетный ``link_gtin_bulk``). Возвращает
    сопоставленные сканы. Общий движок, чтобы одиночная и массовая привязка вели
    себя одинаково.
    """
    target_key = normalize_gtin_key(gtin)
    if not target_key:
        raise HTTPException(status_code=400, detail="Некорректный GTIN")

    # Все «рабочие» сканы этого GTIN (в т.ч. valid/overflow — для перепривязки).
    scans_q = await db.execute(
        select(Scan).where(
            Scan.document_id == doc.id,
            Scan.status.in_(
                [ScanStatus.unknown_product, ScanStatus.valid, ScanStatus.overflow]
            ),
        )
    )
    matched: List[Scan] = [
        s for s in scans_q.scalars().all()
        if normalize_gtin_key(s.gtin) == target_key
    ]

    expected_qty: Optional[int] = None
    if doc.plan:
        for p in doc.plan:
            if isinstance(p, dict) and normalize_gtin_key(p.get("gtin")) == target_key:
                try:
                    expected_qty = int(p.get("expected_qty") or 0)
                except (TypeError, ValueError):
                    expected_qty = None
                break

    valid_count = 0
    for s in matched:
        s.moysklad_product_id = pid
        if name:
            s.product_name = name
        s.error_message = None
        if expected_qty and valid_count + 1 > expected_qty:
            s.status = ScanStatus.overflow
            s.error_message = (
                f"Сверх плана: ожидалось {expected_qty}, отсканировано {valid_count + 1}"
            )
        else:
            s.status = ScanStatus.valid
        valid_count += 1

    # Локальная связка GTIN→товар (следующие загрузки резолвят без МС).
    map_row = (
        await db.execute(
            select(GtinProductMap).where(
                GtinProductMap.user_id == user_id,
                GtinProductMap.gtin == target_key,
            )
        )
    ).scalar_one_or_none()
    if map_row:
        map_row.product_id = pid
        map_row.product_name = name
    else:
        db.add(
            GtinProductMap(
                user_id=user_id,
                gtin=target_key,
                product_id=pid,
                product_name=name,
            )
        )

    # Цена/НДС/кол-во из УПД для привязанного товара в план документа.
    if matched:
        upd_pos = ((doc.upd_meta or {}).get("positions") or {}).get(target_key) or {}
        plan = [
            p
            for p in (doc.plan or [])
            if not (
                isinstance(p, dict)
                and normalize_gtin_key(p.get("gtin")) == target_key
                and p.get("product_id") != pid
            )
        ]
        entry = next(
            (p for p in plan if isinstance(p, dict) and p.get("product_id") == pid),
            None,
        )
        if entry is None:
            entry = {"product_id": pid, "gtin": target_key}
            plan.append(entry)
        entry["gtin"] = target_key
        if name:
            entry["product_name"] = name
        if upd_pos.get("price") is not None:
            entry["price"] = upd_pos["price"]
        if upd_pos.get("vat") is not None:
            entry["vat"] = upd_pos["vat"]
        qty = upd_pos.get("quantity") or len(matched)
        if qty:
            entry["expected_qty"] = qty
        doc.plan = plan

    return matched


async def _push_links_and_barcodes(
    db: AsyncSession,
    current_user: User,
    scans: List[Scan],
    gtin_pid_pairs: List[tuple],
) -> None:
    """После commit: WS-пуш по сканам + закрепление штрихкодов GTIN в МС (best-effort)."""
    from app.worker.tasks import _push_ws_update

    for s in scans:
        await db.refresh(s)
    for s in scans:
        await _push_ws_update(
            str(current_user.id),
            str(s.id),
            s.status,
            s.product_name,
            s.error_message,
            gtin=s.gtin,
            moysklad_product_id=s.moysklad_product_id,
        )
    if not gtin_pid_pairs:
        return
    try:
        ms = await _get_ms_service(current_user, db)
    except HTTPException:
        return  # МС не подключён — сопоставление сканов уже сохранено
    for gtin, pid in gtin_pid_pairs:
        try:
            await ms.add_gtin_barcode_to_product(pid, gtin)
        except Exception as e:
            logger.warning("products.link_gtin.barcode_persist_failed", gtin=gtin, error=str(e))


async def _get_document_owned(
    db: AsyncSession, document_id: UUID, user_id: UUID
) -> Document:
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id, Document.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post("/link-gtin", response_model=LinkGtinResponse)
async def link_gtin_to_product(
    body: LinkGtinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Привязать сканы документа с указанным GTIN к товару МС (одиночно).

    Работает и для unknown_product, и для уже сопоставленных (valid/overflow) —
    последнее позволяет исправить ошибочную привязку. WS-пуш по каждому скану.
    """
    doc = await _get_document_owned(db, body.document_id, current_user.id)
    pid = body.moysklad_product_id.strip()
    if not pid or len(pid) > 64:
        raise HTTPException(status_code=400, detail="Некорректный UUID товара МС")
    name = (body.product_name or "").strip() or None

    matched = await _link_gtin_core(db, doc, current_user.id, body.gtin, pid, name)
    await db.commit()  # связка GtinProductMap сохраняется даже без подходящих сканов
    if matched:
        logger.info(
            "products.link_gtin.done",
            document_id=str(body.document_id),
            gtin=body.gtin,
            product_id=pid,
            updated=len(matched),
        )
    await _push_links_and_barcodes(db, current_user, matched, [(body.gtin, pid)])
    return LinkGtinResponse(updated_count=len(matched))


@router.post("/link-gtin-bulk", response_model=BulkLinkResponse)
async def link_gtin_bulk(
    body: BulkLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Привязать пачку GTIN→товар за один запрос (массовое сопоставление).

    Переиспользует общий движок ``_link_gtin_core`` для каждой связки, коммитит один
    раз, затем один проход WS-пуша и закрепления штрихкодов в МС.
    """
    doc = await _get_document_owned(db, body.document_id, current_user.id)

    all_matched: List[Scan] = []
    pairs: List[tuple] = []
    results: List[BulkLinkResult] = []
    for link in body.links:
        pid = (link.moysklad_product_id or "").strip()
        if not pid or len(pid) > 64:
            continue
        if not normalize_gtin_key(link.gtin):
            continue
        name = (link.product_name or "").strip() or None
        matched = await _link_gtin_core(db, doc, current_user.id, link.gtin, pid, name)
        all_matched.extend(matched)
        pairs.append((link.gtin, pid))
        results.append(BulkLinkResult(gtin=link.gtin, updated=len(matched)))

    await db.commit()
    logger.info(
        "products.link_gtin_bulk.done",
        document_id=str(body.document_id),
        links=len(results),
        updated=len(all_matched),
    )
    await _push_links_and_barcodes(db, current_user, all_matched, pairs)
    return BulkLinkResponse(updated_total=len(all_matched), results=results)


@router.get("/match-suggestions", response_model=List[MatchSuggestion])
async def match_suggestions(
    document_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Подсказки сопоставления для всех несопоставленных GTIN документа.

    По каждому GTIN (сканы unknown_product) берём известное имя (ЧЗ/УПД) и ищем товар
    в МС, отмечая уверенность: high (точное совпадение имени), low (нашлось неточно),
    none (не нашлось). Фронт предвыбирает high для «Подтвердить все».
    """
    import asyncio

    doc = await _get_document_owned(db, document_id, current_user.id)

    scans_q = await db.execute(
        select(Scan).where(
            Scan.document_id == document_id,
            Scan.status == ScanStatus.unknown_product,
        )
    )
    groups: dict = {}
    for s in scans_q.scalars().all():
        key = normalize_gtin_key(s.gtin) or (s.gtin or "")
        if not key:
            continue
        g = groups.setdefault(key, {"gtin": s.gtin or key, "count": 0, "name": None})
        g["count"] += 1
        if not g["name"] and s.product_name:
            g["name"] = s.product_name

    # Фолбэк имени из позиций УПД (приёмка), если у скана имени нет.
    positions = (doc.upd_meta or {}).get("positions") or {}
    for key, g in groups.items():
        if not g["name"]:
            g["name"] = (positions.get(key) or {}).get("name")

    if not groups:
        return []

    ms = await _get_ms_service(current_user, db)

    out: List[MatchSuggestion] = []
    for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        name = g["name"]
        suggestions: List[ProductSearchItem] = []
        best: Optional[ProductSearchItem] = None
        confidence = "none"
        if name:
            rows, sig = await _ms_search_progressive(ms, name)
            suggestions = [ProductSearchItem.model_validate(r) for r in rows]
            if suggestions and sig:
                sigset = set(sig)

                def _score(pname: str) -> float:
                    """Доля значимых слов имени, встретившихся в названии товара МС."""
                    pn = _norm_name(pname)
                    return sum(1 for t in sigset if t in pn) / len(sigset)

                top = max(suggestions, key=lambda p: _score(p.name))
                full_count = sum(1 for p in suggestions if _score(p.name) >= 1.0)
                # high — РОВНО ОДИН товар содержит ВСЕ значимые слова (уверенное
                # совпадение). Несколько полных (напр. разные фасовки) или сильное
                # частичное (≥60%) → low: показываем, но не предвыбираем. Слабее —
                # none: не подсовываем неверный товар, кладовщик ищет вручную.
                if _score(top.name) >= 1.0 and full_count == 1:
                    best, confidence = top, "high"
                elif _score(top.name) >= 0.6:
                    best, confidence = top, "low"
            await asyncio.sleep(0.1)  # щадим лимиты МС при десятках GTIN
        out.append(
            MatchSuggestion(
                gtin=g["gtin"],
                gtin_key=key,
                name=name,
                count=g["count"],
                confidence=confidence,
                best=best,
                suggestions=suggestions,
            )
        )
    return out
