"""cz_owner_marks — снимок остатка марок ЧЗ участника для сверки с ЭДО.

Revision ID: 20260905_02
Revises: 20260905_01
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260905_02"
down_revision = "20260905_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cz_owner_marks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cis_canonical", sa.String(60), nullable=False),
        sa.Column("gtin", sa.String(14), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("package_type", sa.String(16), nullable=True),
        sa.Column("product_group", sa.String(32), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "cis_canonical", name="ix_cz_owner_marks_user_cis"),
    )
    op.create_index("ix_cz_owner_marks_user_id", "cz_owner_marks", ["user_id"])
    op.create_index("ix_cz_owner_marks_cis_canonical", "cz_owner_marks", ["cis_canonical"])


def downgrade() -> None:
    op.drop_table("cz_owner_marks")
