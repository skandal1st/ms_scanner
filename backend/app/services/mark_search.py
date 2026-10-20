"""Case-sensitive mark identity for full scanner codes and UPD identification codes."""
from sqlalchemy import or_
import base64
from app.services.chestnyznak import is_sscc, normalize_km_ai_prefix, parse_gs1_km_gtin_serial
from app.services.scan_input import decode_base64_mark


def mark_key(raw):
    code = normalize_km_ai_prefix(decode_base64_mark(raw) or (raw or '').strip())
    if not is_sscc(code):
        gtin, serial = parse_gs1_km_gtin_serial(code)
        if gtin and serial:
            return gtin + '|' + serial
    return code


def mark_prefixes(raw):
    original = (raw or '').strip()
    code = decode_base64_mark(original) or original
    key = mark_key(code)
    parts = key.split('|', 1)
    if len(parts) != 2 or len(parts[0]) != 14 or not parts[0].isdigit():
        return [code]
    gtin, serial = parts
    envelope = base64.b64encode(code.encode('utf-8')).decode('ascii')
    return list(dict.fromkeys([original, code, envelope, envelope.rstrip('='),
                              gtin + serial, '01' + gtin + '21' + serial, '(01)' + gtin + '(21)' + serial]))


def candidate_condition(column, prefixes):
    # Prefixes only select candidates. mark_key equality below rejects longer
    # serials sharing a prefix, keeping % and _ as literal data, not SQL wildcards.
    if len(prefixes) == 1:
        return column == prefixes[0]
    return or_(*(column.startswith(prefix, autoescape=True) for prefix in prefixes))
