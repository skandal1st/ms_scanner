"""nk_product: кэш карточек Национального каталога (GTIN→наименование/бренд)

Revision ID: 20260907_02
Revises: 20260907_01
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_02"
down_revision = "20260907_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nk_product",
        sa.Column("gtin", sa.String(length=14), primary_key=True),
        sa.Column("found", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("good_name", sa.String(length=500), nullable=True),
        sa.Column("brand_name", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("nk_product")
