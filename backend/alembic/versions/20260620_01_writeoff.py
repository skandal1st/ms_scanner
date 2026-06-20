"""Списание (вывод из оборота ЧЗ): ИНН участника + причина и id документов ЧЗ.

Integration.cz_inn — ИНН участника оборота для тела документов True API.
Document.writeoff_reason — код выбранной причины вывода из оборота.
Document.cz_doc_ids — массив id поданных в ГИС МТ документов (для опроса статуса).

Revision ID: 20260620_01
Revises: 20260619_01
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260620_01"
down_revision = "20260619_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("cz_inn", sa.String(length=12), nullable=True))
    op.add_column("documents", sa.Column("writeoff_reason", sa.String(length=64), nullable=True))
    op.add_column(
        "documents",
        sa.Column("cz_doc_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "cz_doc_ids")
    op.drop_column("documents", "writeoff_reason")
    op.drop_column("integrations", "cz_inn")
