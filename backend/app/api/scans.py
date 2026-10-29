from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_, func
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, field_serializer
from app.services.cz_status import localize_cz_error, localize_cz_verification
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone

from app.services.document_guard import editable_document
from app.services.collection_start import require_collection_started
from app.db.models import DocumentStatus, TsdScanAction
from app.services.scan_events import publish_scan, publish_removed, publish_event
from app.db.session import get_db
from app.db.models import User, Scan, Document, ScanStatus, DocumentKind, Integration, OrganizationProfile, GtinNameMap, EdoDocument, EdoMark
from app.api.deps import get_current_user
from app.services.gtin_product_store import get_gtin_product
from cryptography.fernet import InvalidToken

from app.core.config import settings
from app.core.logging import logger
from app.core.security import decrypt_token
from app.services.chestnyznak import (
    ChestnyZnakService,
    CZApiError,
    cis_confidence_for_pg,
    extract_gtin,
    is_sscc,
    normalize_sscc,
    normalize_gtin_key,
    strip_ai_brackets,
    verify_code_local_gs1,
)
from app.services.gtin_cz_group import get_cached_pg
from app.services.scan_input import decode_base64_mark

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanResponse(BaseModel):
    id: UUID
    document_id: UUID
    code: str
    original_code: Optional[str] = None
    gtin: Optional[str] = None
    status: ScanStatus
    product_name: Optional[str] = None
    moysklad_product_id: Optional[str] = None
    error_message: Optional[str] = None
    scanned_at: datetime
    is_box: bool = False
    box_expanded: bool = False
    package_type: Optional[str] = None
    keep_aggregate: bool = False
    box_quantity: Optional[int] = None
    # Скан обычного штрихкода немаркированного товара (не КМ): box_quantity — кол-во.
    is_barcode: bool = False
    owner_name: Optional[str] = None
    producer_name: Optional[str] = None
    # ИНН владельца марки (ЧЗ) — фронт сверяет с владельцем подписи (cz_inn) в отгрузке.
    owner_inn: Optional[str] = None
    # Марка выведена из оборота / заблокирована (ЧЗ markWithdraw) + причина.
    withdrawn: bool = False
    withdraw_reason: Optional[str] = None
    child_codes: Optional[List[str]] = None
    verified_at: Optional[datetime] = None
    verification: Optional[dict] = None
    # Повторный скан кода, уже присутствующего в ЭТОМ документе. Строка в БД одна
    # (unique document_id+code), статус существующей не меняется — фронт подсвечивает.
    duplicate: bool = False

    model_config = {"from_attributes": True}

    @field_serializer('error_message')
    def russian_error(self, value):
        return localize_cz_error(value)

    @field_serializer('verification')
    def russian_verification(self, value):
        return localize_cz_verification(value)


class CodeSearchHit(BaseModel):
    """Один документ, в котором встречается искомый код маркировки."""
    document_id: UUID
    document_name: str
    document_kind: DocumentKind
    document_status: Optional[DocumentStatus] = None
    accepted_without_cz: bool = False
    scan_id: UUID
    code: str
    status: ScanStatus
    product_name: Optional[str] = None
    scanned_at: datetime


class UpdSearchHit(BaseModel):
    """УПД из ЭДО (Saby), в котором числится искомая марка. direction — Входящий/Исходящий."""
    edo_document_id: UUID
    direction: Optional[str] = None       # «Входящий» / «Исходящий»
    doc_type: Optional[str] = None        # «Реализация», «Поступление», …
    number: Optional[str] = None
    doc_date: Optional[str] = None        # «ДД.ММ.ГГГГ» как в Saby
    counterparty_name: Optional[str] = None
    counterparty_inn: Optional[str] = None
    state_code: Optional[int] = None
    state_name: Optional[str] = None
    cis_raw: str
    gtin: Optional[str] = None


class CodeSearchResponse(BaseModel):
    """Ответ /scans/search: марка в документах приложения + в УПД ЭДО (вход./исход.)."""
    scans: List[CodeSearchHit]
    upds: List[UpdSearchHit]
    edo_synced_at: Optional[datetime] = None
    edo_unparsed_outgoing_documents: int = 0


class CreateScanRequest(BaseModel):
    document_id: UUID
    code: str
    moysklad_product_id: Optional[str] = None


