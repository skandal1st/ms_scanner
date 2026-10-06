"""Document diagnostics and reliable incident delivery."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '20261006_02'
down_revision = '20261006_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cz_logs', sa.Column('trace_id', pg.UUID(as_uuid=True)))
    op.add_column('cz_logs', sa.Column('document_id', pg.UUID(as_uuid=True)))
    op.add_column('cz_logs', sa.Column('original_code', sa.Text()))
    for name in ('trace_id', 'document_id'):
        op.create_index(f'ix_cz_logs_{name}', 'cz_logs', [name])
    op.create_table('incidents',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('fingerprint', sa.String(64), nullable=False, unique=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('document_id', pg.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('stage', sa.String(80), nullable=False),
        sa.Column('reason', sa.String(160), nullable=False), sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('occurrences', sa.Integer(), nullable=False),
        sa.Column('trace_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('app_version', sa.String(64), nullable=False), sa.Column('diagnostics', pg.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True)))
    for name in ('user_id', 'document_id'):
        op.create_index(f'ix_incidents_{name}', 'incidents', [name])
    op.create_table('monitoring_deliveries',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('incident_id', pg.UUID(as_uuid=True), sa.ForeignKey('incidents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('payload', pg.JSONB(), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(160)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_monitoring_deliveries_next_attempt_at', 'monitoring_deliveries', ['next_attempt_at'])


def downgrade():
    op.drop_table('monitoring_deliveries')
    op.drop_table('incidents')
    for name in ('trace_id', 'document_id'):
        op.drop_index(f'ix_cz_logs_{name}', table_name='cz_logs')
    for name in ('original_code', 'document_id', 'trace_id'):
        op.drop_column('cz_logs', name)
