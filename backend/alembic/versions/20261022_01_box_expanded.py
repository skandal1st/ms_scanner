"""Allow an existing SSCC scan to send its leaf marks instead of a transportpack."""
from alembic import op
import sqlalchemy as sa

revision = '20261022_01'
down_revision = '20261020_02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('scans', sa.Column('box_expanded', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    op.drop_column('scans', 'box_expanded')
