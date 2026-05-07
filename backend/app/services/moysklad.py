import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger


class MoySkladService:
    def __init__(self, token: str):
        self.token = token
        self.base_url = settings.MOYSKLAD_API_BASE
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
        }

    async def get_supplies(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Список поступлений товаров."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/supply",
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

    async def get_supply(self, supply_id: str) -> Dict[str, Any]:
        """Детали поступления."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/supply/{supply_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def create_supply(self, name: str, positions: List[Dict]) -> Dict[str, Any]:
        """Создать поступление."""
        body = {
            "name": name,
            "positions": positions,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/entity/supply",
                headers=self.headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def update_supply(self, supply_id: str, scans: List[Dict]) -> Dict[str, Any]:
        """Обновить поступление — добавить отсканированные позиции."""
        positions = [
            {
                "assortment": {"meta": {"href": f"{self.base_url}/entity/product/{s.get('product_id')}", "type": "product"}},
                "quantity": 1,
            }
            for s in scans
            if s.get("product_id")
        ]

        if not positions:
            logger.warning("moysklad.update_supply.no_positions", supply_id=supply_id)
            return {}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{self.base_url}/entity/supply/{supply_id}",
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
