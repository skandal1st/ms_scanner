"""integrations.saby_* — подключение ЭДО Saby (СБИС) по клиенту для контроля марок.

Revision ID: 20260904_01
Revises: 20260903_01
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_01"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("saby_login", sa.String(length=255), nullable=True))
    op.add_column("integrations", sa.Column("saby_password", sa.Text(), nullable=True))  # Fernet
    op.add_column("integrations", sa.Column("saby_account", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("integrations", "saby_account")
    op.drop_column("integrations", "saby_password")
    op.drop_column("integrations", "saby_login")
