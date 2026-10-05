"""Saved inventory shipment statuses and auditable absolute counts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
revision = '20261005_02'
down_revision = '20261005_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_profiles', sa.Column('inventory_include_state_ids', pg.JSONB(), nullable=False, server_default='[]'))
    op.create_table('physical_count_quantities',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', pg.UUID(as_uuid=True), sa.ForeignKey('physical_count_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', pg.UUID(as_uuid=True), sa.ForeignKey('tsd_devices.id')),
        sa.Column('product_key', sa.String(128), nullable=False),
        sa.Column('quantity', sa.Numeric(18, 3), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('request_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('session_id', 'request_id', name='uq_count_quantity_request'),
        sa.UniqueConstraint('session_id', 'product_key', 'revision', name='uq_count_quantity_revision'))
    op.create_index('ix_physical_count_quantities_session_id', 'physical_count_quantities', ['session_id'])


def downgrade():
    op.drop_table('physical_count_quantities')
    op.drop_column('organization_profiles', 'inventory_include_state_ids')
