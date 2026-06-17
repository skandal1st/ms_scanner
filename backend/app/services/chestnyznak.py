import asyncio
import base64
import random
import re
import time
import uuid as uuid_lib
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.logging import logger


@dataclass
class VerifyResult:
    valid: bool
    gtin: Optional[str]
    serial: Optional[str]
    status: str          # IN_CIRCULATION, RETIRED, NOT_FOUND
    product_name: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SsccInfo:
    """Сводка по SSCC-коробу из ЧЗ (метод sscc_check): товар + количество внутри.
    Перечень самих КМ ЧЗ этим методом НЕ отдаёт — только GTIN, серию и число SGTIN."""
    sscc: str
    gtin: Optional[str]
    series: Optional[str]
    quantity: int


@dataclass
class CodeInfo:
    """Сведения о КМ из True API (cises/info + aggregated/list).

    package_type — generalPackageType ЧЗ: UNIT=пачка, GROUP/LEVEL1=блок, BOX/LEVEL2=короб.
    children — ЛИСТОВЫЕ КМ единиц (для блока/короба — пачки, рекурсивно развёрнуто).
    Заполняется для любого найденного кода (в т.ч. обычной пачки — тогда children пуст),
    чтобы показывать владельца/производителя по каждому скану.
    is_aggregate True, если внутри есть единицы (блок/короб)."""
    cis: str
    package_type: Optional[str]
    children: list[str]
    gtin: Optional[str]
    product_name: Optional[str]
    owner_name: Optional[str]
    producer_name: Optional[str]
    product_group: Optional[str]

    @property
    def is_aggregate(self) -> bool:
        return bool(self.children)


def _flatten_aggregate_leaves(node: Any) -> list[str]:
    """Рекурсивно собрать листовые КМ из дерева cises/aggregated/list.
    Узел = dict{код: поддерево} либо list[код]; лист = код с пустым поддеревом."""
    out: list[str] = []
    if isinstance(node, list):
        for x in node:
            if isinstance(x, str):
                out.append(x)
            else:
                out.extend(_flatten_aggregate_leaves(x))
    elif isinstance(node, dict):
        for k, v in node.items():
            if not v:  # [], {}, None → k это лист
                out.append(k)
            else:
                out.extend(_flatten_aggregate_leaves(v))
    return out


# Путь метода ЧЗ «sscc_check» (право SSCC_CHECK). Точный префикс версии/группы
# может отличаться — при 404 поправить здесь (доки ЧЗ давали относительный
# `/reestr/sscc/{sscc}/sscc_check`). Базовый хост — settings.CZ_API_BASE_URL.
CZ_SSCC_CHECK_PATH = "/api/v3/facade/reestr/sscc/{sscc}/sscc_check"


def _random_gtin14_digits() -> str:
    """Случайный 14-значный GTIN с корректной контрольной цифрой (mock unpack без плана)."""
    body = "".join(str(random.randint(0, 9)) for _ in range(13))
    total = 0
    weight = 3
    for ch in reversed(body):
        total += int(ch) * weight
        weight = 4 - weight
    check = (10 - (total % 10)) % 10
    return body + str(check)


# GS (Group Separator) между полями переменной длины в строке элемента GS1 DataMatrix.
# По стандарту ASCII это код **29** (0x1D), не 232 — в части инструкций «ASCII 232» ошибочно.
_FNC1 = "\x1d"


def _tail_serial_after_ai21(rest: str) -> Optional[str]:
    """Серийный номер после AI 21 (с опциональными FNC1 перед 21)."""
    r = rest
    while r.startswith(_FNC1):
        r = r[1:]
    if not r.startswith("21"):
        return None
    raw = r[2:]
    if _FNC1 in raw:
        raw = raw.split(_FNC1, 1)[0]
    serial = raw.strip()
    if len(serial) > 200:
        serial = serial[:200]
    return serial or None


