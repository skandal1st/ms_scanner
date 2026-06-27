"""Парсер УПД (СЧФДОП, формат ФНС 5.03) — извлечение товарных позиций и КМ.

Используется при приёмке маркированной продукции: вместо ручного сканирования
кладовщик загружает электронный УПД, мы достаём из него позиции (НаимТов/КолТов/
артикул) и коды маркировки (КИЗ — листовые КМ, НомУпак — агрегаты/SSCC).

GTIN позиции извлекается из первого КИЗ через parse_gs1_km_gtin_serial (в УПД
отдельного поля GTIN, как правило, нет — он зашит в код 01+GTIN). Фолбэк — артикул
позиции, если это похоже на штрихкод.

Парсер namespace-агностичен (сопоставление по локальному имени тега) и толерантен к
вариациям раскладки (СведТов в ТаблСчФакт). Финальная калибровка — по образцу файла.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from app.core.logging import logger
from app.services.chestnyznak import parse_gs1_km_gtin_serial, normalize_gtin_key


@dataclass
class ParsedPosition:
    name: str
    gtin: Optional[str]
    article: Optional[str]
    quantity: Optional[float]
    # Листовые КИЗ (коды маркировки единиц товара).
    codes: list[str] = field(default_factory=list)
    # Номера упаковок (НомУпак/ИдентТрансУпак): SSCC/агрегаты/короба — храним как есть.
    packages: list[str] = field(default_factory=list)
    # Цена за единицу (ЦенаТов, руб.) и ставка НДС (НалСт, напр. 20 из «20%»).
    price: Optional[float] = None
    vat: Optional[int] = None


@dataclass
class ParsedUpd:
    """Результат разбора УПД: позиции + реквизиты счёта-фактуры (шапка)."""
    positions: list[ParsedPosition]
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None


class UpdParseError(Exception):
    """Не удалось распарсить файл как УПД 5.03."""


def _decode_xml(xml_bytes: bytes) -> str:
    """Декодировать XML и убрать XML-декларацию.

    Декларации кодировки в УПД часто врут: Saby/СБИС пишут ``WINDOWS-1251``, а
    отдают UTF-8. Поэтому реальную кодировку определяем по успешному строгому
    декодированию (UTF-8 → объявленная → cp1251 → latin-1), а не по прологу.
    Декларацию снимаем: expat не умеет cp1251 и не принимает str с объявлением
    не-utf кодировки.
    """
    m = re.search(rb'encoding=["\']([\w\-]+)["\']', xml_bytes[:200])
    declared = m.group(1).decode("ascii", "ignore").lower() if m else None

    text: Optional[str] = None
    for enc in ("utf-8", declared, "cp1251", "latin-1"):
        if not enc:
            continue
        try:
            text = xml_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = xml_bytes.decode("utf-8", errors="replace")

    text = text.lstrip("﻿")  # BOM
    text = re.sub(r"^\s*<\?xml.*?\?>", "", text, count=1, flags=re.DOTALL)
    return text


def _localname(tag: str) -> str:
    """Имя тега без namespace: ``{ns}СведТов`` → ``СведТов``."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _iter_by_localname(root: ET.Element, name: str):
    """Все потомки (на любой глубине) с указанным локальным именем тега."""
    for el in root.iter():
        if _localname(el.tag) == name:
            yield el


def _first_child(el: ET.Element, name: str) -> Optional[ET.Element]:
    for child in el:
        if _localname(child.tag) == name:
            return child
    return None


def _text_codes(node: Optional[ET.Element], names: tuple[str, ...]) -> list[str]:
    """Собрать тексты потомков node с локальными именами из names (рекурсивно)."""
    out: list[str] = []
    if node is None:
        return out
    for el in node.iter():
        if _localname(el.tag) in names:
            val = (el.text or "").strip()
            if val:
                out.append(val)
    return out


