"""cz_box_mode_enabled — опциональный режим распаковки SSCC через ЧЗ + УКЭП.

Revision ID: 20260512_01
Revises: 20260509_03
Create Date: 2026-05-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260512_01"
down_revision = "20260509_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "cz_box_mode_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.alter_column("integrations", "cz_box_mode_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("integrations", "cz_box_mode_enabled")
