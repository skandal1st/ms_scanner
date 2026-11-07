"""Make collection-start shipment dates opt-in for every organization."""
from alembic import op
import sqlalchemy as sa

revision = '20261107_02'
down_revision = '20261107_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_profiles', sa.Column(
        'shipment_date_on_collection_start', sa.Boolean(), nullable=False,
        server_default=sa.false()))


def downgrade():
    op.drop_column('organization_profiles', 'shipment_date_on_collection_start')
