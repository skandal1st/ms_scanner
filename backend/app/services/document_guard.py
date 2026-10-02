"""Serialize document edits and external processing across API/worker processes."""
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import select, text

from app.db.models import Document, DocumentStatus


async def editable_document(db, document_id, user_id):
    doc = (await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Документ не найден")
    if doc.status != DocumentStatus.draft:
        raise HTTPException(409, "Документ обрабатывается или уже завершён. Изменять марки нельзя.")
    return doc


@asynccontextmanager
async def processing_lock(key):
    # Session-level PostgreSQL lock survives per-batch commits and disappears on
    # worker connection loss. Always unlock before returning a pooled connection.
    from app.db.session import engine
    async with engine.connect() as connection:
        acquired = (await connection.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": str(key)}
        )).scalar_one()
        await connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": str(key)}
                )
                await connection.commit()
