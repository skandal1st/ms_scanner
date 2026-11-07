"""Per-organization accounting timezone for collection start."""
from alembic import op
import sqlalchemy as sa

revision = '20261107_01'
down_revision = '20261030_01'
branch_labels = None
depends_on = None


def upgrade():
    # NULL means use the timezone of the device starting the collection.
    op.add_column('organization_profiles', sa.Column('shipment_timezone', sa.String(100), nullable=True))


def downgrade():
    op.drop_column('organization_profiles', 'shipment_timezone')
