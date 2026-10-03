"""Keep the customer order identity for TSD shipment sessions."""
from alembic import op
import sqlalchemy as sa

revision = "20261003_01"
down_revision = "20261002_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("moysklad_customer_order_id", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("customer_order_name", sa.String(500), nullable=True))
    op.create_index("ix_documents_moysklad_customer_order_id", "documents", ["moysklad_customer_order_id"])


def downgrade():
    op.drop_index("ix_documents_moysklad_customer_order_id", table_name="documents")
    op.drop_column("documents", "customer_order_name")
    op.drop_column("documents", "moysklad_customer_order_id")