def _quantity(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    s = raw.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _vat_rate(raw: Optional[str]) -> Optional[int]:
    """Ставка НДС из НалСт: «20%» → 20, «без НДС» → None. Берём целое число процентов."""
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _info_gtin(tov: ET.Element) -> Optional[str]:
    """GTIN из ``<ИнфПолФХЖ2 Идентиф="GTIN" Значен="…"/>`` (прямой потомок СведТов)."""
    for el in _iter_by_localname(tov, "ИнфПолФХЖ2"):
        if (el.get("Идентиф") or "").strip().upper() == "GTIN":
            val = (el.get("Значен") or "").strip()
            if val:
                return val
    return None


def _gtin_for_position(
    codes: list[str], info_gtin: Optional[str], article: Optional[str]
) -> Optional[str]:
    """GTIN-14 позиции.

    Приоритет — GTIN, зашитый в КМ (``01``+GTIN): именно его читает сканер и по нему
    заведён штрихкод товара в МС. Поле ``ИнфПолФХЖ2[GTIN]`` берём только как фолбэк
    (для агрегатных позиций без штучных КИЗ) — оно может расходиться с КМ.
    Последний фолбэк — числовой артикул как штрихкод.
    """
    for code in codes:
        gtin, _ = parse_gs1_km_gtin_serial(code)
        if gtin:
            return normalize_gtin_key(gtin)
    if info_gtin and info_gtin.isdigit():
        return normalize_gtin_key(info_gtin)
    # Фолбэк для позиций без GTIN (короба SSCC/ИдентТрансУпак не несут GTIN):
    # стабильный ключ из цифр артикула поставщика (КодТов). Каталожный поиск по
    # нему не сработает → позиция уйдёт в ручное сопоставление, но связка
    # GtinProductMap(ключ→товар) запомнится и резолвится при повторной загрузке.
    if article:
        art_digits = "".join(c for c in article if c.isdigit())
        if len(art_digits) >= 6:
            return normalize_gtin_key(art_digits)
    return None


def parse_upd_503(xml_bytes: bytes) -> ParsedUpd:
    """Распарсить УПД 5.03 → позиции с КМ + реквизиты счёта-фактуры.

    Бросает UpdParseError, если файл не XML или не содержит товарных позиций (СведТов).
    """
    try:
        root = ET.fromstring(_decode_xml(xml_bytes))
    except ET.ParseError as exc:
        raise UpdParseError(f"Файл не является корректным XML: {exc}") from exc

    # Реквизиты счёта-фактуры из шапки (СвСчФакт): номер и дата документа.
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    schf = next(_iter_by_localname(root, "СвСчФакт"), None)
    if schf is not None:
        invoice_number = (schf.get("НомерДок") or "").strip() or None
        invoice_date = (schf.get("ДатаДок") or "").strip() or None

    positions: list[ParsedPosition] = []
    for tov in _iter_by_localname(root, "СведТов"):
        name = (tov.get("НаимТов") or "").strip()

        # Артикул/код товара из ДопСведТов/@КодТов (в 5.03 — артикул, не GTIN).
        article = None
        dop = _first_child(tov, "ДопСведТов")
        if dop is not None:
            article = (dop.get("КодТов") or dop.get("Артикул") or "").strip() or None

        # Коды в НомСредИдентТов лежат либо текстом дочернего тега (КИЗ/НомУпак),
        # либо в атрибуте самого тега (вариант 1С: <НомСредИдентТов ИдентТрансУпак="00…"/>
        # — короб SSCC). КИЗ → штучные, НомУпак/ИдентТрансУпак → агрегаты/короба.
        codes: list[str] = []
        packages: list[str] = []
        for ident in _iter_by_localname(tov, "НомСредИдентТов"):
            codes.extend(_text_codes(ident, ("КИЗ",)))
            packages.extend(_text_codes(ident, ("НомУпак",)))
            kiz_attr = (ident.get("КИЗ") or "").strip()
            if kiz_attr:
                codes.append(kiz_attr)
            for attr in ("НомУпак", "ИдентТрансУпак"):
                v = (ident.get(attr) or "").strip()
                if v:
                    packages.append(v)

        # Позиции без маркировки (КИЗ/НомУпак) для приёмки нам неинтересны, но
        # позицию всё равно отдаём — фронт покажет «без марок».
        quantity = _quantity(tov.get("КолТов"))
        gtin = _gtin_for_position(codes, _info_gtin(tov), article)

        positions.append(
            ParsedPosition(
                name=name or article or "Без наименования",
                gtin=gtin,
                article=article,
                quantity=quantity,
                codes=codes,
                packages=packages,
                price=_quantity(tov.get("ЦенаТов")),
                vat=_vat_rate(tov.get("НалСт")),
            )
        )

    if not positions:
        raise UpdParseError(
            "В файле не найдено товарных позиций (СведТов). "
            "Ожидается УПД в формате ФНС 5.03."
        )

    logger.info(
        "upd.parsed",
        positions=len(positions),
        codes=sum(len(p.codes) for p in positions),
        packages=sum(len(p.packages) for p in positions),
        invoice_number=invoice_number,
    )
    return ParsedUpd(
        positions=positions,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
    )
