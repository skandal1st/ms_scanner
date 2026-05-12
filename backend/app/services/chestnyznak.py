import asyncio
import base64
import random
import time
import uuid as uuid_lib
from dataclasses import dataclass
from typing import Optional
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


class ChestnyZnakService:
    def __init__(self, token: Optional[str] = None, mock: Optional[bool] = None):
        self.token = token
        self.mock = mock if mock is not None else settings.CZ_MOCK_MODE
        self.base_url = settings.CZ_API_BASE_URL

    async def verify_code(self, code: str) -> VerifyResult:
        """Проверить код маркировки."""
        gtin = _extract_gtin(code)
        serial = _extract_serial(code)

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
                product_name=_mock_product_name(gtin),
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
        """Реальный запрос в ЧЗ API."""
        from app.services.cz_logger import log_cz_request

        start = time.time()
        url = f"{self.base_url}/api/v3/facade/identificationcodes/{code}"
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
                valid = cis_status == "INTRODUCED"
                return VerifyResult(
                    valid=valid,
                    gtin=gtin,
                    serial=serial,
                    status="IN_CIRCULATION" if valid else cis_status,
                    error=None if valid else f"Статус: {cis_status}",
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
                    gtin = "".join(str(random.randint(0, 9)) for _ in range(14))
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
    """GS1 DataMatrix: AI 01 + 14 цифр GTIN."""
    if code.startswith("01") and len(code) >= 16 and code[2:16].isdigit():
        return code[2:16]
    return None


def is_sscc(code: str) -> bool:
    """SSCC-короб — AI 00 + 18 цифр (всего 20 знаков, цифры)."""
    return code.startswith("00") and len(code) == 20 and code.isdigit()


# Алиас для существующих внутренних использований
_extract_gtin = extract_gtin


def _extract_serial(code: str) -> Optional[str]:
    if code.startswith("01") and len(code) >= 16:
        # После AI 01 (14 цифр GTIN) идёт AI 21 (серийный номер)
        rest = code[16:]
        if rest.startswith("21"):
            serial = rest[2:].split("\x1d")[0]  # GS1 разделитель
            return serial[:20]
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
    gtin = extract_gtin(code)
    serial = _extract_serial(code)
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


def _mock_product_name(gtin: Optional[str]) -> str:
    names = ["Молоко 3.2% 1л", "Вода минеральная 0.5л", "Сок апельсиновый 1л",
             "Кефир 2.5% 900г", "Йогурт клубничный 150г"]
    if not gtin:
        return random.choice(names)
    idx = int(gtin[-1]) % len(names) if gtin else 0
    return names[idx]
