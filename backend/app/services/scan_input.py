"""Recognize a Base64 envelope only when its payload is a valid marking code."""
import base64
import binascii
import re

from app.services.chestnyznak import verify_code_local_gs1


def decode_base64_mark(raw: str) -> str | None:
    code = (raw or '').strip()
    # Never reinterpret a normal scanner code, including one with a bad GTIN.
    if re.match(r'^(?:\d{14}|\(01\))', code):
        return None
    if not 28 <= len(code) <= 5464 or len(code) % 4 == 1:
        return None
    if not re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', code):
        return None
    try:
        payload = base64.b64decode(code + '=' * (-len(code) % 4), validate=True)
        decoded = payload.decode('ascii')
    except (ValueError, binascii.Error, UnicodeError):
        return None
    # Reject noncanonical encodings and arbitrary binary/text payloads.
    canonical = base64.b64encode(payload).decode('ascii')
    if code not in (canonical, canonical.rstrip('=')):
        return None
    if not all(32 <= ord(char) <= 126 or char == '\x1d' for char in decoded):
        return None
    compact = len(decoded) == 29 and decoded[:14].isdigit() and '\x1d' not in decoded
    gs1 = bool(re.match(r'^(?:01\d{14}21|\(01\)\d{14}\(21\))', decoded))
    if not (compact or gs1) or not verify_code_local_gs1(decoded).valid:
        return None
    return decoded
