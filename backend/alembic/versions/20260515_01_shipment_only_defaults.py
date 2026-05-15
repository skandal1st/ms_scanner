"""default new documents to shipment

Revision ID: 20260515_01
Revises: 20260512_02
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260515_01"
down_revision: Union[str, None] = "20260512_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ALTER COLUMN kind SET DEFAULT 'demand'")


def downgrade() -> None:
    op.execute("ALTER TABLE documents ALTER COLUMN kind SET DEFAULT 'supply'")
