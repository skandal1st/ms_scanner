"""Russian explanations of CZ statuses; technical values remain in diagnostics."""
import re


STATUS_ERRORS = {
    'INTRODUCED': 'Марка в обороте',
    'IN_CIRCULATION': 'Марка в обороте',
    'WITHDRAWN': 'Марка выведена из оборота',
    'RETIRED': 'Марка выбыла из оборота',
    'WRITTEN_OFF': 'Марка списана',
    'EMITTED': 'Марка выпущена, но ещё не введена в оборот',
    'APPLIED': 'Марка нанесена, но ещё не введена в оборот',
    'DISAGGREGATION': 'Упаковка расформирована',
    'NOT_FOUND': 'Марка не найдена в ЧЗ',
    'CHILD_PROBLEM': 'Есть проблемы с вложенными марками',
}


def cz_status_error(status):
    return STATUS_ERRORS.get(str(status or '').upper(),
        'ЧЗ не подтвердил, что марка находится в обороте. Проверьте сведения о марке в Честном Знаке.')


def localize_cz_error(message):
    if not message:
        return message
    return re.sub(r'Статус(?: в ЧЗ)?:\s*([A-Z][A-Z_0-9]*|None)\b',
                  lambda match: cz_status_error(match.group(1)), message)


def localize_cz_verification(value):
    if not value or not value.get('child_issues'):
        return value
    return {**value, 'child_issues': [{**issue, 'error': localize_cz_error(issue.get('error'))}
                                     for issue in value['child_issues']]}
