"""Приёмка по УПД: реквизиты счёта-фактуры для комментария поступления.

Document.upd_meta — JSONB {invoice_number, invoice_date} из шапки УПД (СвСчФакт),
используется для description поступления МС («Импорт с ЭДО · СФ № … от …»).

Revision ID: 20260627_01
Revises: 20260621_01
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260627_01"
down_revision = "20260621_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("upd_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "upd_meta")
