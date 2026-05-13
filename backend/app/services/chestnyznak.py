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


# FNC1 / GS separator in GS1 DataMatrix element strings.
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


def parse_gs1_km_gtin_serial(code: str) -> tuple[Optional[str], Optional[str]]:
    """
    Извлечь GTIN (14 цифр) и серию из сырой GS1-строки скана.
    Не пересобирает CIS — только чтение для полей scan.gtin / serial и проверок.
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

    if len(code) >= 14 and code[:14].isdigit():
        gtin = code[:14]
        rest = code[14:]
        serial = _tail_serial_after_ai21(rest)
        if serial:
            return (gtin, serial)
        if rest and not rest[0].isdigit() and not rest.startswith(_FNC1):
            serial = _implicit_serial_after_gtin(rest)
            return (gtin, serial)

    return (None, None)


def cis_string_for_moysklad_api(stored: str) -> str:
    """
    Значение поля cis при PUT trackingCodes в МойСклад.

    МС часто ожидает разделитель FNC1 (ASCII 29) между AI 01 (GTIN) и AI 21 (серия),
    тогда как сканер или камера отдают слитную строку вида ``01<14 цифр>21<серия>`` без ``\\x1d``.
    ``Scan.code`` в БД остаётся сырым — правка только на границе вызова API МойСклад.
    """
    if not stored:
        return stored
    s = stored
    if not (s.startswith("01") and len(s) >= 18 and s[2:16].isdigit()):
        return stored
    tail = s[16:]
    if tail.startswith(_FNC1):
        return stored
    if tail.startswith("21"):
        return s[:16] + _FNC1 + tail
    return stored


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
        encoded = quote(code, safe="")
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

