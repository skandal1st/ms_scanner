"""Integration regression. Run on a migrated disposable Postgres DB and Redis."""
import os
import time
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi import Response
from jose import jwt

from app.api import auth, moysklad_vendor as vendor
from app.core.config import settings
from app.core.security import decrypt_token
from app.db.session import AsyncSessionLocal
from app.services.ms_installations import PRIMARY_APP_ID, SECONDARY_APP_ID, installation_query, signing_key, vendor_uid
from app.services.subscriptions import get_subscription


def bearer(app):
    now = int(time.time())
    return "Bearer " + jwt.encode({"iat": now, "exp": now + 60, "jti": str(uuid4())}, signing_key(app), algorithm="HS256")


@unittest.skipUnless(os.getenv("TEST_ISOLATED_MS_INSTALLATIONS") == "1", "requires disposable Postgres and Redis")
class InstallationIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_disable_secondary_keeps_primary_launch_token_and_plan(self):
        account, request = str(uuid4()), str(uuid4())
        async with AsyncSessionLocal() as db:
            for app, token, plan in [(PRIMARY_APP_ID, "primary-fixture", settings.MOYSKLAD_TARIFF_UNLIMITED_ID),
                                     (SECONDARY_APP_ID, "secondary-fixture", settings.MOYSKLAD_TARIFF_BASIC_ID)]:
                body = vendor.ActivateRequest(appUid=vendor_uid(app), cause="Install",
                    access=[{"resource": settings.MOYSKLAD_API_BASE, "access_token": token}],
                    subscription={"tariffId": plan})
                await vendor.activate(app, account, body, Response(), db, bearer(app), request)
            first = (await db.scalars(installation_query(account, PRIMARY_APP_ID))).one()
            second = (await db.scalars(installation_query(account, SECONDARY_APP_ID))).one()
            self.assertNotEqual(first.user_id, second.user_id)
            self.assertEqual(decrypt_token(second.moysklad_token), "secondary-fixture")
            self.assertEqual((await get_subscription(db, second.user_id)).plan, "basic")
            initial_token = first.moysklad_token
            for _ in range(2):
                await vendor.deactivate(SECONDARY_APP_ID, account, db, bearer(SECONDARY_APP_ID), request)
            await db.refresh(first)
            await db.refresh(second)
            self.assertIsNone(second.moysklad_token)
            self.assertFalse((await get_subscription(db, second.user_id)).active)
            self.assertEqual(first.moysklad_token, initial_token)
            self.assertEqual(decrypt_token(first.moysklad_token), "primary-fixture")
            plan = await get_subscription(db, first.user_id)
            self.assertTrue(plan.active)
            self.assertEqual(plan.plan, "unlimited")
            result = await vendor.get_status(PRIMARY_APP_ID, account, db, bearer(PRIMARY_APP_ID))
            self.assertEqual(result["status"], "Activated")
            import httpx
            response = httpx.Response(200, json={"accountId": account, "name": "test"})
            with patch.object(auth.httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                launch = await auth.moysklad_launch(auth.MsLaunchRequest(contextKey="fixture", appId=PRIMARY_APP_ID), db)
            self.assertEqual(auth.decode_token(launch.access_token)["sub"], str(first.user_id))

