import asyncio
import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.chestnyznak import cis_string_for_moysklad_api, normalize_gtin_key

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
    _RATE_LIMIT_DELAYS = (0.6, 1.5, 3.0)

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """HTTP-запрос к МС с ретраем на 429 (rate limit). Прочие статусы —
        как есть; вызывающий сам решает про raise_for_status/412."""
        for attempt in range(len(self._RATE_LIMIT_DELAYS) + 1):
            resp = await client.request(method, url, headers=self.headers, **kwargs)
            if resp.status_code != 429 or attempt == len(self._RATE_LIMIT_DELAYS):
                return resp
            delay = self._RATE_LIMIT_DELAYS[attempt]
            logger.warning(
                "moysklad.rate_limited",
                method=method,
                url=url,
                attempt=attempt + 1,
                delay=delay,
            )
            await asyncio.sleep(delay)
        return resp

    # --- Универсальные методы по типу документа ---

    async def get_documents(
        self, kind: str, limit: int = 50, search: Optional[str] = None
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
            "demand": "customerOrder,agent",
            "supply": "agent",
        }
        expand = expand_by_kind.get(kind)
        base_params: Dict[str, Any] = {"order": "moment,desc"}
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
                }
            )
        return out

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
            gtin = None
            for bc in (asrt.get("barcodes") or []):
                gtin = self._gtin_from_barcode_obj(bc)
                if gtin:
                    break
            # Штрихкоды упаковок товара (раздел «Упаковки» в МС: блок/короб со своим
            # GTIN). Их КМ в УПД должны лечь в тот же товар — индексируем как доп.
            # ключи резолва. Структура МС: packs:[{id, quantity, uom, barcodes:[{...}]}].
            pack_gtins: List[str] = []
            for pk in (asrt.get("packs") or []):
                for bc in (pk.get("barcodes") or []):
                    g = self._gtin_from_barcode_obj(bc)
                    if g and g != gtin and g not in pack_gtins:
                        pack_gtins.append(g)
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
                    # Артикул/код товара МС — для сопоставления коробных (SSCC)
                    # позиций УПД по КодТов, когда у позиции нет своего GTIN.
                    "article": (asrt.get("article") or "").strip() or None,
                    "code": (asrt.get("code") or "").strip() or None,
                    "product_id": product_id,
                    "product_name": product_name,
                    "expected_qty": expected_qty,
                    "pack_gtins": pack_gtins,
                    "marked": marked,
                }
            )
        return plan

    async def _load_positions_rows(self, kind: str, doc_id: str) -> List[Dict[str, Any]]:
        self._validate_kind(kind)
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await self._request_with_retry(
                client,
                "GET",
                f"{self.base_url}/entity/{kind}/{doc_id}/positions",
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

    @staticmethod
    def _scan_units(s: Dict[str, Any]) -> int:
        """Сколько единиц товара представляет скан: короб/штрихкод = quantity, иначе 1."""
        if s.get("is_box") or s.get("is_barcode"):
            return int(s.get("quantity") or 0) or 1
        return 1

    def _tracking_code_entry(
        self, s: Dict[str, Any], ms_tracking_type: Optional[str]
    ) -> Dict[str, str]:
        """trackingCode для МС: короб → transportpack (cis = SSCC как есть, МС резолвит
        состав через ЧЗ), штучный КМ → trackingcode (нормализованный cis)."""
        if s.get("is_box"):
            return {"cis": (s.get("code") or "").strip(), "type": "transportpack"}
        return {
            "cis": cis_string_for_moysklad_api(s["code"], ms_tracking_type),
            "type": "trackingcode",
        }

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
        self,
        kind: str,
        doc_id: str,
        scans: List[Dict],
        position_quantities: Optional[Dict[str, int]] = None,
        position_prices: Optional[Dict[str, Dict[str, Any]]] = None,
        description: Optional[str] = None,
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
                        row_cap = sum(self._scan_units(s) for s in remaining)
                    # Набираем сканы по единицам (короб атомарен — берём целиком,
                    # даже если перешагнёт row_cap); quantity позиции = сумма единиц.
                    take: List[Dict[str, Any]] = []
                    units = 0
                    while remaining and units < row_cap:
                        nxt = remaining.pop(0)
                        take.append(nxt)
                        units += self._scan_units(nxt)
                    if take:
                        payload["quantity"] = units
                        if write_codes:
                            ms_tt = self._moysklad_tracking_type_from_position(row)
                            # Штрихкод немаркированного товара (is_barcode) даёт только
                            # quantity позиции — trackingCode для него не пишем.
                            tc_batch = [
                                self._tracking_code_entry(s, ms_tt)
                                for s in take
                                if s.get("code") and not s.get("is_barcode")
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
                # Кол-во позиции: приоритет — КолТов из УПД (position_quantities),
                # иначе сумма единиц по сканам (fallback на число распознанных кодов).
                qty = (position_quantities or {}).get(product_id)
                try:
                    qty = int(qty) if qty is not None else 0
                except (TypeError, ValueError):
                    qty = 0
                if qty < 1:
                    qty = sum(self._scan_units(s) for s in group)
                position: Dict[str, Any] = {
                    "assortment": {
                        "meta": {
                            "href": f"{self.base_url}/entity/product/{product_id}",
                            "type": "product",
                            "mediaType": "application/json",
                        }
                    },
                    "quantity": qty,
                }
                # Цена (МС хранит в копейках) и НДС из УПД.
                pp = (position_prices or {}).get(product_id) or {}
                if pp.get("price") is not None:
                    try:
                        position["price"] = int(round(float(pp["price"]) * 100))
                    except (TypeError, ValueError):
                        pass
                if pp.get("vat") is not None:
                    try:
                        position["vat"] = int(pp["vat"])
                        position["vatEnabled"] = True
                    except (TypeError, ValueError):
                        pass
                if write_codes:
                    # Штрихкод немаркированного товара (is_barcode) не даёт trackingCode.
                    tcs = [
                        self._tracking_code_entry(s, None)
                        for s in group
                        if s.get("code") and not s.get("is_barcode")
                    ]
                    if tcs:
                        position["trackingCodes"] = tcs
                positions.append(position)

        put_body: Dict[str, Any] = {"positions": positions}
        if description:
            put_body["description"] = description
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._request_with_retry(
                client,
                "PUT",
                f"{self.base_url}/entity/{kind}/{doc_id}",
                json=put_body,
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
                    tc_resp = await self._request_with_retry(
                        client,
                        "POST",
                        tc_url,
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

    # --- Алиасы для backward-совместимости старого приёмочного кода ---
