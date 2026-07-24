from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone

from app.db.session import get_db
from app.db.models import User, Scan, Document, ScanStatus, DocumentKind, Integration
from app.api.deps import get_current_user
from cryptography.fernet import InvalidToken

from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token
from app.services.chestnyznak import (
    ChestnyZnakService,
    CZApiError,
    extract_gtin,
    is_sscc,
    normalize_gtin_key,
)

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanResponse(BaseModel):
    id: UUID
    document_id: UUID
    code: str
    gtin: Optional[str] = None
    status: ScanStatus
    product_name: Optional[str] = None
    moysklad_product_id: Optional[str] = None
    error_message: Optional[str] = None
    scanned_at: datetime
    is_box: bool = False
    box_quantity: Optional[int] = None
    # Скан обычного штрихкода немаркированного товара (не КМ): box_quantity — кол-во.
    is_barcode: bool = False
    owner_name: Optional[str] = None
    producer_name: Optional[str] = None
    child_codes: Optional[List[str]] = None
    # Повторный скан кода, уже присутствующего в ЭТОМ документе. Строка в БД одна
    # (unique document_id+code), статус существующей не меняется — фронт подсвечивает.
    duplicate: bool = False

    model_config = {"from_attributes": True}


class CodeSearchHit(BaseModel):
    """Один документ, в котором встречается искомый код маркировки."""
    document_id: UUID
    document_name: str
    document_kind: DocumentKind
    scan_id: UUID
    code: str
    status: ScanStatus
    product_name: Optional[str] = None
    scanned_at: datetime


class CreateScanRequest(BaseModel):
    document_id: UUID
    code: str
    moysklad_product_id: Optional[str] = None


class PatchScanBody(BaseModel):
    moysklad_product_id: Optional[str] = None


class CreateBoxRequest(BaseModel):
    document_id: UUID
    sscc: str
    # True — раскрыть короб на штучные КМ (real → 501, пока работает только mock).
    # False — сохранить короб целиком (transportpack): один скан, quantity из ЧЗ.
    unpack: bool = True


