"""Shared non-personal packaging evidence learned from CZ responses."""
from alembic import op
import sqlalchemy as sa

revision = '20261029_02'
down_revision = '20261029_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gtin_packaging',
        sa.Column('gtin', sa.String(14), primary_key=True),
        sa.Column('type_mask', sa.Integer(), nullable=False),
        sa.Column('observations', sa.Integer(), nullable=False),
        sa.Column('last_source', sa.String(16), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('type_mask BETWEEN 1 AND 7', name='ck_gtin_packaging_type_mask'),
        sa.CheckConstraint('observations > 0', name='ck_gtin_packaging_observations'))


def downgrade():
    op.drop_table('gtin_packaging')
