"""Explicit evidence for a CZ check, including every leaf of an aggregate."""
from datetime import datetime, timezone
from app.services.chestnyznak import CisCheck, verify_code_local_gs1
from app.services.chestnyznak import cis_compare_forms_for_ms


def acceptable(check):
    return check.found and not check.uncertain and str(check.status or '').upper() == 'INTRODUCED' and not check.mark_withdraw


def record_ms_errors(scans, reason):
    """Attach only errors naming a unique exact CIS; never guess from a GTIN."""
    matched = []
    candidates = {}
    for scan in scans:
        for raw in [scan.code, *(scan.child_codes or [])]:
            for form in cis_compare_forms_for_ms(raw):
                if len(form) >= 18 and form in reason:
                    candidates.setdefault(form, set()).add(str(scan.id))
    ids = {next(iter(values)) for values in candidates.values() if len(values) == 1}
    for scan in scans:
        if str(scan.id) in ids:
            scan.verification = {**(scan.verification or {}), 'ms_error': reason,
                                 'ms_error_at': datetime.now(timezone.utc).isoformat()}
            scan.error_message = 'МойСклад: ' + reason
            from app.db.models import ScanStatus
            scan.status = ScanStatus.invalid
            matched.append(scan)
    return matched


def evidence(check, signature_inn=None):
    inn = str(check.owner_inn or '').strip()
    reference = str(signature_inn or '').strip()
    owner = 'unknown'
    reason = 'ЧЗ не вернул ИНН владельца'
    if check.verification_source == 'cz_check' and not inn:
        reason = 'ЧЗ подтвердил статус марки без сведений о владельце'
    if not reference:
        reason = 'Нет ИНН подключённой подписи ЧЗ для сравнения'
    if inn and reference:
        owner = 'match' if inn == reference else 'mismatch'
        reason = 'Владелец совпадает' if owner == 'match' else 'ИНН владельца не совпадает с ИНН подключённой подписи ЧЗ'
    return {'source': check.verification_source, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'owner_result': owner, 'owner_reason': reason, 'owner_reference_inn': reference or None}


async def check_scans(cz, scans, signature_inn=None):
    """Return one result per input; a failed request never becomes a successful check."""
    if cz is None or cz.mock or not cz.token:
        results = {}
        for scan in scans:
            local = verify_code_local_gs1(scan.code)
            check = CisCheck(code=scan.code, found=False, uncertain=True,
                error='Формат проверен, но проверка ЧЗ не выполнена. Войдите в ЧЗ по УКЭП и повторите.',
                verification_source='format')
            check.verification = {'source': 'format', 'format_valid': local.valid,
                'owner_result': 'unknown', 'owner_reason': 'Нужен действующий вход в ЧЗ',
                'checked_at': None}
            results[scan.code] = check
        return results
    inputs = [scan.code for scan in scans if not scan.is_box]
    answers = {c.code: c for c in await cz.check_codes(inputs)} if inputs else {}
    for scan in scans:
        check = answers.get(scan.code)
        if scan.is_box:
            check = CisCheck(code=scan.code, found=True, status='INTRODUCED', package_type='BOX',
                             verification_source='cz_contents')
        if check is None:
            check = CisCheck(code=scan.code, found=False, uncertain=True,
                error='ЧЗ не вернул результат. Повторите проверку.', verification_source='cz_unavailable')
        check.verification = evidence(check, signature_inn)
        if check.uncertain:
            check.verification['checked_at'] = None
        if check.mark_withdraw:
            check.status = 'WITHDRAWN'
            check.error = 'Марка выведена из оборота или заблокирована' + (f': {check.withdraw_reason}' if check.withdraw_reason else '')
        aggregate = bool(scan.child_codes or check.child_count or str(check.package_type or '').upper() in {'GROUP', 'BOX', 'LEVEL1', 'LEVEL2'})
        if acceptable(check) and aggregate:
            try:
                if scan.is_box:
                    children = list(dict.fromkeys(await cz.unpack_box(scan.code)))
                else:
                    info = await cz.get_code_info(scan.code)
                    children = list(dict.fromkeys(info.children)) if info else []
                if not children:
                    raise ValueError('ЧЗ не вернул состав упаковки')
                checks = {c.code: c for c in await cz.check_codes(children)}
                if scan.is_box:
                    owners = {str(c.owner_inn).strip() for c in checks.values() if c.owner_inn}
                    if len(owners) == 1 and all(c in checks and checks[c].owner_inn for c in children):
                        check.owner_inn = next(iter(owners))
                        check.verification = evidence(check, signature_inn)
                issues = []
                unknown = False
                for code in children:
                    child = checks.get(code)
                    if child is None or child.uncertain:
                        unknown = True
                        issues.append({'code': code, 'error': 'Не удалось проверить в ЧЗ'})
                    elif not acceptable(child):
                        issues.append({'code': code, 'error': child.error or ('Выведена из оборота' if child.mark_withdraw else f'Статус в ЧЗ: {child.status}')})
                check.verified_children = children
                check.verification.update(children_total=len(children),
                    children_checked=sum(1 for c in children if c in checks and not checks[c].uncertain),
                    child_issues=issues)
                if issues:
                    check.uncertain = unknown
                    check.status = 'CHILD_PROBLEM'
                    check.error = f'Проблемы у {len(issues)} вложенных марок: ' + '; '.join(f"{i['code']}: {i['error']}" for i in issues[:3])
            except Exception:
                check.uncertain = True
                check.error = 'Не удалось получить и проверить состав упаковки в ЧЗ. Повторите проверку.'
                check.verification['children_checked'] = 0
                check.verification['checked_at'] = None
        answers[scan.code] = check
    return answers
