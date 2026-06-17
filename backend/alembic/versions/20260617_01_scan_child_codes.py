"""scans.child_codes — состав агрегата (блок/групповая упаковка) из ЧЗ cises/info.

Блок разворачивается в МС поштучно: child_codes хранит КМ вложенных пачек,
box_quantity = их число (для подсчёта в прогрессе).

Revision ID: 20260617_01
Revises: 20260616_01
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260617_01"
down_revision = "20260616_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("child_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "child_codes")
