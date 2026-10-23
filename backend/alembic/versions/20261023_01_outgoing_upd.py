"""Persist outgoing UPD upload identities and exact XML snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261023_01'
down_revision = '20261022_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('outgoing_upds',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('connection_key', sa.String(64), nullable=False),
        sa.Column('options', postgresql.JSONB(), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('xml_text', sa.Text(), nullable=False),
        sa.Column('xml_sha256', sa.String(64), nullable=False),
        sa.Column('external_url', sa.Text()),
        sa.Column('external_state', sa.String(200)),
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_outgoing_upds_user_id', 'outgoing_upds', ['user_id'])


def downgrade():
    op.drop_table('outgoing_upds')
