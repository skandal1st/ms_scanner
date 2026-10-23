"""Seller title UPD 5.03. Only final MS data; never local unsent scans.

Supported: RUB, Russian legal entities / sole traders, no excise charged by
the seller, principal signer. No signing or submission takes place here.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from lxml import etree as ET

from app.schemas.upd_export import UpdExportOptions, UpdParty


class UpdExportError(ValueError):
    pass


def decimal(value, label="сумма", *, allow_negative=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise UpdExportError(f"Не заполнено или некорректно поле: {label}") from None
    if not result.is_finite() or (result < 0 and not allow_negative):
        raise UpdExportError(f"Некорректное поле: {label}")
    return result


def money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(value):
    return format(value, "f")


def party_from_ms(data):
    return UpdParty(name=data.get("legalTitle") or data.get("name") or "",
                    inn=data.get("inn") or "", kpp=data.get("kpp") or "",
                    address=data.get("legalAddress") or "",
                    last_name=data.get("legalLastName") or "",
                    first_name=data.get("legalFirstName") or "",
                    middle_name=data.get("legalMiddleName") or "")


def shipment_requisites(source):
    number = str(source.get("name") or "").strip()
    if not number:
        raise UpdExportError("Не заполнен номер отгрузки в МойСклад")
    try:
        document_date = datetime.fromisoformat(source["moment"]).date()
    except (KeyError, TypeError, ValueError):
        raise UpdExportError("Не заполнена дата отгрузки в МойСклад") from None
    return number, document_date


def _party(parent, tag, party):
    if not re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})", party.inn):
        raise UpdExportError(f"{tag}: заполните ИНН из 10 или 12 цифр")
    if not party.address:
        raise UpdExportError(f"{tag}: заполните юридический адрес")
    node = ET.SubElement(parent, tag)
    identity = ET.SubElement(node, "ИдСв")
    if len(party.inn) == 12:
        if not party.last_name or not party.first_name:
            raise UpdExportError(f"{tag}: заполните фамилию и имя ИП")
        ip = ET.SubElement(identity, "СвИП", ИННФЛ=party.inn)
        _fio(ip, party.last_name, party.first_name, party.middle_name)
    else:
        if not party.name or not re.fullmatch(r"[0-9]{9}", party.kpp):
            raise UpdExportError(f"{tag}: заполните наименование и КПП организации")
        ET.SubElement(identity, "СвЮЛУч", НаимОрг=party.name, ИННЮЛ=party.inn, КПП=party.kpp)
    ET.SubElement(ET.SubElement(node, "Адрес"), "АдрИнф", КодСтр="643",
                  НаимСтран="Россия", АдрТекст=party.address)


def _fio(parent, last, first, middle):
    attrs = {"Фамилия": last, "Имя": first}
    if middle:
        attrs["Отчество"] = middle
    ET.SubElement(parent, "ФИО", **attrs)


@lru_cache(maxsize=1)
def _schema():
    path = Path(__file__).parents[1] / "schemas/fns/ON_NSCHFDOPPR_1_997_01_05_03_05.xsd"
    return ET.XMLSchema(ET.parse(str(path), ET.XMLParser(resolve_entities=False, no_network=True)))


def _marks(extra, codes, seen):
    # Upper-level aggregates are preserved. Their children must not be counted
    # again, including when MS returns a resolved nested tree.
    for code in codes:
        cis, kind = code.get("cis"), code.get("type")
        if not cis or kind not in ("trackingcode", "consumerpack", "transportpack"):
            raise UpdExportError("МойСклад вернул код маркировки без CIS или с неизвестным типом")
        if any(ord(c) < 32 for c in cis):
            raise UpdExportError("Код маркировки содержит управляющие символы. Исправьте CIS в МойСкладе")
        if cis in seen:
            raise UpdExportError("Одна марка или упаковка указана в отгрузке несколько раз")
        seen.add(cis)
        if kind == "transportpack":
            # FNS SSCC contains the 18-digit identifier, without GS1 AI 00.
            sscc = cis[2:] if len(cis) == 20 and cis.startswith("00") else cis
            if not re.fullmatch(r"[0-9]{18}", sscc):
                raise UpdExportError("Некорректный SSCC в отгрузке МойСклад")
            ET.SubElement(extra, "НомСредИдентТов", ИдентТрансУпак=sscc)
        else:
            node = ET.SubElement(extra, "НомСредИдентТов")
            ET.SubElement(node, "НомУпак" if kind == "consumerpack" else "КИЗ").text = cis


def generate_upd(source: dict, options: UpdExportOptions, *, now=None, file_uuid=None):
    """Return (filename, XML bytes), validating both source totals and FNS XSD."""
    currency = (source.get("rate") or {}).get("currency") or {}
    if str(currency.get("code")) != "643":
        raise UpdExportError("Пока поддерживаются только отгрузки в российских рублях (643)")
    for key, party in (("organization", options.seller), ("agent", options.buyer)):
        known_inn = (source.get(key) or {}).get("inn")
        if known_inn and known_inn != party.inn:
            raise UpdExportError("ИНН участника не совпадает с отгрузкой МойСклад")
    for cargo, principal in (("consignor", "organization"), ("consignee", "agent")):
        cargo_href = ((source.get(cargo) or {}).get("meta") or {}).get("href")
        principal_href = ((source.get(principal) or {}).get("meta") or {}).get("href")
        if cargo_href and cargo_href != principal_href:
            raise UpdExportError("Отдельный грузоотправитель или грузополучатель пока не поддерживается")
    if source.get("consigneeBranch") or source.get("consignorBranch"):
        raise UpdExportError("Отгрузки через обособленные подразделения пока не поддерживаются")
    number, document_date = shipment_requisites(source)
    if options.number != number or options.document_date != document_date:
        raise UpdExportError("Номер и дата УПД должны совпадать с отгрузкой МойСклад. Откройте УПД заново для обновления реквизитов")
    invoice = source.get("invoice")
    if options.function == "СЧФДОП" and invoice:
        if options.number != invoice.get("name") or options.document_date.isoformat() != str(invoice.get("moment", ""))[:10]:
            raise UpdExportError("У связанного счёта-фактуры другой номер или дата. Приведите его реквизиты в МойСклад к отгрузке")
    rows = (source.get("positions") or {}).get("rows") or []
    if not rows:
        raise UpdExportError("В отгрузке нет позиций")
    now = now or datetime.now(ZoneInfo("Europe/Moscow"))
    marked = int(any(r.get('trackingCodes') or (r.get('assortment') or {}).get('trackingType') not in (None, 'NOT_TRACKED') for r in rows))
    tobacco = int(any((r.get('assortment') or {}).get('trackingType') in ('TOBACCO', 'OTP', 'NCP') for r in rows))
    # Order 970, as amended by 1032: N1 GUID, N2 traceability,
    # N3 marking, N4 alcohol, N5 tobacco/nicotine, N6 oil, N7 reserve.
    file_id = f"ON_NSCHFDOPPR_{options.receiver_id}_{options.sender_id}_{now:%Y%m%d}_{file_uuid or uuid4()}_0_{marked}_0_{tobacco}_0_00"
    root = ET.Element("Файл", ИдФайл=file_id, ВерсФорм="5.03", ВерсПрог="Скандата 1.0")
    doc = ET.SubElement(root, "Документ", КНД="1115131", Функция=options.function,
                        НаимДокОпр="Универсальный передаточный документ",
                        ДатаИнфПр=now.strftime("%d.%m.%Y"), ВремИнфПр=now.strftime("%H.%M.%S"))
    header = ET.SubElement(doc, "СвСчФакт", НомерДок=number,
                           ДатаДок=document_date.strftime("%d.%m.%Y"))
    _party(header, "СвПрод", options.seller)
    ET.SubElement(ET.SubElement(header, "ГрузОт"), "ОнЖе").text = "он же"
    consignee = options.buyer.model_copy(update={"address": source.get("shipmentAddress") or options.buyer.address})
    _party(header, "ГрузПолуч", consignee)
    _party(header, "СвПокуп", options.buyer)
    ET.SubElement(header, "ДенИзм", КодОКВ="643", НаимОКВ="Российский рубль")
    if source.get("stateContractId"):
        ET.SubElement(header, "ДопСвФХЖ1", ИдГосКон=source["stateContractId"])
    table = ET.SubElement(doc, "ТаблСчФакт")
    net_total = tax_total = gross_total = Decimal(0)
    seen = set()
    any_tax = False
    for i, row in enumerate(rows, 1):
        product = row.get("assortment") or {}
        if (product.get("meta") or {}).get("type") in ("service", "bundle"):
            raise UpdExportError("Услуги и комплекты пока не поддерживаются в XML УПД")
        if product.get("trackingType") == "ALCOHOL" or row.get("declaration"):
            raise UpdExportError("Алкоголь и товары с ГТД/РНПТ пока не поддерживаются в XML УПД")
        qty = decimal(row.get("quantity"), f"количество в строке {i}")
        if not qty:
            raise UpdExportError(f"Нулевое количество в строке {i}")
        unit = decimal(row.get("price"), f"цена в строке {i}") / 100
        discount = decimal(row.get("discount", 0), "скидка", allow_negative=True)
        if discount > 100:
            raise UpdExportError(f"Скидка больше 100% в строке {i}")
        unit *= 1 - discount / 100
        enabled = row.get("vatEnabled", source.get("vatEnabled"))
        if not isinstance(enabled, bool):
            raise UpdExportError(f"Не задан признак НДС в строке {i}")
        rate = decimal(row.get("vat"), "ставка НДС") if enabled else Decimal(0)
        if rate not in map(Decimal, (0, 5, 7, 10, 18, 20, 22)):
            raise UpdExportError(f"Неподдерживаемая ставка НДС в строке {i}")
        if not isinstance(source.get("vatIncluded"), bool):
            raise UpdExportError("Не задан признак включения НДС в цену")
        net_unit = unit / (1 + rate / 100) if enabled and source["vatIncluded"] else unit
        if source["vatIncluded"]:
            gross = money(unit * qty)
            tax = money(gross * rate / (100 + rate)) if enabled else Decimal(0)
            net = gross - tax
        else:
            net = money(unit * qty)
            tax = money(net * rate / 100) if enabled else Decimal(0)
            gross = net + tax
        uom = product.get("uom") or {}
        if not product.get("name") or not uom.get("code") or not uom.get("name"):
            raise UpdExportError(f"В строке {i} не заполнены наименование или единица измерения ОКЕИ")
        attrs = {"НомСтр": str(i), "НаимТов": product["name"], "ОКЕИ_Тов": str(uom["code"]),
                 "НаимЕдИзм": uom["name"], "КолТов": fmt(qty),
                 "ЦенаТов": fmt(net_unit.quantize(Decimal("0.00000000001"), rounding=ROUND_HALF_UP)),
                 "СтТовБезНДС": fmt(net), "НалСт": f"{rate.normalize():f}%" if enabled else "без НДС",
                 "СтТовУчНал": fmt(gross)}
        node = ET.SubElement(table, "СведТов", **attrs)
        extra_attrs = {"ПрТовРаб": "1"}
        if product.get("article"):
            extra_attrs["АртикулТов"] = product["article"]
        if product.get("code"):
            extra_attrs["КодТов"] = product["code"]
        extra = ET.SubElement(node, "ДопСведТов", **extra_attrs)
        _marks(extra, row.get("trackingCodes") or [], seen)
        if product.get("trackingType") not in (None, "NOT_TRACKED") and not row.get("trackingCodes"):
            raise UpdExportError(f"В маркируемой строке {i} нет записанных в МойСклад кодов")
        ET.SubElement(ET.SubElement(node, "Акциз"), "БезАкциз").text = "без акциза"
        ET.SubElement(ET.SubElement(node, "СумНал"), "СумНал" if enabled else "БезНДС").text = fmt(tax) if enabled else "без НДС"
        net_total += net
        tax_total += tax
        gross_total += gross
        any_tax |= enabled
    expected_sum = money(decimal(source.get("sum"), "итог МойСклад") / 100)
    expected_vat = money(decimal(source.get("vatSum"), "НДС МойСклад") / 100)
    if gross_total != expected_sum or tax_total != expected_vat:
        raise UpdExportError("Расчёт сумм УПД не совпал с итогами МойСклад. Проверьте цены, скидки и НДС")
    total = ET.SubElement(table, "ВсегоОпл", СтТовБезНДСВсего=fmt(net_total), СтТовУчНалВсего=fmt(gross_total))
    ET.SubElement(ET.SubElement(total, "СумНалВсего"), "СумНал" if any_tax else "БезНДС").text = fmt(tax_total) if any_tax else "без НДС"
    transfer_date = document_date.strftime("%d.%m.%Y")
    transfer = ET.SubElement(ET.SubElement(doc, "СвПродПер"), "СвПер", СодОпер="Товары переданы", ДатаПер=transfer_date)
    ET.SubElement(transfer, "ОснПер", РеквНаимДок="Отгрузка", РеквНомерДок=source.get("name") or options.number,
                  РеквДатаДок=transfer.get("ДатаПер"))
    signer = ET.SubElement(doc, "Подписант", Должн=options.signer_position, СпосПодтПолном="1")
    _fio(signer, options.signer_last_name, options.signer_first_name, options.signer_middle_name)
    try:
        _schema().assertValid(root)
    except ET.DocumentInvalid as exc:
        error = exc.error_log.last_error
        raise UpdExportError(f"XML не прошёл проверку схемы ФНС: {error.message if error else 'ошибка структуры'}") from None
    try:
        xml = ET.tostring(root, encoding="windows-1251", xml_declaration=True, pretty_print=True)
    except (ValueError, UnicodeError):
        raise UpdExportError("Реквизиты содержат недопустимые для XML символы") from None
    return file_id + ".xml", xml
