"""document plan + unique scan codes

Revision ID: 20260509_02
Revises: 20260509_01
Create Date: 2026-05-09

Добавляет:
- documents.plan (JSONB) — план сборки [{gtin, product_id, product_name, expected_qty}].
  Пустой массив для документов без МС-привязки.
- Уникальный индекс scans (document_id, code) — защита от дублей кодов в одном документе.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260509_02"
down_revision: Union[str, None] = "20260509_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_scans_document_code_unique",
        "scans",
        ["document_id", "code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_scans_document_code_unique", table_name="scans")
    op.drop_column("documents", "plan")
