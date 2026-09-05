"""edo_documents + edo_marks + курсор ленты Saby в integrations (контроль оборота марок).

Revision ID: 20260905_01
Revises: 20260904_02
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "20260905_01"
down_revision = "20260904_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("saby_last_event_id", sa.String(64), nullable=True))
    op.add_column("integrations", sa.Column("saby_last_event_dt", sa.String(32), nullable=True))
    op.add_column("integrations", sa.Column("saby_last_doc_id", sa.String(64), nullable=True))
    op.add_column("integrations", sa.Column("saby_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "edo_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False, server_default="saby"),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("number", sa.String(64), nullable=True),
        sa.Column("doc_date", sa.String(16), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("doc_type", sa.String(64), nullable=True),
        sa.Column("counterparty_inn", sa.String(12), nullable=True),
        sa.Column("counterparty_name", sa.String(500), nullable=True),
        sa.Column("state_code", sa.Integer(), nullable=True),
        sa.Column("state_name", sa.String(200), nullable=True),
        sa.Column("mark_state", JSONB(), nullable=True),
        sa.Column("marks_parsed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("codes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "external_id", name="ix_edo_documents_user_external"),
    )
    op.create_index("ix_edo_documents_user_id", "edo_documents", ["user_id"])
    op.create_index("ix_edo_documents_counterparty_inn", "edo_documents", ["counterparty_inn"])

    op.create_table(
        "edo_marks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("edo_documents.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cis_raw", sa.Text(), nullable=False),
        sa.Column("cis_canonical", sa.String(60), nullable=False),
        sa.Column("gtin", sa.String(14), nullable=True),
        sa.UniqueConstraint("document_id", "cis_canonical", name="ix_edo_marks_doc_cis"),
    )
    op.create_index("ix_edo_marks_document_id", "edo_marks", ["document_id"])
    op.create_index("ix_edo_marks_user_id", "edo_marks", ["user_id"])
    op.create_index("ix_edo_marks_cis_canonical", "edo_marks", ["cis_canonical"])


def downgrade() -> None:
    op.drop_table("edo_marks")
    op.drop_table("edo_documents")
    op.drop_column("integrations", "saby_synced_at")
    op.drop_column("integrations", "saby_last_doc_id")
    op.drop_column("integrations", "saby_last_event_dt")
    op.drop_column("integrations", "saby_last_event_id")
