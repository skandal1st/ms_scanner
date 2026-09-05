"""Клиент ЭДО Saby (СБИС) JSON-RPC API для раздела «Контроль марок».

Авторизация: СБИС.Аутентифицировать (POST /auth/service/) → sid, дальше все вызовы с
заголовком X-SBISSessionID на /service/. Сессия кэшируется в Redis (общий лимит 5 сессий
у пользователя, поэтому переиспользуем). На 401 — переавторизация.

Основные методы: СБИС.СписокДокументов (листинг по направлению/типу/датам),
СБИС.ПрочитатьДокумент (детали + вложения). Формат ответов уточняется на реальных данных —
поэтому парсинг документов best-effort, а сырой ответ логируется (saby.raw).

Док: https://saby.ru/help/integration/api/edo
"""
from typing import Any, Optional

import httpx

from app.core.logging import logger

AUTH_URL = "https://online.sbis.ru/auth/service/"
OAUTH_URL = "https://online.sbis.ru/oauth/service/"
SERVICE_URL = "https://online.sbis.ru/service/?srv=1"

# Типы документов ЭДО (параметр «Тип» внутри объекта «Фильтр» у СБИС.СписокДокументов).
# Исходящая реализация/УПД — «Реализация», входящее поступление — «Поступление».
DOC_TYPE_OUTGOING = "Реализация"
DOC_TYPE_INCOMING = "Поступление"

# ВАЖНО: без charset=utf-8 Saby читает тело как windows-1251 и падает на кириллице
# (ошибка -32700). Авторизация — application/json, вызовы сервиса — application/json-rpc
# (иначе «внутренняя ошибка сервера»).
_AUTH_HEADERS = {"Content-Type": "application/json; charset=utf-8"}
_SERVICE_HEADERS = {"Content-Type": "application/json-rpc; charset=utf-8"}


class SabyError(Exception):
    pass


class SabyAuthError(SabyError):
    pass


