"""Detect deleted shipments without guessing a replacement by document number."""
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Document, DocumentKind, DocumentStatus, Scan


def ensure_active_shipment(shipment: dict) -> None:
    if shipment.get('deleted'):
        name = shipment.get('name') or 'без номера'
        raise ValueError(
            f'Отгрузка {name} удалена в МойСкладе. Обновите отгрузки заказа и выберите действующую. '
            'Отсканированные марки сохранены; перед продолжением перенесите их в действующую отгрузку.'
        )


async def ensure_no_stranded_scans(db, ms, user_id, profile_id, order_id, shipment_id):
    """A replacement must not silently start empty while the deleted source has marks.

    Multiple live shipments of the same order are legitimate. Never move marks
    automatically: the replacement may have a different plan, warehouse or owner.
    External reads occur only on document selection, never in the scan path.
    """
    if not order_id:
        return
    candidates = (await db.execute(select(Document).join(Scan, Scan.document_id == Document.id).where(
        Document.user_id == user_id,
        Document.organization_profile_id == profile_id,
        Document.kind == DocumentKind.demand,
        Document.status != DocumentStatus.accepted,
        Document.moysklad_customer_order_id == str(order_id),
        Document.moysklad_id.is_not(None),
        Document.moysklad_id != shipment_id,
    ).distinct())).scalars().all()
    for doc in candidates:
        try:
            source = await ms.get_document('demand', doc.moysklad_id)
        except Exception as exc:
            raise HTTPException(502, 'Не удалось проверить предыдущую сборку заказа. Повторите попытку.') from exc
        if source.get('deleted'):
            raise HTTPException(409,
                f'У заказа остались отсканированные марки в удалённой отгрузке {doc.name}. '
                'Сначала перенесите сохранённую сборку в действующую отгрузку. Повторно сканировать марки не нужно.')
