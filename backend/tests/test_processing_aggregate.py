from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from app.db.models import Scan
from app.services.chestnyznak import ChestnyZnakService
from app.worker.tasks import (
    _build_moysklad_scans_data,
    _resolve_receipt_package,
)


class FakeCz:
    def __init__(self):
        self.unpack_calls = []
        self.info_calls = []

    async def unpack_box(self, code):
        self.unpack_calls.append(code)
        return ["010466032120585821UNIT001", "010466032120585821UNIT002"]

    async def get_code_info(self, code):
        self.info_calls.append(code)
        return FakeInfo(
            children=["010466032120585821UNIT003"],
            product_name="Тестовый товар",
        )


@dataclass
class FakeInfo:
    children: list[str]
    product_name: str | None = None
    confirmed_unit: bool = False


@pytest.mark.asyncio
async def test_sscc_goes_directly_to_aggregated_list():
    cz = FakeCz()
    sscc = "00946603212000743418"

    scan = Scan(code=sscc, is_box=True)
    await _resolve_receipt_package(cz, scan)

    assert len(scan.child_codes) == scan.box_quantity == 2
    assert scan.product_name is None and scan.is_box
    assert cz.unpack_calls == [sscc]
    assert cz.info_calls == []


@pytest.mark.asyncio
async def test_non_sscc_aggregate_uses_code_info():
    cz = FakeCz()
    code = "02046603212058583710"

    scan = Scan(code=code, is_box=True)
    await _resolve_receipt_package(cz, scan)

    assert scan.child_codes == ["010466032120585821UNIT003"]
    assert scan.product_name == "Тестовый товар" and scan.is_box
    assert cz.unpack_calls == []
    assert cz.info_calls == [code]


@pytest.mark.parametrize('overrides', [
    {}, {'generalPackageType': 'GROUP'}, {'generalPackageType': None}, {'status': 'RETIRED'},
    {'markWithdraw': True}, {'child': ['unresolved-block']}, {'cis': 'different-mark'},
    {'requestedCis': 'different-request'},
])
async def test_upd_package_field_requires_exact_cz_confirmation_for_single_unit(monkeypatch, overrides):
    from app.services import cz_pg_cache
    from app.services.supply_positions import prepare_supply_rows
    code = '010464049917118621Example'
    gtin = code[2:16]
    scan = Scan(code=code, is_box=True, is_barcode=False, gtin=gtin, moysklad_product_id='product')
    info = {'cis': code, 'requestedCis': code, 'gtin': gtin, 'generalPackageType': 'UNIT',
            'status': 'INTRODUCED', 'child': [], **overrides}
    cz = ChestnyZnakService(token='fixture', mock=False, product_groups=['otp'])
    monkeypatch.setattr(cz_pg_cache, 'get_cached_pg', AsyncMock(return_value=None))
    monkeypatch.setattr(cz_pg_cache, 'set_cached_pg', AsyncMock())
    monkeypatch.setattr(cz, '_read_composition', AsyncMock(
        side_effect=[(200, [{'cisInfo': info}]), (404, {})]))

    await _resolve_receipt_package(cz, scan)
    if overrides:
        assert scan.is_box and not scan.child_codes
        return
    assert scan.package_type == 'UNIT' and not scan.is_box
    assert scan.box_quantity is None and not scan.child_codes
    rows = _build_moysklad_scans_data([scan], 'supply', {})
    line = {'product_id': 'product', 'expected_qty': 1, 'items': [{'codes': [code]}]}
    row, = prepare_supply_rows(rows, [], {'physical_plan': [line]})
    assert (row['code'], row['quantity'], row['upd_line'], row['is_box']) == (code, 1, 0, False)


@dataclass
class FakeScan:
    code: str
    gtin: str
    moysklad_product_id: str | None
    is_box: bool
    is_barcode: bool
    box_quantity: int | None
    child_codes: list[str] | None


def test_gtin_pack_quantity_survives_tsd_progress_and_ms_payload():
    from app.services.moysklad import MoySkladService
    from app.api.tsd import _scan_units
    scan = FakeScan("010466051533035921TEST000000001\x1d93TEST", "04660515330359",
                    "flavor", False, False, 10, None)
    rows = _build_moysklad_scans_data([scan], "demand", {})
    assert _scan_units(scan) == 10
    assert rows[0]["quantity"] == 10 and rows[0]["code"] == scan.code
    assert MoySkladService._scan_units(rows[0]) == 10
    assert MoySkladService("fake")._tracking_code_entry(rows[0], "TOBACCO")["type"] == "trackingcode"


def test_demand_whole_sscc_stays_transport_pack_even_with_known_children():
    scan = FakeScan(
        code="00046402759700334510",
        gtin="04640275973096",
        moysklad_product_id="product-1",
        is_box=True,
        is_barcode=False,
        box_quantity=20,
        child_codes=[
            "010464027597309621UNIT001",
            "010464027597309621UNIT002",
        ],
    )

    rows = _build_moysklad_scans_data([scan], "demand", {})

    assert rows == [
        {
            "code": scan.code,
            "gtin": scan.gtin,
            "product_id": "product-1",
            "is_box": True,
            "is_barcode": False,
            "quantity": 20,
        }
    ]


def test_supply_aggregate_still_expands_to_child_codes():
    scan = FakeScan(
        code="00046402759700334510",
        gtin="04640275973096",
        moysklad_product_id="product-1",
        is_box=True,
        is_barcode=False,
        box_quantity=2,
        child_codes=[
            "010464027597309621UNIT001",
            "010464027597309621UNIT002",
        ],
    )

    rows = _build_moysklad_scans_data([scan], "supply", {})

    assert [row["code"] for row in rows] == scan.child_codes
    assert all(row["is_box"] is False for row in rows)
    assert all(row["quantity"] == 1 for row in rows)
