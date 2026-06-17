"""scans.owner_name + producer_name — владелец и производитель КМ из ЧЗ cises/info.

Показываем при проверке кода (в т.ч. для блока/короба).

Revision ID: 20260617_02
Revises: 20260617_01
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_02"
down_revision = "20260617_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("owner_name", sa.String(length=500), nullable=True))
    op.add_column("scans", sa.Column("producer_name", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("scans", "producer_name")
    op.drop_column("scans", "owner_name")
