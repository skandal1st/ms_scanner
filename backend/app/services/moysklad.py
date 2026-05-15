import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.chestnyznak import cis_string_for_moysklad_api, normalize_gtin_key

# Сущность КМ в Remap 1.2: см. Markirovka.md (cis при создании, cis_1162 только в ответе, codetype при GET).

# Для каких МС-документов в позиции пишем коды маркировки (trackingCodes).
# МойСклад сам валидирует CIS; отдельный ввод в оборот через API ЧЗ в приложении не делаем.
WRITE_TRACKING_CODES_KINDS = {"demand", "loss", "salesreturn"}

# Все типы МС-документов, поддерживаемые приложением.
# move (Перемещение) исключён: XSD-схема дескриптора не разрешает update
# для move через scope=custom — мы не можем записать trackingCodes в позиции.
SUPPORTED_KINDS = {"demand", "loss", "salesreturn"}


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
        rows = await self._load_positions_rows(kind, doc_id)

        plan: List[Dict[str, Any]] = []
        for pos in rows:
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
                    if raw is not None and str(raw).strip() != "":
                        raw_str = str(raw).strip()
                        if raw_str.isdigit() and len(raw_str) <= 14:
                            gtin = normalize_gtin_key(raw_str.zfill(14))
                        else:
                            gtin = normalize_gtin_key(raw_str)
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

    async def _load_positions_rows(self, kind: str, doc_id: str) -> List[Dict[str, Any]]:
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(
                f"{self.base_url}/entity/{kind}/{doc_id}/positions",
                headers=self.headers,
                params={"expand": "assortment", "limit": 1000},
            )
            resp.raise_for_status()
            return list(resp.json().get("rows", []))

    @staticmethod
    def _product_id_from_position(pos: Dict[str, Any]) -> Optional[str]:
        asrt = pos.get("assortment") or {}
        return asrt.get("id")

    @staticmethod
    def _moysklad_tracking_type_from_position(pos: Dict[str, Any]) -> Optional[str]:
        """Значение trackingType товара из позиции МС (expand=assortment)."""
        asrt = pos.get("assortment") or {}
        raw = asrt.get("trackingType") or asrt.get("tracking_type")
        if raw is None:
            return None
        if isinstance(raw, str):
            s = raw.strip()
            return s or None
        return str(raw).strip() or None

    def _position_put_payload(self, ms_row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Тело позиции для PUT документа: без «тяжёлого» expand assortment,
        с сохранением цены/НДС/id — иначе МС может не отразить КМ во вкладке маркировки.
        """
        payload: Dict[str, Any] = {}
        for key in (
            "id",
            "meta",
            "quantity",
            "price",
            "discount",
            "vat",
            "vatEnabled",
            "things",
            "pack",
            "slots",
            "slot",
        ):
            if key in ms_row:
                payload[key] = ms_row[key]

        asrt = ms_row.get("assortment") or {}
        meta = asrt.get("meta")
        if isinstance(meta, dict) and meta.get("href"):
            payload["assortment"] = {"meta": meta}
        elif asrt.get("id"):
            et = (
                meta.get("type")
                if isinstance(meta, dict) and meta.get("type")
                else "product"
            )
            payload["assortment"] = {
                "meta": {
                    "href": f"{self.base_url}/entity/{et}/{asrt['id']}",
                    "type": et,
                    "mediaType": "application/json",
                }
            }

        if ms_row.get("trackingCodes") is not None:
            payload["trackingCodes"] = ms_row["trackingCodes"]
        if ms_row.get("trackingCodes_1162") is not None:
            payload["trackingCodes_1162"] = ms_row["trackingCodes_1162"]

        return payload

    async def update_document(
        self, kind: str, doc_id: str, scans: List[Dict]
    ) -> Dict[str, Any]:
        """
        Обновить позиции МС-документа на основе сканов.
        Сканы группируются по product_id: один товар = одна позиция с quantity
        и (если не mock сервера) CIS маркировки.

        Если позиции уже есть в МС (типичный случай), КМ отправляются отдельным
        ``POST /entity/{kind}/{id}/positions/{positionId}/trackingCodes`` по
        документации МС; в ``PUT`` документа поле ``trackingCodes`` у позиций
        не передаётся (избегаем 412 из-за иного пути валидации).

        При HTTP 412 (неверный CIS) на ``PUT`` или ``POST`` trackingCodes **не**
        бросает исключение: возвращает ``{"__moysklad_412__": True, "body": "..."}``.

        В mock-режиме (`CZ_MOCK_MODE=true`) КМ в МС не шлём.
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

        pending: Dict[str, List[Dict]] = {pid: list(g) for pid, g in groups.items()}
        tc_lines = sum(len(v) for v in groups.values())
        # (position_uuid, [{cis, type}, ...]) — POST на сабресурс trackingCodes
        post_tracking_batches: list[tuple[str, list[dict[str, str]]]] = []

        try:
            ms_rows = await self._load_positions_rows(kind, doc_id)
        except Exception as exc:
            logger.warning(
                "moysklad.update_document.fetch_positions_failed",
                kind=kind,
                doc_id=doc_id,
                error=str(exc),
            )
            ms_rows = []

        positions: List[Dict[str, Any]]

        if ms_rows:
            positions = []
            for row in ms_rows:
                pid = self._product_id_from_position(row)
                payload = self._position_put_payload(row)
                if write_codes:
                    payload.pop("trackingCodes", None)
                    payload.pop("trackingCodes_1162", None)
                remaining = pending.get(pid) if pid else None
                if remaining:
                    row_cap_raw = row.get("quantity")
                    try:
                        row_cap = int(row_cap_raw) if row_cap_raw is not None else 0
                    except (TypeError, ValueError):
                        row_cap = 0
                    if row_cap < 1:
                        row_cap = len(remaining)
                    take = remaining[:row_cap]
                    del remaining[: len(take)]
                    if take:
                        payload["quantity"] = len(take)
                        if write_codes:
                            ms_tt = self._moysklad_tracking_type_from_position(row)
                            tc_batch = [
                                {
                                    "cis": cis_string_for_moysklad_api(
                                        s["code"], ms_tt
                                    ),
                                    "type": "trackingcode",
                                }
                                for s in take
                                if s.get("code")
                            ]
                            pos_row_id = row.get("id")
                            if pos_row_id and tc_batch:
                                post_tracking_batches.append(
                                    (str(pos_row_id), tc_batch)
                                )
                positions.append(payload)

            leftover = {k: v for k, v in pending.items() if v}
            if leftover:
                logger.warning(
                    "moysklad.update_document.scans_not_placed",
                    kind=kind,
                    doc_id=doc_id,
                    products=list(leftover.keys()),
                )
        else:
            # Нет позиций из МС — старый путь (новый/пустой документ)
            positions = []
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
                    position["trackingCodes"] = [
                        {
                            "cis": cis_string_for_moysklad_api(s["code"]),
                            "type": "trackingcode",
                        }
                        for s in group
                        if s.get("code")
                    ]
                positions.append(position)

        async with httpx.AsyncClient(timeout=30) as client:
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
                # 412 обрабатывает воркер (исключение из Celery доходит до пользователя).
                if resp.status_code == 412:
                    return {"__moysklad_412__": True, "body": resp.text}
                resp.raise_for_status()

            if write_codes and ms_rows and post_tracking_batches:
                for pos_id, batch in post_tracking_batches:
                    tc_url = (
                        f"{self.base_url}/entity/{kind}/{doc_id}"
                        f"/positions/{pos_id}/trackingCodes"
                    )
                    tc_resp = await client.post(
                        tc_url,
                        headers=self.headers,
                        json=batch,
                    )
                    if tc_resp.status_code >= 400:
                        logger.error(
                            "moysklad.post_tracking_codes.failed",
                            kind=kind,
                            doc_id=doc_id,
                            position_id=pos_id,
                            status=tc_resp.status_code,
                            body=tc_resp.text[:1000],
                            batch_size=len(batch),
                        )
                        if tc_resp.status_code == 412:
                            return {
                                "__moysklad_412__": True,
                                "body": tc_resp.text,
                            }
                        tc_resp.raise_for_status()
                    logger.info(
                        "moysklad.post_tracking_codes.ok",
                        kind=kind,
                        doc_id=doc_id,
                        position_id=pos_id,
                        count=len(batch),
                    )

            logger.info(
                "moysklad.update_document.ok",
                kind=kind,
                doc_id=doc_id,
                positions_sent=len(positions),
                scans_grouped=tc_lines,
                sent_tracking_codes=write_codes,
                merged_from_ms=bool(ms_rows),
                tracking_via_post=bool(post_tracking_batches),
            )
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
