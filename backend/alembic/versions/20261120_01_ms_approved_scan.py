"""Allow explicit inclusion of an applied mark in a MoySklad shipment."""
from alembic import op


revision = '20261120_01'
down_revision = '20261112_01'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE scanstatus ADD VALUE IF NOT EXISTS 'ms_approved'")


def downgrade():
    pass  # PostgreSQL cannot drop a single enum value.