class SabyClient:
    def __init__(
        self,
        login: Optional[str] = None,
        password: Optional[str] = None,
        account: Optional[str] = None,
        *,
        app_client_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        self.login = login
        self.password = password
        self.account = account
        self.app_client_id = app_client_id
        self.app_secret = app_secret
        self.secret_key = secret_key

    async def authenticate(self) -> tuple[str, str]:
        """Авторизация. Возвращает (имя_заголовка, токен): сервисная (X-SBISAccessToken)
        если заданы ключи приложения, иначе логин/пароль (X-SBISSessionID)."""
        if self.app_client_id:
            return ("X-SBISAccessToken", await self._auth_service())
        return ("X-SBISSessionID", await self._auth_login())

    async def _auth_login(self) -> str:
        """СБИС.Аутентифицировать → sid."""
        param: dict[str, Any] = {"Логин": self.login, "Пароль": self.password}
        if self.account:
            param["НомерАккаунта"] = self.account
        body = {
            "jsonrpc": "2.0",
            "method": "СБИС.Аутентифицировать",
            "params": {"Параметр": param},
            "protocol": 2,
            "id": 0,
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(AUTH_URL, json=body, headers=_AUTH_HEADERS)
        try:
            data = r.json()
        except Exception:
            raise SabyAuthError(f"Saby авторизация: не JSON (HTTP {r.status_code})")
        if isinstance(data, dict) and data.get("error"):
            msg = (data["error"] or {}).get("message") or str(data["error"])
            raise SabyAuthError(f"Saby авторизация: {msg}")
        sid = data.get("result") if isinstance(data, dict) else None
        if not sid or not isinstance(sid, str):
            raise SabyAuthError("Saby авторизация: пустой идентификатор сессии")
        logger.info("saby.auth.ok", login=self.login)
        return sid

    async def _auth_service(self) -> str:
        """Сервисная авторизация: POST /oauth/service/ → access_token."""
        body: dict[str, Any] = {"app_client_id": self.app_client_id}
        if self.app_secret:
            body["app_secret"] = self.app_secret
        if self.secret_key:
            body["secret_key"] = self.secret_key
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(OAUTH_URL, json=body, headers=_AUTH_HEADERS)
        try:
            data = r.json()
        except Exception:
            raise SabyAuthError(f"Saby сервисная авторизация: не JSON (HTTP {r.status_code}) {r.text[:200]}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = (err or {}).get("message") if isinstance(err, dict) else err
            det = (err or {}).get("details") if isinstance(err, dict) else None
            logger.warning("saby.oauth.error", error=err)
            raise SabyAuthError(f"Saby сервисная авторизация: {msg or ''} {('· ' + str(det)) if det else ''}".strip())
        token = None
        if isinstance(data, dict):
            token = data.get("token") or data.get("access_token")
        elif isinstance(data, str):
            token = data
        if not token:
            raise SabyAuthError(f"Saby сервисная авторизация: пустой токен (ответ: {str(data)[:200]})")
        logger.info("saby.oauth.ok", app_client_id=self.app_client_id)
        return token

    async def call(self, method: str, params: dict, auth: tuple[str, str], protocol: Optional[int] = None) -> Any:
        """Вызов метода сервиса. auth = (имя_заголовка, токен). SabyAuthError на 401.

        protocol — версия метода (СБИС.СписокИзменений требует 3). БЕЗ поля "jsonrpc":
        оно тоже задаёт версию (jsonrpc "2.0" → «метод/2»). Для СписокИзменений нужен protocol=3.
        """
        body = {"method": method, "params": params, "id": 0}
        if protocol is not None:
            body["protocol"] = protocol
        # Диагностика: точное тело запроса (уточняем структуру параметров на реальных данных).
        logger.info("saby.request", method=method, params=params)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                SERVICE_URL,
                json=body,
                headers={**_SERVICE_HEADERS, auth[0]: auth[1]},
            )
        if r.status_code == 401:
            raise SabyAuthError("Сессия Saby истекла")
        try:
            data = r.json()
        except Exception:
            logger.warning("saby.non_json", method=method, status=r.status_code, body=r.text[:500])
            raise SabyError(f"{method}: не JSON (HTTP {r.status_code}) {r.text[:200]}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"] or {}
            # Полная ошибка (с details/data) — в лог и в текст исключения для уточнения.
            logger.warning("saby.error", method=method, error=err)
            # 401-подобная ошибка внутри тела — тоже переавторизация
            if str(err.get("code")) in ("401", "-32001") or "session" in str(err.get("message", "")).lower():
                raise SabyAuthError(str(err.get("message") or err))
            detail = err.get("details") or err.get("message") or str(err)
            raise SabyError(f"{method}: {err.get('message') or ''} {('· ' + str(detail)) if detail else ''}".strip())
        return data.get("result") if isinstance(data, dict) else data

    async def changes_page(
        self,
        auth: tuple[str, str],
        *,
        date_from: str,
        date_to: Optional[str] = None,
        event_id: Optional[str] = None,
        doc_id: Optional[str] = None,
        with_extension: bool = True,
    ) -> Any:
        """Одна страница СБИС.СписокИзменений (лента событий ДО). Возвращает сырой result
        `{Документ:[...], Навигация:{ЕстьЕще}}`.

        КУРСОР (проверено на проде): период — `ДатаВремяС`/`ДатаВремяПо` («ДД.ММ.ГГГГ ЧЧ.ММ.СС»);
        объект `Навигация` НЕ слать (ломает → /2, размер стр. фикс. 25). Для след. страницы —
        `ИдентификаторСобытия`+`ИдентификаторДокумента`+`ДатаВремяС` последнего события.
        `protocol=3`. `ПолныйСертификатЭП:Нет` ускоряет; `ДопПоля:Расширение` → СостояниеМарк."""
        flt: dict[str, Any] = {"ДатаВремяС": date_from, "ПолныйСертификатЭП": "Нет"}
        if date_to:
            flt["ДатаВремяПо"] = date_to
        if event_id:
            flt["ИдентификаторСобытия"] = event_id
        if doc_id:
            flt["ИдентификаторДокумента"] = doc_id
        if with_extension:
            flt["ДопПоля"] = "Расширение"
        return await self.call("СБИС.СписокИзменений", {"Фильтр": flt}, auth, protocol=3)

    async def download(self, auth: tuple[str, str], url: str) -> bytes:
        """Скачать бинарное вложение по Файл.Ссылка (GET с тем же auth-заголовком)."""
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url, headers={auth[0]: auth[1]})
        if r.status_code != 200:
            raise SabyError(f"Скачивание вложения: HTTP {r.status_code}")
        return r.content


def _extract_doc_list(result: Any) -> list:
    """Достать список документов из ответа (формат может быть list или {Документ:[...]} и т.п.)."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("Документ", "Документы", "Result", "items", "list"):
            v = result.get(key)
            if isinstance(v, list):
                return v
    return []


def parse_document(d: dict) -> dict:
    """Разбор документа из СБИС.СписокИзменений в плоскую строку для UI.

    Тип документа — в `Регламент.Название` («Реализация»); статус ЭДО — в объекте
    `Состояние{Код,Название,Описание,НеполнаяОбработка}`. «Не принят покупателем»
    (марки зависли) = ждём действия покупателя: Описание/Название содержит «ожида»
    (напр. код 4 «Доставлен / Ожидается утверждение», код 23 «Ожидает подписания»)
    либо НеполнаяОбработка=Да; терминальные (аннулирован/отозван/завершён) — не зависшие.
    """
    state = d.get("Состояние") if isinstance(d.get("Состояние"), dict) else {}
    reglam = d.get("Регламент") if isinstance(d.get("Регламент"), dict) else {}
    contractor = d.get("Контрагент") or {}
    if not isinstance(contractor, dict):
        contractor = {}
    ur = contractor.get("СвЮЛ") if isinstance(contractor.get("СвЮЛ"), dict) else {}
    inn = ur.get("ИНН") or contractor.get("ИНН")
    name = ur.get("Название") or contractor.get("Название") or contractor.get("Наименование")

    code = state.get("Код")
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    state_name = state.get("Название") or ""
    state_desc = state.get("Описание") or ""
    incomplete = str(state.get("НеполнаяОбработка") or "").lower() == "да"

    haystack = f"{state_name} {state_desc}".lower()
    terminal = any(w in haystack for w in ("аннулир", "отозв", "удал"))
    awaiting = ("ожида" in haystack) or ("не подписан" in haystack)
    unsigned = (not terminal) and (awaiting or incomplete)

    rasm = d.get("Расширение") if isinstance(d.get("Расширение"), dict) else {}
    mark_state = None
    if rasm.get("СостояниеМарк") or rasm.get("СостояниеГосСистемы"):
        mark_state = {
            "mark": rasm.get("СостояниеМарк"),
            "gis": rasm.get("СостояниеГосСистемы"),
        }
    return {
        "id": d.get("Идентификатор"),
        "number": d.get("Номер"),
        "date": d.get("Дата"),
        "type": reglam.get("Название") or d.get("Название"),
        "direction": d.get("Направление"),
        "counterparty_name": name,
        "counterparty_inn": inn,
        "state_code": code,
        "state_name": state_name or None,
        "state_desc": state_desc or None,
        "incomplete": incomplete,
        "unsigned": unsigned,
        "mark_state": mark_state,
        "note": d.get("Примечание") or None,
    }


def last_event_cursor(docs: list) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Курсор для следующей страницы: (ИдентификаторСобытия, ДатаВремя, ИдентификаторДокумента)
    последнего события последнего документа страницы."""
    for doc in reversed(docs):
        evs = doc.get("Событие") or []
        if evs:
            ev = evs[-1]
            return ev.get("Идентификатор"), ev.get("ДатаВремя"), doc.get("Идентификатор")
    return None, None, None


def primary_upd_link(doc: dict) -> Optional[str]:
    """Ссылка на первичное исходящее вложение УПД/реализации (ФНС-XML с марками).

    Ищем во всех событиях: Вложение с Направление=Исходящий, Служебный=Нет и именем/типом
    формализованного счёта-фактуры/УПД (ON_NSCHFDOPPR…). Возвращает Файл.Ссылка или None."""
    def _is_upd(att: dict) -> bool:
        f = att.get("Файл") or {}
        nm = (att.get("Название") or "").lower()
        fname = (f.get("Имя") or "") if isinstance(f, dict) else ""
        if att.get("Направление") != "Исходящий" or str(att.get("Служебный")).lower() != "нет":
            return False
        if not (isinstance(f, dict) and f.get("Ссылка")):
            return False
        return ("nschfdoppr" in fname.lower()) or ("упд" in nm) or ("фактура" in nm)

    for ev in (doc.get("Событие") or []):
        for att in (ev.get("Вложение") or []):
            if _is_upd(att):
                return (att.get("Файл") or {}).get("Ссылка")
    # запасной путь — вложения на уровне документа
    for att in (doc.get("Вложение") or []):
        if _is_upd(att):
            return (att.get("Файл") or {}).get("Ссылка")
    return None
