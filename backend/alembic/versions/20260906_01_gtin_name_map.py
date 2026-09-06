"""gtin_name_map: база знаний GTIN→наименование из УПД (ЭДО)

Revision ID: 20260906_01
Revises: 20260905_04
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260906_01"
down_revision = "20260905_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gtin_name_map",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("gtin", sa.String(length=14), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="upd"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gtin_name_map_user_id", "gtin_name_map", ["user_id"])
    op.create_unique_constraint("ix_gtin_name_map_user_gtin", "gtin_name_map", ["user_id", "gtin"])


def downgrade() -> None:
    op.drop_constraint("ix_gtin_name_map_user_gtin", "gtin_name_map", type_="unique")
    op.drop_index("ix_gtin_name_map_user_id", table_name="gtin_name_map")
    op.drop_table("gtin_name_map")