def _implicit_serial_after_gtin(rest: str) -> Optional[str]:
    """Серия без явного AI 21 (первый символ после GTIN — не цифра и не FNC1)."""
    if not rest or rest[0].isdigit() or rest.startswith(_FNC1):
        return None
    raw = rest
    if _FNC1 in raw:
        raw = raw.split(_FNC1, 1)[0]
    serial = raw.strip()
    if len(serial) > 200:
        serial = serial[:200]
    return serial or None


def _looks_like_bare_gtin_serial_tail(s: str) -> bool:
    """
    Строка вида «14 цифр GTIN + хвост» без литералов AI ``01``/``21`` в начале
    (некоторые сканеры/режимы для табака отдают так).
    """
    t = (s or "").strip()
    if len(t) < 15:
        return False
    if not t[:14].isdigit():
        return False
    if t.startswith("01") and len(t) >= 16 and t[2:16].isdigit():
        return False
    return bool(t[14:])


def _normalize_bare_gtin_serial_to_gs1_element_string(s: str) -> str:
    """Склейка ``01``+GTIN14+``21``+хвост для единого разбора и МС."""
    t = (s or "").strip()
    if not _looks_like_bare_gtin_serial_tail(t):
        return t
    return "01" + t[:14] + "21" + t[14:]


def parse_gs1_km_gtin_serial(code: str) -> tuple[Optional[str], Optional[str]]:
    """
    Извлечь GTIN (14 цифр) и серию из сырой GS1-строки скана.
    Поддерживается «голый» формат ``<14цифр GTIN><хвост>`` без ``01``/``21`` в начале.
    Не пересобирает CIS в общем случае — только чтение для полей scan.gtin / serial и проверок.
    """
    if not code:
        return (None, None)

    if code.startswith("01") and len(code) >= 16 and code[2:16].isdigit():
        gtin = code[2:16]
        tail = code[16:]
        serial = _tail_serial_after_ai21(tail) or _implicit_serial_after_gtin(tail)
        return (gtin, serial)

    m = re.search(r"(?:^|\x1d)01(\d{14})", code)
    if m:
        gtin = m.group(1)
        tail = code[m.end() :]
        serial = _tail_serial_after_ai21(tail) or _implicit_serial_after_gtin(tail)
        return (gtin, serial)

    # 14 цифр подряд с начала + хвост, но не стандартный ``01``+14цифр в позиции 0
    if len(code) >= 15 and code[:14].isdigit() and not (
        code.startswith("01") and len(code) >= 16 and code[2:16].isdigit()
    ):
        gtin = code[:14]
        rest = code[14:]
        serial = _tail_serial_after_ai21(rest)
        if serial:
            return (gtin, serial)
        if rest and not rest[0].isdigit() and not rest.startswith(_FNC1):
            serial = _implicit_serial_after_gtin(rest)
            if serial:
                return (gtin, serial)
        return (gtin, rest)

    return (None, None)


# Значение = длина серийной части после AI ``21`` в коде идентификации для ``cis``.
# Типы не из списка — в МС уходит полная нормализованная строка (риск 17102 → дополнять по факту).
# Enum МС: https://jsr.io/@moysklad/moysklad-ts/0.1.35/src/types/tracking-type.ts
MOYSKLAD_CIS_DOCUMENT_SERIAL_LEN_BY_TRACKING_TYPE: dict[str, int] = {
    # КИ 24 символа (01+14+21+6) — как у напитков/воды в материалах ЧЗ по «коду идентификации»
    "SOFT_DRINKS": 6,
    "WATER": 6,
    "MILK": 6,
    "FOOD_SUPPLEMENT": 6,
    "SANITIZER": 6,
    # Длина серии КИ — применяется ТОЛЬКО к «голым» кодам без разделителя GS (у кодов
    # с GS серия берётся точно по GS). Подтверждено по ЧЗ cises/info: серия 7.
    "TOBACCO": 7,
    "OTP": 7,  # альтернативная табачная продукция (кальянный табак)
    "NCP": 7,  # никотиносодержащая продукция
}


