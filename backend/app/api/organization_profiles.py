from typing import Optional, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.deps import get_active_organization_profile
from app.services.customer_order_filters import CustomerOrderFilter, CustomerOrderFilterList, profile_order_filters
from app.core.security import decrypt_token
from app.db.models import Integration, OrganizationProfile, User, Workplace
from app.db.session import get_db
from app.services.moysklad import MoySkladService

router = APIRouter(prefix="/organization-profiles", tags=["organization-profiles"])


class WorkplaceResponse(BaseModel):
    id: UUID
    organization_profile_id: UUID
    name: str
    scan_mode: Literal['com', 'tsd'] = 'com'
    store_ids: list[str] = Field(default_factory=list)
    is_default: bool
    is_active: bool
    model_config = {"from_attributes": True}


class ProfileResponse(BaseModel):
    id: UUID
    moysklad_organization_id: Optional[str]
    name: str
    is_default: bool
    cz_inn: Optional[str]
    has_cz: bool
    inventory_store_ids: list[str] = Field(default_factory=list)
    workplaces: list[WorkplaceResponse] = Field(default_factory=list)


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    is_default: Optional[bool] = None
    inventory_store_ids: Optional[list[str]] = None


class WorkplaceRequest(BaseModel):
    organization_profile_id: UUID
    name: str = Field(min_length=1, max_length=255)
    store_ids: list[str] = Field(default_factory=list)
    is_default: bool = False
    scan_mode: Literal['com', 'tsd'] = 'com'


class WorkplaceModeRequest(BaseModel):
    scan_mode: Literal['com', 'tsd']


def _profile_response(profile: OrganizationProfile, workplaces: list[Workplace]) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id,
        moysklad_organization_id=profile.moysklad_organization_id,
        name=profile.name,
        is_default=profile.is_default,
        cz_inn=profile.cz_inn,
        has_cz=bool(profile.cz_token),
        inventory_store_ids=list(profile.inventory_store_ids or []),
        workplaces=[WorkplaceResponse.model_validate(w) for w in workplaces],
    )


async def _list(db: AsyncSession, user_id) -> list[ProfileResponse]:
    profiles = (
        await db.execute(
            select(OrganizationProfile)
            .where(OrganizationProfile.user_id == user_id)
            .order_by(OrganizationProfile.is_default.desc(), OrganizationProfile.name)
        )
    ).scalars().all()
    workplaces = (
        await db.execute(select(Workplace).where(Workplace.user_id == user_id))
    ).scalars().all()
    by_profile: dict[UUID, list[Workplace]] = {}
    for workplace in workplaces:
        by_profile.setdefault(workplace.organization_profile_id, []).append(workplace)
    return [_profile_response(p, by_profile.get(p.id, [])) for p in profiles]


@router.get("/", response_model=list[ProfileResponse])
async def list_profiles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _list(db, current_user.id)


@router.get("/order-filters", response_model=list[CustomerOrderFilter])
async def get_order_filters(profile: OrganizationProfile = Depends(get_active_organization_profile)):
    return profile_order_filters(profile)


class ShipmentStateRequest(BaseModel):
    state_id: UUID | None = None


@router.get('/shipment-status-settings')
async def shipment_status_settings(current_user: User = Depends(get_current_user),
                                   profile: OrganizationProfile = Depends(get_active_organization_profile),
                                   db: AsyncSession = Depends(get_db)):
    from app.api.tsd import _ms_for_user
    ms = await _ms_for_user(db, current_user.id)
    try:
        states = await ms.get_shipment_states()
    except Exception as exc:
        raise HTTPException(502, 'Не удалось загрузить статусы отгрузок из МойСклада. Повторите попытку.') from exc
    return {'state_id': profile.shipment_sent_state_id, 'states': states}


@router.put('/shipment-status-settings')
async def save_shipment_status(body: ShipmentStateRequest, current_user: User = Depends(get_current_user),
                               profile: OrganizationProfile = Depends(get_active_organization_profile),
                               db: AsyncSession = Depends(get_db)):
    if body.state_id:
        available = await shipment_status_settings(current_user, profile, db)
        if str(body.state_id) not in {v['id'] for v in available['states']}:
            raise HTTPException(400, 'Статус отгрузки недоступен. Обновите список статусов.')
    profile.shipment_sent_state_id = body.state_id
    await db.commit()
    return {'state_id': body.state_id}


