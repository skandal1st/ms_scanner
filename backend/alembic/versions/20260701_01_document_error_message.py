"""Приёмка: причина неуспешной отправки документа в МС.

Document.error_message — текст ошибки процессинга (напр. истёк токен ЧЗ, коды
упаковок не удалось развернуть). Показывается на странице приёмки вместо ложного
«МойСклад ещё обрабатывает».

Revision ID: 20260701_01
Revises: 20260627_01
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260701_01"
down_revision = "20260627_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "error_message")
