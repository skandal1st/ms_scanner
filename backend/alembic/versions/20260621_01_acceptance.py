"""Приёмка по УПД: товарная группа документа + запоминание соответствия GTIN→товар.

Document.product_group — выбранная перед загрузкой УПД товарная группа.
gtin_product_map — связки GTIN→товар МС для авто-резолва при следующих загрузках.

Revision ID: 20260621_01
Revises: 20260620_01
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260621_01"
down_revision = "20260620_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("product_group", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "gtin_product_map",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gtin", sa.String(length=14), nullable=False),
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "gtin", name="ix_gtin_product_map_user_gtin"),
    )
    op.create_index(
        "ix_gtin_product_map_user_id", "gtin_product_map", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_gtin_product_map_user_id", table_name="gtin_product_map")
    op.drop_table("gtin_product_map")
    op.drop_column("documents", "product_group")
