"""Инвентаризация: снимок остатка МС (ms_stock_snapshot) + Integration.inventory_store_ids.

Revision ID: 20260905_03
Revises: 20260905_02
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "20260905_03"
down_revision = "20260905_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("inventory_store_ids", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "ms_stock_snapshot",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("gtin", sa.String(14), nullable=False),
        sa.Column("product_name", sa.String(500), nullable=True),
        sa.Column("folder_id", sa.String(64), nullable=True),
        sa.Column("folder_name", sa.String(500), nullable=True),
        sa.Column("qty", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("store_breakdown", JSONB(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "gtin", name="ix_ms_stock_snapshot_user_gtin"),
    )
    op.create_index("ix_ms_stock_snapshot_user_id", "ms_stock_snapshot", ["user_id"])
    op.create_index("ix_ms_stock_snapshot_gtin", "ms_stock_snapshot", ["gtin"])


def downgrade() -> None:
    op.drop_table("ms_stock_snapshot")
    op.drop_column("integrations", "inventory_store_ids")
