"""scan status overflow

Revision ID: 20260509_03
Revises: 20260509_02
Create Date: 2026-05-09

Добавляет значение `overflow` в enum `scanstatus` — отдельный статус
для сверхпланового скана. UX: визуально выделяется красным как ошибка,
но при подтверждении документа уходит в МС вместе с valid-сканами.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260509_03"
down_revision: Union[str, None] = "20260509_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE требует отдельной транзакции от других DDL,
    # но в нашей миграции это единственный шаг — autocommit ок.
    op.execute("ALTER TYPE scanstatus ADD VALUE IF NOT EXISTS 'overflow'")


def downgrade() -> None:
    # PostgreSQL не позволяет удалить значение из enum без пересоздания типа.
    # На откате значение остаётся в типе — само по себе оно безопасно.
    pass
