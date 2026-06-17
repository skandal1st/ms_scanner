"""scans.is_box + box_quantity — короба SSCC, сохранённые целиком (transportpack).

Revision ID: 20260616_01
Revises: 20260604_01
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260616_01"
down_revision = "20260604_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column(
            "is_box",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "scans",
        sa.Column("box_quantity", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "box_quantity")
    op.drop_column("scans", "is_box")
