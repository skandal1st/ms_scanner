"""Allow the ERP delivery queue to also carry marketplace outcomes."""
from alembic import op

revision = "20261125_01"
down_revision = "20261120_01"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("monitoring_deliveries", "incident_id", nullable=True)


def downgrade():
    # Pending widget deliveries must be drained before downgrading.
    op.execute("DELETE FROM monitoring_deliveries WHERE incident_id IS NULL AND delivered_at IS NOT NULL")
    op.alter_column("monitoring_deliveries", "incident_id", nullable=False)
