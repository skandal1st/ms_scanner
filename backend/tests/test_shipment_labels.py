from types import SimpleNamespace as NS
from app.services.shipment_labels import update_shipment_metadata, shipment_display_name


def test_reopening_legacy_label_does_not_duplicate_order_or_counterparty():
    doc = NS(name='42 - (52) ООО Покупатель', customer_order_name=None)
    update_shipment_metadata(doc, {'name': '52', 'agent': {'name': 'ООО Покупатель'},
                                  'customerOrder': {'name': '42'}})
    assert shipment_display_name(doc) == '(42) 52 ООО Покупатель'
    assert doc.name == '42 - (52) ООО Покупатель'
    update_shipment_metadata(doc, {'name': '53', 'agent': {'name': 'ИП Новый'}}, '43')
    assert shipment_display_name(doc) == '(43) 53 ИП Новый'


def test_missing_parts_and_invoice_linked_order():
    doc = NS(name='Ручной документ', customer_order_name=None)
    assert shipment_display_name(doc) == 'Ручной документ'
    update_shipment_metadata(doc, {'name': '52'})
    assert shipment_display_name(doc) == '52'
    update_shipment_metadata(doc, {'name': '52', 'agent': {'name': 'Покупатель'}}, '42')
    assert shipment_display_name(doc) == '(42) 52 Покупатель'
    update_shipment_metadata(doc, {'name': '52', 'agent': {'name': 'Покупатель'}})
    assert shipment_display_name(doc) == '(42) 52 Покупатель'
