"""Separate opt-in statuses for beginning collection, preserving legacy settings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261005_05'
down_revision = '20261005_04'
branch_labels = None
depends_on = None

def upgrade():
    for name in ('shipment_start_state_id', 'customer_order_start_state_id'):
        op.add_column('organization_profiles', sa.Column(name, postgresql.UUID(as_uuid=True), nullable=True))

def downgrade():
    for name in ('customer_order_start_state_id', 'shipment_start_state_id'):
        op.drop_column('organization_profiles', name)
