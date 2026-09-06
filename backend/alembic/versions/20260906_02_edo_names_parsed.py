"""edo_documents.names_parsed: флаг извлечённых имён из УПД (инкрементальный backfill)

Revision ID: 20260906_02
Revises: 20260906_01
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "20260906_02"
down_revision = "20260906_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "edo_documents",
        sa.Column("names_parsed", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("edo_documents", "names_parsed")
