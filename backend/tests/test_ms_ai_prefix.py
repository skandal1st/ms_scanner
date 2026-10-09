import pytest
from app.services.chestnyznak import cis_string_for_moysklad_api
from app.services.moysklad import MoySkladService

GTIN = '04640195516144'

@pytest.mark.parametrize('prefix', ['(01)' + GTIN + '(21)', '(01)' + GTIN + '21', '01' + GTIN + '(21)', '01' + GTIN + '21'])
@pytest.mark.parametrize('serial', ['-msfq+j', '(12)+ab', 'ab(21)+'])
def test_ms_normalizes_only_prefix_ai_brackets(prefix, serial):
    code = prefix + serial
    expected = '01' + GTIN + '21' + serial
    assert cis_string_for_moysklad_api(code, 'OTP') == expected
    assert MoySkladService('test')._tracking_code_entry({'code': code}, 'OTP') == {'cis': expected, 'type': 'trackingcode'}
    assert MoySkladService._cis_dedup_key(code) == MoySkladService._cis_dedup_key(expected)


def test_ms_bracketed_scan_preserves_serial_and_removes_crypto_at_gs():
    raw = '(01)' + GTIN + '(21)-msfq+j\x1d93ABCD'
    assert cis_string_for_moysklad_api(raw, 'OTP') == '01' + GTIN + '21-msfq+j'
