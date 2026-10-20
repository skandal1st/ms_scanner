"""Read-only CZ history; observations are never inferred movements or sales."""
import asyncio
from datetime import datetime, timezone
import re
import time

import httpx

from app.core.config import settings
from app.services.chestnyznak import cis_for_cz_info, normalize_km_ai_prefix, is_sscc
from app.services.cz_logger import log_cz_request
from app.services.cz_status import STATUS_ERRORS
from app.services.mark_search import mark_key
from app.services.scan_input import decode_base64_mark

AUTH_ERROR = 'Войдите в Честный Знак в настройках выбранного юрлица и обновите сведения.'
REASONS = {'RETAIL': 'Розничная продажа', 'OWN_USE': 'Для собственных нужд',
           'DESTRUCTION': 'Уничтожение', 'LOSS': 'Утрата', 'DAMAGE': 'Повреждение',
           'EXPIRATION': 'Истечение срока годности', 'EXPORT': 'Экспорт',
           'PRODUCTION_USE': 'Использование в производстве'}


def clean(value, limit=1000):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def identity(raw):
    code = normalize_km_ai_prefix(decode_base64_mark(raw) or raw.strip())
    key = mark_key(code)
    if len(key) > 128 or not (is_sscc(code) or (len(key) > 15 and key[:14].isdigit() and key[14] == '|')):
        raise ValueError('Введите код маркировки, код идентификации или код транспортной упаковки.')
    return code, key


