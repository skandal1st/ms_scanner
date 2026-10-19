"""Per-organization optional CZ checks during acceptance."""
from alembic import op
import sqlalchemy as sa

revision = '20261019_01'
down_revision = '20261009_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_profiles', sa.Column('acceptance_cz_check_enabled',
                  sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('organization_profiles', 'acceptance_cz_check_enabled')
