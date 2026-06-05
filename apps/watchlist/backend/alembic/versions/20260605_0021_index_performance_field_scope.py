"""expand index-compatible performance fields

Revision ID: 20260605_0021
Revises: 20260602_0020
Create Date: 2026-06-05 16:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260605_0021"
down_revision = "20260602_0020"
branch_labels = None
depends_on = None


INDEX_PERFORMANCE_FIELD_KEYS = [
    "return_ytd",
    "return_mtd",
    "return_1w",
    "return_1m",
    "return_1y",
    "annualized_return",
    "return_3y",
    "return_5y",
    "max_drawdown",
    "attr.current_drawdown",
    "volatility",
    "sharpe_ratio",
]
FUND_PRODUCT_SCOPE = ["mutual_fund", "cef", "etf"]


def _field_table() -> sa.TableClause:
    return sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
    )


def upgrade() -> None:
    field_table = _field_table()
    op.get_bind().execute(
        sa.update(field_table)
        .where(field_table.c.field_key.in_(INDEX_PERFORMANCE_FIELD_KEYS))
        .values(instrument_scope_json=["fund", "index"], product_scope_json=[])
    )


def downgrade() -> None:
    field_table = _field_table()
    op.get_bind().execute(
        sa.update(field_table)
        .where(field_table.c.field_key.in_(INDEX_PERFORMANCE_FIELD_KEYS))
        .values(instrument_scope_json=["fund"], product_scope_json=FUND_PRODUCT_SCOPE)
    )
