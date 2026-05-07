"""initial schema

Revision ID: 20260506_01
Revises:
Create Date: 2026-05-06

Полная начальная схема: users, integrations (с УКЭП-полями для ЧЗ),
oauth_states, documents, scans, cz_logs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260506_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("moysklad_token", sa.Text(), nullable=True),
        sa.Column("moysklad_account_id", sa.String(length=255), nullable=True),
        sa.Column("moysklad_account_name", sa.String(length=255), nullable=True),
        sa.Column("cz_token", sa.Text(), nullable=True),
        sa.Column("cz_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cz_cert_thumbprint", sa.String(length=64), nullable=True),
        sa.Column("cz_cert_subject", sa.String(length=500), nullable=True),
        sa.Column("cz_auth_method", sa.String(length=16), nullable=False, server_default="mock"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("state"),
    )
    op.create_index("ix_oauth_states_state", "oauth_states", ["state"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("moysklad_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "processing", "accepted", name="documentstatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_moysklad_id", "documents", ["moysklad_id"])

    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("gtin", sa.String(length=14), nullable=True),
        sa.Column("serial", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "valid", "invalid", "duplicate", name="scanstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("product_name", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scans_document_id", "scans", ["document_id"])

    op.create_table(
        "cz_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("request_method", sa.String(length=10), nullable=False),
        sa.Column("request_url", sa.Text(), nullable=False),
        sa.Column("request_body", sa.JSON(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cz_logs_user_id", "cz_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cz_logs_user_id", table_name="cz_logs")
    op.drop_table("cz_logs")
    op.drop_index("ix_scans_document_id", table_name="scans")
    op.drop_table("scans")
    op.drop_index("ix_documents_moysklad_id", table_name="documents")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_table("documents")
    sa.Enum(name="scanstatus").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="documentstatus").drop(op.get_bind(), checkfirst=False)
    op.drop_index("ix_oauth_states_state", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_table("integrations")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
