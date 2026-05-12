import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger


# Для каких МС-документов в позиции пишем коды маркировки (trackingCodes).
# МойСклад сам валидирует CIS; отдельный ввод в оборот через API ЧЗ в приложении не делаем.
WRITE_TRACKING_CODES_KINDS = {"supply", "demand", "loss", "salesreturn"}

# Все типы МС-документов, поддерживаемые приложением.
# move (Перемещение) исключён: XSD-схема дескриптора не разрешает update
# для move через scope=custom — мы не можем записать trackingCodes в позиции.
SUPPORTED_KINDS = {"supply", "demand", "loss", "salesreturn"}


class MoySkladService:
    def __init__(self, token: str):
        self.token = token
        self.base_url = settings.MOYSKLAD_API_BASE
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
        }

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"Неподдерживаемый тип документа МС: {kind}")

    # --- Универсальные методы по типу документа ---

    async def get_documents(self, kind: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Список МС-документов выбранного типа."""
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/{kind}",
                headers=self.headers,
                params={"limit": limit, "order": "moment,desc"},
            )
            resp.raise_for_status()
            data = resp.json()

        rows = data.get("rows", [])
        return [
            {
                "id": r["id"],
                # `dict.get(key, default)` возвращает default ТОЛЬКО если ключ
                # отсутствует. Если МС вернёт {"name": null} — придёт None,
                # фронт сломается на name.trim(). Поэтому `or ""`.
                "name": r.get("name") or "",
                "moment": r.get("moment"),
            }
            for r in rows
        ]

    async def get_document(self, kind: str, doc_id: str) -> Dict[str, Any]:
        """Детали МС-документа выбранного типа."""
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/{kind}/{doc_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def build_plan(self, kind: str, doc_id: str) -> List[Dict[str, Any]]:
        """
        Построить план сборки из позиций МС-документа.
        Возвращает [{gtin, product_id, product_name, expected_qty}].
        Использует expand=positions.assortment чтобы получить товары вместе
        с позициями — избегаем N+1 запросов.
        """
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self.base_url}/entity/{kind}/{doc_id}/positions",
                headers=self.headers,
                params={"expand": "assortment", "limit": 1000},
            )
            resp.raise_for_status()
            data = resp.json()

        plan: List[Dict[str, Any]] = []
        for pos in data.get("rows", []):
            asrt = pos.get("assortment") or {}
            product_id = asrt.get("id")
            if not product_id:
                # пропускаем услуги/неопределённые позиции
                continue
            product_name = asrt.get("name") or ""
            barcodes = asrt.get("barcodes") or []
            # Ищем GTIN среди штрихкодов товара (поле gtin или ean13).
            # Нормализуем до GTIN-14: сканер DataMatrix шлёт `01<14цифр>`,
            # extract_gtin извлекает 14 цифр после AI 01. Если в МС товар
            # с EAN-13 (13 цифр) — добавляем ведущий 0 (стандарт GS1).
            # Иначе план не сматчится с реальным сканом.
            gtin = None
            for bc in barcodes:
                if isinstance(bc, dict):
                    raw = bc.get("gtin") or bc.get("ean13") or bc.get("ean8")
                    if raw:
                        gtin = raw.zfill(14) if raw.isdigit() and len(raw) <= 14 else raw
                        break
            qty = pos.get("quantity") or 0
            try:
                expected_qty = int(qty)
            except (TypeError, ValueError):
                expected_qty = 0
            if expected_qty <= 0:
                continue
            plan.append(
                {
                    "gtin": gtin,
                    "product_id": product_id,
                    "product_name": product_name,
                    "expected_qty": expected_qty,
                }
            )
        return plan

    async def update_document(
        self, kind: str, doc_id: str, scans: List[Dict]
    ) -> Dict[str, Any]:
        """
        Обновить позиции МС-документа на основе сканов.
        Сканы группируются по product_id: один товар = одна позиция с quantity
        и (если не mock сервера) trackingCodes — список CIS маркировки.

        В mock-режиме (`CZ_MOCK_MODE=true`) trackingCodes не шлём: МС валидирует
        CIS через ЧЗ, фейковые коды из dev не пройдут. В проде пишем коды для
        supply и отгрузочных типов — валидность обеспечивает МойСклад.
        """
        self._validate_kind(kind)
        write_codes = kind in WRITE_TRACKING_CODES_KINDS and not settings.CZ_MOCK_MODE

        # Группировка по product_id
        groups: Dict[str, List[Dict]] = {}
        for s in scans:
            product_id = s.get("product_id")
            if not product_id:
                continue
            groups.setdefault(product_id, []).append(s)

        if not groups:
            logger.warning(
                "moysklad.update_document.no_positions",
                kind=kind,
                doc_id=doc_id,
            )
            return {}

        positions: List[Dict[str, Any]] = []
        for product_id, group in groups.items():
            position: Dict[str, Any] = {
                "assortment": {
                    "meta": {
                        "href": f"{self.base_url}/entity/product/{product_id}",
                        "type": "product",
                        "mediaType": "application/json",
                    }
                },
                "quantity": len(group),
            }
            if write_codes:
                # МС требует поле `type` у каждого trackingCode.
                # Допустимые: trackingcode | transportpack | consumerpack.
                position["trackingCodes"] = [
                    {"cis": s["code"], "type": "trackingcode"}
                    for s in group if s.get("code")
                ]
            positions.append(position)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{self.base_url}/entity/{kind}/{doc_id}",
                headers=self.headers,
                json={"positions": positions},
            )
            if resp.status_code >= 400:
                # raise_for_status() прячет тело ответа МС с реальной причиной.
                # Логируем body, чтобы не приходилось дёргать diff в проде.
                logger.error(
                    "moysklad.update_document.failed",
                    kind=kind,
                    doc_id=doc_id,
                    status=resp.status_code,
                    body=resp.text[:1000],
                    sent_codes=write_codes,
                )
                resp.raise_for_status()
            return resp.json()

    async def find_product_by_gtin(self, gtin: str) -> Optional[Dict[str, Any]]:
        """
        Точный поиск товара по штрихкоду через `assortment?filter=barcode=...`.
        - Поле фильтра — `barcode` (ед.ч.). `barcodes` (мн.ч.) даёт 412 от МС.
        - Сканер шлёт GTIN-14 (`01<14цифр>`), но в МС товар может быть заведён
          с EAN-13. По стандарту GS1 GTIN-14 = `0` + GTIN-13 для 13-значных
          штрихкодов, поэтому если поиск по 14-значному пуст — пробуем
          без ведущего нуля.
        - Endpoint `/entity/assortment` отдаёт смешанные сущности (product/
          variant/service), нас интересует только product — по нему делаем
          обновление positions[].
        """
        candidates: list[str] = [gtin]
        if len(gtin) == 14 and gtin.startswith("0"):
            candidates.append(gtin[1:])
        async with httpx.AsyncClient(timeout=15) as client:
            for value in candidates:
                resp = await client.get(
                    f"{self.base_url}/entity/assortment",
                    headers=self.headers,
                    params={"filter": f"barcode={value}", "limit": 1},
                )
                if resp.status_code != 200:
                    continue
                for row in resp.json().get("rows", []):
                    if (row.get("meta") or {}).get("type") == "product":
                        return row
        return None

    async def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Карточка товара по UUID — имя для ручной привязки КМ к позиции."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/product/{product_id}",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    # --- Алиасы для backward-совместимости старого приёмочного кода ---

    async def get_supplies(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.get_documents("supply", limit=limit)

    async def get_supply(self, supply_id: str) -> Dict[str, Any]:
        return await self.get_document("supply", supply_id)

    async def update_supply(self, supply_id: str, scans: List[Dict]) -> Dict[str, Any]:
        return await self.update_document("supply", supply_id, scans)
