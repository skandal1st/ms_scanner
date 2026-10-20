"""Persist per-organization latest CZ mark information and accessible history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261020_02'
down_revision = '20261020_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mark_history_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_profile_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organization_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mark_key', sa.Text(), nullable=False),
        sa.Column('lookup_code', sa.Text(), nullable=False),
        sa.Column('current', postgresql.JSONB(), nullable=True),
        sa.Column('events', postgresql.JSONB(), nullable=True),
        sa.Column('info_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('history_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('refresh_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('refreshing', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('info_error', sa.Text(), nullable=True),
        sa.Column('history_error', sa.Text(), nullable=True),
        sa.UniqueConstraint('organization_profile_id', 'mark_key', name='uq_mark_history_profile_key'))
    op.create_index('ix_mark_history_snapshots_user_id', 'mark_history_snapshots', ['user_id'])


def downgrade():
    op.drop_table('mark_history_snapshots')
