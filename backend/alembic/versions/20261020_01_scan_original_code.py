"""Keep original scanner input when a Base64 marking envelope is decoded."""
from alembic import op
import sqlalchemy as sa

revision = '20261020_01'
down_revision = '20261019_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('scans', sa.Column('original_code', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('scans', 'original_code')