async def _ensure_document_owner(
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
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _normalize_moysklad_product_id(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > 64:
        raise HTTPException(
            status_code=400,
            detail="Идентификатор товара в МойСклад слишком длинный",
        )
    return s


async def _create_scan_record(
    db: AsyncSession,
    document_id: UUID,
    code: str,
    current_user_id: UUID,
    moysklad_product_id: Optional[str] = None,
    *,
    is_box: bool = False,
    box_quantity: Optional[int] = None,
    gtin_override: Optional[str] = None,
) -> tuple[Scan, bool]:
    """
    Создаёт скан. Возвращает (scan, is_duplicate).

    Если код уже есть в ЭТОМ документе — возвращает существующий скан как есть
    (статус не меняется) с ``is_duplicate=True`` (фронт подсветит строку, новую не
    добавляет). Если код есть в ДРУГОМ документе того же типа — новый скан получает
    статус ``used_in_other_doc`` и в ЧЗ/МС не проверяется.

    Для коробов «целиком» (``is_box=True``): ``code`` — это SSCC, GTIN берётся из
    ``gtin_override`` (из ЧЗ sscc_check, у SSCC своего GTIN в коде нет), а проверку
    делает ``verify_box_task`` вместо ``verify_code_task``.
    """
    code = code.strip()
    gtin = gtin_override if is_box else extract_gtin(code)

    doc_row = await db.execute(select(Document).where(Document.id == document_id))
    doc_obj = doc_row.scalar_one()

    initial_name = None
    if moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == moysklad_product_id:
                initial_name = (p.get("product_name") or "").strip() or None
                break

    # Конфликт: код уже есть в ДРУГОМ документе ТОГО ЖЕ типа (приёмка↔приёмка /
    # отгрузка↔отгрузка). Движение приёмка→отгрузка — норма, поэтому фильтр по kind.
    conflict_q = await db.execute(
        select(Document.name)
        .join(Scan, Scan.document_id == Document.id)
        .where(
            Scan.code == code,
            Document.user_id == current_user_id,
            Document.id != document_id,
            Document.kind == doc_obj.kind,
            Scan.status.in_(
                [
                    ScanStatus.valid,
                    ScanStatus.overflow,
                    ScanStatus.pending,
                    ScanStatus.unknown_product,
                ]
            ),
        )
        .limit(1)
    )
    conflict_doc_name = conflict_q.scalar_one_or_none()

    scan = Scan(
        document_id=document_id,
        code=code,
        gtin=gtin,
        moysklad_product_id=moysklad_product_id,
        status=(
            ScanStatus.used_in_other_doc
            if conflict_doc_name is not None
            else ScanStatus.pending
        ),
        product_name=initial_name,
        error_message=(
            f"Код уже используется в документе «{conflict_doc_name}»"
            if conflict_doc_name is not None
            else None
        ),
        is_box=is_box,
        box_quantity=box_quantity,
    )
    db.add(scan)
    try:
        await db.commit()
    except IntegrityError:
        # Повторный скан того же кода в ЭТОМ документе. Строку не добавляем и НЕ
        # меняем статус существующей (она может быть valid) — фронт подсветит её.
        await db.rollback()
        existing_q = await db.execute(
            select(Scan).where(
                Scan.document_id == document_id,
                Scan.code == code,
            )
        )
        existing = existing_q.scalar_one()
        logger.info(
            "scan.duplicate",
            document_id=str(document_id),
            scan_id=str(existing.id),
            gtin=extract_gtin(code),
        )
        return existing, True

    await db.refresh(scan)

    # Код-конфликт уже в финальном статусе used_in_other_doc — проверять в ЧЗ/МС
    # не нужно (в документ при проведении он всё равно не уйдёт).
    if conflict_doc_name is not None:
        logger.info(
            "scan.used_in_other_doc",
            document_id=str(document_id),
            scan_id=str(scan.id),
            gtin=scan.gtin,
            conflict_doc=conflict_doc_name,
            user_id=str(current_user_id),
        )
        return scan, False

    # Очередь Celery: проверка формата кода / mock ЧЗ.
    # Короб «целиком» проверяется отдельной задачей (агрегат уже подтверждён sscc_check).
    logger.info(
        "scan.created",
        document_id=str(document_id),
        scan_id=str(scan.id),
        gtin=scan.gtin,
        is_box=is_box,
        user_id=str(current_user_id),
    )
    if is_box:
        from app.worker.tasks import verify_box_task
        verify_box_task.delay(str(scan.id), str(current_user_id))
    else:
        from app.worker.tasks import verify_code_task
        verify_code_task.delay(str(scan.id), code, str(current_user_id))
    return scan, False


def _classify_barcode(
    doc: Document, code: str
) -> tuple[Optional[dict], Optional[dict]]:
    """Классифицировать «плоский» штрихкод по плану документа.

    Возвращает (немаркированная_позиция, маркированная_позиция): совпавшая по GTIN
    запись плана. Срабатывает только для обычного штрихкода (все цифры, 8..14) —
    настоящий КМ (Data Matrix) содержит серийник/спецсимволы и сюда не попадает.
    Обе None → не штрихкод либо нет совпадения (обычный флоу проверки КМ).
    """
    c = (code or "").strip()
    if not (c.isdigit() and 8 <= len(c) <= 14):
        return None, None
    key = normalize_gtin_key(c)
    if not key:
        return None, None
    for p in doc.plan or []:
        if not isinstance(p, dict):
            continue
        if normalize_gtin_key(p.get("gtin")) != key:
            continue
        if p.get("marked"):
            return None, p
        return p, None
    return None, None


async def _create_or_increment_barcode_scan(
    db: AsyncSession,
    document_id: UUID,
    code: str,
    plan_entry: dict,
) -> tuple[Scan, bool]:
    """Штрихкодовый скан немаркированного товара: скан = +1 единица.

    Повторный скан того же штрихкода в документе наращивает ``box_quantity`` (а не
    возвращает «дубль»). ЧЗ не вызывается — скан сразу ``valid``. Возвращает (scan, False).
    """
    code = code.strip()
    key = normalize_gtin_key(code)
    existing = (
        await db.execute(
            select(Scan).where(
                Scan.document_id == document_id,
                Scan.code == code,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Уже был как штрихкодовый — наращиваем кол-во. Если вдруг тот же код есть как
        # обычный скан (маловероятно) — не трогаем, отдаём как дубль.
        if not existing.is_barcode:
            return existing, True
        existing.box_quantity = (existing.box_quantity or 0) + 1
        await db.commit()
        await db.refresh(existing)
        return existing, False

    scan = Scan(
        document_id=document_id,
        code=code,
        gtin=key,
        moysklad_product_id=(plan_entry.get("product_id") or None),
        product_name=(plan_entry.get("product_name") or "").strip() or None,
        status=ScanStatus.valid,
        is_barcode=True,
        box_quantity=1,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan, False


@router.post("/", response_model=ScanResponse, status_code=201)
async def create_scan(
    body: CreateScanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Создать скан кода маркировки (KM, не SSCC). Для коробов — POST /scans/box.

    Немаркированный товар собирается сканом обычного штрихкода (EAN-13): если код —
    «плоский» штрихкод и совпал с немаркированной позицией плана, добавляем кол-во
    без вызова ЧЗ. Штрихкод маркированного товара — подсказка отсканировать КМ.
    """
    doc = await _ensure_document_owner(body.document_id, current_user, db)
    if is_sscc(body.code):
        raise HTTPException(
            400,
            "Это код короба (SSCC). Используйте /scans/box.",
        )

    unmarked_entry, marked_entry = _classify_barcode(doc, body.code)
    if marked_entry is not None:
        raise HTTPException(
            400,
            "Это маркированный товар — отсканируйте код маркировки (Data Matrix), "
            "а не штрихкод.",
        )
    if unmarked_entry is not None:
        scan, is_dup = await _create_or_increment_barcode_scan(
            db, body.document_id, body.code, unmarked_entry
        )
        logger.info(
            "scan.barcode",
            document_id=str(body.document_id),
            scan_id=str(scan.id),
            gtin=scan.gtin,
            qty=scan.box_quantity,
            user_id=str(current_user.id),
        )
        resp = ScanResponse.model_validate(scan)
        resp.duplicate = is_dup
        return resp

    scan, is_dup = await _create_scan_record(
        db,
        body.document_id,
        body.code,
        current_user.id,
        _normalize_moysklad_product_id(body.moysklad_product_id),
    )
    resp = ScanResponse.model_validate(scan)
    resp.duplicate = is_dup
    return resp


@router.post("/box", response_model=List[ScanResponse], status_code=201)
async def create_box_scans(
    body: CreateBoxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Принять SSCC-код короба.

    ``unpack=True`` — раскрыть на индивидуальные KM (по скану на каждый). Реальное
    раскрытие через ЧЗ пока не реализовано (501); работает только в mock.

    ``unpack=False`` — сохранить короб целиком: один скан с ``is_box=True`` и
    ``box_quantity`` из ЧЗ (``sscc_check``); в МС уйдёт одним ``transportpack``.

    В проде (CZ_MOCK_MODE=false) требуется включённый режим коробов в настройках и
    действующий вход в Честный Знак по УКЭП; в dev/mock — без ограничений.
    Возвращает массив созданных сканов (включая дубли — статус duplicate).
    """
    doc = await _ensure_document_owner(body.document_id, current_user, db)
    sscc = body.sscc.strip()
    if not is_sscc(sscc):
        raise HTTPException(400, "Это не SSCC-код короба")

    int_result = await db.execute(
        select(Integration).where(Integration.user_id == current_user.id)
    )
    integration = int_result.scalar_one_or_none()

    if not settings.CZ_MOCK_MODE:
        if not integration or not integration.cz_box_mode_enabled:
            raise HTTPException(
                status_code=403,
                detail="Работа с коробами отключена. Включите режим в настройках и войдите через УКЭП.",
            )
        try:
            cz_token = (
                decrypt_token(integration.cz_token)
                if integration.cz_token
                else None
            )
        except InvalidToken:
            raise HTTPException(
                status_code=502,
                detail="Токен Честного Знака повреждён. Выйдите и войдите снова по УКЭП.",
            )
        expired = (
            integration.cz_token_expires_at is not None
            and integration.cz_token_expires_at <= datetime.now(timezone.utc)
        )
        if not cz_token or expired:
            raise HTTPException(
                status_code=403,
                detail="Нужен действующий вход в Честный Знак (УКЭП) для работы с коробами.",
            )
        cz = ChestnyZnakService(token=cz_token, mock=False)
    else:
        cz = ChestnyZnakService(token=None)

    plan_gtins = [
        item.get("gtin")
        for item in (doc.plan or [])
        if isinstance(item, dict) and item.get("gtin")
    ]

    # Короб целиком: один скан-короб, quantity и GTIN из ЧЗ sscc_check.
    if not body.unpack:
        try:
            info = await cz.get_sscc_info(sscc, plan_gtins)
        except CZApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        scan, is_dup = await _create_scan_record(
            db,
            body.document_id,
            sscc,
            current_user.id,
            is_box=True,
            box_quantity=info.quantity,
            gtin_override=info.gtin,
        )
        resp = ScanResponse.model_validate(scan)
        resp.duplicate = is_dup
        return [resp]

    # Раскрытие на штучные КМ.
    try:
        member_codes = await cz.unpack_box(sscc, plan_gtins)
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Раскрытие SSCC через API Честного Знака пока не настроено. "
            "Переключите тумблер на «целиком» или сканируйте коды поштучно.",
        )

    responses: List[ScanResponse] = []
    for code in member_codes:
        scan, is_dup = await _create_scan_record(
            db, body.document_id, code, current_user.id
        )
        resp = ScanResponse.model_validate(scan)
        resp.duplicate = is_dup
        responses.append(resp)

    return responses


@router.patch("/item/{scan_id}", response_model=ScanResponse)
async def patch_scan_product(
    scan_id: UUID,
    body: PatchScanBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Задать или снять явную привязку скана к товару МС (UUID)."""
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

    data = body.model_dump(exclude_unset=True)
    if "moysklad_product_id" not in data:
        return ScanResponse.model_validate(scan)

    scan.moysklad_product_id = _normalize_moysklad_product_id(body.moysklad_product_id)

    doc_row = await db.execute(select(Document).where(Document.id == scan.document_id))
    doc_obj = doc_row.scalar_one()
    scan.product_name = None
    if scan.moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == scan.moysklad_product_id:
                scan.product_name = (p.get("product_name") or "").strip() or None
                break

    await db.commit()
    await db.refresh(scan)
    return ScanResponse.model_validate(scan)


@router.get("/search", response_model=List[CodeSearchHit])
async def search_scans_by_code(
    code: str = Query(..., min_length=1, description="Код маркировки (KM или SSCC)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Найти все документы пользователя, в которых уже есть указанный код маркировки.

    Совпадение по точному коду скана либо по вхождению кода в состав короба
    (``child_codes``). Объявлен ВЫШЕ ``/{document_id}``, иначе FastAPI распарсит
    «search» как UUID документа.
    """
    code = code.strip()
    result = await db.execute(
        select(Scan, Document)
        .join(Document, Scan.document_id == Document.id)
        .where(
            Document.user_id == current_user.id,
            (Scan.code == code) | (Scan.child_codes.contains([code])),
        )
        .order_by(Scan.scanned_at.desc())
    )
    return [
        CodeSearchHit(
            document_id=doc.id,
            document_name=doc.name,
            document_kind=doc.kind,
            scan_id=scan.id,
            code=scan.code,
            status=scan.status,
            product_name=scan.product_name,
            scanned_at=scan.scanned_at,
        )
        for scan, doc in result.all()
    ]


@router.get("/{document_id}", response_model=List[ScanResponse])
async def list_scans(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_document_owner(document_id, current_user, db)
    result = await db.execute(
        select(Scan)
        .where(Scan.document_id == document_id)
        .order_by(Scan.scanned_at.desc())
    )
    return [ScanResponse.model_validate(s) for s in result.scalars().all()]


@router.delete("/by-document/{document_id}", status_code=204)
async def delete_document_scans(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Удалить все марки (сканы) документа из БД."""
    await _ensure_document_owner(document_id, current_user, db)
    result = await db.execute(
        delete(Scan).where(Scan.document_id == document_id)
    )
    await db.commit()
    logger.info(
        "scans.cleared",
        document_id=str(document_id),
        count=result.rowcount,
    )


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
