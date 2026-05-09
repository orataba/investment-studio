"""rename watchlist chart column copy

Revision ID: 20260509_0019
Revises: 20260508_0018
Create Date: 2026-05-09 13:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260509_0019"
down_revision = "20260508_0018"
branch_labels = None
depends_on = None


def _set_price_chart_copy(*, label: str, description: str) -> None:
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
    )
    op.get_bind().execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "price_chart_1m")
        .values(label=label, description=description)
    )


def upgrade() -> None:
    _set_price_chart_copy(
        label="Chart 1M",
        description="1-month NAV chart from the current chart read model.",
    )


def downgrade() -> None:
    _set_price_chart_copy(
        label="Spark Chart",
        description="1-month NAV spark chart from the current chart read model.",
    )
