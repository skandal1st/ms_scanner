"""Keep shipment identity separate from legacy, user-supplied document names."""


def update_shipment_metadata(doc, shipment, order_name=None):
    doc.moysklad_name = shipment.get('name') or None
    doc.agent_name = (shipment.get('agent') or {}).get('name') or None
    order = shipment.get('customerOrder') or {}
    if order_name is not None:
        doc.customer_order_name = order_name
    elif order.get('name'):
        doc.customer_order_name = order['name']


def shipment_display_name(doc):
    number = getattr(doc, 'moysklad_name', None)
    if not number:
        return doc.name
    order = getattr(doc, 'customer_order_name', None)
    agent = getattr(doc, 'agent_name', None)
    return ' '.join(part for part in (f'({order})' if order else None, number, agent) if part)
