"""Record verification scope and owner evidence separately from scan status."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261009_01'
down_revision = '20261008_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('scans', sa.Column('verification', postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column('scans', 'verification')
