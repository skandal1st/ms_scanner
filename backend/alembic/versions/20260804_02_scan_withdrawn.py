"""scans.withdrawn + withdraw_reason — марка выведена из оборота / заблокирована (ЧЗ).

markWithdraw/withdrawReason из cises/info. В отгрузке помеченные марки подсвечиваются
и вызывают предупреждение при отправке, но не блокируют (статус скана не меняется).

Revision ID: 20260804_02
Revises: 20260804_01
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_02"
down_revision = "20260804_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("withdrawn", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "scans",
        sa.Column("withdraw_reason", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "withdraw_reason")
    op.drop_column("scans", "withdrawn")