class PatchScanBody(BaseModel):
    moysklad_product_id: Optional[str] = None


class PackModeRequest(BaseModel):
    unpack: bool


async def set_scan_pack_mode(db, scan, user_id, unpack):
    await editable_document(db, scan.document_id, user_id)
    await db.refresh(scan)
    from app.services.scan_packaging import scan_package_type
    if scan.is_barcode or scan_package_type(scan) not in {"GROUP", "BOX"}:
        raise HTTPException(400, "Выберите блок или короб")
    if scan.status in {ScanStatus.pending, ScanStatus.invalid, ScanStatus.used_in_other_doc}:
        raise HTTPException(409, "Дождитесь проверки упаковки или устраните ошибку марки")
    if unpack and not scan.child_codes:
        from app.worker.tasks import unpack_scan_task
        old_status = scan.status.value
        scan.status = ScanStatus.pending
        scan.error_message = None
        await db.commit()
        try:
            unpack_scan_task.delay(str(scan.id), str(user_id), old_status)
        except Exception as exc:
            scan.status = ScanStatus(old_status)
            await db.commit()
            raise HTTPException(503, "Не удалось запустить раскрытие упаковки. Повторите попытку.") from exc
    else:
        scan.keep_aggregate = not unpack
        if scan.is_box:
            scan.box_expanded = unpack
            if unpack:
                scan.child_codes = list(dict.fromkeys(scan.child_codes))
                scan.box_quantity = len(scan.child_codes)
        scan.error_message = None
        await db.commit()
    await db.refresh(scan)
    await publish_scan(user_id, scan)
    return ScanResponse.model_validate(scan)


