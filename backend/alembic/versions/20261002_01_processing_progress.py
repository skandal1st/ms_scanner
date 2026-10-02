"""Progress for resumable large MoySklad transfers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261002_01"
down_revision = "20260927_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("processing_progress", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("documents", "processing_progress")