def date_value(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def geography(ci):
    mod = ci.get('ownerMod') if isinstance(ci.get('ownerMod'), dict) else {}
    address = clean(mod.get('address'))
    region = clean(mod.get('regionName'), 200) or clean(mod.get('region'), 200)
    if region:
        return address, region, 'cz'
    # Only explicit subjects in the reported address. Never derive a region from
    # the owner's tax ID, town name or our customer's registered address.
    if address:
        for pattern, suffix in [(r'(?:^|,\s*)обл\.?\s+([^,]+)', ' область'),
                                (r'(?:^|,\s*)([^,]+?\s+(?:область|край|республика|автономный округ))(?=,|$)', '')]:
            match = re.search(pattern, address, re.I)
            if match:
                return address, match.group(1).strip() + suffix, 'address'
        for city in ['Москва', 'Санкт-Петербург', 'Севастополь']:
            if re.match(r'^(?:г\.?\s*)?' + re.escape(city) + r'(?:,|$)', address, re.I):
                return address, city, 'address'
    return address, None, None


def current_info(ci):
    status = clean(ci.get('status'), 100)
    reason = clean(ci.get('withdrawReason'), 100)
    address, region, region_source = geography(ci)
    return {'cis': clean(ci.get('cis')), 'product_name': clean(ci.get('productName')),
            'status': status, 'status_label': STATUS_ERRORS.get(status, 'Статус не распознан' if status else 'Статус не передан'),
            'owner_name': clean(ci.get('ownerName')), 'owner_inn': clean(ci.get('ownerInn'), 12),
            'owner_address': address, 'region': region, 'region_source': region_source,
            'withdrawn': status in {'WITHDRAWN', 'RETIRED', 'WRITTEN_OFF'} or ci.get('markWithdraw') is True,
            'withdraw_reason': reason, 'withdraw_reason_label': REASONS.get(reason, 'Иная причина' if reason else None)}


def history_events(body, key):
    if not isinstance(body, list):
        raise ValueError('ЧЗ вернул историю в неизвестном формате. Повторите обновление.')
    result, seen = [], set()
    for event in body:
        if not isinstance(event, dict) or mark_key(event.get('cis')) != key:
            raise ValueError('ЧЗ вернул историю другого кода или неполные данные. Повторите обновление.')
        item = {'operation_at': date_value(event.get('operationDate')),
                'recorded_at': date_value(event.get('timestamp')),
                'status': clean(event.get('status'), 100),
                'status_label': STATUS_ERRORS.get(event.get('status'), 'Статус не распознан'),
                'owner_inn': clean(event.get('ownerInn'), 12),
                'document_id': clean(event.get('docId')), 'parent': clean(event.get('parent'))}
        signature = tuple(item.values())
        if signature not in seen:
            result.append(item)
            seen.add(signature)
    # Operation and registration times can disagree due to backdated documents.
    # Keep both; order on the actual document date and never fill missing dates.
    return sorted(result, key=lambda e: (e['operation_at'] is None, e['operation_at'] or '', e['recorded_at'] or ''))


async def fetch_history(code, token, groups, user_id, preferred_pg=None):
    """No state-changing CZ methods; at most one small info batch per group."""
    outcome = {'current': None, 'events': None, 'info_error': None, 'history_error': None}
    if not token:
        return {**outcome, 'info_error': AUTH_ERROR, 'history_error': AUTH_ERROR}
    key = mark_key(code)
    groups = list(dict.fromkeys([g for g in [preferred_pg, *groups] if g]))
    groups = groups or settings.cz_product_groups_list
    base = settings.CZ_API_BASE_URL.rstrip('/') + '/api/v3/true-api'
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'Content-Type': 'application/json'}

    async def request(client, url, *, params, payload=None):
        started = time.monotonic()
        response, body, error = None, None, None
        try:
            response = await client.post(url, params=params, json=payload)
            body = response.json()
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        except ValueError:
            error = 'Некорректный JSON в ответе ЧЗ'
        await log_cz_request('POST', url, payload or params, response.status_code if response is not None else None,
                             body, int((time.monotonic()-started)*1000), user_id=str(user_id), error=error)
        if response is not None and response.status_code == 401:
            raise PermissionError(AUTH_ERROR)
        return response.status_code if response is not None else None, body

    try:
        async with asyncio.timeout(65), httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), headers=headers) as client:
            cis = None
            uncertain = False
            for pg in groups[:6]:
                lookup = cis_for_cz_info(code, pg)
                status, body = await request(client, base+'/cises/info', params={'pg': pg}, payload=[lookup])
                if status != 200 or not isinstance(body, list):
                    uncertain = True
                    continue
                matches = [entry['cisInfo'] for entry in body if isinstance(entry, dict)
                           and isinstance(entry.get('cisInfo'), dict) and not entry.get('errorCode')
                           and not entry['cisInfo'].get('errorCode') and entry['cisInfo'].get('cis')
                           and mark_key(entry['cisInfo'].get('requestedCis') or entry['cisInfo']['cis']) == key
                           and mark_key(entry['cisInfo']['cis']) == key]
                if len(matches) == 1:
                    outcome['current'] = current_info(matches[0])
                    cis = matches[0]['cis']
                    break
                if len(matches) > 1:
                    uncertain = True
            if cis is None:
                outcome['info_error'] = ('Не удалось получить сведения ЧЗ. Повторите обновление.' if uncertain
                                         else 'ЧЗ не предоставил сведения по этой марке в доступных товарных группах.')
                # History may still exist for an archived mark or when cises/info
                # is temporarily unavailable. Its canonical identity is known.
                cis = cis_for_cz_info(code, preferred_pg or groups[0])
            status, body = await request(client, base+'/cises/history', params={'cis': cis})
            if status == 200:
                try:
                    outcome['events'] = history_events(body, key)
                except ValueError as exc:
                    outcome['history_error'] = str(exc)
            elif status == 404:
                outcome['history_error'] = 'ЧЗ не предоставил доступные события по этой марке. Это не подтверждает отсутствие движения.'
            elif status == 403:
                outcome['history_error'] = 'Нет доступа к истории этой марки у выбранного юрлица.'
            else:
                outcome['history_error'] = 'Не удалось получить историю ЧЗ. Повторите обновление.'
    except PermissionError:
        outcome['info_error'] = AUTH_ERROR if outcome['current'] is None else None
        outcome['history_error'] = AUTH_ERROR
    except TimeoutError:
        outcome['info_error'] = 'ЧЗ не ответил вовремя. Повторите обновление.' if outcome['current'] is None else None
        outcome['history_error'] = 'ЧЗ не ответил вовремя. История не обновлена.'
    return outcome
