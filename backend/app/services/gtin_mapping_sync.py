"""Explicit MS-to-local mapping sync; MS cards and existing scans are never mutated."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from fastapi import HTTPException
from sqlalchemy import select, update, func
from app.db.models import GtinAuditFinding, GtinAuditRun, GtinProductMap, Integration
from app.core.security import decrypt_token
from app.core.logging import logger
from app.services.gtin_audit import Catalog
from app.services.moysklad import MoySkladService


class Selection(BaseModel):
    run_id: UUID
    finding_id: UUID
    target_product_id: UUID


class Confirmation(Selection):
    expected_product_id: str = Field(max_length=64)
    expected_product_name: str | None = Field(default=None, max_length=500)
    expected_updated_at: datetime
    target_name: str = Field(min_length=1, max_length=500)


def mapping_view(row):
    return {'product_id': row.product_id, 'product_name': row.product_name,
            'updated_at': row.updated_at} if row else None


async def inspect_selection(db, user_id, body):
    finding = (await db.execute(select(GtinAuditFinding).join(GtinAuditRun).where(
        GtinAuditRun.id == body.run_id, GtinAuditRun.user_id == user_id, GtinAuditRun.status == 'done',
        GtinAuditFinding.id == body.finding_id, GtinAuditFinding.kind == 'mapping'))).scalar_one_or_none()
    if finding is None:
        raise HTTPException(404, 'Привязка не найдена в доступном отчёте.')
    target = str(body.target_product_id)
    allowed = {card['id'] for card in finding.candidates or []}
    if finding.saved_card:
        allowed.add(finding.saved_card['id'])
    if target not in allowed:
        raise HTTPException(409, 'Карточка отсутствует в этом отчёте. Обновите проверку GTIN.')
    row = (await db.execute(select(GtinProductMap).where(
        GtinProductMap.user_id == user_id, GtinProductMap.gtin == finding.gtin))).scalar_one_or_none()
    if row is None:
        raise HTTPException(409, 'Сохранённая привязка удалена. Обновите проверку GTIN.')
    before, gtin = mapping_view(row), finding.gtin
    integration = (await db.execute(select(Integration).where(Integration.user_id == user_id))).scalar_one_or_none()
    if not integration or not integration.moysklad_token:
        raise HTTPException(409, 'МойСклад не подключён. Подключите его в настройках.')
    token = decrypt_token(integration.moysklad_token)
    await db.rollback()  # No connection/row lock held while reading MS.
    try:
        product = await MoySkladService(token).get_product_by_id(target)
    except Exception as exc:
        raise HTTPException(502, 'Не удалось прочитать карточку МС. Повторите синхронизацию позже.') from exc
    if not product or product.get('id') != target:
        raise HTTPException(409, 'Карточка МС удалена или недоступна. Обновите проверку GTIN.')
    catalog = Catalog()
    catalog.add(product)
    card = catalog.cards[target]
    if card['archived'] or gtin not in card['gtins']:
        raise HTTPException(409, 'Карточка МС в архиве или GTIN больше не указан в ней. Обновите проверку.')
    return gtin, before, card


async def preview(db, user_id, body):
    gtin, before, card = await inspect_selection(db, user_id, body)
    return {'gtin': gtin, 'before': before, 'target': card}


async def apply(db, user_id, body):
    gtin, before, card = await inspect_selection(db, user_id, body)
    expected = {'product_id': body.expected_product_id, 'product_name': body.expected_product_name,
                'updated_at': body.expected_updated_at}
    if before != expected or card['name'] != body.target_name:
        raise HTTPException(409, 'Данные изменились после просмотра. Заново откройте синхронизацию и проверьте замену.')
    changed = (await db.execute(update(GtinProductMap).where(
        GtinProductMap.user_id == user_id, GtinProductMap.gtin == gtin,
        GtinProductMap.product_id == body.expected_product_id,
        GtinProductMap.product_name == body.expected_product_name,
        GtinProductMap.updated_at == body.expected_updated_at,
    ).values(product_id=card['id'], product_name=card['name'], updated_at=func.now())
        .returning(GtinProductMap.updated_at))).scalar_one_or_none()
    if changed is None:
        await db.rollback()
        raise HTTPException(409, 'Привязку уже изменили в другом окне. Обновите список и проверьте замену.')
    await db.commit()
    logger.info('gtin_control.mapping_synced', user_id=str(user_id), gtin=gtin,
                old_product_id=before['product_id'], product_id=card['id'])
    return {'gtin': gtin, 'mapping': {'product_id': card['id'], 'product_name': card['name'], 'updated_at': changed}}
