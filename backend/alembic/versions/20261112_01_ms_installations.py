"""Scope MoySklad accounts to their application installation."""
import sqlalchemy as sa
from alembic import op

revision = "20261112_01"
down_revision = "20261109_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("integrations", sa.Column("moysklad_app_id", sa.String(64),
                  nullable=False, server_default="8fa85fbe-eae4-4445-a431-212f735ee9e5"))
    op.create_unique_constraint("uq_ms_installation", "integrations", ["moysklad_account_id", "moysklad_app_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM integrations WHERE moysklad_app_id <> '8fa85fbe-eae4-4445-a431-212f735ee9e5')")):
        raise RuntimeError("Нельзя объединять установленные версии решения")
    op.drop_constraint("uq_ms_installation", "integrations", type_="unique")
    op.drop_column("integrations", "moysklad_app_id")