@router.put("/order-filters", response_model=list[CustomerOrderFilter])
async def save_order_filters(body: CustomerOrderFilterList,
    profile: OrganizationProfile = Depends(get_active_organization_profile), db: AsyncSession = Depends(get_db)):
    profile.customer_order_filters = [item.model_dump(mode="json") for item in body.filters]
    await db.commit()
    return profile.customer_order_filters


@router.get("/order-filter-options")
async def get_order_filter_options(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    integration = (await db.execute(select(Integration).where(Integration.user_id == current_user.id))).scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(400, "МойСклад не подключён")
    try:
        return await MoySkladService(decrypt_token(integration.moysklad_token)).get_customer_order_filter_options()
    except Exception as exc:
        raise HTTPException(502, "Не удалось загрузить проекты, статусы заказов и поле «Где продажа» из МойСклада. Повторите попытку.") from exc


@router.post("/sync", response_model=list[ProfileResponse])
async def sync_profiles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = (
        await db.execute(select(Integration).where(Integration.user_id == current_user.id))
    ).scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(400, "МойСклад не подключён")
    try:
        organizations = await MoySkladService(
            decrypt_token(integration.moysklad_token)
        ).get_organizations()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Не удалось загрузить юрлица из МойСклад. Проверьте права решения "
                "на просмотр юрлиц и переустановите его после обновления дескриптора."
            ),
        ) from exc
    existing = (
        await db.execute(
            select(OrganizationProfile).where(OrganizationProfile.user_id == current_user.id)
        )
    ).scalars().all()
    by_ms_id = {p.moysklad_organization_id: p for p in existing if p.moysklad_organization_id}
    unbound_default = next((p for p in existing if p.is_default and not p.moysklad_organization_id), None)
    for index, item in enumerate(organizations):
        profile = by_ms_id.get(item["id"])
        if profile is None and index == 0 and unbound_default is not None:
            profile = unbound_default
            profile.moysklad_organization_id = item["id"]
        if profile is None:
            profile = OrganizationProfile(
                user_id=current_user.id,
                moysklad_organization_id=item["id"],
                name=item["name"],
                is_default=not existing and index == 0,
            )
            db.add(profile)
        else:
            profile.name = item["name"]
    await db.commit()
    return await _list(db, current_user.id)


@router.put("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: UUID,
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = (
        await db.execute(
            select(OrganizationProfile).where(
                OrganizationProfile.id == profile_id,
                OrganizationProfile.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Профиль юрлица не найден")
    if body.name is not None:
        profile.name = body.name.strip()
    if body.inventory_store_ids is not None:
        profile.inventory_store_ids = list(dict.fromkeys(body.inventory_store_ids))
    if body.is_default:
        await db.execute(
            update(OrganizationProfile)
            .where(OrganizationProfile.user_id == current_user.id)
            .values(is_default=False)
        )
        profile.is_default = True
    await db.commit()
    await db.refresh(profile)
    workplaces = (
        await db.execute(select(Workplace).where(Workplace.organization_profile_id == profile.id))
    ).scalars().all()
    return _profile_response(profile, list(workplaces))


@router.post("/workplaces", response_model=WorkplaceResponse)
async def create_workplace(
    body: WorkplaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = (
        await db.execute(
            select(OrganizationProfile).where(
                OrganizationProfile.id == body.organization_profile_id,
                OrganizationProfile.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Профиль юрлица не найден")
    if body.is_default:
        await db.execute(
            update(Workplace)
            .where(Workplace.organization_profile_id == profile.id)
            .values(is_default=False)
        )
    workplace = Workplace(
        user_id=current_user.id,
        organization_profile_id=profile.id,
        name=body.name.strip(),
        scan_mode=body.scan_mode,
        store_ids=list(dict.fromkeys(body.store_ids)),
        is_default=body.is_default,
    )
    db.add(workplace)
    await db.commit()
    await db.refresh(workplace)
    return WorkplaceResponse.model_validate(workplace)


@router.patch("/workplaces/{workplace_id}/scan-mode", response_model=WorkplaceResponse)
async def update_workplace_mode(workplace_id: UUID, body: WorkplaceModeRequest,
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workplace = (await db.execute(select(Workplace).where(
        Workplace.id == workplace_id, Workplace.user_id == current_user.id,
        Workplace.is_active.is_(True)))).scalar_one_or_none()
    if workplace is None:
        raise HTTPException(404, "Рабочее место не найдено")
    workplace.scan_mode = body.scan_mode
    await db.commit()
    await db.refresh(workplace)
    return WorkplaceResponse.model_validate(workplace)
