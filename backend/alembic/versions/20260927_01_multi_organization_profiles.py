"""multi organization profiles and workplaces

Revision ID: 20260927_01
Revises: 20260919_01
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260927_01"
down_revision = "20260919_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("moysklad_organization_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cz_token", sa.Text(), nullable=True),
        sa.Column("cz_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cz_cert_thumbprint", sa.String(64), nullable=True),
        sa.Column("cz_cert_subject", sa.String(500), nullable=True),
        sa.Column("cz_auth_method", sa.String(16), nullable=False, server_default="mock"),
        sa.Column("cz_box_mode_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cz_inn", sa.String(12), nullable=True),
        sa.Column("cz_product_groups", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("inventory_store_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "moysklad_organization_id", name="uq_org_profile_user_ms_org"),
    )
    op.create_index("ix_org_profiles_user_id", "organization_profiles", ["user_id"])
    op.create_index("ix_org_profiles_ms_org", "organization_profiles", ["moysklad_organization_id"])

    op.execute("""
        INSERT INTO organization_profiles (
            id, user_id, name, is_default, cz_token, cz_token_expires_at,
            cz_cert_thumbprint, cz_cert_subject, cz_auth_method,
            cz_box_mode_enabled, cz_inn, cz_product_groups,
            inventory_store_ids, created_at, updated_at
        )
        SELECT gen_random_uuid(), u.id, 'Основное юрлицо', true, i.cz_token,
               i.cz_token_expires_at, i.cz_cert_thumbprint, i.cz_cert_subject,
               COALESCE(i.cz_auth_method, 'mock'), COALESCE(i.cz_box_mode_enabled, false),
               i.cz_inn, COALESCE(i.cz_product_groups, '[]'::jsonb),
               COALESCE(i.inventory_store_ids, '[]'::jsonb), now(), now()
        FROM users u
        LEFT JOIN integrations i ON i.user_id = u.id
    """)

    op.create_table(
        "workplaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organization_profiles.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("store_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workplaces_user_id", "workplaces", ["user_id"])
    op.create_index("ix_workplaces_profile_id", "workplaces", ["organization_profile_id"])

    op.add_column("documents", sa.Column("organization_profile_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("workplace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("moysklad_organization_id", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("moysklad_store_id", sa.String(64), nullable=True))
    op.create_foreign_key("fk_documents_org_profile", "documents", "organization_profiles", ["organization_profile_id"], ["id"])
    op.create_foreign_key("fk_documents_workplace", "documents", "workplaces", ["workplace_id"], ["id"])
    op.create_index("ix_documents_org_profile_id", "documents", ["organization_profile_id"])
    op.create_index("ix_documents_ms_org_id", "documents", ["moysklad_organization_id"])
    op.execute("""
        UPDATE documents d
        SET organization_profile_id = p.id
        FROM organization_profiles p
        WHERE p.user_id = d.user_id AND p.is_default = true
    """)


def downgrade() -> None:
    op.drop_index("ix_documents_ms_org_id", table_name="documents")
    op.drop_index("ix_documents_org_profile_id", table_name="documents")
    op.drop_constraint("fk_documents_workplace", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_org_profile", "documents", type_="foreignkey")
    op.drop_column("documents", "moysklad_store_id")
    op.drop_column("documents", "moysklad_organization_id")
    op.drop_column("documents", "workplace_id")
    op.drop_column("documents", "organization_profile_id")
    op.drop_table("workplaces")
    op.drop_table("organization_profiles")
