"""Confirm ambiguous UPD tobacco identifiers before writing tracking codes to MS."""
import re
import time

import httpx

from app.core.diagnostics import response_body
from app.core.logging import logger
from app.services.cz_logger import log_cz_request


def needs_cis_confirmation(code: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{14}[!-~]{7}AAAA", code))


async def confirm_document_cis(rows: list[dict], cz) -> list[dict]:
    """Use only the matching UNIT CIS returned by CZ; preserve original rows.

    AAAA can also belong to a legitimate serial. Never trim it without an
    official response. requestedCis binds each answer to its original input.
    """
    pending = dict.fromkeys(
        row['code'] for row in rows
        if not row.get('is_barcode') and not row.get('is_box')
        and needs_cis_confirmation(row['code'])
    )
    if not pending:
        return rows
    if cz.mock or not cz.token:
        raise ValueError('Для подтверждения кодов с хвостом AAAA войдите в Честный Знак по УКЭП и повторите отправку.')

    resolved = {}
    url = f'{cz.base_url}/api/v3/true-api/cises/info'
    headers = {'Authorization': f'Bearer {cz.token}'}
    async with httpx.AsyncClient(timeout=15) as client:
        for pg in cz.product_groups:
            if pg not in {'tobacco', 'otp', 'ncp'}:
                continue
            codes = list(pending)
            for offset in range(0, len(codes), 100):
                batch = codes[offset:offset + 100]
                started = time.monotonic()
                try:
                    response = await client.post(url, params={'pg': pg}, headers=headers, json=batch)
                    body = response_body(response)
                except httpx.HTTPError as exc:
                    await log_cz_request('POST', f'{url}?pg={pg}', batch, None, None,
                                         int((time.monotonic() - started) * 1000),
                                         error=type(exc).__name__)
                    continue
                await log_cz_request('POST', f'{url}?pg={pg}', batch, response.status_code,
                                     body, int((time.monotonic() - started) * 1000))
                if response.status_code != 200 or not isinstance(body, list):
                    continue
                answers = {}
                duplicates = set()
                for entry in body:
                    if not isinstance(entry, dict):
                        continue
                    ci = entry.get('cisInfo')
                    if not isinstance(ci, dict):
                        continue
                    original = ci.get('requestedCis')
                    if not isinstance(original, str) or original not in batch:
                        continue
                    if original in answers:
                        duplicates.add(original)
                    answers[original] = (entry, ci)
                for original, (entry, ci) in answers.items():
                    if original in duplicates or entry.get('errorCode') or ci.get('errorCode'):
                        continue
                    canonical = ci.get('cis')
                    if (canonical != original[:21]
                            or ci.get('status') != 'INTRODUCED'
                            or ci.get('markWithdraw')
                            or ci.get('generalPackageType') != 'UNIT'
                            or ci.get('child')):
                        continue
                    resolved[original] = canonical
                    pending.pop(original, None)
            if not pending:
                break
    if pending:
        logger.warning('process_document.cis_unconfirmed', count=len(pending))
        raise ValueError(f'Честный Знак не подтвердил КИ для {len(pending)} кодов с хвостом AAAA. Отправка остановлена до записи марок в МойСклад. Проверьте коды и подключение к ЧЗ и повторите отправку.')
    logger.info('process_document.cis_confirmed', count=len(resolved),
                examples=[{'original': raw, 'sent': cis} for raw, cis in list(resolved.items())[:20]])
    return [{**row, 'code': resolved.get(row['code'], row['code'])} for row in rows]
