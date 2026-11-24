import pytest
from app.services.chestnyznak import cis_string_for_moysklad_api, normalize_cis
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


@pytest.mark.parametrize('head', [GTIN, '01' + GTIN + '21'])
@pytest.mark.parametrize('serial', ['lc%C1q6', 'lc%c1q6'])
@pytest.mark.parametrize(('tail', 'confidence'), [
    ('', 'exact'), ('\x1d93ABCD', 'exact'), ('93ABCD', 'reconstructed'),
    ('%C193ABCD', 'reconstructed'), ('%c193ABCD', 'reconstructed'),
])
def test_ms_preserves_printable_percent_sequence_in_serial(head, serial, tail, confidence):
    code = head + serial + tail
    assert normalize_cis(code, 'OTP') == (head + serial, confidence)
    entry = MoySkladService('test')._tracking_code_entry({'code': code}, 'OTP')
    assert entry['cis'] == head + serial


def test_unknown_tracking_type_preserves_ambiguous_percent_sequence():
    code = GTIN + 'lc%C1q6'
    assert normalize_cis(code, None) == (code, 'ambiguous')
