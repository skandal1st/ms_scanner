"""Packaging evidence separated by GTIN and code format."""
from alembic import op
import sqlalchemy as sa

revision = '20261030_01'
down_revision = '20261029_02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gtin_packaging_formats',
        sa.Column('gtin', sa.String(14), primary_key=True),
        sa.Column('code_format', sa.String(24), primary_key=True),
        sa.Column('type_mask', sa.Integer(), nullable=False),
        sa.Column('observations', sa.Integer(), nullable=False),
        sa.Column('last_quantity', sa.Integer(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('type_mask BETWEEN 1 AND 7', name='ck_packaging_format_mask'),
        sa.CheckConstraint('observations > 0', name='ck_packaging_format_observations'))


def downgrade():
    op.drop_table('gtin_packaging_formats')
