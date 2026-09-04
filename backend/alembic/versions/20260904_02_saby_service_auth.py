"""integrations.saby_app_* — сервисная авторизация приложения Saby (oauth/service).

Revision ID: 20260904_02
Revises: 20260904_01
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_02"
down_revision = "20260904_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("saby_app_client_id", sa.String(length=128), nullable=True))
    op.add_column("integrations", sa.Column("saby_app_secret", sa.Text(), nullable=True))   # Fernet
    op.add_column("integrations", sa.Column("saby_secret_key", sa.Text(), nullable=True))   # Fernet


def downgrade() -> None:
    op.drop_column("integrations", "saby_secret_key")
    op.drop_column("integrations", "saby_app_secret")
    op.drop_column("integrations", "saby_app_client_id")
