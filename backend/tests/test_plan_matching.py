from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

from app.api import scans, documents
from app.services.moysklad import MoySkladService
from app.services.plan_matching import unique_plan_product
from tests.test_release_processing import Result


async def test_plan_preserves_all_unit_barcodes_and_pack_aliases():
    ms = MoySkladService("fake")
    ms._load_positions_rows = AsyncMock(return_value=[{"quantity": 2, "assortment": {
        "id": "monster", "name": "МОНСТР", "trackingType": "TOBACCO",
        "barcodes": [{"ean13": "2000000091327"}, {"gtin": "04620543080527"}],
        "packs": [{"barcodes": [{"gtin": "04680887550469"}]}],
    }}])
    plan = await ms.build_plan("demand", "doc")
    assert plan[0]["gtins"] == ["02000000091327", "04620543080527"]
    assert unique_plan_product(plan, "4620543080527")["product_id"] == "monster"
    assert unique_plan_product(plan, "04680887550469")["product_id"] == "monster"
    serialized = documents.PlanItem(**plan[0]).model_dump()
    assert serialized["gtins"] == plan[0]["gtins"]
    assert serialized["pack_gtins"] == plan[0]["pack_gtins"]


def test_ambiguous_gtin_never_selects_arbitrary_product():
    plan = [{"gtin": "04620164405358", "product_id": "true"},
            {"gtin": "04620164405358", "product_id": "hard"}]
    assert unique_plan_product(plan, "04620164405358") is None
    assert unique_plan_product(plan, "04620543080503") is None


async def test_scan_prefers_exact_plan_alias_over_stale_cache(monkeypatch):
    user_id, doc_id = uuid4(), uuid4()
    doc = NS(kind="demand", plan=[{"gtin": "02000000028163", "gtins": ["04620164405358"],
                                  "product_id": "true", "product_name": "True"}])
    monkeypatch.setattr(scans, "editable_document", AsyncMock(return_value=doc))
    cached = AsyncMock(return_value=("hard", "True Hard"))
    monkeypatch.setattr(scans, "get_gtin_product", cached)
    db = NS(execute=AsyncMock(return_value=Result(None)), add=lambda value: None,
            commit=AsyncMock(), refresh=AsyncMock())
    scan, duplicate = await scans._create_scan_record(
        db, doc_id, "010462016440535821TEST000000001\x1d93TEST", user_id)
    assert not duplicate
    assert scan.moysklad_product_id == "true"
    assert scan.product_name == "True"
    cached.assert_not_awaited()


async def test_manual_position_overrides_gtin_without_changing_raw_mark(monkeypatch):
    doc = NS(kind="demand", plan=[
        {"gtin": "04620164405358", "product_id": "true", "product_name": "True"},
        {"gtin": "04620543080503", "product_id": "chosen", "product_name": "Выбранный товар"},
    ])
    monkeypatch.setattr(scans, "editable_document", AsyncMock(return_value=doc))
    cached = AsyncMock()
    monkeypatch.setattr(scans, "get_gtin_product", cached)
    db = NS(execute=AsyncMock(return_value=Result(None)), add=lambda value: None,
            commit=AsyncMock(), refresh=AsyncMock())
    raw = "010462016440535821TEST000000001\x1d93TEST"
    scan, duplicate = await scans._create_scan_record(db, uuid4(), raw, uuid4(), moysklad_product_id="chosen")
    assert not duplicate and scan.moysklad_product_id == "chosen"
    assert scan.product_name == "Выбранный товар"
    assert scan.gtin == "04620164405358" and scan.code == raw
    cached.assert_not_awaited()
