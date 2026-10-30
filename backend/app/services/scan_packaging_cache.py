"""Compatibility entrypoint for the permanent shared packaging catalogue."""
from app.db.session import AsyncSessionLocal
from app.services.packaging_catalog import is_confirmed_unit


async def has_confirmed_unit(user_id, document_id, gtin, *, db=None, code=None):
    # Only public packaging metadata is shared. User/product mapping remains local.
    if db is not None:
        if code:
            from app.services.packaging_formats import confirmed_unit
            specific = await confirmed_unit(db, code)
            if specific is not None:
                return specific
        return await is_confirmed_unit(db, gtin)
    async with AsyncSessionLocal() as session:
        return await is_confirmed_unit(session, gtin)
