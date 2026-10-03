"""Keep packaging type separate from transport-pack and expansion mode."""
from alembic import op
import sqlalchemy as sa

revision = '20261003_03'
down_revision = '20261003_02'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('scans', sa.Column('package_type', sa.String(16), nullable=True))
    op.add_column('scans', sa.Column('keep_aggregate', sa.Boolean(), nullable=False, server_default='false'))
    op.execute("UPDATE scans SET package_type=CASE WHEN code ~ '^00[0-9]{18}$' THEN 'BOX' ELSE 'GROUP' END WHERE is_box")
    op.execute("UPDATE scans SET package_type='GROUP', keep_aggregate=CASE WHEN jsonb_typeof(child_codes)='array' THEN jsonb_array_length(child_codes)=0 ELSE true END WHERE NOT is_box AND NOT is_barcode AND box_quantity > 1")

def downgrade():
    op.drop_column('scans', 'keep_aggregate')
    op.drop_column('scans', 'package_type')