@router.post("/item/{scan_id}/pack-mode", response_model=ScanResponse)
async def change_scan_pack_mode(scan_id: UUID, body: PackModeRequest,
                                current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    scan = (await db.execute(select(Scan).join(Document).where(
        Scan.id == scan_id, Document.user_id == current_user.id))).scalar_one_or_none()
    if not scan:
        raise HTTPException(404, "Марка не найдена")
    return await set_scan_pack_mode(db, scan, current_user.id, body.unpack)


class CreateBoxRequest(BaseModel):
    document_id: UUID
    sscc: str
    # True — раскрыть короб на штучные КМ (ЧЗ aggregated/list по SSCC).
    # False — сохранить короб целиком (transportpack): один скан, quantity из ЧЗ.
    unpack: bool = True


# Кап на размер одной массовой загрузки списка марок (защита от гигантских payload).
_BULK_MAX_CODES = 5000


class BulkScanRequest(BaseModel):
    document_id: UUID
    codes: List[str]
    # Как трактовать SSCC-короба в списке: True — раскрыть на штучные КМ (mock-only),
    # False — сохранить короб целиком (transportpack). По умолчанию — целиком.
    unpack_boxes: bool = False


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
    device_id: Optional[UUID] = None,
    sscc_unpack: Optional[bool] = None,
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
    decoded = decode_base64_mark(code) if not is_box else None
    original_code = code if decoded is not None else None
    code = decoded if decoded is not None else code.strip()
    gtin = gtin_override if is_box else extract_gtin(code)

    doc_obj = await editable_document(db, document_id, current_user_id)

    initial_name = None
    if moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == moysklad_product_id:
                initial_name = (p.get("product_name") or "").strip() or None
                break

    # Конфликт: код уже есть в ДРУГОМ документе ТОГО ЖЕ типа (приёмка↔приёмка /
    # отгрузка↔отгрузка). Движение приёмка→отгрузка — норма, поэтому фильтр по kind.
    conflict_statement = (
        select(Document.name)
        .join(Scan, Scan.document_id == Document.id)
        .where(
            Scan.code == code,
            Document.user_id == current_user_id,
            Document.id != document_id,
            Document.kind == doc_obj.kind,
            Document.upd_meta['superseded_by_document_id'].astext.is_(None),
            Scan.status.in_(
                [
                    ScanStatus.valid,
                    ScanStatus.overflow,
                    ScanStatus.pending,
                    ScanStatus.scanned,
                    ScanStatus.unknown_product,
                ]
            ),
        )
        .limit(1)
    )
    if (getattr(doc_obj, 'upd_meta', None) or {}).get('shipment_correction'):
        conflict_statement = conflict_statement.where(or_(Document.moysklad_id.is_(None), Document.moysklad_id != doc_obj.moysklad_id))
    conflict_q = await db.execute(conflict_statement)
    conflict_doc_name = conflict_q.scalar_one_or_none()

    # Пакетный флоу: обычный КМ проверяем ЛОКАЛЬНО при скане (формат GS1 + контрольная
    # сумма GTIN) — мгновенно, без ЧЗ. Статус `scanned` = «принят, ждёт пакетной проверки
    # в ЧЗ по кнопке». Кривой формат → сразу `invalid`. Короб «целиком» (is_box) остаётся
    # на прежней задаче verify_box_task (агрегат подтверждён sscc_check, не КМ).
    local_status = ScanStatus.scanned
    local_error: Optional[str] = None
    local_serial: Optional[str] = None
    if conflict_doc_name is not None:
        local_status = ScanStatus.used_in_other_doc
        local_error = f"Код уже используется в документе «{conflict_doc_name}»"
    elif not is_box:
        vr = verify_code_local_gs1(code)
        if vr.valid:
            local_status = ScanStatus.scanned
            gtin = normalize_gtin_key(vr.gtin) or gtin
            local_serial = vr.serial
        else:
            local_status = ScanStatus.invalid
            local_error = vr.error
            gtin = (normalize_gtin_key(vr.gtin) if vr.gtin else None) or gtin
    else:
        local_status = ScanStatus.pending  # короб «целиком» → verify_box_task

    # GS-less ambiguity gate. Keyboard/HID-ввод теряет разделитель GS, серия (AI 21)
    # и крипто-хвост склеиваются. Если товарная группа известна (cache-first из
    # gtin_cz_group — БЕЗ синхронного похода в МС, инвариант скана соблюдён) и серию
    # нельзя достоверно отделить (ambiguous — напр. серия нестандартной длины), сразу
    # помечаем скан невалидным с понятным сообщением, а не отдаём в МС заведомо неверный
    # КИ. reconstructed/exact и коды с настоящим GS проходят как обычно. Группа неизвестна
    # (промах кэша) → не судим, оставляем scanned (доберётся пакетной проверкой).
    # См. project_hid_datamatrix_gs_spike.
    if local_status == ScanStatus.scanned and gtin and "\x1d" not in code:
        pg = await get_cached_pg(db, gtin)
        if pg and cis_confidence_for_pg(code, pg) == "ambiguous":
            local_status = ScanStatus.invalid
            local_error = (
                "Код без разделителя GS: не удаётся надёжно распознать серию. "
                "Включите передачу GS-разделителя на сканере или режим COM-порт."
            )

    # Опознание товара по базе знаний GTIN→товар (наполняется при отгрузках/приёмках):
    # подставляем запомненные товар МС и наименование СРАЗУ при скане, локально, без ЧЗ/МС —
    # кладовщик видит наименование и прогресс по плану мгновенно, не дожидаясь пакетной
    # проверки. Пакетная «Проверить марки» затем подтвердит код в ЧЗ и при необходимости
    # уточнит связку. Только для обычных КМ (короб опознаётся своей задачей).
    resolved_pid = moysklad_product_id
    if not is_box and gtin:
        from app.services.plan_matching import unique_plan_product
        planned = unique_plan_product(doc_obj.plan, gtin)
        if resolved_pid is None and planned:
            resolved_pid = planned["product_id"]
            initial_name = (planned.get("product_name") or "").strip() or None
        if resolved_pid is None:
            known = await get_gtin_product(db, current_user_id, gtin)
            if known:
                resolved_pid, kb_name = known
                if initial_name is None:
                    initial_name = kb_name
        if initial_name is None:
            # Фолбэк имени: база наименований (УПД / Национальный каталог), даже когда
            # товар МС по GTIN ещё не привязан — хоть что-то показать кладовщику.
            nm_name = (
                await db.execute(
                    select(GtinNameMap.product_name).where(
                        GtinNameMap.user_id == current_user_id,
                        GtinNameMap.gtin == gtin,
                    )
                )
            ).scalar_one_or_none()
            if nm_name:
                initial_name = (nm_name or "").strip() or None

    scan_values = dict(
        document_id=document_id,
        code=code,
        original_code=original_code,
        gtin=gtin,
        serial=local_serial,
        moysklad_product_id=resolved_pid,
        status=local_status,
        product_name=initial_name,
        error_message=local_error,
        is_box=is_box,
        box_quantity=box_quantity,
        package_type="BOX" if is_sscc(code) else ("GROUP" if is_box or (box_quantity and box_quantity > 1) else "UNIT"),
        keep_aggregate=bool(not is_box and box_quantity and box_quantity > 1),
    )
    from app.services.scan_packaging import needs_packaging_discovery
    discover_packaging = (not is_box and local_status == ScanStatus.scanned
                          and needs_packaging_discovery(doc_obj.plan, gtin, resolved_pid, box_quantity))
    if discover_packaging and resolved_pid and not box_quantity:
        from app.services.scan_packaging_cache import has_confirmed_unit
        if await has_confirmed_unit(current_user_id, document_id, gtin, db=db):
            discover_packaging = False
    if discover_packaging:
        scan_values.update(status=ScanStatus.pending, verification={'source': 'packaging_pending', 'checked_at': None})
    if is_box and sscc_unpack is not None and local_status == ScanStatus.pending:
        scan_values.update(box_expanded=sscc_unpack,
                           verification={'source': 'sscc_pending', 'unpack': sscc_unpack})
    scan = None
    if original_code is not None:
        canonical_exists = (await db.execute(select(Scan.id).where(
            Scan.document_id == document_id, Scan.code == code))).scalar_one_or_none()
        if canonical_exists is None:
            # Re-scanning a formerly rejected envelope repairs that row instead of
            # leaving a stale format error alongside a second copy of the same mark.
            scan = (await db.execute(select(Scan).where(
                Scan.document_id == document_id, Scan.code == original_code.strip(),
                Scan.status == ScanStatus.invalid, Scan.verified_at.is_(None),
                or_(Scan.verification.is_(None), func.jsonb_typeof(Scan.verification) == 'null'),
                Scan.is_box.is_(False), Scan.is_barcode.is_(False),
            ))).scalar_one_or_none()
    if scan is None:
        scan = Scan(**scan_values)
        db.add(scan)
    else:
        for name, value in scan_values.items():
            setattr(scan, name, value)
    if device_id:
        db.add(TsdScanAction(device_id=device_id, document_id=document_id, scan=scan))
    remember_assignment = bool(moysklad_product_id and not is_box and gtin
                               and local_status == ScanStatus.scanned)
    try:
        if remember_assignment:
            from app.services.gtin_product_store import remember_gtin_product
            await db.flush()  # Detect duplicate scans before the map upsert.
            await remember_gtin_product(db, current_user_id, gtin, moysklad_product_id, initial_name)
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
        if original_code is not None and existing.original_code is None:
            existing.original_code = original_code
            await db.commit()
            await db.refresh(existing)
        logger.info(
            "scan.duplicate",
            document_id=str(document_id),
            scan_id=str(existing.id),
            gtin=extract_gtin(code),
        )
        return existing, True

    await db.refresh(scan)
    if remember_assignment and device_id is None:
        from app.worker.tasks import persist_assigned_gtin_task
        persist_assigned_gtin_task.delay(str(current_user_id), gtin, moysklad_product_id, str(document_id))
    await publish_scan(current_user_id, scan)
    if original_code is not None:
        logger.info('scan.input_decoded', document_id=str(document_id), scan_id=str(scan.id),
                    encoding='base64', input_length=len(original_code), code_length=len(code))

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

    # Обычный КМ уже проверен локально (status=scanned/invalid) — в ЧЗ не ходим,
    # это делает пакетная задача verify_document_task по кнопке «Проверить марки».
    # Короб «целиком» проверяется отдельной задачей (агрегат подтверждён sscc_check).
    logger.info(
        "scan.created",
        document_id=str(document_id),
        scan_id=str(scan.id),
        gtin=scan.gtin,
        status=scan.status,
        is_box=is_box,
        user_id=str(current_user_id),
    )
    if is_box:
        from app.worker.tasks import verify_box_task, discover_sscc_task
        task = discover_sscc_task if sscc_unpack is not None else verify_box_task
        try:
            task.delay(str(scan.id), str(current_user_id))
        except Exception:
            scan.status = ScanStatus.invalid
            scan.error_message = 'Не удалось запустить проверку короба. Удалите короб и отсканируйте повторно.'
            await db.commit()
            await publish_scan(current_user_id, scan)
    elif discover_packaging:
        from app.worker.tasks import resolve_scan_packaging_task
        try:
            resolve_scan_packaging_task.delay(str(scan.id), str(current_user_id))
        except Exception:
            scan.status = ScanStatus.scanned
            scan.verification = None
            scan.error_message = 'Не удалось запустить определение упаковки. Нажмите «Проверить марки».'
            await db.commit()
            await publish_scan(current_user_id, scan)
            logger.warning('scan.packaging_queue_failed', scan_id=str(scan.id))
    return scan, False


def _classify_barcode(
    plan: Optional[list], code: str
) -> tuple[Optional[dict], Optional[dict]]:
    """Классифицировать «плоский» штрихкод по плану документа.

    Возвращает (немаркированная_позиция, маркированная_позиция): совпавшая по GTIN
    запись плана. Срабатывает только для обычного штрихкода (все цифры, 8..14) —
    настоящий КМ (Data Matrix) содержит серийник/спецсимволы и сюда не попадает.
    Обе None → не штрихкод либо нет совпадения (обычный флоу проверки КМ).

    Принимает сам список плана (а не ORM-документ): в массовой загрузке объект
    документа истекает после per-code commit, и обращение к ``doc.plan`` в цикле
    вызвало бы неявную async-подгрузку (MissingGreenlet).
    """
    c = (code or "").strip()
    if not (c.isdigit() and 8 <= len(c) <= 14):
        return None, None
    key = normalize_gtin_key(c)
    if not key:
        return None, None
    for p in plan or []:
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
    *, device_id: Optional[UUID] = None,
) -> tuple[Scan, bool]:
    """Штрихкодовый скан немаркированного товара: скан = +1 единица.

    Повторный скан того же штрихкода в документе наращивает ``box_quantity`` (а не
    возвращает «дубль»). ЧЗ не вызывается — скан сразу ``valid``. Возвращает (scan, False).
    """
    owner = (await db.execute(select(Document.user_id).where(Document.id == document_id))).scalar_one()
    await editable_document(db, document_id, owner)
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
        if device_id:
            db.add(TsdScanAction(device_id=device_id, document_id=document_id, scan=existing))
        await db.commit()
        await db.refresh(existing)
        await publish_scan(owner, existing)
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
    if device_id:
        db.add(TsdScanAction(device_id=device_id, document_id=document_id, scan=scan))
    await db.commit()
    await db.refresh(scan)
    await publish_scan(owner, scan)
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
    doc = await editable_document(db, body.document_id, current_user.id)
    require_collection_started(doc)
    if is_sscc(body.code):
        raise HTTPException(
            400,
            "Это код короба (SSCC). Используйте /scans/box.",
        )

    unmarked_entry, marked_entry = _classify_barcode(doc.plan, body.code)
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


