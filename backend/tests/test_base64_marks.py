import base64
import pytest

from app.services.scan_input import decode_base64_mark
from app.services.mark_search import mark_key, mark_prefixes

RAW = '04603743309261GKgWkbWAAAAdW6a'
ENCODED = 'MDQ2MDM3NDMzMDkyNjFHS2dXa2JXQUFBQWRXNmE='


@pytest.mark.parametrize('code', [RAW, '04603743309261BBBXCJJAAAAem7G',
    '010460374330926121GKgWkbW\x1d93TAIL', '(01)04603743309261(21)GKgWkbW\x1d93TAIL'])
def test_valid_compact_and_gs1_payloads_are_decoded_without_changing_case_or_separators(code):
    encoded = base64.b64encode(code.encode()).decode()
    assert decode_base64_mark(encoded) == code
    assert decode_base64_mark(encoded.rstrip('=')) == code
    assert decode_base64_mark(code) is None


@pytest.mark.parametrize('code', ['', 'ordinary arbitrary text', 'https://example.test/somewhere',
    '04603743309262GKgWkbWAAAAdW6a', '04603743309261', 'MANUAL:123', '00123456789012345678',
    '04603743309261GKgWkbWAAAAdW6a\n', '04603743309261GKgWkbWAAAAdW6a\x00',
    '010460374330926121ABC\nSECOND', 'Пример произвольной строки'])
def test_nonmark_bad_gtin_binary_and_multiline_payloads_are_not_decoded(code):
    assert decode_base64_mark(base64.b64encode(code.encode()).decode()) is None


@pytest.mark.parametrize('code', ['not-base64!', ENCODED + '=', ENCODED[:10] + '\n' + ENCODED[10:],
    base64.b64encode(ENCODED.encode()).decode(), 'M' * 6000, 'MDQ2MDM3NDMzMDkyNjFHS2dXa2JXQUFBQWRXNmF='])
def test_malformed_nested_oversized_and_noncanonical_base64_are_rejected(code):
    assert decode_base64_mark(code) is None


def test_search_identity_is_the_same_but_does_not_lowercase_serials():
    assert mark_key(ENCODED) == mark_key(RAW) == '04603743309261|GKgWkbW'
    assert mark_key(RAW.lower()) != mark_key(RAW)
    assert RAW in mark_prefixes(ENCODED)
    assert ENCODED in mark_prefixes(RAW)
