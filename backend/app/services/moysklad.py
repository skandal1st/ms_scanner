import asyncio
from collections import deque
from typing import Awaitable, Callable
import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.customer_order_filters import moysklad_order_filter_conditions
from app.services.chestnyznak import (
    cis_string_for_moysklad_api,
    normalize_km_ai_prefix,
    normalize_gtin_key,
    parse_gs1_km_gtin_serial,
)

# Сущность КМ в Remap 1.2: см. Markirovka.md (cis при создании, cis_1162 только в ответе, codetype при GET).

# Для каких МС-документов в позиции пишем коды маркировки (trackingCodes).
# МойСклад сам валидирует CIS; отдельный ввод в оборот через API ЧЗ в приложении не делаем.
# supply — приёмка по УПД: КМ пишутся в позиции поступления (требует <supply><update/>
# в дескрипторе, см. moysklad-descriptor.xml).
WRITE_TRACKING_CODES_KINDS = {"demand", "supply"}

# Типы документов, которые приложение умеет вести (создавать/листать как Document).
# demand — отгрузка (коды в МС). supply — приёмка по УПД (коды в МС).
# loss — списание (вывод из оборота через ЧЗ, МС не пишем).
# move (Перемещение) исключён: XSD-схема дескриптора не разрешает update
# для move через scope=custom — мы не можем записать trackingCodes в позиции.
SUPPORTED_KINDS = {"demand", "loss", "supply"}


def customer_order_links(order: dict, kind: str) -> list[dict]:
    # CustomerOrder.demands also contains retaildemand references.
    links = []
    for ref in order.get("demands") or []:
        if not isinstance(ref, dict):
            continue
        meta = ref.get("meta") or {}
        ref_kind = meta.get("type")
        if not ref_kind:
            href = meta.get("href") or ""
            ref_kind = next((value for value in ("retaildemand", "demand")
                             if f"/entity/{value}/" in href), "demand" if not href else None)
        if ref_kind == kind:
            links.append(ref)
    return links


def customer_order_empty_message(order: dict) -> str:
    if customer_order_links(order, "demand"):
        return "Связанные отгрузки уже собраны или недоступны для этого рабочего места. Проверьте склад и статус отгрузки."
    if order.get("invoicesOut"):
        return "Доступных отгрузок по заказу и связанным счетам не найдено. Проверьте склад, статус и связи документов в МойСкладе."
    if customer_order_links(order, "retaildemand"):
        return "К заказу привязана розничная продажа. Для сборки нужна обычная отгрузка; сборка розничных продаж пока не поддерживается."
    return "В МойСкладе у заказа нет связанной отгрузки. Создайте отгрузку из этого заказа в МойСкладе, затем обновите список."


def entity_reference_id(ref: dict) -> str:
    return str(ref.get("id") or MoySkladService._id_from_href((ref.get("meta") or {}).get("href", "")))


def invoice_reference_ids(document: dict) -> set[str]:
    return {entity_reference_id(ref) for ref in document.get("invoicesOut") or []
            if isinstance(ref, dict) and entity_reference_id(ref)}


def shipment_matches_customer_order(shipment: dict, order: dict, order_id: str) -> bool:
    return (entity_reference_id(shipment.get("customerOrder") or {}) == order_id
            or bool(invoice_reference_ids(shipment) & invoice_reference_ids(order)))


def customer_order_direct_shipment_count(order: dict) -> Optional[int]:
    count = len(customer_order_links(order, "demand"))
    # A zero direct count does not prove absence: the shipment may be linked through an invoice.
    return count if count or not invoice_reference_ids(order) else None


