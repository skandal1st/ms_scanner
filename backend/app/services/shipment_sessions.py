"""Choose an existing shipment even after its collection has completed."""
from sqlalchemy import or_, select

from app.db.models import Document, DocumentKind, DocumentStatus


def shipment_session_query(user_id, moysklad_id, profile_id=None):
    query = select(Document).where(
        Document.user_id == user_id,
        Document.kind == DocumentKind.demand,
        Document.moysklad_id == moysklad_id,
        Document.upd_meta['superseded_by_document_id'].astext.is_(None),
        or_(Document.upd_meta['shipment_correction'].astext.is_(None),
            Document.status == DocumentStatus.accepted),
    )
    if profile_id is not None:
        query = query.where(Document.organization_profile_id == profile_id)
    # Legacy empty previews can be newer than the completed collection. They
    # must never cause another collection to start for this MS shipment.
    return query.order_by((Document.status == DocumentStatus.accepted).desc(), Document.created_at.desc())


def visible_documents(documents, counts):
    completed = {(d.organization_profile_id, d.moysklad_id) for d in documents
                 if d.kind == DocumentKind.demand and d.moysklad_id
                 and d.status == DocumentStatus.accepted
                 and not (d.upd_meta or {}).get('superseded_by_document_id')}
    return [d for d in documents
            if not (d.upd_meta or {}).get('superseded_by_document_id')
            and not (d.kind == DocumentKind.demand and d.status == DocumentStatus.draft
                     and (d.organization_profile_id, d.moysklad_id) in completed
                     and not counts.get(d.id, 0)
                     and not (d.upd_meta or {}).get('shipment_correction'))]
