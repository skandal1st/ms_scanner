"""Резолв и кэш «GTIN → товарная группа ЧЗ (pg)» через свою БД + МойСклад.

Логика (см. GtinCzGroup): для набора GTIN сначала читаем свою таблицу; для промахов
идём в МойСклад (find_product_by_gtin → trackingType → pg) и записываем результат в
таблицу для последующих проверок. Найденные pg подставляются в перебор товарных групп
при проверке марок первыми — это лечит «КМ/КИ не найден» на всех сканах, когда нужная
группа не включена галочкой у клиента. Всё best-effort: любая ошибка (нет токена МС,
товара, сети) деградирует к обычному перебору групп клиента.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import GtinCzGroup, Integration
from app.services.chestnyznak import cz_pg_from_ms_tracking_type, normalize_gtin_key
from app.services.cz_pg_cache import set_cached_pg


async def _get_cached_from_db(db, gtins: list[str]) -> dict[str, str]:
    """GTIN → pg из своей таблицы для переданного набора GTIN."""
    if not gtins:
        return {}
    rows = await db.execute(
        select(GtinCzGroup.gtin, GtinCzGroup.product_group).where(
            GtinCzGroup.gtin.in_(gtins)
        )
    )
    return {g: pg for g, pg in rows.all()}


async def _store_pg(db, gtin: str, pg: str, source: str = "ms") -> None:
    """Upsert GTIN → pg (глобальная таблица, ON CONFLICT по PK gtin)."""
    stmt = pg_insert(GtinCzGroup).values(
        gtin=gtin, product_group=pg, source=source
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["gtin"],
        set_={"product_group": pg, "source": source, "updated_at": stmt.excluded.updated_at},
    )
    await db.execute(stmt)


async def resolve_pgs_for_gtins(db, user_id, gtins: list[str]) -> set[str]:
    """Определить товарные группы (pg) для набора GTIN: своя БД → МС → запись в БД.

    Возвращает множество найденных pg (для добавления в перебор ЧЗ). Не бросает —
    при любой ошибке возвращает то, что успели определить.
    """
    keys = sorted({k for k in (normalize_gtin_key(g) for g in gtins) if k})
    if not keys:
        return set()

    cached = await _get_cached_from_db(db, keys)
    resolved: set[str] = set(cached.values())
    missing = [k for k in keys if k not in cached]
    if not missing:
        return resolved

    # Промахи добираем из МС по trackingType карточки товара.
    integ = (
        await db.execute(select(Integration).where(Integration.user_id == user_id))
    ).scalar_one_or_none()
    if not integ or not integ.moysklad_token:
        return resolved
    try:
        ms_token = decrypt_token(integ.moysklad_token)
    except Exception as exc:
        logger.warning("gtin_cz_group.ms_decrypt_failed", error=str(exc))
        return resolved

    from app.services.moysklad import MoySkladService

    ms = MoySkladService(ms_token)
    changed = False
    for gtin in missing:
        try:
            product = await ms.find_product_by_gtin(gtin)
        except Exception as exc:
            logger.warning("gtin_cz_group.ms_lookup_failed", gtin=gtin, error=str(exc))
            continue
        if not product:
            continue
        pg = cz_pg_from_ms_tracking_type(product.get("trackingType"))
        if not pg:
            continue
        await _store_pg(db, gtin, pg, source="ms")
        await set_cached_pg(gtin, pg)  # засеиваем и Redis-кэш порядка групп
        resolved.add(pg)
        changed = True
        logger.info("gtin_cz_group.resolved", gtin=gtin, pg=pg)
    if changed:
        await db.commit()
    return resolved
