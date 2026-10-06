from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from jose import JWTError
from uuid import UUID

from app.db.session import get_db
from app.db.models import User, Integration, OrganizationProfile, Document
from app.core.security import decode_token

bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if user_id is None or payload.get("type") != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


async def require_active_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    from app.services.subscriptions import get_subscription
    state = await get_subscription(db, current_user.id)
    if not state.active:
        raise HTTPException(403, state.message)
    return current_user


async def require_full_edition(
    current_user: User = Depends(get_current_user),
) -> User:
    """Гейт для модулей, доступных только в полной версии (Инвентаризация,
    Контроль марок, приёмка из ЭДО). Версия из каталога МойСклад (`ms_lite`)
    их не получает. Проверка на сервере — не полагаемся только на скрытие в UI."""
    if current_user.edition != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот раздел доступен только в полной версии Скандаты.",
        )
    return current_user


async def get_active_organization_profile(
    x_organization_profile: UUID | None = Header(
        default=None, alias="X-Organization-Profile"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationProfile:
    """Профиль юрлица текущего рабочего места.

    Заголовок всегда проверяется по user_id: подставить профиль другого аккаунта
    нельзя. Для старых клиентов лениво создаётся профиль по умолчанию из Integration.
    """
    query = select(OrganizationProfile).where(
        OrganizationProfile.user_id == current_user.id
    )
    if x_organization_profile:
        query = query.where(OrganizationProfile.id == x_organization_profile)
    else:
        query = query.order_by(OrganizationProfile.is_default.desc(), OrganizationProfile.created_at)
    profile = (await db.execute(query)).scalars().first()
    if profile:
        return profile
    if x_organization_profile:
        raise HTTPException(status_code=403, detail="Профиль юрлица недоступен")

    integration = (
        await db.execute(select(Integration).where(Integration.user_id == current_user.id))
    ).scalar_one_or_none()
    profile = OrganizationProfile(
        user_id=current_user.id,
        name="Основное юрлицо",
        is_default=True,
        cz_token=integration.cz_token if integration else None,
        cz_token_expires_at=integration.cz_token_expires_at if integration else None,
        cz_cert_thumbprint=integration.cz_cert_thumbprint if integration else None,
        cz_cert_subject=integration.cz_cert_subject if integration else None,
        cz_auth_method=(integration.cz_auth_method if integration else None) or "mock",
        cz_box_mode_enabled=bool(integration and integration.cz_box_mode_enabled),
        cz_inn=integration.cz_inn if integration else None,
        cz_product_groups=list(integration.cz_product_groups or []) if integration else [],
        inventory_store_ids=list(integration.inventory_store_ids or []) if integration else [],
    )
    db.add(profile)
    await db.flush()
    # Пользователь мог существовать до появления профилей и не иметь Integration.
    # Его старые документы не должны исчезнуть из списка после миграции.
    await db.execute(
        update(Document)
        .where(
            Document.user_id == current_user.id,
            Document.organization_profile_id.is_(None),
        )
        .values(organization_profile_id=profile.id)
    )
    await db.commit()
    await db.refresh(profile)
    return profile
