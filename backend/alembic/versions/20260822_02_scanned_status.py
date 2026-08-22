"""scan status scanned — локально валидный формат КМ, ещё не проверенный в ЧЗ.

Пакетный флоу: при скане ставим `scanned` (только локальная проверка GS1, без ЧЗ),
проверка в ЧЗ идёт пачкой по кнопке «Проверить марки». UX: нейтральный статус,
не зелёный (valid = подтверждён ЧЗ) и не красный.

Revision ID: 20260822_02
Revises: 20260822_01
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260822_02"
down_revision: Union[str, None] = "20260822_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE scanstatus ADD VALUE IF NOT EXISTS 'scanned'")


def downgrade() -> None:
    # PostgreSQL не позволяет удалить значение из enum без пересоздания типа.
    pass
