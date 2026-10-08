"""Persist raw shipment numbers and counterparties for consistent desktop labels."""
from alembic import op
import sqlalchemy as sa

revision = '20261008_01'
down_revision = '20261006_02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('documents', sa.Column('moysklad_name', sa.String(255), nullable=True))
    op.add_column('documents', sa.Column('agent_name', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('documents', 'agent_name')
    op.drop_column('documents', 'moysklad_name')
