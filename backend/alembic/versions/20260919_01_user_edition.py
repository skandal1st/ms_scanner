"""user_edition: издание аккаунта (ms_lite | full)

Revision ID: 20260919_01
Revises: 20260909_01
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20260919_01"
down_revision = "20260909_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Существующие аккаунты грандфатерятся в full (не ломаем доступ к модулям).
    op.add_column(
        "users",
        sa.Column(
            "edition",
            sa.String(length=16),
            nullable=False,
            server_default="full",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "edition")
