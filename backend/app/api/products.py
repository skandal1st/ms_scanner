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


@router.post("/link-gtin", response_model=LinkGtinResponse)
async def link_gtin_to_product(
    body: LinkGtinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Привязать сканы документа с указанным GTIN к товару МС.

    Работает и для ещё не сопоставленных (unknown_product), и для уже
    сопоставленных (valid/overflow) сканов — последнее позволяет ИСПРАВИТЬ
    ошибочно привязанный при загрузке УПД товар, перепривязав GTIN к другому.
    Статус пересчитывается (valid/overflow по плану), WS-пуш идёт на каждый
    обновлённый скан, чтобы фронт перерисовал список без перезагрузки.
    """
    doc_q = await db.execute(
        select(Document).where(
            Document.id == body.document_id,
            Document.user_id == current_user.id,
        )
    )
    doc = doc_q.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pid = body.moysklad_product_id.strip()
    if not pid or len(pid) > 64:
        raise HTTPException(status_code=400, detail="Некорректный UUID товара МС")

    target_key = normalize_gtin_key(body.gtin)
    if not target_key:
        raise HTTPException(status_code=400, detail="Некорректный GTIN")

    name = (body.product_name or "").strip() or None

    # Берём сканы этого GTIN во всех «рабочих» статусах, включая уже
    # сопоставленные (valid/overflow) — чтобы можно было перепривязать ошибочно
    # сопоставленный товар. duplicate оставляем как есть.
    scans_q = await db.execute(
        select(Scan).where(
            Scan.document_id == body.document_id,
            Scan.status.in_(
                [ScanStatus.unknown_product, ScanStatus.valid, ScanStatus.overflow]
            ),
        )
    )
    matched: List[Scan] = []
    for s in scans_q.scalars().all():
        if normalize_gtin_key(s.gtin) == target_key:
            matched.append(s)

    # Подсчёт уже принятых валидных сканов с этим GTIN — для overflow-проверки
    # на случай, если сопоставление прилетает после превышения плана.
    expected_qty: Optional[int] = None
    if doc.plan:
        for p in doc.plan:
            if isinstance(p, dict) and normalize_gtin_key(p.get("gtin")) == target_key:
                try:
                    expected_qty = int(p.get("expected_qty") or 0)
                except (TypeError, ValueError):
                    expected_qty = None
                break

    # matched уже содержит ВСЕ сканы этого GTIN (в т.ч. ранее valid/overflow),
    # поэтому overflow считаем с нуля по ним — внешний подсчёт не нужен (иначе
    # ранее валидные сканы этого GTIN были бы посчитаны дважды).
    valid_count = 0

    updated = 0
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
        updated += 1

    # Запоминаем соответствие GTIN→товар локально — чтобы следующие загрузки УПД
    # резолвили его без МС (find_product_by_gtin может быть недоступен / товар без
    # штрихкода). Дополняет МС-персистенцию ниже (add_gtin_barcode_to_product).
    map_row = (
        await db.execute(
            select(GtinProductMap).where(
                GtinProductMap.user_id == current_user.id,
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
                user_id=current_user.id,
                gtin=target_key,
                product_id=pid,
                product_name=name,
            )
        )

    # Переносим в план документа цену/НДС/кол-во из УПД для привязанного товара —
    # иначе при «Отправить в МС» поступление запишется без цены и НДС (до ручной
    # привязки план не содержал этот товар). Источник — upd_meta["positions"],
    # которое import_upd заполняет по GTIN независимо от сопоставления товара.
    if updated:
        upd_pos = ((doc.upd_meta or {}).get("positions") or {}).get(target_key) or {}
        # Перепривязка: убираем прежнюю (ошибочную) запись этого GTIN с другим
        # товаром — иначе в плане остаётся «фантомная» позиция чужого товара с
        # ценой/кол-вом из УПД, и при отправке в МС он завышает поступление.
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
        # Кол-во: приоритет КолТов из УПД, иначе число сопоставленных сканов.
        qty = upd_pos.get("quantity") or len(matched)
        if qty:
            entry["expected_qty"] = qty
        doc.plan = plan

    # Коммитим всегда — связка GtinProductMap должна сохраниться даже если
    # подходящих unknown_product сканов в этом документе не оказалось.
    await db.commit()
    if updated:
        for s in matched:
            await db.refresh(s)
        # WS-пуш по каждому скану, чтобы фронт мгновенно перерисовал.
        from app.worker.tasks import _push_ws_update

        for s in matched:
            await _push_ws_update(
                str(current_user.id),
                str(s.id),
                s.status,
                s.product_name,
                s.error_message,
                gtin=s.gtin,
                moysklad_product_id=s.moysklad_product_id,
            )
        logger.info(
            "products.link_gtin.done",
            document_id=str(body.document_id),
            gtin=body.gtin,
            product_id=pid,
            updated=updated,
        )

    # Закрепляем GTIN за товаром в МС — чтобы следующие сканы этого GTIN
    # матчились автоматически (find_product_by_gtin) и не требовали ручного
    # сопоставления повторно. Best-effort: не ломаем ответ при сбое МС.
    try:
        ms = await _get_ms_service(current_user, db)
        await ms.add_gtin_barcode_to_product(pid, body.gtin)
    except HTTPException:
        pass  # МС не подключён — само сопоставление сканов уже выполнено
    except Exception as e:
        logger.warning("products.link_gtin.barcode_persist_failed", error=str(e))

    return LinkGtinResponse(updated_count=updated)
