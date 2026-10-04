from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.api.organization_profiles import WorkplaceModeRequest, WorkplaceRequest, update_workplace_mode

def test_collection_mode_is_explicit_and_old_workplaces_default_to_com():
    assert WorkplaceRequest(organization_profile_id=uuid4(), name="Склад").scan_mode == "com"
    with pytest.raises(ValidationError):
        WorkplaceModeRequest(scan_mode="auto")

async def test_mode_update_is_scoped_to_owner_and_active_workplace():
    user, workplace_id = NS(id=uuid4()), uuid4()
    workplace = NS(id=workplace_id, organization_profile_id=uuid4(),name="Склад",scan_mode="com",store_ids=[],is_default=True,is_active=True)
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda:workplace)),commit=AsyncMock(),refresh=AsyncMock())
    result = await update_workplace_mode(workplace_id,WorkplaceModeRequest(scan_mode="tsd"),user,db)
    assert result.scan_mode == "tsd"
    query = str(db.execute.call_args.args[0])
    assert "workplaces.user_id" in query and "workplaces.is_active IS true" in query
    assert db.execute.call_args.args[0].compile().params["user_id_1"] == user.id
    db.commit.assert_awaited_once()

async def test_foreign_or_inactive_workplace_cannot_be_changed():
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda:None)),commit=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await update_workplace_mode(uuid4(),WorkplaceModeRequest(scan_mode="tsd"),NS(id=uuid4()),db)
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()
