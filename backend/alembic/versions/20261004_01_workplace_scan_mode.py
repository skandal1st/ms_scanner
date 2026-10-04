"""Persist workstation COM/TSD collection mode."""
from alembic import op
import sqlalchemy as sa
revision = "20261004_01"
down_revision = "20261003_04"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("workplaces", sa.Column("scan_mode", sa.String(8), nullable=False, server_default="com"))
    op.create_check_constraint("ck_workplace_scan_mode", "workplaces", "scan_mode IN ('com', 'tsd')")
def downgrade():
    op.drop_constraint("ck_workplace_scan_mode", "workplaces", type_="check")
    op.drop_column("workplaces", "scan_mode")
