"""Serialize document edits and external processing across API/worker processes."""
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import Document, DocumentStatus


async def editable_document(db, document_id, user_id):
    doc = (await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Документ не найден")
    if (getattr(doc, 'upd_meta', None) or {}).get('superseded_by_document_id'):
        raise HTTPException(409, 'Марки перенесены в действующую отгрузку. Откройте её заново из заказа покупателя.')
    if doc.status != DocumentStatus.draft:
        raise HTTPException(409, "Документ обрабатывается или уже завершён. Изменять марки нельзя.")
    return doc


async def lock_ms_document(db, user_id, kind, moysklad_id):
    # All find-or-create entry points share this transaction lock, including PC and TSD.
    # No profile in the key: PC may resolve the profile from the MS organization later.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                     {"key": f"document:{user_id}:{getattr(kind, 'value', kind)}:{moysklad_id}"})


@asynccontextmanager
async def processing_lock(key, wait=False):
    # Session-level PostgreSQL lock survives per-batch commits and disappears on
    # worker connection loss. Always unlock before returning a pooled connection.
    from app.db.session import engine
    async with engine.connect() as connection:
        if wait:
            await connection.execute(text("SET LOCAL lock_timeout = '20s'"))
            try:
                await connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": str(key)})
            except DBAPIError as exc:
                if getattr(exc.orig, 'sqlstate', None) == '55P03':
                    raise HTTPException(409, 'Отгрузка занята другой операцией. Повторите открытие через несколько секунд.') from exc
                raise
            acquired = True
        else:
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
