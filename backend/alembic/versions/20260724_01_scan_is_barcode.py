"""scans.is_barcode — скан обычного штрихкода немаркированного товара.

Немаркированный товар в сборке отгрузки собирается сканом EAN-13 (не КМ): ЧЗ не
вызывается, box_quantity накапливает кол-во (скан = +1), в МС уходит только quantity
позиции без trackingCode.

Revision ID: 20260724_01
Revises: 20260701_01
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_01"
down_revision = "20260701_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column(
            "is_barcode",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("scans", "is_barcode")
