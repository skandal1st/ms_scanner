"""edo_documents: входящие УПД для приёмки — ссылка на вложение + отметка о приёмке

Revision ID: 20260907_01
Revises: 20260906_02
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_01"
down_revision = "20260906_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ссылка на первичное вложение УПД (Saby Файл.Ссылка) — чтобы принять из ЭДО без
    # повторного скана ленты. accepted_document_id: id созданной приёмки (NULL = новое,
    # ещё не принято — по нему считаем бейдж «новых входящих»).
    op.add_column("edo_documents", sa.Column("upd_link", sa.Text(), nullable=True))
    op.add_column(
        "edo_documents",
        sa.Column("accepted_document_id", sa.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edo_documents", "accepted_document_id")
    op.drop_column("edo_documents", "upd_link")
