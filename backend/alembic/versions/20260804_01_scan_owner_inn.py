"""scans.owner_inn — ИНН владельца марки из ЧЗ (cises/info ownerInn).

Для сверки владельца марки с владельцем подписи (Integration.cz_inn) в отгрузке:
несовпадение подсвечивается на фронте, но отгрузку не блокирует.

Revision ID: 20260804_01
Revises: 20260724_01
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_01"
down_revision = "20260724_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("owner_inn", sa.String(length=12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "owner_inn")
