"""integrations.cz_product_groups — товарные группы (pg) ЧЗ, которые маркирует клиент.

Пер-клиентский список товарных групп для сужения перебора в True API (cises/info и т.п.).
Пустой список → fallback на глобальный settings.CZ_PRODUCT_GROUPS.

Revision ID: 20260822_01
Revises: 20260804_02
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260822_01"
down_revision = "20260804_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "cz_product_groups",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("integrations", "cz_product_groups")
