"""gtin_cz_group — глобальный кэш «GTIN → товарная группа ЧЗ (pg)».

Засевается из trackingType карточки МойСклад, чтобы пакетная проверка марок сразу
знала правильную pg и не давала «КМ/КИ не найден», когда группа не включена у клиента.

Revision ID: 20260903_01
Revises: 20260822_02
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260903_01"
down_revision = "20260822_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gtin_cz_group",
        sa.Column("gtin", sa.String(length=14), primary_key=True),
        sa.Column("product_group", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ms"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("gtin_cz_group")