def cis_identification_document_code(normalized_cis: str, serial_symbol_len: int) -> str:
    """
    Укороченный код для документов/МС: ``01`` + 14 цифр GTIN + ``21`` + первые
    ``serial_symbol_len`` символов серийной части (криптохвост не включаем).
    """
    s = (normalized_cis or "").strip()
    need = 18 + serial_symbol_len
    if len(s) <= need or not s.startswith("01"):
        return s
    if len(s) < 18 or not s[2:16].isdigit() or s[16:18] != "21":
        return s
    return s[:need]


def cis_compare_forms_for_ms(raw: str) -> list[str]:
    """Варианты cis для сопоставления с текстом ошибки МС (полный КМ и укороченные КИ)."""
    base = cis_string_for_moysklad_api(raw, moysklad_tracking_type=None)
    out: list[str] = [base]
    for serial_len in (6, 7):
        short = cis_identification_document_code(base, serial_len)
        if short != base:
            out.append(short)
    # уникальные, порядок: полный затем 6 затем 7
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def cis_string_for_moysklad_api(
    stored: str, moysklad_tracking_type: Optional[str] = None
) -> str:
    """
    Значение поля cis при записи КМ в МойСклад (PUT позиции или POST …/trackingCodes).

    Нормативно КМ — цепочка (GTIN, серия, криптохвост и др. AI) с разделителями **GS**
    (ASCII **29**, ``\\x1d``). ``Scan.code`` хранит сырой ввод.

    - Строка **«14 цифр GTIN + хвост»** без префиксов ``01``/``21`` приводится к виду
      ``01``+GTIN+``21``+хвост (частый вывод сканера по табаку).
    - Регистр после ``21`` не меняем.
    - ``%c1`` / ``%C1`` в серийной части удаляем.

    Если задан ``assortment.trackingType`` из МС, в ``cis`` может уходить **укороченный
    код идентификации**: для напитков, воды, молока, БАД, антисептиков — **24** символа
    (``21`` + 6 знаков серии); для табака, OTP, NCP — **25** (``21`` + 7). Остальные
    типы маркировки МС — полная строка (доп. типы добавляйте в
    ``MOYSKLAD_CIS_DOCUMENT_SERIAL_LEN_BY_TRACKING_TYPE`` после проверки на 17102).
    """
    if not stored:
        return stored
    parts: list[str] = []
    for ch in stored.strip():
        o = ord(ch)
        if o == 0x1D:
            parts.append(ch)
        elif 0x20 <= o <= 0x7E:
            parts.append(ch)
    s = "".join(parts)

    tt = (moysklad_tracking_type or "").strip().upper()
    serial_len = MOYSKLAD_CIS_DOCUMENT_SERIAL_LEN_BY_TRACKING_TYPE.get(tt)

    def _trim_serial(serial: str, had_gs: bool) -> str:
        serial = re.sub(r"(?i)%c1", "", serial)
        # Серия отделена GS → точная граница, длину не трогаем. Иначе (голый код,
        # криптохвост прилип без разделителя) — режем по длине типа маркировки.
        if not had_gs and serial_len is not None and len(serial) > serial_len:
            serial = serial[:serial_len]
        return serial

    # КЛЮЧЕВОЕ: сохраняем форму кода как он зарегистрирован в ЧЗ = как отсканирован.
    # Структурный GS1 (01<GTIN>21<serial>) → cis С AI 01/21. «Голый» (<GTIN><serial>
    # без AI) → cis БЕЗ 01/21. Навязывать 01/21 голым кодам НЕЛЬЗЯ — ЧЗ их в такой
    # форме не находит (404 при проведении в МС). Проверено на проде 2026-06-16:
    # МС и ЧЗ принимают `04660321208767Yi3P&E>` (голый КИ, серия 7), а `01…21…` — нет.

    # 1) Структурный GS1-код: 01 + GTIN(14) + 21 + serial[ + GS + криптохвост ]
    if s.startswith("01") and len(s) >= 18 and s[2:16].isdigit() and s[16:18] == "21":
        gtin = s[2:16]
        cut = s[18:].split(_FNC1, 1)
        serial = _trim_serial(cut[0], len(cut) > 1)
        return "01" + gtin + "21" + serial

    # 2) «Голый» код: GTIN(14) + serial[ + GS + криптохвост ], без AI 01/21
    if len(s) >= 15 and s[:14].isdigit():
        gtin = s[:14]
        cut = s[14:].split(_FNC1, 1)
        serial = _trim_serial(cut[0], len(cut) > 1)
        return gtin + serial

    return s


