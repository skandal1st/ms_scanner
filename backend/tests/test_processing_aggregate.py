from dataclasses import dataclass

import pytest

from app.worker.tasks import (
    _build_moysklad_scans_data,
    _expand_aggregate_for_processing,
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

    @property
    def is_aggregate(self):
        return bool(self.children)


@pytest.mark.asyncio
async def test_sscc_goes_directly_to_aggregated_list():
    cz = FakeCz()
    sscc = "00946603212000743418"

    children, product_name = await _expand_aggregate_for_processing(cz, sscc)

    assert len(children) == 2
    assert product_name is None
    assert cz.unpack_calls == [sscc]
    assert cz.info_calls == []


@pytest.mark.asyncio
async def test_non_sscc_aggregate_uses_code_info():
    cz = FakeCz()
    code = "02046603212058583710"

    children, product_name = await _expand_aggregate_for_processing(cz, code)

    assert children == ["010466032120585821UNIT003"]
    assert product_name == "Тестовый товар"
    assert cz.unpack_calls == []
    assert cz.info_calls == [code]


@dataclass
class FakeScan:
    code: str
    gtin: str
    moysklad_product_id: str | None
    is_box: bool
    is_barcode: bool
    box_quantity: int | None
    child_codes: list[str] | None


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
