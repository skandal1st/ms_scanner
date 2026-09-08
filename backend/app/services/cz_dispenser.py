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
from datetime import datetime, timedelta
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

# До какого возраста переиспользуем уже готовую выгрузку ЧЗ, не создавая новую.
# Генерация в ЧЗ медленная (минуты, иногда десятки) — свежий готовый результат
# отдаём сразу, чтобы не ждать и не плодить задачи.
DEFAULT_REUSE_AGE_S = 2 * 3600


def _parse_dispenser_ts(s: Optional[str]) -> Optional[datetime]:
    """createDate задачи ЧЗ ('2026-09-08T12:32:36.947') → naive UTC datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "").split("+")[0])
    except ValueError:
        return None


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


class CzDispenserPending(CzDispenserError):
    """Выгрузка ещё формируется на стороне ЧЗ (не готова за отведённое время).

    Отличается от CzDispenserError: не ошибка токена/договора, а «зайдите позже» —
    задача продолжает генерироваться в ЧЗ и будет переиспользована следующим прогоном.
    """


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

    async def _tasks(self, pg_code: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/tasks",
                headers=self.headers,
                params={"page": 0, "size": 100, "pg": pg_code},
            )
        return r.json().get("list", []) if r.status_code == 200 else []

    async def _results(self, pg_code: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/results",
                headers=self.headers,
                params={"page": 0, "size": 100, "pg": pg_code},
            )
        return r.json().get("list", []) if r.status_code == 200 else []

    async def _task_status(self, task_id: str, pg_code: int) -> Optional[str]:
        for t in await self._tasks(pg_code):
            if t.get("id") == task_id:
                return t.get("currentStatus")
        return None

    async def _result_id(self, task_id: str, pg_code: int) -> Optional[str]:
        for x in await self._results(pg_code):
            if (
                x.get("taskId") == task_id
                and x.get("downloadStatus") == "SUCCESS"
                and x.get("available") == "AVAILABLE"
            ):
                return x.get("id")
        return None

    def _own_fresh_tasks(
        self, tasks: list[dict], pg_code: int, inn: str, max_age_s: int
    ) -> list[dict]:
        """Наши задачи FILTERED_CIS_REPORT по группе/ИНН не старше max_age_s.

        Строгий фильтр обязателен: среди результатов ЧЗ бывают чужие отчёты (напр.
        VIOLATIONS) — их нельзя парсить как выгрузку остатка.
        """
        cutoff = datetime.utcnow() - timedelta(seconds=max_age_s)
        out: list[dict] = []
        for t in tasks:
            if t.get("name") != "FILTERED_CIS_REPORT":
                continue
            if inn and str(t.get("orgInn") or "") != inn:
                continue
            if str(t.get("productGroupCode") or "") != str(pg_code):
                continue
            ts = _parse_dispenser_ts(t.get("createDate"))
            if ts is None or ts < cutoff:
                continue
            out.append(t)
        return out

    async def find_reusable_result(
        self, pg_string: str, inn: str, *, max_age_s: int = DEFAULT_REUSE_AGE_S
    ) -> Optional[str]:
        """result_id недавней ГОТОВОЙ (SUCCESS+AVAILABLE) выгрузки нашей группы/ИНН.

        Позволяет отдать остаток сразу, не создавая новую задачу и не ожидая
        медленную генерацию ЧЗ.
        """
        code = CZ_PG_STRING_TO_CODE.get(pg_string)
        if code is None:
            return None
        fresh_ids = {
            t.get("id")
            for t in self._own_fresh_tasks(await self._tasks(code), code, inn, max_age_s)
        }
        if not fresh_ids:
            return None
        for x in await self._results(code):
            if (
                x.get("taskId") in fresh_ids
                and x.get("downloadStatus") == "SUCCESS"
                and x.get("available") == "AVAILABLE"
            ):
                logger.info(
                    "dispenser.reuse_result", pg=pg_string, task_id=x.get("taskId")
                )
                return x.get("id")
        return None

    async def find_inflight_task(
        self, pg_string: str, inn: str, *, max_age_s: int = DEFAULT_REUSE_AGE_S
    ) -> Optional[str]:
        """task_id недавней ещё живой задачи (PREPARATION/COMPLETED) нашей группы/ИНН.

        Чтобы повторный «обновить» ждал уже идущую генерацию, а не плодил дубли.
        """
        code = CZ_PG_STRING_TO_CODE.get(pg_string)
        if code is None:
            return None
        best_id: Optional[str] = None
        best_ts: Optional[datetime] = None
        for t in self._own_fresh_tasks(await self._tasks(code), code, inn, max_age_s):
            if t.get("currentStatus") not in ("PREPARATION", "COMPLETED"):
                continue
            ts = _parse_dispenser_ts(t.get("createDate"))
            if best_ts is None or (ts is not None and ts > best_ts):
                best_ts, best_id = ts, t.get("id")
        return best_id

    async def download_result(self, pg_string: str, result_id: str) -> list[OwnerCis]:
        """Скачать и распарсить готовый результат по его id.

        Файл может кратко отдавать 403 сразу после SUCCESS (финализация на стороне
        ЧЗ) — несколько ретраев с паузой.
        """
        rf = None
        for attempt in range(6):
            async with httpx.AsyncClient(timeout=180) as c:
                rf = await c.get(
                    f"{self.base}/results/{result_id}/file", headers=self.headers
                )
            if rf.status_code == 200 and rf.content[:2] == b"PK":
                return _parse_filtered_cis_zip(rf.content, pg_string)
            logger.warning(
                "dispenser.download_retry", pg=pg_string, attempt=attempt, status=rf.status_code
            )
            await asyncio.sleep(15)
        raise CzDispenserError(
            f"export {pg_string}: скачивание не ZIP (HTTP {rf.status_code if rf else '?'})"
        )

    async def wait_and_download(
        self,
        pg_string: str,
        task_id: str,
        *,
        timeout_s: int = 1800,
        poll_s: int = 10,
    ) -> list[OwnerCis]:
        """Дождаться готового результата задачи и скачать/распарсить CSV.

        Опрашиваем именно ``/results`` (SUCCESS+AVAILABLE), а не статус ``COMPLETED``
        в ``/tasks``: готовая задача быстро уходит в ARCHIVE (результат → NOT_AVAILABLE),
        и ожидание строго по COMPLETED промахивалось мимо короткого окна скачивания.
        Если за timeout_s результат не готов — CzDispenserPending («ещё формируется»).
        """
        pg_code = CZ_PG_STRING_TO_CODE[pg_string]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            result_id = await self._result_id(task_id, pg_code)
            if result_id:
                return await self.download_result(pg_string, result_id)
            st = await self._task_status(task_id, pg_code)
            if st in ("FAILED", "CANCELED"):
                raise CzDispenserError(f"export {pg_string} task {task_id}: статус {st}")
            await asyncio.sleep(poll_s)
        raise CzDispenserPending(
            f"export {pg_string}: ЧЗ не сформировал выгрузку за {timeout_s}с"
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
