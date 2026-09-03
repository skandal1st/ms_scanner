"""Асинхронная выгрузка кодов маркировки участника из ЧЗ (сервис экспорта dispenser).

Флоу True API (раздел 8 доки): создать задачу FILTERED_CIS_REPORT → опрашивать статус →
получить resultId → скачать ZIP с CSV всех КИ, числящихся за участником (остатки в ГИС МТ).
Для инвентаризации: полный перечень марок пользователя (по товарным группам с договором).

Эндпоинты (подтверждены на проде): base = {CZ}/api/v3/true-api/dispenser
- POST /tasks                     — создать задачу, вернёт {id, currentStatus}
- GET  /tasks?page&size&pg        — список задач со статусами (COMPLETED/PREPARATION/FAILED)
- GET  /results?page&size&pg      — список результатов (downloadStatus SUCCESS + id)
- GET  /results/{resultId}/file   — ZIP с CSV

Генерация у ЧЗ асинхронная и НЕбыстрая (минуты) — опрос с запасом по времени.
"""
import asyncio
import csv
import io
import json
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger

# Товарная группа (строковый код pg) → числовой код productGroupCode (Catalog из доки True API).
CZ_PG_STRING_TO_CODE: dict[str, int] = {
    "lp": 1, "shoes": 2, "tobacco": 3, "perfumery": 4, "tires": 5, "electronics": 6,
    "milk": 8, "bicycle": 9, "wheelchairs": 10, "alcohol": 11, "otp": 12, "water": 13,
    "furs": 14, "beer": 15, "ncp": 16, "bio": 17, "antiseptic": 19, "petfood": 20,
    "seafood": 21, "nabeer": 22, "softdrinks": 23, "meat": 25, "vetpharma": 26,
    "toys": 27, "radio": 28, "titan": 31, "conserve": 32, "vegetableoil": 33,
    "opticfiber": 34, "chemistry": 35, "books": 36, "grocery": 37, "pharmaraw": 38,
    "construction": 39, "fire": 40, "heater": 41, "cableraw": 42, "autofluids": 43,
    "polymer": 44, "sweets": 45, "carparts": 48, "furslp": 49, "nicotindev": 50,
    "gadgets": 51, "frozen": 52, "fertilizers": 53, "homeware": 54,
}

# Уровни упаковки для выгрузки остатков: единица + агрегаты (блок/короб/паллета).
DEFAULT_PACKAGE_TYPES = ["UNIT", "LEVEL1", "LEVEL2", "LEVEL3", "LEVEL4"]


@dataclass
class OwnerCis:
    """Одна строка выгрузки FILTERED_CIS_REPORT."""
    cis: str
    gtin: Optional[str]
    status: Optional[str]
    package_type: Optional[str]
    product_group: Optional[str]
    owner_inn: Optional[str]
    product_name: Optional[str]


class CzDispenserError(Exception):
    pass


