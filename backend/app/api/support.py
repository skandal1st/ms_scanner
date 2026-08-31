"""Техподдержка: создание обращения в AXIMA ERP из кнопки «Написать в поддержку».

Endpoint аутентифицирован — тикет обогащается контактом кладовщика (email, аккаунт МС),
чтобы поддержке не пришлось выяснять, кто написал. Сам транспорт — `services.support`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Integration, User
from app.db.session import get_db
from app.services import support

router = APIRouter(prefix="/support", tags=["support"])

# Категории обращения — фикс-список для UI (значение → человекочитаемая тема письма).
SUPPORT_CATEGORIES = {
    "acceptance": "Приёмка",
    "shipment": "Отгрузка",
    "writeoff": "Списание",
    "check": "Проверка марок",
    "moysklad": "Интеграция с МойСклад",
    "chestnyznak": "Интеграция с Честным Знаком",
    "general": "Другое",
}


class TicketRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=5, max_length=5000)
    category: str = "general"
    # Страница/раздел, откуда написали — помогает поддержке (необязательно).
    page: Optional[str] = Field(default=None, max_length=120)


class TicketResponse(BaseModel):
    ok: bool
    ref: Optional[str] = None  # номер/ID тикета в AXIMA, если ERP его вернул


@router.get("/categories")
async def list_categories() -> list[dict]:
    """Список категорий обращения для селекта на фронте."""
    return [{"value": k, "label": v} for k, v in SUPPORT_CATEGORIES.items()]


@router.post("/ticket", response_model=TicketResponse)
async def create_ticket(
    body: TicketRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    category = body.category if body.category in SUPPORT_CATEGORIES else "general"

    result = await db.execute(
        select(Integration).where(Integration.user_id == user.id)
    )
    integration = result.scalar_one_or_none()
    ms_account = integration.moysklad_account_name if integration else None

    try:
        ref = await support.create_ticket(
            subject=body.subject.strip(),
            message=body.message.strip(),
            category=category,
            category_label=SUPPORT_CATEGORIES[category],
            reporter_email=user.email,
            reporter_user_id=str(user.id),
            ms_account=ms_account,
            page=body.page,
        )
    except support.SupportError as exc:
        # Понятный русский текст (в т.ч. с запасным e-mail) уже внутри исключения.
        raise HTTPException(status_code=502, detail=str(exc))

    return TicketResponse(ok=True, ref=ref)
