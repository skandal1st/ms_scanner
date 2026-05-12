"""scans.moysklad_product_id — ручная привязка КМ к товару МС при отгрузке."""

import sqlalchemy as sa
from alembic import op

revision = "20260512_02"
down_revision = "20260512_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("moysklad_product_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "moysklad_product_id")