class CzDispenser:
    def __init__(self, token: str):
        self.token = token
        self.base = f"{settings.CZ_API_BASE_URL}/api/v3/true-api/dispenser"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    async def create_filtered_cis_task(
        self,
        pg_string: str,
        inn: str,
        *,
        package_types: Optional[list[str]] = None,
        status: str = "INTRODUCED",
    ) -> Optional[str]:
        """Создать задачу выгрузки КИ участника по товарной группе. Вернёт task_id или None.

        None — если у участника нет договора по группе (403) или группа не поддерживается.
        """
        code = CZ_PG_STRING_TO_CODE.get(pg_string)
        if code is None:
            logger.warning("dispenser.unknown_pg", pg=pg_string)
            return None
        params = json.dumps(
            {
                "participantInn": inn,
                "packageType": package_types or DEFAULT_PACKAGE_TYPES,
                "status": status,
            }
        )
        body = {
            "format": "CSV",
            "name": "FILTERED_CIS_REPORT",
            "periodicity": "SINGLE",
            "productGroupCode": str(code),
            "params": params,
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/tasks", headers=self.headers, json=body)
        if r.status_code == 403:
            logger.info("dispenser.no_contract", pg=pg_string)
            return None
        if r.status_code >= 300:
            raise CzDispenserError(f"create task {pg_string}: HTTP {r.status_code} {r.text[:200]}")
        task_id = (r.json() or {}).get("id")
        logger.info("dispenser.task_created", pg=pg_string, task_id=task_id)
        return task_id

    async def _task_status(self, task_id: str, pg_code: int) -> Optional[str]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/tasks",
                headers=self.headers,
                params={"page": 0, "size": 100, "pg": pg_code},
            )
        if r.status_code != 200:
            return None
        for t in r.json().get("list", []):
            if t.get("id") == task_id:
                return t.get("currentStatus")
        return None

    async def _result_id(self, task_id: str, pg_code: int) -> Optional[str]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/results",
                headers=self.headers,
                params={"page": 0, "size": 100, "pg": pg_code},
            )
        if r.status_code != 200:
            return None
        for x in r.json().get("list", []):
            if (
                x.get("taskId") == task_id
                and x.get("downloadStatus") == "SUCCESS"
                and x.get("available") == "AVAILABLE"
            ):
                return x.get("id")
        return None

    async def wait_and_download(
        self,
        pg_string: str,
        task_id: str,
        *,
        timeout_s: int = 1800,
        poll_s: int = 10,
    ) -> list[OwnerCis]:
        """Дождаться готовности задачи и скачать/распарсить CSV. Пусто, если не готово/ошибка."""
        pg_code = CZ_PG_STRING_TO_CODE[pg_string]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = await self._task_status(task_id, pg_code)
            if st == "COMPLETED":
                break
            if st in ("FAILED", "CANCELED"):
                raise CzDispenserError(f"export {pg_string} task {task_id}: статус {st}")
            await asyncio.sleep(poll_s)
        else:
            raise CzDispenserError(f"export {pg_string}: не дождались COMPLETED за {timeout_s}с")

        result_id = await self._result_id(task_id, pg_code)
        if not result_id:
            raise CzDispenserError(f"export {pg_string}: нет готового результата (resultId)")

        # Файл может кратко отдавать 403 сразу после SUCCESS (финализация на стороне ЧЗ) —
        # несколько ретраев с паузой.
        rf = None
        for attempt in range(6):
            async with httpx.AsyncClient(timeout=180) as c:
                rf = await c.get(f"{self.base}/results/{result_id}/file", headers=self.headers)
            if rf.status_code == 200 and rf.content[:2] == b"PK":
                return _parse_filtered_cis_zip(rf.content, pg_string)
            logger.warning(
                "dispenser.download_retry", pg=pg_string, attempt=attempt, status=rf.status_code
            )
            await asyncio.sleep(15)
        raise CzDispenserError(
            f"export {pg_string}: скачивание не ZIP (HTTP {rf.status_code if rf else '?'})"
        )


def _parse_filtered_cis_zip(zip_bytes: bytes, pg_string: str) -> list[OwnerCis]:
    """Распарсить ZIP выгрузки FILTERED_CIS_REPORT.

    Первая строка CSV — дескриптор ``Filter(...)``, вторая — заголовок с колонкой
    ``requestedCis`` (КИ), далее данные. Колонки: requestedCis, gtin, …, status,
    …, packageType, productGroup, ownerInn, productName и др.
    """
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out: list[OwnerCis] = []
    for name in z.namelist():
        with z.open(name) as f:
            text = f.read().decode("utf-8", "replace")
        lines = text.splitlines()
        # Найти строку заголовка (содержит requestedCis) — до неё строка Filter(...).
        header_idx = next(
            (i for i, ln in enumerate(lines) if "requestedCis" in ln), None
        )
        if header_idx is None:
            continue
        reader = csv.DictReader(lines[header_idx:])
        for row in reader:
            cis = (row.get("requestedCis") or "").strip()
            if not cis:
                continue
            out.append(
                OwnerCis(
                    cis=cis,
                    gtin=(row.get("gtin") or "").strip() or None,
                    status=(row.get("status") or "").strip() or None,
                    package_type=(row.get("packageType") or "").strip() or None,
                    product_group=(row.get("productGroup") or "").strip() or pg_string,
                    owner_inn=(row.get("ownerInn") or "").strip() or None,
                    product_name=(row.get("productName") or "").strip() or None,
                )
            )
    return out
