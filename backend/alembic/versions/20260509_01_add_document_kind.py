"""add document kind

Revision ID: 20260509_01
Revises: 20260506_01
Create Date: 2026-05-09

Добавляет колонку documents.kind (enum: supply|demand|loss|move|salesreturn).
Все существующие документы получают значение supply, чтобы старый поток
приёмки продолжил работать без изменений.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_01"
down_revision: Union[str, None] = "20260506_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    documentkind = sa.Enum(
        "supply",
        "demand",
        "loss",
        "move",
        "salesreturn",
        name="documentkind",
    )
    documentkind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "documents",
        sa.Column(
            "kind",
            documentkind,
            nullable=False,
            server_default="supply",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "kind")
    sa.Enum(name="documentkind").drop(op.get_bind(), checkfirst=False)
