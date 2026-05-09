import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger


# Для каких МС-документов в позиции нужно записывать коды маркировки
# (поле trackingCodes). Для приёмки маркировка идёт в ЧЗ (accept_batch),
# в МС достаточно факта поступления без trackingCodes.
WRITE_TRACKING_CODES_KINDS = {"demand", "loss", "move", "salesreturn"}

# Все типы МС-документов, поддерживаемые приложением.
SUPPORTED_KINDS = {"supply", "demand", "loss", "move", "salesreturn"}


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
                "name": r.get("name", ""),
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
            gtin = None
            for bc in barcodes:
                if isinstance(bc, dict):
                    gtin = bc.get("gtin") or bc.get("ean13") or bc.get("ean8")
                    if gtin:
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
        и (для отгрузочных типов) trackingCodes — список CIS маркировки.
        """
        self._validate_kind(kind)
        write_codes = kind in WRITE_TRACKING_CODES_KINDS

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
                    }
                },
                "quantity": len(group),
            }
            if write_codes:
                position["trackingCodes"] = [
                    {"cis": s["code"]} for s in group if s.get("code")
                ]
            positions.append(position)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{self.base_url}/entity/{kind}/{doc_id}",
                headers=self.headers,
                json={"positions": positions},
            )
            resp.raise_for_status()
            return resp.json()

    async def find_product_by_gtin(self, gtin: str) -> Optional[Dict[str, Any]]:
        """Поиск товара по GTIN."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/product",
                headers=self.headers,
                params={"filter": f"barcodes={gtin}"},
            )
            if resp.status_code != 200:
                return None
            rows = resp.json().get("rows", [])
            return rows[0] if rows else None

    # --- Алиасы для backward-совместимости старого приёмочного кода ---

    async def get_supplies(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.get_documents("supply", limit=limit)

    async def get_supply(self, supply_id: str) -> Dict[str, Any]:
        return await self.get_document("supply", supply_id)

    async def update_supply(self, supply_id: str, scans: List[Dict]) -> Dict[str, Any]:
        return await self.update_document("supply", supply_id, scans)
