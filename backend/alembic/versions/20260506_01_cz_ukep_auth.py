"""cz ukep auth fields

Revision ID: 20260506_01
Revises:
Create Date: 2026-05-06

Adds fields to `integrations` to support УКЭП-based ЧЗ authorization
(CryptoPro Browser Plugin flow). The access_token is short-lived (~1h)
and obtained per user via challenge/sign/exchange.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260506_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("cz_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("cz_cert_thumbprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("cz_cert_subject", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "cz_auth_method",
            sa.String(length=16),
            nullable=False,
            server_default="mock",
        ),
    )


def downgrade() -> None:
    op.drop_column("integrations", "cz_auth_method")
    op.drop_column("integrations", "cz_cert_subject")
    op.drop_column("integrations", "cz_cert_thumbprint")
    op.drop_column("integrations", "cz_token_expires_at")
