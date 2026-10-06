"""Marketplace subscriptions; existing marketplace accounts await synchronization."""
from alembic import op
import sqlalchemy as sa

revision = '20261006_01'
down_revision = '20261005_05'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('subscription_managed', 'subscription_active', 'subscription_trial'):
        op.add_column('integrations', sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('integrations', sa.Column('subscription_tariff_id', sa.String(64)))
    for name in ('subscription_expires_at', 'subscription_updated_at'):
        op.add_column('integrations', sa.Column(name, sa.DateTime(timezone=True)))
    # OAuth accounts also have accountId. Only known marketplace editions are marked here.
    op.execute("UPDATE integrations i SET subscription_managed = true FROM users u "
               "WHERE i.user_id = u.id AND u.edition = 'ms_lite' AND i.moysklad_account_id IS NOT NULL")


def downgrade():
    for name in ('subscription_updated_at', 'subscription_expires_at', 'subscription_tariff_id',
                 'subscription_trial', 'subscription_active', 'subscription_managed'):
        op.drop_column('integrations', name)
