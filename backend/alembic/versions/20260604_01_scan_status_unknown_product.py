"""scan status unknown_product

Revision ID: 20260604_01
Revises: 20260515_01
Create Date: 2026-06-04

Добавляет значение `unknown_product` в enum `scanstatus` — для сканов с валидной
КМ, но без найденного товара в МС (не в плане и не в каталоге по штрихкоду).
Кладовщик должен вручную сопоставить такой код с товаром МС перед отгрузкой.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260604_01"
down_revision: Union[str, None] = "20260515_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE scanstatus ADD VALUE IF NOT EXISTS 'unknown_product'")


def downgrade() -> None:
    # PostgreSQL не позволяет удалить значение из enum без пересоздания типа.
    pass
