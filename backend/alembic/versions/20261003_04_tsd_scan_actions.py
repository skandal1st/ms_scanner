"""Track terminal scan inputs for safe collaborative undo."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_04"
down_revision = "20261003_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tsd_scan_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tsd_devices.id"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scans.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)))
    for column in ("device_id", "document_id", "scan_id"):
        op.create_index(f"ix_tsd_scan_actions_{column}", "tsd_scan_actions", [column])
    op.create_index("ix_tsd_scan_actions_undo", "tsd_scan_actions",
                    ["device_id", "document_id", "created_at", "id"],
                    postgresql_where=sa.text("undone_at IS NULL AND scan_id IS NOT NULL"))


def downgrade():
    op.drop_table("tsd_scan_actions")