class ChestnyZnakService:
    def __init__(self, token: Optional[str] = None, mock: Optional[bool] = None):
        self.token = token
        self.mock = mock if mock is not None else settings.CZ_MOCK_MODE
        self.base_url = settings.CZ_API_BASE_URL

    async def verify_code(self, code: str) -> VerifyResult:
        """Проверить код маркировки (CIS в запросах — сырая строка, без пересборки)."""
        gtin, serial = parse_gs1_km_gtin_serial(code)

        if self.mock:
            return await self._mock_verify(code, gtin, serial)

        return await self._real_verify(code, gtin, serial)

    async def _mock_verify(self, code: str, gtin: Optional[str], serial: Optional[str]) -> VerifyResult:
        """Имитация ответа ЧЗ с реалистичной задержкой."""
        await asyncio.sleep(random.uniform(0.1, 0.6))

        # 85% валидных, 10% невалидных, 5% не найдены
        roll = random.random()
        if roll < 0.85:
            return VerifyResult(
                valid=True,
                gtin=gtin,
                serial=serial,
                status="IN_CIRCULATION",
                product_name=None,
            )
        elif roll < 0.95:
            return VerifyResult(
                valid=False,
                gtin=gtin,
                serial=serial,
                status="RETIRED",
                error="Код выведен из оборота",
            )
        else:
            return VerifyResult(
                valid=False,
                gtin=gtin,
                serial=serial,
                status="NOT_FOUND",
                error="Код не найден в системе",
            )

    async def _real_verify(self, code: str, gtin: Optional[str], serial: Optional[str]) -> VerifyResult:
        """Реальный запрос в ЧЗ API GET .../identificationcodes/{cis} (cis в path — URL-encode)."""
        from app.services.cz_logger import log_cz_request

        start = time.time()
        cis_for_request = _normalize_bare_gtin_serial_to_gs1_element_string(code)
        encoded = quote(cis_for_request, safe="")
        url = f"{self.base_url}/api/v3/facade/identificationcodes/{encoded}"
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                duration_ms = int((time.time() - start) * 1000)

                await log_cz_request(
                    method="GET",
                    url=url,
                    request_body=None,
                    response_status=resp.status_code,
                    response_body=resp.json() if resp.status_code == 200 else None,
                    duration_ms=duration_ms,
                )

                if resp.status_code == 404:
                    return VerifyResult(valid=False, gtin=gtin, serial=serial,
                                        status="NOT_FOUND", error="Код не найден")
                resp.raise_for_status()
                data = resp.json()

                cis_status = data.get("status", "")
                valid = str(cis_status).upper() == "INTRODUCED"
                api_gtin = _gtin_from_cz_facade_payload(data)
                api_serial = _serial_from_cz_facade_payload(data, serial)
                api_name = _product_name_from_cz_facade_payload(data)
                out_gtin = api_gtin or gtin
                out_serial = api_serial if api_serial else serial
                if out_serial is not None:
                    out_serial = str(out_serial).strip() or None
                return VerifyResult(
                    valid=valid,
                    gtin=out_gtin,
                    serial=out_serial,
                    status="IN_CIRCULATION" if valid else cis_status,
                    error=None if valid else f"Статус: {cis_status}",
                    product_name=api_name,
                )

        except httpx.TimeoutException:
            raise CZApiError("Таймаут запроса к Честный Знак")
        except httpx.HTTPStatusError as e:
            raise CZApiError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")

    async def unpack_box(
        self, sscc: str, plan_gtins: Optional[list[str]] = None
    ) -> list[str]:
        """
        Раскрыть SSCC-короб на индивидуальные коды маркировки (KM).
        В mock режиме генерирует 3-5 фейковых KM на основе GTIN из плана документа,
        чтобы коды попали в ожидаемые позиции. Если plan_gtins пустой —
        используется случайный 14-значный GTIN.
        Реальный режим должен звать ЧЗ /api/v3/facade/aggregation или подобный
        — пока NotImplemented.
        """
        if self.mock:
            await asyncio.sleep(random.uniform(0.2, 0.5))
            count = random.randint(3, 5)
            codes: list[str] = []
            pool = list(plan_gtins or [])
            for i in range(count):
                if pool:
                    gtin = pool[i % len(pool)]
                else:
                    # 13 случайных цифр + корректная контрольная (на случай dev с CZ_MOCK_MODE=false)
                    gtin = _random_gtin14_digits()
                serial = "".join(
                    random.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(13)
                )
                # GS1 DataMatrix: 01<gtin14>21<serial>
                codes.append(f"01{gtin}21{serial}")
            logger.info(
                "cz.unpack_box.mock", sscc=sscc, count=count, plan_gtins=len(pool)
            )
            return codes
        raise NotImplementedError(
            "Реальное раскрытие коробов в ЧЗ ещё не реализовано"
        )

    async def get_sscc_info(
        self, sscc: str, plan_gtins: Optional[list[str]] = None
    ) -> SsccInfo:
        """
        Сводка по SSCC-коробу для сохранения «целиком» (без раскрытия): GTIN, серия,
        число SGTIN внутри. Real-режим зовёт ЧЗ ``GET …/reestr/sscc/{sscc}/sscc_check``
        (нужно право SSCC_CHECK на сертификате). Mock берёт GTIN из плана документа и
        случайное количество, чтобы поток работал без реального ЧЗ.

        Перечень индивидуальных КМ этот метод не возвращает — для «целиком» он и не
        нужен: в МС уходит один transportpack-код, состав резолвит МС/ЧЗ.
        """
        if self.mock:
            await asyncio.sleep(random.uniform(0.2, 0.5))
            pool = list(plan_gtins or [])
            gtin = normalize_gtin_key(pool[0]) if pool else _random_gtin14_digits()
            quantity = random.randint(3, 5)
            logger.info(
                "cz.sscc_check.mock", sscc=sscc, gtin=gtin, quantity=quantity
            )
            return SsccInfo(sscc=sscc, gtin=gtin, series=None, quantity=quantity)

        from app.services.cz_logger import log_cz_request

        start = time.time()
        url = f"{self.base_url}{CZ_SSCC_CHECK_PATH.format(sscc=quote(sscc, safe=''))}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                duration_ms = int((time.time() - start) * 1000)
                await log_cz_request(
                    method="GET",
                    url=url,
                    request_body=None,
                    response_status=resp.status_code,
                    response_body=resp.json() if resp.status_code == 200 else None,
                    duration_ms=duration_ms,
                )
                if resp.status_code == 404:
                    raise CZApiError("SSCC-короб не найден в Честном Знаке")
                resp.raise_for_status()
                return _sscc_info_from_payload(sscc, resp.json())
        except httpx.TimeoutException:
            raise CZApiError("Таймаут запроса к Честный Знак (sscc_check)")
        except httpx.HTTPStatusError as e:
            raise CZApiError(
                f"HTTP {e.response.status_code} при sscc_check: {e.response.text[:200]}"
            )

    async def get_code_info(self, code: str) -> Optional["CodeInfo"]:
        """Сведения о КМ через True API: владелец/производитель/товар + состав агрегата.

        Сначала ``POST /cises/info?pg=<group>`` (перебор ``cz_product_groups_list``,
        первый ответ 200 с cisInfo без errorCode). Код нормализуем — **убираем скобки**
        AI-разделителей (сканер логистической упаковки даёт «(02)…(37)…»; ЧЗ требует
        КИ без скобок). Если код — агрегат (есть child) — добиваем ``cises/aggregated/list``
        для получения ВСЕХ листовых КМ (короб → блоки → пачки, рекурсивно).
        Возвращает CodeInfo для любого найденного кода (для пачки children пуст),
        либо None если код нигде не найден. В mock не используется.
        """
        if self.mock or not self.token:
            return None

        from app.services.cz_logger import log_cz_request

        cis = code.replace("(", "").replace(")", "").strip()
        info_url = f"{self.base_url}/api/v3/true-api/cises/info"
        agg_url = f"{self.base_url}/api/v3/true-api/cises/aggregated/list"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "accept": "application/json",
            "Content-Type": "application/json",
        }
        for pg in settings.cz_product_groups_list:
            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        info_url, params={"pg": pg}, headers=headers, json=[cis]
                    )
                    body = resp.json() if resp.status_code == 200 else None
                    await log_cz_request(
                        method="POST",
                        url=f"{info_url}?pg={pg}",
                        request_body=[cis],
                        response_status=resp.status_code,
                        response_body=body,
                        duration_ms=int((time.time() - start) * 1000),
                    )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                logger.warning("cz.code_info.http_error", pg=pg, error=str(exc))
                continue

            if resp.status_code != 200 or not isinstance(body, list) or not body:
                continue
            entry = body[0] if isinstance(body[0], dict) else {}
            if entry.get("errorCode"):
                continue  # не та группа → следующая
            ci = entry.get("cisInfo") or {}
            first_layer = [c for c in (ci.get("child") or []) if isinstance(c, str)]

            # Для агрегата (есть вложения) забираем ВСЕ листовые КМ рекурсивно.
            children: list[str] = []
            if first_layer:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        ar = await client.post(
                            agg_url, params={"pg": pg}, headers=headers, json=[cis]
                        )
                        abody = ar.json() if ar.status_code == 200 else None
                        await log_cz_request(
                            method="POST",
                            url=f"{agg_url}?pg={pg}",
                            request_body=[cis],
                            response_status=ar.status_code,
                            response_body=abody if isinstance(abody, dict) else None,
                            duration_ms=0,
                        )
                    if isinstance(abody, dict) and abody.get(cis):
                        children = _flatten_aggregate_leaves(abody[cis])
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    logger.warning("cz.aggregated_list.http_error", pg=pg, error=str(exc))
                if not children:
                    children = first_layer  # фолбэк: хотя бы первый слой

            info = CodeInfo(
                cis=ci.get("cis") or cis,
                package_type=ci.get("generalPackageType") or ci.get("packageType"),
                children=children,
                gtin=_digits_gtin14_from_value(ci.get("gtin")),
                product_name=(ci.get("productName") or None),
                owner_name=(ci.get("ownerName") or None),
                producer_name=(ci.get("producerName") or ci.get("manufacturerName") or None),
                product_group=ci.get("productGroup") or pg,
            )
            logger.info(
                "cz.code_info.ok",
                pg=pg,
                package_type=info.package_type,
                children=len(info.children),
                is_aggregate=info.is_aggregate,
            )
            return info
        logger.info("cz.code_info.not_found", groups=len(settings.cz_product_groups_list))
        return None

    async def accept_batch(self, codes: list[str], document_id: str) -> bool:
        """Подтвердить приёмку партии кодов."""
        if self.mock:
            logger.info("cz.accept_batch.mock", count=len(codes), document_id=document_id)
            await asyncio.sleep(0.5)
            return True
        # TODO: реализовать когда будет реальный ЧЗ токен
        raise NotImplementedError("Реальная отправка в ЧЗ ещё не реализована")

    async def request_cert_key(self) -> dict:
        """Шаг 1 challenge-flow: GET /api/v3/auth/cert/key → {uuid, data}.

        В mock возвращает синтетический challenge без сетевого вызова —
        фронт всё равно подпишет его mock-плагином или пропустит подпись.
        """
        if self.mock:
            return {
                "uuid": str(uuid_lib.uuid4()),
                "data": base64.b64encode(b"mock-challenge").decode(),
            }

        from app.services.cz_logger import log_cz_request

        start = time.time()
        url = f"{self.base_url}/api/v3/auth/cert/key"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                duration_ms = int((time.time() - start) * 1000)
                body = resp.json() if resp.status_code == 200 else None
                await log_cz_request(
                    method="GET",
                    url=url,
                    request_body=None,
                    response_status=resp.status_code,
                    response_body=body,
                    duration_ms=duration_ms,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            raise CZApiError("Таймаут запроса к Честный Знак (cert/key)")
        except httpx.HTTPStatusError as e:
            raise CZApiError(f"HTTP {e.response.status_code} при cert/key: {e.response.text[:200]}")

    async def exchange_cert_signature(self, uuid: str, signed_data: str) -> dict:
        """Шаг 3 challenge-flow: POST /api/v3/auth/cert/ → {token, expire}.

        В mock возвращает фейковый токен с TTL 1 час.
        """
        if self.mock:
            return {"token": f"mock-{uuid[:8]}", "expire": 3600}

        from app.services.cz_logger import log_cz_request

        start = time.time()
        url = f"{self.base_url}/api/v3/auth/cert/"
        # Не пишем signed_data в request_body — оно длинное и чувствительное.
        # log_cz_request редактирует /auth/cert/ автоматически, но передаём явно.
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={"uuid": uuid, "data": signed_data})
                duration_ms = int((time.time() - start) * 1000)
                body = resp.json() if resp.status_code == 200 else None
                await log_cz_request(
                    method="POST",
                    url=url,
                    request_body={"uuid": uuid, "data": "<redacted>"},
                    response_status=resp.status_code,
                    response_body=body,
                    duration_ms=duration_ms,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            raise CZApiError("Таймаут запроса к Честный Знак (cert exchange)")
        except httpx.HTTPStatusError as e:
            raise CZApiError(f"HTTP {e.response.status_code} при cert exchange: {e.response.text[:200]}")


class CZApiError(Exception):
    pass


def extract_gtin(code: str) -> Optional[str]:
    """GTIN для плана/матчинга: извлечение из сырой CIS без пересборки строки."""
    g, _ = parse_gs1_km_gtin_serial(code)
    return g


def is_sscc(code: str) -> bool:
    """SSCC-короб — AI 00 + 18 цифр (20 знаков). Раскладка — только на клиенте (normalizeScannerInput)."""
    c = code.strip()
    return c.startswith("00") and len(c) == 20 and c.isdigit()


def _digits_gtin14_from_value(v: Any) -> Optional[str]:
    if v is None:
        return None
    d = "".join(c for c in str(v) if c.isdigit())
    if len(d) == 13:
        return d.zfill(14)
    if len(d) == 14:
        return d
    if len(d) > 14:
        return d[-14:]
    return None


def normalize_gtin_key(v: Any) -> Optional[str]:
    """
    Единый GTIN-14 для сравнения плана МС и поля scan.gtin.
    Устраняет расхождение «тот же товар, разные строки» (13 vs 14 цифр,
    ведущий ноль, хвост из лишних цифр из ответа ЧЗ).
    """
    if v is None:
        return None
    d = "".join(c for c in str(v).strip() if c.isdigit())
    if not d:
        return None
    if len(d) > 14:
        return d[-14:]
    if len(d) == 14:
        return d
    if len(d) == 13:
        return d.zfill(14)
    return d.zfill(14)


def _gtin_from_cz_facade_payload(data: dict) -> Optional[str]:
    for key in ("gtin", "identifiedGtin", "gtin14", "gtin13"):
        g = _digits_gtin14_from_value(data.get(key))
        if g:
            return g
    return None


def _serial_from_cz_facade_payload(data: dict, fallback: Optional[str]) -> Optional[str]:
    for key in ("serialNumber", "serial", "sn"):
        v = data.get(key)
        if v is not None and str(v).strip():
            s = str(v).strip()
            return s[:200] if len(s) > 200 else s
    return fallback


def _product_name_from_cz_facade_payload(data: dict) -> Optional[str]:
    for key in ("productName", "goodName", "productGroupName", "brand", "producerName"):
        v = data.get(key)
        if v is not None and str(v).strip():
            s = str(v).strip()
            return s[:500] if len(s) > 500 else s
    return None


def _sscc_info_from_payload(sscc: str, data: Any) -> "SsccInfo":
    """Разобрать ответ ЧЗ sscc_check в SsccInfo. Имена полей у ЧЗ возможны разные —
    берём наиболее вероятные варианты (gtin, серия, количество SGTIN)."""
    obj: Any = data
    if isinstance(obj, dict) and isinstance(obj.get("data"), (dict, list)):
        obj = obj["data"]
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        obj = {}

    gtin = None
    for key in ("gtin", "GTIN", "gtin14", "gtin13"):
        gtin = _digits_gtin14_from_value(obj.get(key))
        if gtin:
            break

    series = None
    for key in ("series", "seriesNumber", "batch", "batchNumber", "production"):
        v = obj.get(key)
        if v is not None and str(v).strip():
            series = str(v).strip()
            break

    quantity = 0
    for key in (
        "quantity",
        "count",
        "sgtinCount",
        "countSgtin",
        "sgtins",
        "sgtinsCount",
        "amount",
    ):
        v = obj.get(key)
        if v is None:
            continue
        try:
            quantity = int(v)
            break
        except (TypeError, ValueError):
            continue

    return SsccInfo(sscc=sscc, gtin=gtin, series=series, quantity=quantity)


def _gs1_check_digit_ok(gtin14: str) -> bool:
    """Проверка контрольной цифры GTIN-14 (модуль 10 GS1)."""
    if len(gtin14) != 14 or not gtin14.isdigit():
        return False
    body, check = gtin14[:13], int(gtin14[13])
    total = 0
    weight = 3
    for ch in reversed(body):
        total += int(ch) * weight
        weight = 4 - weight
    calc = (10 - (total % 10)) % 10
    return calc == check


def verify_code_local_gs1(code: str) -> VerifyResult:
    """Проверка структуры КМ без API Честного Знака (формат GS1 + контрольная сумма GTIN)."""
    gtin, serial = parse_gs1_km_gtin_serial(code)
    if not gtin or len(gtin) != 14 or not gtin.isdigit():
        return VerifyResult(
            valid=False,
            gtin=gtin,
            serial=serial,
            status="BAD_FORMAT",
            error="Некорректный формат: ожидается AI 01 и GTIN из 14 цифр",
        )
    if not _gs1_check_digit_ok(gtin):
        return VerifyResult(
            valid=False,
            gtin=gtin,
            serial=serial,
            status="BAD_GTIN",
            error="Неверная контрольная сумма GTIN",
        )
    if not serial or not serial.strip():
        return VerifyResult(
            valid=False,
            gtin=gtin,
            serial=serial,
            status="NO_SERIAL",
            error="Не удалось извлечь серийный номер (AI 21)",
        )
    return VerifyResult(
        valid=True,
        gtin=gtin,
        serial=serial.strip(),
        status="IN_CIRCULATION",
        product_name=None,
    )