class MoySkladService:
    # Ширина свежего окна для локального поиска по контрагенту/заказу (МС `search`
    # их не индексирует). Найдём совпадения среди последних N документов.
    # ВАЖНО: МС разворачивает `expand` (agent/customerOrder) только при limit ≤ 100 —
    # при 101+ имена контрагентов приходят пустыми. Поэтому строго 100.
    SEARCH_SCAN_LIMIT = 100

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

    # МС ограничивает частоту запросов (429, code 1049). При приёмке идёт много
    # обращений подряд (позиции + trackingCodes), поэтому на 429 ждём и повторяем.
    _RATE_LIMIT_DELAYS = (1.0, 2.0, 5.0, 10.0, 20.0)
    _READ_RETRY_DELAYS = (1.0, 2.0, 5.0)
    _READ_RETRY_STATUSES = {502, 503, 504}

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Повторяем 429; временные ошибки и транспортные сбои — только для GET.

        Повтор записи после таймаута/5xx может продублировать уже принятые марки.
        Вызывающий сам решает про raise_for_status/412 после исчерпания попыток.
        """
        from app.core.diagnostics import capture_request, response_body, safe_url
        method = method.upper()
        for attempt in range(len(self._RATE_LIMIT_DELAYS) + 1):
            try:
                resp = await client.request(method, url, headers=self.headers, **kwargs)
            except httpx.HTTPError as exc:
                capture_request('moysklad', method, url, kwargs.get('json'), error=type(exc).__name__)
                if (method == "GET" and isinstance(exc, httpx.TransportError)
                        and attempt < len(self._READ_RETRY_DELAYS)):
                    delay = self._READ_RETRY_DELAYS[attempt]
                    logger.warning("moysklad.read_retry", method=method, url=safe_url(url),
                                   attempt=attempt + 1, delay=delay, error=type(exc).__name__)
                    await asyncio.sleep(delay)
                    continue
                raise
            capture_request('moysklad', method, url, kwargs.get('json'), resp.status_code,
                            response_body(resp) if resp.status_code >= 400 else None)
            if resp.status_code == 429 and attempt < len(self._RATE_LIMIT_DELAYS):
                delay = self._RATE_LIMIT_DELAYS[attempt]
                event = "moysklad.rate_limited"
            elif (method == "GET" and resp.status_code in self._READ_RETRY_STATUSES
                  and attempt < len(self._READ_RETRY_DELAYS)):
                delay = self._READ_RETRY_DELAYS[attempt]
                event = "moysklad.read_retry"
            else:
                return resp
            try:
                delay = max(delay, min(60.0, float(resp.headers.get("Retry-After", 0))),
                            min(60.0, float(resp.headers.get("X-Lognex-Retry-After", 0)) / 1000))
            except ValueError:
                pass
            logger.warning(
                event,
                method=method,
                url=safe_url(url),
                status=resp.status_code,
                attempt=attempt + 1,
                delay=delay,
            )
            await resp.aclose()
            await asyncio.sleep(delay)
        return resp

    # --- Универсальные методы по типу документа ---

    async def get_documents(
        self,
        kind: str,
        limit: int = 50,
        search: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Список МС-документов выбранного типа.

        expand=customerOrder,agent — чтобы вытащить имя связанного заказа покупателя
        и контрагента для отображения в селекторе (UX: "00123 — ООО Покупатель (#00045)").

        search — поиск по номеру / связанному заказу / контрагенту, **регистронезависимо**.
        Полнотекстовый `search` МС ищет только по полям самого документа (номер/описание)
        и НЕ индексирует имя связанного контрагента/заказа. Поэтому: серверный `search`
        оставляем ради поиска по номеру за пределами свежего окна, а совпадения по
        контрагенту/заказу добираем локальной фильтрацией свежей выборки (casefold).
        """
        self._validate_kind(kind)
        # expand зависит от типа: поле customerOrder есть только у отгрузки (demand);
        # у поступления (supply) есть agent (поставщик), но нет customerOrder; у
        # списания (loss) нет ни того, ни другого. Лишний expand МС отклоняет (400).
        expand_by_kind = {
            "demand": "customerOrder,agent,store",
            "supply": "agent,store",
        }
        expand = expand_by_kind.get(kind)
        base_params: Dict[str, Any] = {"order": "moment,desc"}
        if organization_id:
            organization_href = (
                f"{self.base_url}/entity/organization/{organization_id}"
            )
            base_params["filter"] = f"organization={organization_href}"
        if expand:
            base_params["expand"] = expand

        async def _fetch(extra: Dict[str, Any]) -> List[Dict[str, Any]]:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base_url}/entity/{kind}",
                    headers=self.headers,
                    params={**base_params, **extra},
                )
                resp.raise_for_status()
                return resp.json().get("rows", []) or []

        q = (search or "").strip()
        if not q:
            rows = await _fetch({"limit": limit})
        else:
            # МС `search` не найдёт по контрагенту → берём серверный search (номера)
            # + широкое свежее окно и фильтруем регистронезависимо у себя.
            server_rows = await _fetch({"limit": limit, "search": q})
            recent_rows = await _fetch({"limit": self.SEARCH_SCAN_LIMIT})
            merged: Dict[str, Dict[str, Any]] = {}
            for r in server_rows + recent_rows:
                if r.get("id") and r["id"] not in merged:
                    merged[r["id"]] = r
            ql = q.casefold()

            def _matches(r: Dict[str, Any]) -> bool:
                order = r.get("customerOrder") or {}
                agent = r.get("agent") or {}
                haystack = " ".join(
                    s
                    for s in (
                        r.get("name") or "",
                        order.get("name") if isinstance(order, dict) else "",
                        agent.get("name") if isinstance(agent, dict) else "",
                    )
                    if s
                )
                return ql in haystack.casefold()

            rows = sorted(
                (r for r in merged.values() if _matches(r)),
                key=lambda r: r.get("moment") or "",
                reverse=True,
            )

        out: List[Dict[str, Any]] = []
        for r in rows:
            order = r.get("customerOrder") or {}
            order_name = order.get("name") if isinstance(order, dict) else None
            agent = r.get("agent") or {}
            agent_name = agent.get("name") if isinstance(agent, dict) else None
            store = r.get("store") or {}
            out.append(
                {
                    "id": r["id"],
                    # `dict.get(key, default)` возвращает default ТОЛЬКО если ключ
                    # отсутствует. Если МС вернёт {"name": null} — придёт None,
                    # фронт сломается на name.trim(). Поэтому `or ""`.
                    "name": r.get("name") or "",
                    "moment": r.get("moment"),
                    "customer_order_name": order_name or None,
                    "agent_name": agent_name or None,
                    "store_id": self._id_from_href(
                        ((store.get("meta") or {}).get("href") or "")
                    ) if isinstance(store, dict) else None,
                    "store_name": (store.get("name") or None) if isinstance(store, dict) else None,
                }
            )
        return out

    async def get_customer_orders(self, organization_id: Optional[str], search: Optional[str] = None,
                                  limit: int = 50, offset: int = 0, *, order_filter: Optional[dict] = None) -> list[dict]:
        params = {"limit": min(limit, 100), "offset": offset, "order": "moment,desc", "expand": "agent,store,state"}
        filters = []
        if organization_id:
            filters.append(f"organization={self.base_url}/entity/organization/{organization_id}")
        if order_filter:
            filters.extend(moysklad_order_filter_conditions(self.base_url, order_filter))
        if filters:
            params["filter"] = ";".join(filters)
        if search and search.strip():
            params["search"] = search.strip()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await self._request_with_retry(client, "GET", f"{self.base_url}/entity/customerorder", params=params)
            response.raise_for_status()
            return response.json().get("rows", [])

    async def get_customer_order_filter_options(self) -> dict:
        result = {"projects": [], "sale_values": [], "states": [], "sale_attribute_id": None,
                  "sale_dictionary_id": None, "marking_values": [], "marking_attribute_id": None, "marking_dictionary_id": None,
                  "delivery_values": [], "delivery_attribute_id": None, "delivery_dictionary_id": None, "warnings": []}
        async with httpx.AsyncClient(timeout=15) as client:
            async def dictionary(path):
                rows, offset = [], 0
                while True:
                    response = await self._request_with_retry(client, "GET", f"{self.base_url}/{path}",
                                                              params={"limit": 1000, "offset": offset})
                    response.raise_for_status()
                    page = response.json().get("rows", [])
                    rows.extend(page)
                    if len(page) < 1000:
                        return rows
                    offset += len(page)
            try:
                result["projects"] = [{"id": row["id"], "name": row["name"]}
                                      for row in await dictionary("entity/project") if not row.get("archived")]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403:
                    raise
                result["warnings"].append("Нет прав на просмотр проектов. Обновите XML решения и переустановите его в МойСкладе.")
            try:
                response = await self._request_with_retry(client, "GET", f"{self.base_url}/entity/customerorder/metadata")
                response.raise_for_status()
                result["states"] = [{"id": row["id"], "name": row["name"]}
                                    for row in response.json().get("states", [])]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403:
                    raise
                result["warnings"].append("Нет прав на просмотр статусов заказов покупателей в МойСкладе.")
            attrs = await dictionary("entity/customerorder/metadata/attributes")
            for prefix, title in (("sale", "Где продажа"), ("marking", "Маркировка"), ("delivery", "Тип доставки")):
                matches = [row for row in attrs if (row.get("name") or "").strip().casefold() == title.casefold()]
                if len(matches) != 1 or matches[0].get("type") != "customentity":
                    result["warnings"].append(f"В заказах покупателей нужно одно поле «{title}» типа «Пользовательский справочник».")
                    continue
                attr = matches[0]
                dictionary_id = self._id_from_href((attr.get("customEntityMeta") or {}).get("href", ""))
                result[prefix + "_attribute_id"] = attr["id"]
                result[prefix + "_dictionary_id"] = dictionary_id
                try:
                    result[prefix + "_values"] = [{"id": row["id"], "name": row["name"]}
                        for row in await dictionary(f"entity/customentity/{dictionary_id}") if not row.get("archived")]
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 403:
                        raise
                    result["warnings"].append(f"Нет прав на справочник «{title}». Обновите XML решения и переустановите его в МойСкладе.")
        return result

    async def get_customer_order(self, order_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await self._request_with_retry(client, "GET", f"{self.base_url}/entity/customerorder/{order_id}",
                                                      params={"expand": "agent,store"})
            response.raise_for_status()
            return response.json()

    async def get_customer_order_demands(self, order_id: str, organization_id: Optional[str],
                                        *, order: Optional[dict] = None) -> list[dict]:
        # Remap does not support demand?filter=customerOrder=... . Use the order's linked demand IDs.
        order = order if order is not None else await self.get_customer_order(order_id)
        references = customer_order_links(order, "demand")
        ids = list(dict.fromkeys(ref.get("id") or self._id_from_href((ref.get("meta") or {}).get("href", ""))
                                 for ref in references if isinstance(ref, dict)))
        ids = [doc_id for doc_id in ids if doc_id]
        rows = []
        async with httpx.AsyncClient(timeout=15) as client:
            invoice_ids = invoice_reference_ids(order)
            if invoice_ids:
                # invoiceout itself needs additional Vendor permissions; demand already exposes
                # invoicesOut. Scan the customer's shipments with full pagination, not a recent window.
                filters = []
                if organization_id:
                    filters.append(f"organization={self.base_url}/entity/organization/{organization_id}")
                agent_href = ((order.get("agent") or {}).get("meta") or {}).get("href")
                if agent_href:
                    filters.append(f"agent={agent_href}")
                offset = 0
                while True:
                    params = {"limit": 1000, "offset": offset, "order": "moment,desc"}
                    if filters:
                        params["filter"] = ";".join(filters)
                    response = await self._request_with_retry(client, "GET", f"{self.base_url}/entity/demand", params=params)
                    response.raise_for_status()
                    page = response.json().get("rows", [])
                    ids.extend(row["id"] for row in page if row.get("id")
                               and invoice_ids & invoice_reference_ids(row))
                    if len(page) < 1000:
                        break
                    offset += len(page)
                ids = list(dict.fromkeys(ids))
            for start in range(0, len(ids), 100):
                filters = [f"id={doc_id}" for doc_id in ids[start:start + 100]]
                if organization_id:
                    filters.append(f"organization={self.base_url}/entity/organization/{organization_id}")
                response = await self._request_with_retry(client, "GET", f"{self.base_url}/entity/demand", params={
                    "filter": ";".join(filters), "expand": "agent,store", "order": "moment,desc",
                    "limit": 100,
                })
                response.raise_for_status()
                rows.extend(response.json().get("rows", []))
        return sorted(rows, key=lambda row: row.get("moment") or "", reverse=True)

    async def get_document(self, kind: str, doc_id: str) -> Dict[str, Any]:
        """Детали МС-документа выбранного типа."""
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/{kind}/{doc_id}",
                headers=self.headers,
                params={'expand': 'agent,customerOrder'} if kind == 'demand' else None,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_organizations(self) -> List[Dict[str, Any]]:
        """Юрлица аккаунта МС для настройки профилей."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await self._request_with_retry(
                client,
                "GET",
                f"{self.base_url}/entity/organization",
                params={"limit": 1000},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {"id": row.get("id"), "name": row.get("name") or "Без названия"}
            for row in data.get("rows", [])
            if row.get("id")
        ]

    @staticmethod
    def _gtin_from_barcode_obj(bc: Any) -> Optional[str]:
        """barcode-объект МС ({gtin|ean13|ean8: ...}) → нормализованный GTIN-14 key или None.

        Нормализуем до GTIN-14: сканер DataMatrix шлёт `01<14цифр>`. Если в МС товар
        с EAN-13 (13 цифр) — добавляем ведущий 0 (стандарт GS1), иначе скан не сматчится.
        """
        if not isinstance(bc, dict):
            return None
        raw = bc.get("gtin") or bc.get("ean13") or bc.get("ean8")
        if raw is None or str(raw).strip() == "":
            return None
        raw_str = str(raw).strip()
        if raw_str.isdigit() and len(raw_str) <= 14:
            return normalize_gtin_key(raw_str.zfill(14))
        return normalize_gtin_key(raw_str)

    async def build_plan(self, kind: str, doc_id: str) -> List[Dict[str, Any]]:
        """
        Построить план сборки из позиций МС-документа.
        Возвращает [{gtin, article, code, product_id, product_name, expected_qty, pack_gtins}].
        `pack_gtins` — GTIN'ы упаковок товара (блок/короб) для резолва на тот же товар.
        `article`/`code` — для сопоставления коробных (SSCC) позиций УПД по КодТов.
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
            # Ищем GTIN среди штрихкодов базовой единицы товара (поле gtin или ean13).
            gtins: List[str] = []
            for bc in (asrt.get("barcodes") or []):
                value = self._gtin_from_barcode_obj(bc)
                if value and value not in gtins:
                    gtins.append(value)
            gtin = gtins[0] if gtins else None
            # Штрихкоды упаковок товара (раздел «Упаковки» в МС: блок/короб со своим
            # GTIN). Их КМ в УПД должны лечь в тот же товар — индексируем как доп.
            # ключи резолва. Структура МС: packs:[{id, quantity, uom, barcodes:[{...}]}].
            pack_gtins: List[str] = []
            pack_quantities: Dict[str, int] = {}
            for pk in (asrt.get("packs") or []):
                try:
                    pack_qty = float(pk.get("quantity") or 0)
                except (ValueError, TypeError):
                    pack_qty = 0
                for bc in (pk.get("barcodes") or []):
                    g = self._gtin_from_barcode_obj(bc)
                    if g and g != gtin and g not in pack_gtins:
                        pack_gtins.append(g)
                    if g and g not in gtins and pack_qty > 1 and pack_qty.is_integer():
                        if g in pack_quantities and pack_quantities[g] != int(pack_qty):
                            pack_quantities[g] = 0  # conflicting packaging metadata
                        else:
                            pack_quantities[g] = int(pack_qty)
            qty = pos.get("quantity") or 0
            try:
                expected_qty = int(qty)
            except (TypeError, ValueError):
                expected_qty = 0
            if expected_qty <= 0:
                continue
            # Маркированность товара по trackingType МС: пусто/NOT_TRACKED —
            # немаркированный (собирается сканом штрихкода, без КМ и ЧЗ).
            tt = (asrt.get("trackingType") or "").strip()
            marked = bool(tt) and tt != "NOT_TRACKED"
            plan.append(
                {
                    "gtin": gtin,
                    "gtins": gtins,
                    # Артикул/код товара МС — для сопоставления коробных (SSCC)
                    # позиций УПД по КодТов, когда у позиции нет своего GTIN.
                    "article": (asrt.get("article") or "").strip() or None,
                    "code": (asrt.get("code") or "").strip() or None,
                    "product_id": product_id,
                    "product_name": product_name,
                    "expected_qty": expected_qty,
                    "pack_gtins": pack_gtins,
                    "pack_quantities": pack_quantities,
                    "marked": marked,
                }
            )
        return plan

    async def _load_positions_rows(self, kind: str, doc_id: str) -> List[Dict[str, Any]]:
        self._validate_kind(kind)
        rows = []
        async with httpx.AsyncClient(timeout=30) as client:
            offset = 0
            while True:
                resp = await self._request_with_retry(
                    client, "GET", f"{self.base_url}/entity/{kind}/{doc_id}/positions",
                    params={"expand": "assortment", "limit": 100, "offset": offset, "codetype": "gs1"},
                )
                resp.raise_for_status()
                page = resp.json().get("rows", [])
                rows.extend(page)
                if len(page) < 100:
                    return rows
                offset += len(page)

    async def _load_tracking_codes(self, client, kind, doc_id, position_id):
        codes = []
        offset = 0
        while True:
            resp = await self._request_with_retry(
                client, "GET", f"{self.base_url}/entity/{kind}/{doc_id}/positions/{position_id}/trackingCodes",
                params={"codetype": "gs1", "limit": 100, "offset": offset},
            )
            resp.raise_for_status()
            payload = resp.json()
            page = payload if isinstance(payload, list) else payload.get("rows", [])
            codes.extend(page)
            if len(page) < 100:
                return codes
            offset += len(page)

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

    @staticmethod
    def _scan_units(s: Dict[str, Any]) -> int:
        """Сколько единиц товара представляет скан: короб/штрихкод = quantity, иначе 1."""
        return int(s.get("quantity") or 0) or 1

    def _tracking_code_entry(
        self, s: Dict[str, Any], ms_tracking_type: Optional[str]
    ) -> Dict[str, str]:
        """trackingCode для МС: SSCC-короб → transportpack (МС резолвит состав через ЧЗ),
        прочие агрегаты и штучный КМ → trackingcode.

        Только SSCC (AI ``00`` + 18 цифр) — транспортная упаковка. Групповые коды с
        GS1 AI ``02`` (``02``+GTIN, напр. коробки Chabacco) МС принимает как ОБЫЧНУЮ
        марку (1 код = короб, «кол-во кодов может отличаться»), а не transportpack —
        отправляем их СЫРЫМИ (нормализация ломает не-``01`` формат) как trackingcode."""
        code = (s.get("code") or "").strip()
        if s.get("is_box"):
            if code.startswith("00") and len(code) == 20 and code.isdigit():
                return {"cis": code, "type": "transportpack"}
            return {"cis": code, "type": "trackingcode"}
        return {
            "cis": cis_string_for_moysklad_api(s["code"], ms_tracking_type),
            "type": "trackingcode",
        }

    @staticmethod
    def _cis_dedup_key(cis: Optional[str]) -> str:
        """Канонический ключ КМ для дедупа: GTIN|серия, независимо от формы записи.

        Один и тот же код МС может хранить «голым» (``<GTIN><серия>``) и отдавать на
        чтении в gs1-форме (``01<GTIN>21<серия>``) — сравнение по сырой строке их не
        свяжет. Приводим к (GTIN, серия) через тот же парсер, что и остальной поток.
        """
        g, s = parse_gs1_km_gtin_serial(normalize_km_ai_prefix((cis or "").strip()))
        if g and s:
            return f"{g}|{s.strip()}"
        return (cis or "").strip()

    def _tracking_batch(
        self,
        scans: List[Dict[str, Any]],
        ms_tracking_type: Optional[str],
        seen_cis: set,
        doc_id: str,
    ) -> List[Dict[str, str]]:
        """trackingCodes для позиции с отбросом повторяющихся кодов.

        МойСклад отклоняет ВЕСЬ документ, если один и тот же КМ встречается дважды
        («в документе несколько одинаковых кодов …») — как внутри нашей отправки, так и
        относительно уже записанных в позицию кодов (повторная отгрузка). Сравниваем по
        каноническому ключу GTIN|серия; ``seen_cis`` общий на весь документ и заранее
        засеян кодами, которые уже лежат в позициях МС.
        """
        out: List[Dict[str, str]] = []
        for s in scans:
            if not s.get("code") or s.get("is_barcode"):
                continue
            entry = self._tracking_code_entry(s, ms_tracking_type)
            cis = (entry.get("cis") or "").strip()
            key = self._cis_dedup_key(cis)
            if not key or key in seen_cis:
                if key:
                    logger.info(
                        "moysklad.update_document.dup_cis_skipped",
                        doc_id=doc_id,
                        cis=cis[:60],
                    )
                continue
            seen_cis.add(key)
            out.append(entry)
        return out

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

    TRACKING_BATCH_SIZE = 500

    async def get_shipment_states(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._request_with_retry(client, 'GET', f'{self.base_url}/entity/demand/metadata')
            response.raise_for_status()
            return [{'id': row['id'], 'name': row['name']} for row in response.json().get('states', [])]

    async def get_customer_order_states(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._request_with_retry(client, 'GET', f'{self.base_url}/entity/customerorder/metadata')
            response.raise_for_status()
            return [{'id': row['id'], 'name': row['name']} for row in response.json().get('states', [])]

    async def resolve_shipment_order(self, shipment: dict, order_id: Optional[str]) -> Optional[str]:
        candidate = order_id or entity_reference_id(shipment.get('customerOrder') or {})
        if candidate:
            order = await self.get_customer_order(candidate)
            if not shipment_matches_customer_order(shipment, order, candidate):
                raise ValueError('Связь отгрузки с заказом покупателя изменилась. Откройте отгрузку из правильного заказа заново.')
            return candidate
        if not invoice_reference_ids(shipment):
            return None
        # Invoice chains have no direct customerOrder on demand. Never guess by document name.
        filters = [f'{key}={shipment[key]["meta"]["href"]}' for key in ('agent', 'organization')
                   if (shipment.get(key) or {}).get('meta', {}).get('href')]
        matches, offset = set(), 0
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                response = await self._request_with_retry(client, 'GET', f'{self.base_url}/entity/customerorder',
                    params={'limit': 1000, 'offset': offset, **({'filter': ';'.join(filters)} if filters else {})})
                response.raise_for_status()
                rows = response.json().get('rows', [])
                matches.update(row['id'] for row in rows if shipment_matches_customer_order(shipment, row, row['id']))
                if len(matches) > 1:
                    raise ValueError('Через счёт найдены несколько заказов. Откройте отгрузку из нужного заказа покупателя.')
                if len(rows) < 1000:
                    break
                offset += len(rows)
        if not matches:
            raise ValueError('Не удалось найти заказ покупателя по связанному счёту. Откройте отгрузку из заказа покупателя.')
        return next(iter(matches))

    async def change_collection_state(self, kind: str, doc_id: str, state_id: str):
        if kind not in ('demand', 'customerorder'):
            raise ValueError('Смена статуса начала сборки доступна только для отгрузки и заказа')
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await self._request_with_retry(client, 'PUT', f'{self.base_url}/entity/{kind}/{doc_id}',
                json={'state': {'meta': {'href': f'{self.base_url}/entity/{kind}/metadata/states/{state_id}',
                                        'type': 'state', 'mediaType': 'application/json'}}})
            response.raise_for_status()
        logger.info('moysklad.collection_state.changed', kind=kind, doc_id=doc_id, state_id=state_id)

    async def update_document(
        self, kind: str, doc_id: str, scans: List[Dict],
        position_quantities: Optional[Dict[str, int]] = None,
        position_prices: Optional[Dict[str, Dict[str, Any]]] = None,
        description: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Preserve existing positions; send all codes in bounded, resumable batches.

        Never repeat an uncertain POST blindly. A subsequent attempt reads every
        saved code before sending the remainder, including partially saved batches.
        """
        self._validate_kind(kind)
        write_codes = kind in WRITE_TRACKING_CODES_KINDS and not settings.CZ_MOCK_MODE
        groups = {}
        for scan in scans:
            pid = scan.get("product_id")
            if not pid:
                raise ValueError("Не все марки сопоставлены с товарами МойСклад")
            groups.setdefault(pid, []).append(scan)
        if not groups:
            raise ValueError("Нет товаров для отправки в МойСклад")

        # A read failure must never be treated as an empty document.
        ms_rows = await self._load_positions_rows(kind, doc_id)
        pending = {pid: deque(group) for pid, group in groups.items()}
        last_row = {self._product_id_from_position(row): i for i, row in enumerate(ms_rows)}
        positions = []
        allocated = []
        for i, row in enumerate(ms_rows):
            pid = self._product_id_from_position(row)
            payload = self._position_put_payload(row)
            payload.pop("trackingCodes", None)
            payload.pop("trackingCodes_1162", None)
            remaining = pending.get(pid)
            take = []
            if remaining:
                cap = max(1, int(row.get("quantity") or 0))
                units = 0
                # The final row of a product receives overflow as well.
                while remaining and (units < cap or last_row.get(pid) == i):
                    item = remaining.popleft()
                    take.append(item)
                    units += self._scan_units(item)
                payload["quantity"] = units
            pp = (position_prices or {}).get(pid) or {}
            if pp.get("price") is not None:
                payload["price"] = int(round(float(pp["price"]) * 100))
            if pp.get("vat") is not None:
                payload.update(vat=int(pp["vat"]), vatEnabled=True)
            positions.append(payload)
            allocated.append((row, take))

        new_products = {}
        for pid, remaining in pending.items():
            if not remaining:
                continue
            group = list(remaining)
            units = sum(self._scan_units(item) for item in group)
            qty = max(units, int((position_quantities or {}).get(pid) or 0))
            payload = {"assortment": {"meta": {
                "href": f"{self.base_url}/entity/product/{pid}",
                "type": "product", "mediaType": "application/json",
            }}, "quantity": qty}
            pp = (position_prices or {}).get(pid) or {}
            if pp.get("price") is not None:
                payload["price"] = int(round(float(pp["price"]) * 100))
            if pp.get("vat") is not None:
                payload.update(vat=int(pp["vat"]), vatEnabled=True)
            positions.append(payload)
            new_products[pid] = group
        if len(positions) > 1000:
            raise ValueError("В документе больше 1000 позиций. Разделите его на несколько документов МойСклад.")

        body = {"positions": positions}
        if description:
            body["description"] = description
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
            if kind == 'demand':
                from app.services.shipment_guard import ensure_active_shipment
                response = await self._request_with_retry(client, 'GET', f'{self.base_url}/entity/demand/{doc_id}')
                response.raise_for_status()
                ensure_active_shipment(response.json())
            # Read before mutation: a failed code read also aborts safely.
            seen = set()
            if write_codes:
                for row in ms_rows:
                    if not row.get("id"):
                        raise ValueError("МойСклад не вернул идентификатор позиции")
                    for tc in await self._load_tracking_codes(client, kind, doc_id, row["id"]):
                        key = self._cis_dedup_key(tc.get("cis") or tc.get("cis_1162"))
                        if key:
                            seen.add(key)
            batches = []
            for row, group in allocated:
                codes = self._tracking_batch(group, self._moysklad_tracking_type_from_position(row), seen, doc_id) if write_codes else []
                if codes:
                    batches.append((row["id"], codes))

            resp = await self._request_with_retry(client, "PUT", f"{self.base_url}/entity/{kind}/{doc_id}", json=body)
            if resp.status_code == 412:
                return {"__moysklad_412__": True, "body": resp.text}
            resp.raise_for_status()
            # New positions are created without an unbounded embedded code array.
            if write_codes and new_products:
                refreshed = await self._load_positions_rows(kind, doc_id)
                for pid, group in new_products.items():
                    row = next((r for r in refreshed if self._product_id_from_position(r) == pid), None)
                    if not row or not row.get("id"):
                        raise ValueError("Не удалось найти созданную позицию МойСклад. Повторите отправку.")
                    codes = self._tracking_batch(group, self._moysklad_tracking_type_from_position(row), seen, doc_id)
                    if codes:
                        batches.append((row["id"], codes))
            total = sum(len(codes) for _, codes in batches)
            sent = 0
            if on_progress:
                await on_progress(sent, total)
            for pos_id, codes in batches:
                for offset in range(0, len(codes), self.TRACKING_BATCH_SIZE):
                    batch = codes[offset:offset + self.TRACKING_BATCH_SIZE]
                    tc_resp = await self._request_with_retry(
                        client, "POST", f"{self.base_url}/entity/{kind}/{doc_id}/positions/{pos_id}/trackingCodes", json=batch,
                    )
                    if tc_resp.status_code == 412:
                        return {"__moysklad_412__": True, "body": tc_resp.text}
                    tc_resp.raise_for_status()
                    sent += len(batch)
                    if on_progress:
                        await on_progress(sent, total)
                    logger.info("moysklad.tracking_batch.sent", kind=kind, doc_id=doc_id, sent=sent, total=total)
            logger.info("moysklad.update_document.ok", kind=kind, doc_id=doc_id, positions_sent=len(positions), codes_sent=sent)
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
                resp = await self._request_with_retry(
                    client,
                    "GET",
                    f"{self.base_url}/entity/assortment",
                    params={"filter": f"barcode={value}", "limit": 1},
                )
                if resp.status_code != 200:
                    continue
                for row in resp.json().get("rows", []):
                    if (row.get("meta") or {}).get("type") == "product":
                        return row
        return None

    async def search_products(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Поиск товаров в каталоге МС по строке (name/article/code).

        Используется при ручном сопоставлении скана с неизвестным GTIN: кладовщик
        вводит фрагмент названия, бэк отдаёт совпадения.

        ВАЖНО: параметр `search` на агрегированном `/entity/assortment` МС молча
        игнорирует (всегда отдаёт первую страницу без фильтрации). Полнотекстовый
        поиск по словам работает на `/entity/product` — его и используем.
        """
        query = (query or "").strip()
        if not query:
            return []
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/product",
                headers=self.headers,
                params={"search": query, "limit": limit},
            )
            if resp.status_code != 200:
                logger.warning(
                    "ms.search_products.failed",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return []
            rows = resp.json().get("rows", []) or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            if (r.get("meta") or {}).get("type") != "product":
                continue
            barcodes_raw = r.get("barcodes") or []
            barcodes: List[str] = []
            for bc in barcodes_raw:
                if not isinstance(bc, dict):
                    continue
                value = bc.get("gtin") or bc.get("ean13") or bc.get("ean8") or bc.get("code128")
                if value:
                    barcodes.append(str(value))
            out.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name") or "",
                    "article": r.get("article") or "",
                    "code": r.get("code") or "",
                    "barcodes": barcodes,
                }
            )
        return out

    async def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Карточка товара по UUID — имя для ручной привязки КМ к позиции.

        Через _request_with_retry: при импорте УПД имя освежается для КАЖДОГО
        сопоставленного GTIN (десятки последовательных запросов), поэтому без
        ретрая МС отдаёт 429 (code 1049) и обновление имени тихо срывается.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await self._request_with_retry(
                client,
                "GET",
                f"{self.base_url}/entity/product/{product_id}",
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def add_gtin_barcode_to_product(self, product_id: str, gtin: str) -> bool:
        """Дописать GTIN в штрихкоды товара МС — чтобы будущие сканы этого GTIN
        матчились автоматически через find_product_by_gtin (filter=barcode=...),
        и кладовщику не приходилось сопоставлять повторно.

        Идемпотентно: если штрихкод уже есть (с учётом ведущего нуля GTIN-14↔EAN-13),
        ничего не делает. Best-effort — при ошибке возвращает False, не бросает.
        PUT в МС — частичное обновление, но массив barcodes заменяется целиком,
        поэтому отправляем существующие + новый.
        """
        g = (gtin or "").strip()
        if not g or not g.isdigit():
            return False
        variants = {g}
        if len(g) == 14 and g.startswith("0"):
            variants.add(g[1:])
        if len(g) == 13:
            variants.add("0" + g)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/product/{product_id}",
                headers=self.headers,
            )
            if resp.status_code != 200:
                logger.warning(
                    "ms.add_barcode.get_failed",
                    status=resp.status_code,
                    product_id=product_id,
                )
                return False
            barcodes = resp.json().get("barcodes") or []
            for bc in barcodes:
                if isinstance(bc, dict) and any(str(v) in variants for v in bc.values()):
                    return True  # уже привязан
            key = "gtin" if len(g) == 14 else "ean13"
            new_barcodes = list(barcodes) + [{key: g}]
            put = await client.put(
                f"{self.base_url}/entity/product/{product_id}",
                headers=self.headers,
                json={"barcodes": new_barcodes},
            )
            if put.status_code not in (200, 201):
                logger.warning(
                    "ms.add_barcode.put_failed",
                    status=put.status_code,
                    body=put.text[:300],
                    product_id=product_id,
                )
                return False
            logger.info("ms.add_barcode.ok", product_id=product_id, gtin=g)
            return True

    async def remove_gtin_barcode_from_product(self, product_id: str, gtin: str) -> bool:
        """Убрать GTIN из штрихкодов товара МС (при смене привязки — снять со старой
        карточки). Учитывает ведущий ноль GTIN-14↔EAN-13. Best-effort: при ошибке False,
        не бросает. Если штрихкода не было — считается успехом (True)."""
        g = (gtin or "").strip()
        if not g or not g.isdigit():
            return False
        variants = {g}
        if len(g) == 14 and g.startswith("0"):
            variants.add(g[1:])
        if len(g) == 13:
            variants.add("0" + g)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/entity/product/{product_id}", headers=self.headers
            )
            if resp.status_code != 200:
                logger.warning(
                    "ms.remove_barcode.get_failed", status=resp.status_code, product_id=product_id
                )
                return False
            barcodes = resp.json().get("barcodes") or []
            kept = [
                bc for bc in barcodes
                if not (isinstance(bc, dict) and any(str(v) in variants for v in bc.values()))
            ]
            if len(kept) == len(barcodes):
                return True  # такого штрихкода нет — нечего убирать
            put = await client.put(
                f"{self.base_url}/entity/product/{product_id}",
                headers=self.headers,
                json={"barcodes": kept},
            )
            if put.status_code not in (200, 201):
                logger.warning(
                    "ms.remove_barcode.put_failed",
                    status=put.status_code, body=put.text[:300], product_id=product_id,
                )
                return False
            logger.info("ms.remove_barcode.ok", product_id=product_id, gtin=g)
            return True

    # --- Инвентаризация: склады и учётный остаток ---

    @staticmethod
    def _id_from_href(href: str) -> str:
        """UUID сущности из meta.href (…/entity/<type>/<uuid>[?params])."""
        href = (href or "").split("?", 1)[0]
        return href.rstrip("/").rsplit("/", 1)[-1]

    async def get_stores(self) -> List[Dict[str, Any]]:
        """Список складов МС ({id, name, href}) — для карты «наши склады» инвентаризации.

        В custom scope право на `/entity/store` часто закрыто (403, code 1016). Тогда
        собираем склады из документов (demand/supply, expand=store) — на них право есть.
        Мультиюрлицо: разные склады = разные юрлица, кладовщик выбирает свой."""
        stores: Dict[str, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=30) as client:
            # Прямой справочник складов — если право есть (полнее и включает пустые склады).
            resp = await self._request_with_retry(
                client, "GET", f"{self.base_url}/entity/store", params={"limit": 1000}
            )
            if resp.status_code == 200:
                for r in resp.json().get("rows", []) or []:
                    href = (r.get("meta") or {}).get("href", "")
                    if href:
                        stores[href] = {"id": r.get("id") or self._id_from_href(href),
                                        "name": r.get("name") or "", "href": href}
                return list(stores.values())
            # Фолбэк: склады из недавних документов (expand=store требует limit ≤ 100).
            for ent in ("demand", "supply"):
                resp = await self._request_with_retry(
                    client, "GET", f"{self.base_url}/entity/{ent}",
                    params={"expand": "store", "limit": 100, "order": "moment,desc"},
                )
                if resp.status_code != 200:
                    continue
                for row in resp.json().get("rows", []) or []:
                    st = row.get("store") or {}
                    href = (st.get("meta") or {}).get("href", "")
                    if href and href not in stores:
                        stores[href] = {"id": self._id_from_href(href),
                                        "name": st.get("name") or "", "href": href}
        return list(stores.values())

    async def get_stock_map(self, store_hrefs: List[str], *, group_by: str = 'product', full_snapshot: bool = False) -> Dict[str, Dict[str, Any]]:
        """Учётный остаток по товарам (report/stock/all, groupBy=product) по складам.

        Возвращает {product_id: {qty, folder_id, folder_name}}. Пустой store_hrefs = все
        склады. Остаток по нескольким складам агрегируется (периметр «наших складов»,
        мультиюрлицо). folder — группа товаров МС («бренд» для среза инвентаризации).
        Может вернуть 403, если у токена нет права на отчёт остатков — вызывающий ловит."""
        params: Dict[str, Any] = {"groupBy": group_by, "limit": 1000}
        filters = [f"store={h}" for h in store_hrefs]
        if full_snapshot:
            filters.extend(['stockMode=all', 'quantityMode=all', 'archived=false', 'archived=true'])
        if filters:
            params["filter"] = ';'.join(filters)
        out: Dict[str, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=60) as client:
            offset = 0
            while True:
                resp = await self._request_with_retry(
                    client, "GET", f"{self.base_url}/report/stock/all",
                    params=dict(params, offset=offset),
                )
                if resp.status_code != 200:
                    logger.warning("ms.stock.failed", status=resp.status_code, body=resp.text[:300])
                    resp.raise_for_status()
                rows = resp.json().get("rows", []) or []
                for r in rows:
                    pid = self._id_from_href((r.get("meta") or {}).get("href", ""))
                    if not pid:
                        continue
                    folder = r.get("folder") or {}
                    out[pid] = {
                        "qty": r.get("stock") if r.get("stock") is not None else r.get("quantity", 0),
                        "product_name": r.get('name'),
                        "folder_id": self._id_from_href((folder.get("meta") or {}).get("href", "")) or None,
                        "folder_name": folder.get("pathName") or folder.get("name") or None,
                    }
                if len(rows) < 1000:
                    break
                offset += 1000
        return out

    async def get_products_barcode_map(self) -> Dict[str, Dict[str, Any]]:
        """Карта {GTIN-14: {id, name, folder_id, folder_name}} из каталога /entity/product.

        Один пакетный проход (пагинация 1000) вместо точечных find_product_by_gtin на каждый
        GTIN — резко меньше запросов и почти нет 429. GTIN нормализуется (13↔14) как на стороне
        ЧЗ. Бренд (папка) берётся из product.pathName / productFolder."""
        out: Dict[str, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=60) as client:
            offset = 0
            while True:
                resp = await self._request_with_retry(
                    client, "GET", f"{self.base_url}/entity/product",
                    params={"limit": 1000, "offset": offset},
                )
                resp.raise_for_status()
                rows = resp.json().get("rows", []) or []
                for r in rows:
                    folder = r.get("productFolder") or {}
                    info = {
                        "id": r.get("id"),
                        "name": r.get("name") or "",
                        "folder_id": self._id_from_href((folder.get("meta") or {}).get("href", "")) or None,
                        "folder_name": r.get("pathName") or None,
                    }
                    for bc in (r.get("barcodes") or []):
                        if not isinstance(bc, dict):
                            continue
                        for v in bc.values():
                            key = normalize_gtin_key(str(v))
                            if key:
                                out.setdefault(key, info)
                if len(rows) < 1000:
                    break
                offset += 1000
        return out

    # --- Алиасы для backward-совместимости старого приёмочного кода ---
