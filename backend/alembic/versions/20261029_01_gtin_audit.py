"""Persistent read-only GTIN audit snapshots and paginated findings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '20261029_01'
down_revision = '20261023_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gtin_audit_runs',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('phase', sa.String(32), nullable=False),
        sa.Column('products_checked', sa.Integer(), nullable=False),
        sa.Column('summary', pg.JSONB()), sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_gtin_audit_runs_user_id', 'gtin_audit_runs', ['user_id'])
    op.create_index('uq_gtin_audit_active_user', 'gtin_audit_runs', ['user_id'], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('gtin_audit_findings',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', pg.UUID(as_uuid=True), sa.ForeignKey('gtin_audit_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('category', sa.String(40), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False), sa.Column('gtin', sa.String(14), nullable=False),
        sa.Column('saved_product_id', sa.String(64)), sa.Column('saved_product_name', sa.String(500)),
        sa.Column('saved_card', pg.JSONB()), sa.Column('candidates', pg.JSONB(), nullable=False),
        sa.Column('search_text', sa.Text(), nullable=False))
    op.create_index('ix_gtin_audit_findings_page', 'gtin_audit_findings', ['run_id', 'kind', 'priority', 'gtin'])


def downgrade():
    op.drop_table('gtin_audit_findings')
    op.drop_table('gtin_audit_runs')