async def _resolve_cz_for_boxes(
    current_user: User, db: AsyncSession, document_id: UUID
) -> ChestnyZnakService:
    """Собрать сервис ЧЗ для работы с коробами.

    В mock-режиме — без ограничений. В проде требует включённого режима коробов и
    действующего входа по УКЭП (иначе HTTPException). Вынесено из ``create_box_scans``,
    чтобы переиспользовать в массовой загрузке списка марок (``/scans/bulk``).
    """
    if settings.CZ_MOCK_MODE:
        return ChestnyZnakService(token=None)

    integration = (
        await db.execute(
            select(OrganizationProfile)
            .join(Document, Document.organization_profile_id == OrganizationProfile.id)
            .where(
                Document.id == document_id,
                OrganizationProfile.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = (
            await db.execute(select(Integration).where(Integration.user_id == current_user.id))
        ).scalar_one_or_none()
    if not integration or not integration.cz_box_mode_enabled:
        raise HTTPException(
            status_code=403,
            detail="Работа с коробами отключена. Включите режим в настройках и войдите через УКЭП.",
        )
    try:
        cz_token = (
            decrypt_token(integration.cz_token) if integration.cz_token else None
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
    return ChestnyZnakService(
        token=cz_token, mock=False, product_groups=integration.cz_product_groups
    )


async def _create_box_scans_core(
    db: AsyncSession,
    document_id: UUID,
    plan_gtins: list,
    sscc: str,
    unpack: bool,
    current_user_id: UUID,
    cz: ChestnyZnakService,
    device_id: Optional[UUID] = None,
) -> List[ScanResponse]:
    """Ядро создания сканов из SSCC-короба (целиком или с раскрытием на штучные КМ).

    Общее для ``/scans/box`` и ``/scans/bulk``. Валидацию SSCC, сбор ``cz`` и
    ``plan_gtins`` делает вызывающий — сюда передаём примитивы (а не ORM-документ),
    т.к. в массовой загрузке документ истекает после per-code commit.
    """
    sscc = normalize_sscc(sscc)

    # Authorization was checked locally by _resolve_cz_for_boxes. External calls
    # belong to the worker: even unpacking keeps a single durable SSCC scan.
    scan, is_dup = await _create_scan_record(
        db, document_id, sscc, current_user_id, is_box=True,
        device_id=device_id, sscc_unpack=unpack,
    )
    resp = ScanResponse.model_validate(scan)
    resp.duplicate = is_dup
    return [resp]


def _plan_gtins(plan: Optional[list]) -> list:
    """GTIN'ы плана документа для проверки состава короба в ЧЗ."""
    return [
        item.get("gtin")
        for item in (plan or [])
        if isinstance(item, dict) and item.get("gtin")
    ]


@router.post("/box", response_model=List[ScanResponse], status_code=201)
async def create_box_scans(
    body: CreateBoxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Принять SSCC-код короба.

    Сразу сохранить SSCC со статусом pending и запустить фоновую проверку.
    ``unpack=True`` — состав будет в child_codes, в МС уйдут отдельные КМ.
    ``unpack=False`` — количество из ЧЗ, в МС уйдёт один transportpack.

    В проде (CZ_MOCK_MODE=false) требуется включённый режим коробов в настройках и
    действующий вход в Честный Знак по УКЭП; в dev/mock — без ограничений.
    Возвращает массив созданных сканов (включая дубли — статус duplicate).
    """
    doc = await editable_document(db, body.document_id, current_user.id)
    require_collection_started(doc)
    sscc = normalize_sscc(body.sscc)
    if not is_sscc(sscc):
        raise HTTPException(400, "Это не SSCC-код короба")

    cz = await _resolve_cz_for_boxes(current_user, db, body.document_id)
    return await _create_box_scans_core(
        db, doc.id, _plan_gtins(doc.plan), sscc, body.unpack, current_user.id, cz
    )


@router.post("/bulk", response_model=List[ScanResponse], status_code=201)
async def create_bulk_scans(
    body: BulkScanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Массовая загрузка списка марок в документ (отгрузка).

    Кладовщик вставляет/загружает готовый список кодов, а не сканирует по одному.
    Каждый код проходит ту же диспетчеризацию, что и одиночный скан:
    SSCC-короб → раскрытие/целиком, «плоский» EAN немаркированной позиции → +кол-во,
    обычный КМ → ``_create_scan_record`` + ``verify_code_task`` (ЧЗ-проверка,
    overflow, распределение по плану — всё как при сканировании).

    Возвращает массив сканов (дубли — с ``duplicate=True``). Статусы ``pending``
    дотягиваются по WS по мере проверки в Celery.
    """
    doc = await editable_document(db, body.document_id, current_user.id)
    require_collection_started(doc)

    # Нормализуем список: strip, отбрасываем пустые, дедуп с сохранением порядка.
    seen: set[str] = set()
    codes: List[str] = []
    for raw in body.codes:
        c = normalize_sscc(raw or "")
        if not c or c in seen:
            continue
        seen.add(c)
        codes.append(c)
    if not codes:
        raise HTTPException(400, "Список марок пуст")
    if len(codes) > _BULK_MAX_CODES:
        raise HTTPException(
            400,
            f"Слишком много кодов за раз (макс. {_BULK_MAX_CODES}). "
            "Разбейте список на части.",
        )

    # Примитивы фиксируем ДО цикла: ``_create_scan_record`` коммитит на каждый код,
    # после чего ORM-объекты ``current_user``/``doc`` истекают, и повторное обращение
    # к ``current_user.id`` / ``doc.plan`` в цикле вызвало бы неявную async-подгрузку
    # (MissingGreenlet → 500). PK (``id``) не истекает, но берём его один раз явно.
    user_id = current_user.id
    plan_items = list(doc.plan or [])
    plan_gtins = _plan_gtins(plan_items)

    # ЧЗ для коробов собираем лениво — только если в списке реально есть SSCC.
    cz: Optional[ChestnyZnakService] = None
    responses: List[ScanResponse] = []
    for code in codes:
        if is_sscc(code):
            if cz is None:
                cz = await _resolve_cz_for_boxes(current_user, db, body.document_id)
            responses.extend(
                await _create_box_scans_core(
                    db, body.document_id, plan_gtins, code, body.unpack_boxes, user_id, cz
                )
            )
            continue

        unmarked_entry, marked_entry = _classify_barcode(plan_items, code)
        if marked_entry is not None:
            # Штрихкод маркированного товара в списке — не КМ; помечаем как invalid,
            # чтобы кладовщик увидел проблемную строку, а не молча её потерять.
            scan, is_dup = await _create_scan_record(
                db, body.document_id, code, user_id
            )
            resp = ScanResponse.model_validate(scan)
            resp.duplicate = is_dup
            responses.append(resp)
            continue
        if unmarked_entry is not None:
            scan, is_dup = await _create_or_increment_barcode_scan(
                db, body.document_id, code, unmarked_entry
            )
            resp = ScanResponse.model_validate(scan)
            resp.duplicate = is_dup
            responses.append(resp)
            continue

        scan, is_dup = await _create_scan_record(
            db, body.document_id, code, user_id
        )
        resp = ScanResponse.model_validate(scan)
        resp.duplicate = is_dup
        responses.append(resp)

    logger.info(
        "scan.bulk",
        document_id=str(body.document_id),
        total=len(codes),
        created=len(responses),
        user_id=str(user_id),
    )
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

    doc_obj = await editable_document(db, scan.document_id, current_user.id)
    scan.product_name = None
    if scan.moysklad_product_id and doc_obj.plan:
        for p in doc_obj.plan:
            if isinstance(p, dict) and p.get("product_id") == scan.moysklad_product_id:
                scan.product_name = (p.get("product_name") or "").strip() or None
                break

    await db.commit()
    await db.refresh(scan)
    await publish_scan(current_user.id, scan)
    return ScanResponse.model_validate(scan)


def _canon_cis(code: str) -> str:
    """Канонический CIS для матча со снимками ЭДО/ЧЗ (как в edo_sync/_save_marks)."""
    s = strip_ai_brackets(code or "").strip()
    s = s.split("\x1d", 1)[0]
    import re as _re
    return _re.sub(r"(?i)%c1", "", s).strip()


@router.get("/search", response_model=CodeSearchResponse)
async def search_scans_by_code(
    code: str = Query(..., min_length=1, description="Код маркировки (KM или SSCC)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Найти все места, где встречается указанный код маркировки:
    - ``scans`` — документы приложения (приёмка/отгрузка/списание), совпадение по
      точному коду скана либо по вхождению в состав короба (``child_codes``);
    - ``upds`` — УПД из ЭДО (Saby), где числится эта марка, с пометкой
      входящий/исходящий (матч по каноническому CIS).

    Объявлен ВЫШЕ ``/{document_id}``, иначе FastAPI распарсит «search» как UUID.
    """
    code = code.strip()
    if not code:
        return CodeSearchResponse(scans=[], upds=[])
    from sqlalchemy import func, exists, or_, case, cast, literal
    from sqlalchemy.dialects.postgresql import JSONB
    from app.services.mark_search import mark_key, mark_prefixes, candidate_condition
    key = mark_key(code)
    prefixes = mark_prefixes(code)
    child_codes = case((func.jsonb_typeof(Scan.child_codes) == 'array', Scan.child_codes),
                       else_=cast(literal('[]'), JSONB))
    child = func.jsonb_array_elements_text(child_codes).table_valued('value')
    child_match = exists(select(1).select_from(child).where(
        candidate_condition(child.c.value, prefixes)).correlate(Scan))
    result = await db.execute(
        select(Scan, Document)
        .join(Document, Scan.document_id == Document.id)
        .where(
            Document.user_id == current_user.id,
            or_(candidate_condition(Scan.code, prefixes), child_match),
        )
        .order_by(Scan.scanned_at.desc())
    )
    scans = [
        CodeSearchHit(
            document_id=doc.id,
            document_name=doc.name,
            document_kind=doc.kind,
            document_status=doc.status,
            accepted_without_cz=doc.kind == DocumentKind.supply and doc.status == DocumentStatus.accepted
                and (doc.upd_meta or {}).get('acceptance_cz_check_enabled') is False,
            scan_id=scan.id,
            code=scan.code,
            status=scan.status,
            product_name=scan.product_name,
            scanned_at=scan.scanned_at,
        )
        for scan, doc in result.all()
        if any(mark_key(raw) == key for raw in [scan.code, *(scan.child_codes or [])])
    ]

    # УПД из ЭДО: матч по каноническому CIS (edo_marks.cis_canonical).
    canon = _canon_cis(code)
    upds: List[UpdSearchHit] = []
    if canon:
        upd_rows = await db.execute(
            select(EdoMark, EdoDocument)
            .join(EdoDocument, EdoMark.document_id == EdoDocument.id)
            .where(
                EdoMark.user_id == current_user.id,
                EdoDocument.user_id == current_user.id,
                candidate_condition(EdoMark.cis_canonical, prefixes),
            )
            .order_by(EdoDocument.created_at.desc())
        )
        upds = [
            UpdSearchHit(
                edo_document_id=ed.id,
                direction=ed.direction,
                doc_type=ed.doc_type,
                number=ed.number,
                doc_date=ed.doc_date,
                counterparty_name=ed.counterparty_name,
                counterparty_inn=ed.counterparty_inn,
                state_code=ed.state_code,
                state_name=ed.state_name,
                cis_raw=mark.cis_raw,
                gtin=mark.gtin,
            )
            for mark, ed in upd_rows.all()
            if mark_key(mark.cis_canonical) == key
        ]

    # A UPD can carry both a full mark and its identification form.
    upds = list({hit.edo_document_id: hit for hit in upds}.values())
    synced_at = (await db.execute(select(Integration.saby_synced_at).where(
        Integration.user_id == current_user.id))).scalar_one_or_none()
    unparsed = (await db.execute(select(func.count()).select_from(EdoDocument).where(
        EdoDocument.user_id == current_user.id, EdoDocument.direction == 'Исходящий',
        EdoDocument.marks_parsed.is_(False)))).scalar_one()
    return CodeSearchResponse(scans=scans, upds=upds, edo_synced_at=synced_at,
                              edo_unparsed_outgoing_documents=unparsed)


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
    await editable_document(db, document_id, current_user.id)
    result = await db.execute(
        delete(Scan).where(Scan.document_id == document_id)
    )
    await db.commit()
    await publish_event(current_user.id, {"type": "scans_reset", "document_id": str(document_id)})
    logger.info(
        "scans.cleared",
        document_id=str(document_id),
        count=result.rowcount,
    )


class DeleteBulkRequest(BaseModel):
    document_id: UUID
    scan_ids: List[UUID]


@router.post("/delete-bulk")
async def delete_scans_bulk(
    body: DeleteBulkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Удалить пачку сканов документа по их id (напр. позицию не из плана целиком)."""
    await editable_document(db, body.document_id, current_user.id)
    if not body.scan_ids:
        return {"deleted": 0}
    result = await db.execute(
        delete(Scan).where(
            Scan.document_id == body.document_id,
            Scan.id.in_(body.scan_ids),
        )
    )
    await db.commit()
    logger.info(
        "scans.delete_bulk",
        document_id=str(body.document_id),
        count=result.rowcount,
    )
    await publish_event(current_user.id, {"type": "scans_changed", "document_id": str(body.document_id)})
    return {"deleted": result.rowcount}


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
    await editable_document(db, scan.document_id, current_user.id)
    await db.delete(scan)
    await db.commit()
    await publish_removed(current_user.id, scan.document_id, scan.id)
