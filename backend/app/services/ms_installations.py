from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Integration


PRIMARY_APP_ID = "8fa85fbe-eae4-4445-a431-212f735ee9e5"
SECONDARY_APP_ID = "868ba481-8f12-4611-ad37-8476a18b0b5e"


def signing_key(app_id: str) -> str:
    if app_id == PRIMARY_APP_ID:
        return settings.MOYSKLAD_VENDOR_SECRET_KEY
    if app_id == SECONDARY_APP_ID:
        # These two versions currently share the vendor signing key. Their
        # JSON API access tokens are issued separately for each installation.
        return settings.MOYSKLAD_SECONDARY_VENDOR_SECRET_KEY or settings.MOYSKLAD_VENDOR_SECRET_KEY
    raise HTTPException(404, "Неизвестная версия решения")


def vendor_uid(app_id: str) -> str:
    if app_id == PRIMARY_APP_ID:
        return settings.MOYSKLAD_APP_UID
    if app_id == SECONDARY_APP_ID:
        return settings.MOYSKLAD_SECONDARY_APP_UID
    raise HTTPException(404, "Неизвестная версия решения")


def installation_query(account_id: str, app_id: str):
    return select(Integration).where(
        Integration.moysklad_account_id == account_id,
        Integration.moysklad_app_id == app_id,
    )
