import asyncio
import base64
import random
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


# Должен совпадать с frontend/src/lib/scannerLayout.ts (USB-сканер + русская раскладка).
_RU_SCAN_LAYOUT: dict[str, str] = {
    "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y", "г": "u", "ш": "i", "щ": "o", "з": "p", "х": "[", "ъ": "]",
    "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h", "о": "j", "л": "k", "д": "l", "ж": ";", "э": "'",
    "я": "z", "ч": "x", "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m", "б": ",", "ю": ".", "ё": "`",
    "Й": "Q", "Ц": "W", "У": "E", "К": "R", "Е": "T", "Н": "Y", "Г": "U", "Ш": "I", "Щ": "O", "З": "P", "Х": "{", "Ъ": "}",
    "Ф": "A", "Ы": "S", "В": "D", "А": "F", "П": "G", "Р": "H", "О": "J", "Л": "K", "Д": "L", "Ж": ":", "Э": '"',
    "Я": "Z", "Ч": "X", "С": "C", "М": "V", "И": "B", "Т": "N", "Ь": "M", "Б": "<", "Ю": ">", "Ё": "~",
}


def _normalize_ru_scan_layout(s: str) -> str:
    return "".join(_RU_SCAN_LAYOUT.get(ch, ch) for ch in s)


def canonicalize_marking_scan_code(code: str) -> str:
    """
    Нормализация строки КМ из сканера: ЙЦУКЕН→латиница, при необходимости AI 01 и 21.
    Совпадает по смыслу с normalizeScannerInput на фронте + типичные потери префиксов.
    """
    s = _normalize_ru_scan_layout(code.strip())
    if s.startswith("01") and len(s) >= 16 and s[2:16].isdigit():
        return s
    if len(s) < 14 or not s[:14].isdigit():
        return s
    gtin14, rest = s[:14], s[14:]
    if rest.startswith("21"):
        return "01" + s
    if rest.startswith("\x1d"):
        tail = rest.lstrip("\x1d")
        if tail.startswith("21"):
            return "01" + gtin14 + rest
    if rest and not rest[0].isdigit() and not rest.startswith("\x1d"):
        return "01" + gtin14 + "21" + rest
    return s


class ChestnyZnakService:
    def __init__(self, token: Optional[str] = None, mock: Optional[bool] = None):
        self.token = token
        self.mock = mock if mock is not None else settings.CZ_MOCK_MODE
        self.base_url = settings.CZ_API_BASE_URL

    async def verify_code(self, code: str) -> VerifyResult:
        """Проверить код маркировки."""
        c = canonicalize_marking_scan_code(code)
        gtin = _extract_gtin_canonical(c)
        serial = _extract_serial_canonical(c)

        if self.mock:
            return await self._mock_verify(c, gtin, serial)

        return await self._real_verify(c, gtin, serial)

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


def _extract_gtin_canonical(code: str) -> Optional[str]:
    """GTIN из уже нормализованной GS1-строки (после canonicalize_marking_scan_code)."""
    if code.startswith("01") and len(code) >= 16 and code[2:16].isdigit():
        return code[2:16]
    return None


def extract_gtin(code: str) -> Optional[str]:
    """GS1: AI 01 + 14 цифр GTIN (с учётом раскладки и типичных потерь префиксов)."""
    return _extract_gtin_canonical(canonicalize_marking_scan_code(code))


def is_sscc(code: str) -> bool:
    """SSCC-короб — AI 00 + 18 цифр (всего 20 знаков, цифры)."""
    c = _normalize_ru_scan_layout(code.strip())
    return c.startswith("00") and len(c) == 20 and c.isdigit()


# Алиас для существующих внутренних использований
_extract_gtin = extract_gtin


def _extract_serial_canonical(code: str) -> Optional[str]:
    """Серия AI 21 после GTIN; часто перед 21 идёт GS (\\x1d). Длина серии у КМ может быть >20."""
    if not (code.startswith("01") and len(code) >= 16):
        return None
    rest = code[16:]
    while rest.startswith("\x1d"):
        rest = rest[1:]
    if not rest.startswith("21"):
        return None
    raw = rest[2:]
    if "\x1d" in raw:
        raw = raw.split("\x1d", 1)[0]
    serial = raw.strip()
    if len(serial) > 200:
        serial = serial[:200]
    return serial or None


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


def _extract_serial(code: str) -> Optional[str]:
    return _extract_serial_canonical(canonicalize_marking_scan_code(code))


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
    c = canonicalize_marking_scan_code(code)
    gtin = _extract_gtin_canonical(c)
    serial = _extract_serial_canonical(c)
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

