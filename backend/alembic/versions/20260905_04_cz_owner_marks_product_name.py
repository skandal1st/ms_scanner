"""cz_owner_marks: наименование товара из ЧЗ (для сверки «Без группы» / нет в МС).

Revision ID: 20260905_04
Revises: 20260905_03
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_04"
down_revision = "20260905_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cz_owner_marks", sa.Column("product_name", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("cz_owner_marks", "product_name")
