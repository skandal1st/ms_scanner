"""Извлечение ИНН из субъекта сертификата — нужно для документов вывода из оборота ЧЗ."""

from app.api.integrations import _parse_inn_from_subject


def test_inn_plain():
    assert _parse_inn_from_subject("CN=ООО Ромашка, ИНН=7707083893") == "7707083893"


def test_inn_yul_label():
    assert _parse_inn_from_subject("ИНН ЮЛ=7707083893, O=Ромашка") == "7707083893"


def test_inn_oid_fl_12_digits_strips_leading_zeros():
    # OID ИНН ФЛ (12 цифр), ведущие нули срезаются группой 0*
    assert _parse_inn_from_subject("1.2.643.3.131.1.1=007707083893") == "7707083893"


def test_inn_oid_yul():
    assert _parse_inn_from_subject("1.2.643.100.4=7707083893") == "7707083893"


def test_inn_absent_returns_none():
    assert _parse_inn_from_subject("CN=Ромашка, O=ООО") is None
    assert _parse_inn_from_subject(None) is None
