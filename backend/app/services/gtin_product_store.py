"""База знаний «GTIN → товар МС» (GtinProductMap) — общий доступ на чтение/запись.

Наполняется при КАЖДОМ успешном сверении GTIN с товаром МойСклад: при проверке марок
отгрузки (resolve product_id/имя по штрихкоду) и при приёмке (авто-резолв по каталогу +
ручная привязка). Даёт быстрый локальный резолв на первом этапе (без похода в МС) и
служит фолбэком имени товара, когда МС по GTIN ничего не отдал.

Таблица пер-клиентская (product_id — идентификатор товара конкретного аккаунта МС),
ключ GTIN нормализуется (GTIN-14) для устойчивого сравнения 13↔14 цифр.
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.db.models import GtinProductMap
from app.services.chestnyznak import normalize_gtin_key


async def get_gtin_product(
    db, user_id, gtin: Optional[str]
) -> Optional[tuple[str, Optional[str]]]:
    """Запомненный товар для GTIN: ``(product_id, product_name)`` либо None."""
    key = normalize_gtin_key(gtin)
    if not key:
        return None
    row = (
        await db.execute(
            select(GtinProductMap).where(
                GtinProductMap.user_id == user_id,
                GtinProductMap.gtin == key,
            )
        )
    ).scalar_one_or_none()
    if row and row.product_id:
        return (row.product_id, row.product_name)
    return None


async def remember_gtin_product(
    db,
    user_id,
    gtin: Optional[str],
    product_id: Optional[str],
    product_name: Optional[str],
) -> None:
    """Запомнить связку GTIN→товар (upsert). Имя не затираем пустым.

    Не коммитит — коммит на вызывающей стороне. Конкурентные вставки одного ключа
    (несколько сканов одного GTIN в параллельных задачах) безопасны за счёт
    ON CONFLICT по уникальному индексу (user_id, gtin).
    """
    key = normalize_gtin_key(gtin)
    if not key or not product_id:
        return
    name = (product_name or "").strip() or None
    stmt = pg_insert(GtinProductMap).values(
        user_id=user_id, gtin=key, product_id=product_id, product_name=name
    )
    stmt = stmt.on_conflict_do_update(
        constraint="ix_gtin_product_map_user_gtin",
        set_={
            "product_id": product_id,
            # новое имя перекрывает старое только когда оно не пустое
            "product_name": func.coalesce(stmt.excluded.product_name, GtinProductMap.product_name),
            "updated_at": func.now(),
        },
    )
    try:
        await db.execute(stmt)
    except Exception as exc:  # база знаний не критична — не роняем основной поток
        logger.warning("gtin_product_store.remember_failed", gtin=key, error=str(exc))
