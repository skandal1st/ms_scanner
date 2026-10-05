"""API терминалов сбора данных: QR-привязка, список отгрузок и комплектация."""
import json
import secrets
import httpx
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_active_organization_profile, get_current_user
from app.api.scans import (
    ScanResponse,
    PackModeRequest, set_scan_pack_mode,
    _classify_barcode,
    _create_or_increment_barcode_scan,
    _create_scan_record,
    _create_box_scans_core,
    _plan_gtins,
    _resolve_cz_for_boxes,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.security import create_tsd_access_token, decode_token, decrypt_token
from app.db.models import (
    Document,
    DocumentKind,
    DocumentStatus,
    Integration,
    OrganizationProfile,
    Scan,
    TsdDevice,
    TsdDocumentSession,
    TsdScanAction,
    User,
    Workplace,
)
from app.db.session import get_db
from app.services.document_guard import lock_ms_document
from app.services.scan_events import publish_scan, publish_removed, publish_event
from app.services.chestnyznak import is_sscc, normalize_sscc
from app.services.moysklad import (MoySkladService, customer_order_links, customer_order_empty_message,
    customer_order_direct_shipment_count, shipment_matches_customer_order)
from app.services.customer_order_filters import CustomerOrderFilter, profile_order_filters, resolve_order_filter

router = APIRouter(prefix="/tsd", tags=["tsd"])
bearer = HTTPBearer()
PAIR_PREFIX = "tsd_pair:"
PAIR_TTL_SECONDS = 300


class PairingRequest(BaseModel):
    workplace_id: Optional[UUID] = None


class PairingResponse(BaseModel):
    code: str
    payload: str
    expires_in: int
    workplace_name: str


class PairingExchangeRequest(BaseModel):
    code: str = Field(min_length=16, max_length=256)
    device_name: str = Field(default="ТСД АТОЛ", min_length=1, max_length=255)


class DeviceAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    device_id: UUID
    device_name: str
    workplace_name: str
    organization_name: str


class DeviceContext(BaseModel):
    device_id: UUID
    device_name: str
    workplace_id: UUID
    workplace_name: str
    organization_profile_id: UUID
    organization_name: str


class TsdDocumentItem(BaseModel):
    moysklad_id: str
    local_document_id: Optional[UUID] = None
    name: str
    agent_name: Optional[str] = None
    customer_order_name: Optional[str] = None
    store_name: Optional[str] = None
    moment: Optional[str] = None
    collected: int = 0
    expected: int = 0
    in_work: bool = False
    active_on_other_device: bool = False


class SelectDocumentRequest(BaseModel):
    moysklad_id: str = Field(min_length=1, max_length=255)
    customer_order_id: Optional[UUID] = None


class TsdOrderItem(BaseModel):
    moysklad_id: str
    name: str
    agent_name: Optional[str] = None
    store_name: Optional[str] = None
    moment: Optional[str] = None
    state_name: Optional[str] = None
    shipment_count: Optional[int] = None
    retail_sale_count: int = 0
    in_work: bool = False


class TsdOrderShipments(BaseModel):
    order_id: str
    order_name: str
    shipments: list[TsdDocumentItem]
    empty_shipments_message: Optional[str] = None


class TsdDocumentDetail(BaseModel):
    id: UUID
    name: str
    status: str
    plan: list[dict]
    scans: list[ScanResponse]
    session_id: UUID
    active_on_other_device: bool = False
    customer_order_name: Optional[str] = None
    customer_order_id: Optional[str] = None


class TsdScanRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    moysklad_product_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class TsdDeviceRow(BaseModel):
    id: UUID
    name: str
    workplace_name: str
    is_active: bool
    last_seen_at: Optional[datetime]
    created_at: datetime


async def _ms_for_user(db: AsyncSession, user_id: UUID) -> MoySkladService:
    integration = (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(400, "МойСклад не подключён")
    return MoySkladService(decrypt_token(integration.moysklad_token))


async def _default_workplace(
    db: AsyncSession, user: User, profile: OrganizationProfile
) -> Workplace:
    workplace = (
        await db.execute(
            select(Workplace)
            .where(
                Workplace.user_id == user.id,
                Workplace.organization_profile_id == profile.id,
                Workplace.is_active.is_(True),
            )
            .order_by(Workplace.is_default.desc(), Workplace.created_at)
        )
    ).scalars().first()
    if workplace:
        return workplace
    workplace = Workplace(
        user_id=user.id,
        organization_profile_id=profile.id,
        name="Основной склад",
        store_ids=[],
        is_default=True,
    )
    db.add(workplace)
    await db.flush()
    return workplace


@router.post("/pairings", response_model=PairingResponse)
async def create_pairing(
    body: PairingRequest,
    current_user: User = Depends(get_current_user),
    profile: OrganizationProfile = Depends(get_active_organization_profile),
    db: AsyncSession = Depends(get_db),
):
    if body.workplace_id:
        workplace = (
            await db.execute(
                select(Workplace).where(
                    Workplace.id == body.workplace_id,
                    Workplace.user_id == current_user.id,
                    Workplace.organization_profile_id == profile.id,
                    Workplace.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not workplace:
            raise HTTPException(404, "Рабочее место не найдено")
    else:
        workplace = await _default_workplace(db, current_user, profile)
        await db.commit()

    code = secrets.token_urlsafe(32)
    payload = {"user_id": str(current_user.id), "workplace_id": str(workplace.id)}
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        await redis.set(f"{PAIR_PREFIX}{code}", json.dumps(payload), ex=PAIR_TTL_SECONDS)
    finally:
        await redis.aclose()
    logger.info(
        "tsd.pairing.issued",
        user_id=str(current_user.id),
        workplace_id=str(workplace.id),
    )
    return PairingResponse(
        code=code,
        payload=f"SKANDATA:TSD:{code}",
        expires_in=PAIR_TTL_SECONDS,
        workplace_name=workplace.name,
    )


@router.post("/auth/exchange", response_model=DeviceAuthResponse)
async def exchange_pairing(body: PairingExchangeRequest, db: AsyncSession = Depends(get_db)):
    raw_code = body.code.strip()
    code = raw_code.removeprefix("SKANDATA:TSD:")
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        raw = await redis.getdel(f"{PAIR_PREFIX}{code}")
    finally:
        await redis.aclose()
    if not raw:
        raise HTTPException(401, "QR-код устарел или уже использован")
    data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    workplace = (
        await db.execute(
            select(Workplace).where(
                Workplace.id == data["workplace_id"],
                Workplace.user_id == data["user_id"],
                Workplace.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not workplace:
        raise HTTPException(403, "Рабочее место отключено")
    profile = await db.get(OrganizationProfile, workplace.organization_profile_id)
    device = TsdDevice(
        user_id=workplace.user_id,
        workplace_id=workplace.id,
        name=body.device_name.strip(),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    token = create_tsd_access_token(
        {"sub": str(device.user_id), "device_id": str(device.id)}
    )
    logger.info(
        "tsd.pairing.exchanged",
        user_id=str(device.user_id),
        device_id=str(device.id),
        workplace_id=str(workplace.id),
    )
    return DeviceAuthResponse(
        access_token=token,
        device_id=device.id,
        device_name=device.name,
        workplace_name=workplace.name,
        organization_name=profile.name if profile else "Юрлицо",
    )


async def get_tsd_device(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TsdDevice:
    error = HTTPException(401, "Сессия ТСД истекла", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "tsd_access" or not payload.get("device_id"):
            raise error
        device = await db.get(TsdDevice, payload["device_id"])
    except (JWTError, ValueError):
        raise error
    if not device or not device.is_active or str(device.user_id) != payload.get("sub"):
        raise error
    device.last_seen_at = datetime.now(timezone.utc)
    return device


async def _device_scope(db: AsyncSession, device: TsdDevice):
    workplace = await db.get(Workplace, device.workplace_id)
    if not workplace or not workplace.is_active:
        raise HTTPException(403, "Рабочее место отключено")
    profile = await db.get(OrganizationProfile, workplace.organization_profile_id)
    user = await db.get(User, device.user_id)
    if not profile or not user or not user.is_active:
        raise HTTPException(403, "Доступ ТСД отозван")
    return user, workplace, profile


@router.get("/me", response_model=DeviceContext)
async def device_me(
    device: TsdDevice = Depends(get_tsd_device), db: AsyncSession = Depends(get_db)
):
    _, workplace, profile = await _device_scope(db, device)
    await db.commit()
    return DeviceContext(
        device_id=device.id,
        device_name=device.name,
        workplace_id=workplace.id,
        workplace_name=workplace.name,
        organization_profile_id=profile.id,
        organization_name=profile.name,
    )


def _scan_units(scan: Scan) -> int:
    return int(scan.box_quantity or 1)


def _ms_entity_id(entity: Optional[dict]) -> Optional[str]:
    entity = entity or {}
    return entity.get("id") or MoySkladService._id_from_href((entity.get("meta") or {}).get("href", "")) or None


def _order_ms_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 403:
            return HTTPException(403, "Нет доступа к заказам покупателей в МойСкладе. Обновите права решения в МойСкладе.")
        if exc.response.status_code == 404:
            return HTTPException(404, "Заказ покупателя не найден в МойСкладе")
    return HTTPException(502, "Не удалось загрузить заказ или его отгрузки из МойСклада. Повторите попытку.")


@router.get("/order-filters", response_model=list[CustomerOrderFilter])
async def get_tsd_order_filters(device: TsdDevice = Depends(get_tsd_device), db: AsyncSession = Depends(get_db)):
    _, _, profile = await _device_scope(db, device)
    return profile_order_filters(profile)


@router.get("/orders", response_model=list[TsdOrderItem])
async def list_tsd_orders(
    search: Optional[str] = None, offset: int = 0,
    device: TsdDevice = Depends(get_tsd_device), db: AsyncSession = Depends(get_db),
    filter_id: Optional[UUID] = None,
):
    if offset < 0:
        raise HTTPException(400, "Некорректная страница списка заказов")
    _, workplace, profile = await _device_scope(db, device)
    selected_filter = resolve_order_filter(profile, filter_id)
    ms = await _ms_for_user(db, device.user_id)
    try:
        rows = await ms.get_customer_orders(profile.moysklad_organization_id, search, offset=offset,
                                            **({"order_filter": selected_filter} if selected_filter else {}))
    except Exception as exc:
        logger.warning("tsd.orders.moysklad_failed", device_id=str(device.id), error=str(exc))
        raise _order_ms_error(exc) from exc
    demand_orders = {_ms_entity_id(demand): row["id"] for row in rows
                     for demand in customer_order_links(row, "demand") if _ms_entity_id(demand)}
    order_names = {row["id"]: row.get("name") for row in rows}
    local_docs = (await db.execute(select(Document).where(
        Document.user_id == device.user_id, Document.organization_profile_id == profile.id,
        Document.kind == DocumentKind.demand, Document.status != DocumentStatus.accepted,
        or_(Document.moysklad_customer_order_id.in_([row["id"] for row in rows]),
            Document.moysklad_id.in_(list(demand_orders))),
    ))).scalars().all()
    if workplace.store_ids:
        local_docs = [doc for doc in local_docs if doc.moysklad_store_id in workplace.store_ids]
    for doc in local_docs:
        linked_order = demand_orders.get(doc.moysklad_id)
        if linked_order:
            doc.moysklad_customer_order_id = linked_order
            doc.customer_order_name = order_names.get(linked_order)
    sessions = (await db.execute(select(TsdDocumentSession).where(
        TsdDocumentSession.document_id.in_([doc.id for doc in local_docs]),
        TsdDocumentSession.status == "active",
    ))).scalars().all() if local_docs else []
    scans = (await db.execute(select(Scan.document_id).where(
        Scan.document_id.in_([doc.id for doc in local_docs]),
    ))).scalars().all() if local_docs else []
    active_docs = {session.document_id for session in sessions} | set(scans)
    work_orders = {doc.moysklad_customer_order_id for doc in local_docs if doc.id in active_docs}
    await db.commit()
    return [TsdOrderItem(
        moysklad_id=row["id"], name=row.get("name") or "Без номера",
        agent_name=(row.get("agent") or {}).get("name"), store_name=(row.get("store") or {}).get("name"),
        state_name=(row.get("state") or {}).get("name"), moment=row.get("moment"),
        shipment_count=customer_order_direct_shipment_count(row),
        retail_sale_count=len(customer_order_links(row, "retaildemand")),
        in_work=row["id"] in work_orders,
    ) for row in rows]


@router.get("/orders/{order_id}/shipments", response_model=TsdOrderShipments)
async def get_tsd_order_shipments(
    order_id: UUID, device: TsdDevice = Depends(get_tsd_device), db: AsyncSession = Depends(get_db),
):
    _, workplace, profile = await _device_scope(db, device)
    ms = await _ms_for_user(db, device.user_id)
    try:
        order = await ms.get_customer_order(str(order_id))
        if profile.moysklad_organization_id and _ms_entity_id(order.get("organization")) != profile.moysklad_organization_id:
            raise HTTPException(403, "Заказ покупателя относится к другому юрлицу")
        rows = await ms.get_customer_order_demands(str(order_id), profile.moysklad_organization_id, order=order)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("tsd.order_shipments.moysklad_failed", order_id=str(order_id), error=str(exc))
        raise _order_ms_error(exc) from exc
    # Validate actual shipment scope, not the optional warehouse on the order header.
    rows = [row for row in rows if not row.get("deleted")
            and (not profile.moysklad_organization_id or _ms_entity_id(row.get("organization")) == profile.moysklad_organization_id)
            and (not workplace.store_ids or _ms_entity_id(row.get("store")) in workplace.store_ids)]
    local_docs = (await db.execute(select(Document).where(
        Document.user_id == device.user_id, Document.organization_profile_id == profile.id,
        Document.kind == DocumentKind.demand, Document.moysklad_id.in_([row["id"] for row in rows]),
    ))).scalars().all()
    docs = {doc.moysklad_id: doc for doc in local_docs}
    scans = (await db.execute(select(Scan).where(Scan.document_id.in_([doc.id for doc in local_docs])))).scalars().all() if local_docs else []
    sessions = (await db.execute(select(TsdDocumentSession).where(
        TsdDocumentSession.document_id.in_([doc.id for doc in local_docs]), TsdDocumentSession.status == "active",
    ))).scalars().all() if local_docs else []
    shipments = []
    for row in rows:
        doc = docs.get(row["id"])
        if doc and doc.status == DocumentStatus.accepted:
            continue
        if doc:
            doc.moysklad_customer_order_id = str(order_id)
            doc.customer_order_name = order.get("name")
        collected = sum(_scan_units(scan) for scan in scans if doc and scan.document_id == doc.id
                        and scan.status.value in {"scanned", "valid", "overflow"})
        active = [session for session in sessions if doc and session.document_id == doc.id]
        shipments.append(TsdDocumentItem(
            moysklad_id=row["id"], local_document_id=doc.id if doc else None,
            name=row.get("name") or "Без номера", customer_order_name=order.get("name"),
            agent_name=(row.get("agent") or {}).get("name"), store_name=(row.get("store") or {}).get("name"),
            moment=row.get("moment"), collected=collected,
            expected=sum(int(p.get("expected_qty") or 0) for p in (doc.plan or [])) if doc else 0,
            in_work=bool(collected or active), active_on_other_device=any(session.device_id != device.id for session in active),
        ))
    await db.commit()
    return TsdOrderShipments(order_id=str(order_id), order_name=order.get("name") or "Без номера", shipments=shipments,
                             empty_shipments_message=customer_order_empty_message(order) if not shipments else None)


@router.get("/documents", response_model=list[TsdDocumentItem])
async def list_tsd_documents(
    search: Optional[str] = None,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    _, workplace, profile = await _device_scope(db, device)
    ms = await _ms_for_user(db, device.user_id)
    try:
        rows = await ms.get_documents(
            "demand", limit=50, search=search, organization_id=profile.moysklad_organization_id
        )
    except Exception as exc:
        # Уже начатые сборки остаются доступны при кратком сбое МойСклада.
        logger.warning("tsd.documents.moysklad_failed", device_id=str(device.id), error=str(exc))
        rows = []
    allowed_stores = set(workplace.store_ids or [])
    if allowed_stores:
        rows = [r for r in rows if r.get("store_id") in allowed_stores]
    local_docs = (
        await db.execute(
            select(Document).where(
                Document.user_id == device.user_id,
                Document.organization_profile_id == profile.id,
                Document.kind == DocumentKind.demand,
                Document.status != DocumentStatus.accepted,
            )
        )
    ).scalars().all()
    if allowed_stores:
        local_docs = [d for d in local_docs if d.moysklad_store_id in allowed_stores]
    local_docs = [d for d in local_docs if not (getattr(d, 'upd_meta', None) or {}).get('superseded_by_document_id')]
    by_ms = {d.moysklad_id: d for d in local_docs}
    listed_ids = {r["id"] for r in rows}
    query = (search or "").strip().casefold()
    for doc in local_docs:
        if not doc.moysklad_id or doc.moysklad_id in listed_ids:
            continue
        if query and query not in doc.name.casefold():
            continue
        rows.append({
            "id": doc.moysklad_id, "name": doc.name, "moment": doc.created_at.isoformat(),
            "agent_name": None, "customer_order_name": None, "store_name": None,
        })
    scans = (
        await db.execute(
            select(Scan).where(Scan.document_id.in_([d.id for d in local_docs]))
        )
    ).scalars().all() if local_docs else []
    collected: dict[UUID, int] = {}
    for scan in scans:
        if scan.status.value in {"scanned", "valid", "overflow"}:
            collected[scan.document_id] = collected.get(scan.document_id, 0) + _scan_units(scan)
    sessions = (
        await db.execute(
            select(TsdDocumentSession).where(
                TsdDocumentSession.document_id.in_([d.id for d in local_docs]),
                TsdDocumentSession.status == "active",
            )
        )
    ).scalars().all() if local_docs else []
    session_by_doc = {s.document_id: s for s in sessions}
    result = []
    for row in rows:
        doc = by_ms.get(row["id"])
        session = session_by_doc.get(doc.id) if doc else None
        result.append(
            TsdDocumentItem(
                moysklad_id=row["id"],
                local_document_id=doc.id if doc else None,
                name=row["name"],
                agent_name=row.get("agent_name"),
                customer_order_name=row.get("customer_order_name"),
                store_name=row.get("store_name"),
                moment=row.get("moment"),
                collected=collected.get(doc.id, 0) if doc else 0,
                expected=sum(int(p.get("expected_qty") or 0) for p in (doc.plan or [])) if doc else 0,
                in_work=bool(doc and (collected.get(doc.id, 0) or session)),
                active_on_other_device=bool(session and session.device_id != device.id),
            )
        )
    await db.commit()
    return result


async def _owned_tsd_document(
    db: AsyncSession, device: TsdDevice, document_id: UUID
) -> Document:
    _, workplace, profile = await _device_scope(db, device)
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == device.user_id,
                Document.organization_profile_id == profile.id,
                Document.kind == DocumentKind.demand,
                Document.status != DocumentStatus.accepted,
            )
        )
    ).scalar_one_or_none()
    if not doc or ((workplace.store_ids or []) and doc.moysklad_store_id not in workplace.store_ids):
        raise HTTPException(404, "Отгрузка недоступна на этом рабочем месте")
    if (getattr(doc, 'upd_meta', None) or {}).get('superseded_by_document_id'):
        raise HTTPException(409, 'Марки перенесены в действующую отгрузку. Откройте её заново из заказа покупателя.')
    return doc


@router.post("/documents/select", response_model=TsdDocumentDetail)
async def select_tsd_document(
    body: SelectDocumentRequest,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    _, workplace, profile = await _device_scope(db, device)
    await lock_ms_document(db, device.user_id, DocumentKind.demand, body.moysklad_id)
    doc = (
        await db.execute(
            select(Document)
            .where(
                Document.user_id == device.user_id,
                Document.organization_profile_id == profile.id,
                Document.moysklad_id == body.moysklad_id,
                Document.kind == DocumentKind.demand,
                Document.status != DocumentStatus.accepted,
            )
            .order_by(Document.created_at.desc())
        )
    ).scalars().first()
    ms = await _ms_for_user(db, device.user_id)
    # Resolve the order by identity, and recheck a saved shipment before opening it from an order.
    try:
        ms_doc = await ms.get_document("demand", body.moysklad_id)
    except Exception as exc:
        raise HTTPException(502, "Не удалось загрузить отгрузку из МойСклада. Повторите попытку.") from exc
    from app.services.shipment_guard import ensure_active_shipment, ensure_no_stranded_scans
    try:
        ensure_active_shipment(ms_doc)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    order_id = _ms_entity_id((ms_doc or {}).get("customerOrder"))
    order_name = ((ms_doc or {}).get("customerOrder") or {}).get("name")
    if body.customer_order_id:
        try:
            order = await ms.get_customer_order(str(body.customer_order_id))
        except Exception as exc:
            raise _order_ms_error(exc) from exc
        if not shipment_matches_customer_order(ms_doc, order, str(body.customer_order_id)):
            raise HTTPException(409, "Отгрузка больше не связана с выбранным заказом. Обновите список отгрузок.")
        if profile.moysklad_organization_id and _ms_entity_id(order.get("organization")) != profile.moysklad_organization_id:
            raise HTTPException(403, "Заказ покупателя относится к другому юрлицу")
        order_id = str(body.customer_order_id)
        order_name = order.get("name")
    if ms_doc:
        org_id = _ms_entity_id(ms_doc.get("organization"))
        store_id = _ms_entity_id(ms_doc.get("store"))
        if profile.moysklad_organization_id and org_id != profile.moysklad_organization_id:
            raise HTTPException(403, "Отгрузка относится к другому юрлицу")
        if workplace.store_ids and store_id not in workplace.store_ids:
            raise HTTPException(403, "Отгрузка относится к другому складу")
    await ensure_no_stranded_scans(db, ms, device.user_id, profile.id, order_id, body.moysklad_id)
    if doc is None:
        doc = Document(
            user_id=device.user_id,
            moysklad_id=body.moysklad_id,
            organization_profile_id=profile.id,
            workplace_id=workplace.id,
            moysklad_organization_id=org_id,
            moysklad_store_id=store_id,
            moysklad_customer_order_id=order_id,
            customer_order_name=order_name,
            name=ms_doc.get("name") or f"Отгрузка {body.moysklad_id[:8]}",
            kind=DocumentKind.demand,
            plan=await ms.build_plan("demand", body.moysklad_id),
        )
        db.add(doc)
        await db.flush()
    else:
        if workplace.store_ids and doc.moysklad_store_id not in workplace.store_ids:
            raise HTTPException(403, "Отгрузка относится к другому складу")
        from app.services.legacy_pack_plan import refresh_legacy_pack_plan
        await refresh_legacy_pack_plan(db, doc, ms)
        doc.workplace_id = workplace.id
        if body.customer_order_id:
            doc.moysklad_customer_order_id = order_id
            doc.customer_order_name = order_name
    session = (
        await db.execute(
            select(TsdDocumentSession).where(
                TsdDocumentSession.device_id == device.id,
                TsdDocumentSession.document_id == doc.id,
                TsdDocumentSession.status == "active",
            )
        )
    ).scalar_one_or_none()
    if session is None:
        session = TsdDocumentSession(device_id=device.id, document_id=doc.id)
        db.add(session)
    session.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    scans = (
        await db.execute(select(Scan).where(Scan.document_id == doc.id).order_by(Scan.scanned_at.desc()))
    ).scalars().all()
    other = (
        await db.execute(
            select(func.count(TsdDocumentSession.id)).where(
                TsdDocumentSession.document_id == doc.id,
                TsdDocumentSession.status == "active",
                TsdDocumentSession.device_id != device.id,
            )
        )
    ).scalar_one()
    return TsdDocumentDetail(
        id=doc.id,
        name=doc.name,
        status=doc.status.value,
        plan=list(doc.plan or []),
        scans=[ScanResponse.model_validate(s) for s in scans],
        session_id=session.id,
        active_on_other_device=bool(other),
        customer_order_name=doc.customer_order_name,
        customer_order_id=doc.moysklad_customer_order_id,
    )


@router.get("/documents/{document_id}", response_model=TsdDocumentDetail)
async def get_tsd_document(
    document_id: UUID,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    doc = await _owned_tsd_document(db, device, document_id)
    from app.services.legacy_pack_plan import refresh_legacy_pack_plan
    if doc.status == DocumentStatus.draft and any('pack_quantities' not in p for p in doc.plan or []):
        await refresh_legacy_pack_plan(db, doc, await _ms_for_user(db, device.user_id))
    session = (
        await db.execute(
            select(TsdDocumentSession).where(
                TsdDocumentSession.device_id == device.id,
                TsdDocumentSession.document_id == doc.id,
                TsdDocumentSession.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(409, "Сессия сборки завершена. Откройте отгрузку снова")
    scans = (
        await db.execute(select(Scan).where(Scan.document_id == doc.id).order_by(Scan.scanned_at.desc()))
    ).scalars().all()
    other = (
        await db.execute(select(func.count(TsdDocumentSession.id)).where(
            TsdDocumentSession.document_id == doc.id,
            TsdDocumentSession.status == "active",
            TsdDocumentSession.device_id != device.id,
        ))
    ).scalar_one()
    await db.commit()
    return TsdDocumentDetail(
        id=doc.id, name=doc.name, status=doc.status.value, plan=list(doc.plan or []),
        scans=[ScanResponse.model_validate(s) for s in scans], session_id=session.id,
        active_on_other_device=bool(other),
        customer_order_name=doc.customer_order_name,
        customer_order_id=doc.moysklad_customer_order_id,
    )


@router.post("/documents/{document_id}/scans/{scan_id}/pack-mode", response_model=ScanResponse)
async def change_tsd_pack_mode(document_id: UUID, scan_id: UUID, body: PackModeRequest,
                               device: TsdDevice = Depends(get_tsd_device), db: AsyncSession = Depends(get_db)):
    await _owned_tsd_document(db, device, document_id)
    scan = (await db.execute(select(Scan).where(Scan.id == scan_id, Scan.document_id == document_id))).scalar_one_or_none()
    if not scan:
        raise HTTPException(404, "Марка не найдена")
    return await set_scan_pack_mode(db, scan, device.user_id, body.unpack)


@router.post("/documents/{document_id}/scans", response_model=ScanResponse)
async def create_tsd_scan(
    document_id: UUID,
    body: TsdScanRequest,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    device_id = device.id
    doc = await _owned_tsd_document(db, device, document_id)
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, device.user_id)
    user, _, _ = await _device_scope(db, device)
    target = None
    if body.moysklad_product_id:
        target = next((p for p in doc.plan or [] if p.get("product_id") == body.moysklad_product_id), None)
        if target is None:
            raise HTTPException(400, "Выбранная позиция отсутствует в плане отгрузки. Обновите список позиций.")
    code = normalize_sscc(body.code)
    if is_sscc(code):
        if target:
            raise HTTPException(400, "Для короба включите автоматический выбор позиции. Ручная привязка доступна для отдельных марок.")
        cz = await _resolve_cz_for_boxes(user, db, doc.id)
        responses = await _create_box_scans_core(
            db, doc.id, _plan_gtins(doc.plan), code, False, device.user_id, cz, device_id=device.id
        )
        return responses[0]
    unmarked, marked = _classify_barcode(doc.plan, code)
    if marked is not None:
        raise HTTPException(400, "Это маркированный товар — отсканируйте Data Matrix")
    if unmarked is not None:
        if target and target.get("product_id") != unmarked.get("product_id"):
            raise HTTPException(400, "Штрихкод относится к другой позиции. Включите автоматический выбор или выберите нужный товар.")
        scan, duplicate = await _create_or_increment_barcode_scan(db, doc.id, code, unmarked, device_id=device.id)
    else:
        if target and target.get("marked") is False:
            raise HTTPException(400, "Выбрана немаркированная позиция. Сканируйте её обычный штрихкод.")
        scan, duplicate = await _create_scan_record(
            db, doc.id, code, device.user_id, moysklad_product_id=body.moysklad_product_id, device_id=device.id
        )
    response = ScanResponse.model_validate(scan)
    response.duplicate = duplicate
    logger.info(
        "tsd.scan.created", device_id=str(device_id), document_id=str(document_id), scan_id=str(scan.id)
    )
    return response


@router.delete("/documents/{document_id}/scans/last", response_model=ScanResponse)
async def delete_last_tsd_scan(
    document_id: UUID,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    doc = await _owned_tsd_document(db, device, document_id)
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, device.user_id)
    action_and_scan = (
        await db.execute(
            select(TsdScanAction, Scan).join(Scan, Scan.id == TsdScanAction.scan_id).where(
                TsdScanAction.document_id == doc.id, TsdScanAction.device_id == device.id,
                TsdScanAction.undone_at.is_(None), TsdScanAction.scan_id.is_not(None), Scan.document_id == doc.id,
            ).order_by(TsdScanAction.created_at.desc(), TsdScanAction.id.desc()).limit(1).with_for_update()
        )
    ).first()
    if not action_and_scan:
        raise HTTPException(404, "У этого ТСД нет сканов для отмены. Старые марки можно удалить выбором в списке.")
    action, scan = action_and_scan
    action.undone_at = datetime.now(timezone.utc)
    decrease_quantity = scan.is_barcode and (scan.box_quantity or 1) > 1
    if decrease_quantity:
        scan.box_quantity -= 1
    response = ScanResponse.model_validate(scan)
    if not decrease_quantity:
        await db.delete(scan)
    await db.commit()
    if decrease_quantity:
        await publish_scan(device.user_id, scan)
    else:
        await publish_removed(device.user_id, doc.id, scan.id)
    logger.info(
        "tsd.scan.undone", device_id=str(device.id), document_id=str(doc.id), scan_id=str(scan.id)
    )
    return response


@router.delete("/documents/{document_id}/scans/{scan_id}", response_model=ScanResponse)
async def delete_tsd_scan(
    document_id: UUID,
    scan_id: UUID,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    await _owned_tsd_document(db, device, document_id)
    from app.services.document_guard import editable_document
    doc = await editable_document(db, document_id, device.user_id)
    scan = (await db.execute(select(Scan).where(
        Scan.id == scan_id, Scan.document_id == doc.id,
    ))).scalar_one_or_none()
    if not scan:
        raise HTTPException(404, "Марка не найдена в этой отгрузке")
    response = ScanResponse.model_validate(scan)
    await db.delete(scan)
    await db.commit()
    await publish_removed(device.user_id, doc.id, scan.id)
    logger.info(
        "tsd.scan.deleted", device_id=str(device.id), document_id=str(doc.id), scan_id=str(scan.id)
    )
    return response


@router.post("/documents/{document_id}/complete")
async def complete_tsd_session(
    document_id: UUID,
    device: TsdDevice = Depends(get_tsd_device),
    db: AsyncSession = Depends(get_db),
):
    doc = await _owned_tsd_document(db, device, document_id)
    session = (
        await db.execute(
            select(TsdDocumentSession).where(
                TsdDocumentSession.device_id == device.id,
                TsdDocumentSession.document_id == doc.id,
                TsdDocumentSession.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(409, "Сессия сборки уже завершена")
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "tsd.session.completed", device_id=str(device.id), document_id=str(doc.id)
    )
    return {"status": "completed", "document_id": str(doc.id)}


@router.get("/devices", response_model=list[TsdDeviceRow])
async def list_devices(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(TsdDevice, Workplace)
            .join(Workplace, Workplace.id == TsdDevice.workplace_id)
            .where(TsdDevice.user_id == current_user.id, TsdDevice.is_active.is_(True))
            .order_by(TsdDevice.created_at.desc())
        )
    ).all()
    return [
        TsdDeviceRow(
            id=d.id, name=d.name, workplace_name=w.name, is_active=d.is_active,
            last_seen_at=d.last_seen_at, created_at=d.created_at,
        ) for d, w in rows
    ]


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(
    device_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    device = (
        await db.execute(select(TsdDevice).where(
            TsdDevice.id == device_id, TsdDevice.user_id == current_user.id
        ))
    ).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "ТСД не найден")
    device.is_active = False
    device.revoked_at = datetime.now(timezone.utc)
    await db.execute(update(TsdDocumentSession).where(
        TsdDocumentSession.device_id == device.id,
        TsdDocumentSession.status == "active",
    ).values(status="revoked", completed_at=datetime.now(timezone.utc)))
    await db.commit()
    await publish_event(current_user.id, {"type": "tsd_device_revoked", "device_id": str(device.id)})
