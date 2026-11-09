"""Keep packaging hierarchy independently from assembly scan granularity."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261109_01'
down_revision = '20261107_02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('document_packaging',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('root_key', sa.Text(), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('tree', postgresql.JSONB(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.UniqueConstraint('document_id', 'root_key', name='uq_document_packaging_root'))
    op.create_index('ix_document_packaging_document_id', 'document_packaging', ['document_id'])


def downgrade():
    op.drop_table('document_packaging')
