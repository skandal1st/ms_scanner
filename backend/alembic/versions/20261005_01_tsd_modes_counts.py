"""Terminal permissions and independent physical count sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
revision = '20261005_01'
down_revision = '20261004_01'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('tsd_devices', sa.Column('allowed_modes', pg.JSONB(), nullable=False, server_default='["shipment"]'))
    op.create_table('physical_count_sessions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('organization_profile_id', pg.UUID(as_uuid=True), sa.ForeignKey('organization_profiles.id'), nullable=False),
        sa.Column('workplace_id', pg.UUID(as_uuid=True), sa.ForeignKey('workplaces.id')),
        sa.Column('document_id', pg.UUID(as_uuid=True), sa.ForeignKey('documents.id')),
        sa.Column('mode', sa.String(16), nullable=False), sa.Column('name', sa.String(500), nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('plan', pg.JSONB(), nullable=False, server_default='[]'),
        sa.Column('settings', pg.JSONB(), nullable=False, server_default='{}'), sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('completed_at', sa.DateTime(timezone=True)))
    for column in ('user_id', 'organization_profile_id', 'document_id'):
        op.create_index(f'ix_physical_count_sessions_{column}', 'physical_count_sessions', [column])
    op.create_table('physical_count_scans',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', pg.UUID(as_uuid=True), sa.ForeignKey('physical_count_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', pg.UUID(as_uuid=True), sa.ForeignKey('tsd_devices.id'), nullable=False),
        sa.Column('product_key', sa.String(128), nullable=False), sa.Column('code', sa.Text(), nullable=False),
        sa.Column('code_hash', sa.String(64), nullable=False), sa.Column('quantity', sa.Numeric(18,3), nullable=False),
        sa.Column('is_barcode', sa.Boolean(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('session_id', 'code_hash', name='uq_physical_count_code'))
    op.create_index('ix_physical_count_scans_session_id', 'physical_count_scans', ['session_id'])

def downgrade():
    op.drop_table('physical_count_scans')
    op.drop_table('physical_count_sessions')
    op.drop_column('tsd_devices', 'allowed_modes')
