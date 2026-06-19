"""scan status used_in_other_doc

Revision ID: 20260619_01
Revises: 20260617_02
Create Date: 2026-06-19

Добавляет значение `used_in_other_doc` в enum `scanstatus` — для сканов, чей код
уже встречается в другом документе того же типа (приёмка↔приёмка / отгрузка↔
отгрузка). Такой скан добавляется как предупреждение и НЕ уходит в МойСклад/ЧЗ
при проведении документа.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260619_01"
down_revision: Union[str, None] = "20260617_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE scanstatus ADD VALUE IF NOT EXISTS 'used_in_other_doc'")


def downgrade() -> None:
    # PostgreSQL не позволяет удалить значение из enum без пересоздания типа.
    pass
