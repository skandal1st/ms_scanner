"""Saved customer order filters shared by desktop and TSD."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_02"
down_revision = "20261003_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organization_profiles", sa.Column("customer_order_filters", postgresql.JSONB(),
                  nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("organization_profiles", "customer_order_filters")
