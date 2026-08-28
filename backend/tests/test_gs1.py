"""Парсинг GS1-кодов маркировки и нормализация GTIN — самая каверзная логика скана.

Регрессии здесь ломают приёмку/отгрузку молча (коды не матчатся с планом,
уходят битыми в МС), поэтому покрываем формат-разбор без ЧЗ.
"""

from app.services.chestnyznak import (
    parse_gs1_km_gtin_serial,
    extract_gtin,
    is_sscc,
    normalize_gtin_key,
    verify_code_local_gs1,
)


def _gtin14_check_digit(first13: str) -> str:
    """Контрольная цифра GTIN-14 по стандартному алгоритму GS1 (веса 3/1 справа)."""
    s = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(first13)))
    return str((10 - (s % 10)) % 10)


def valid_gtin14(first13: str = "029000000000") -> str:
    first13 = first13.zfill(13)
    return first13 + _gtin14_check_digit(first13)


# ── parse_gs1_km_gtin_serial / extract_gtin ─────────────────────────────────

def test_parse_ai01_ai21_extracts_gtin_and_serial():
    gtin = valid_gtin14()
    code = f"01{gtin}21ABC123"
    g, serial = parse_gs1_km_gtin_serial(code)
    assert g == gtin
    assert serial == "ABC123"


def test_parse_with_gs_separator():
    gtin = valid_gtin14()
    code = f"01{gtin}21ABC\x1d91EE06"
    g, serial = parse_gs1_km_gtin_serial(code)
    assert g == gtin
    # серия обрезается по разделителю GS, крипто-хвост (91...) не попадает
    assert serial == "ABC"


def test_parse_garbage_returns_none():
    assert parse_gs1_km_gtin_serial("hello") == (None, None)
    assert parse_gs1_km_gtin_serial("") == (None, None)


def test_extract_gtin_matches_parse():
    gtin = valid_gtin14()
    code = f"01{gtin}21XYZ"
    assert extract_gtin(code) == gtin


# ── is_sscc ─────────────────────────────────────────────────────────────────

def test_is_sscc_true_for_20_digit_00():
    assert is_sscc("00" + "1" * 18) is True


def test_is_sscc_false_for_wrong_len_or_prefix():
    assert is_sscc("01" + "1" * 18) is False   # не 00
    assert is_sscc("00" + "1" * 17) is False    # 19 знаков
    assert is_sscc("00abc0000000000000") is False  # не только цифры


# ── normalize_gtin_key ──────────────────────────────────────────────────────

def test_normalize_gtin_pads_13_to_14():
    assert normalize_gtin_key("4607177930183") == "04607177930183"


def test_normalize_gtin_trims_to_last_14():
    assert normalize_gtin_key("0104607177930183") == "04607177930183"


def test_normalize_gtin_strips_non_digits():
    assert normalize_gtin_key(" 0460-7177-930183 ") == "04607177930183"


def test_normalize_gtin_none_and_empty():
    assert normalize_gtin_key(None) is None
    assert normalize_gtin_key("   ") is None


# ── verify_code_local_gs1 (формат + контрольная сумма) ──────────────────────

def test_verify_local_valid_code():
    gtin = valid_gtin14()
    res = verify_code_local_gs1(f"01{gtin}21SER123")
    assert res.valid is True
    assert res.gtin == gtin
    assert res.serial == "SER123"


def test_verify_local_bad_checksum():
    # берём валидный GTIN и портим контрольную цифру
    good = valid_gtin14()
    bad_last = str((int(good[-1]) + 1) % 10)
    bad = good[:-1] + bad_last
    res = verify_code_local_gs1(f"01{bad}21SER123")
    assert res.valid is False
    assert res.status == "BAD_GTIN"


def test_verify_local_bad_format():
    res = verify_code_local_gs1("not-a-code")
    assert res.valid is False
    assert res.status == "BAD_FORMAT"


def test_verify_local_missing_serial():
    gtin = valid_gtin14()
    res = verify_code_local_gs1(f"01{gtin}")
    assert res.valid is False
    assert res.status == "NO_SERIAL"
