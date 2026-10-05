"""Optional shipment state after successful transfer."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261005_03'
down_revision = '20261005_02'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('organization_profiles', sa.Column('shipment_sent_state_id', postgresql.UUID(as_uuid=True), nullable=True))

def downgrade():
    op.drop_column('organization_profiles', 'shipment_sent_state_id')
