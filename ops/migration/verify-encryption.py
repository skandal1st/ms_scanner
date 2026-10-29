"""Run inside the backend: verify restored tokens without exposing any values."""
import asyncio

from sqlalchemy import select

from app.core.security import decrypt_token
from app.db.models import Integration, OrganizationProfile
from app.db.session import AsyncSessionLocal, engine


async def main():
    checked = 0
    async with AsyncSessionLocal() as session:
        fields = [Integration.moysklad_token, Integration.cz_token,
                  Integration.saby_password, Integration.saby_app_secret,
                  Integration.saby_secret_key]
        for row in (await session.execute(select(*fields))).all():
            for value in row:
                if value:
                    assert decrypt_token(value)
                    checked += 1
        for value in (await session.execute(select(OrganizationProfile.cz_token))).scalars():
            if value:
                assert decrypt_token(value)
                checked += 1
    await engine.dispose()
    print(f'Restored encrypted values successfully verified: {checked}')


asyncio.run(main())
