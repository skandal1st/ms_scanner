"""gtin_archive: архив несопоставимых GTIN в инвентаризации

Revision ID: 20260909_01
Revises: 20260907_02
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260909_01"
down_revision = "20260907_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gtin_archive",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("gtin", sa.String(length=14), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_gtin_archive_user_id", "gtin_archive", ["user_id"])
    op.create_unique_constraint("ix_gtin_archive_user_gtin", "gtin_archive", ["user_id", "gtin"])


def downgrade() -> None:
    op.drop_constraint("ix_gtin_archive_user_gtin", "gtin_archive", type_="unique")
    op.drop_index("ix_gtin_archive_user_id", table_name="gtin_archive")
    op.drop_table("gtin_archive")
