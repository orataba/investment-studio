"""seed default portfolio

Revision ID: 20260416_0003
Revises: 20260416_0002
Create Date: 2026-04-16 16:30:00
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa


revision = "20260416_0003"
down_revision = "20260416_0002"
branch_labels = None
depends_on = None


portfolio_record = sa.table(
    "portfolio_record",
    sa.column("portfolio_id", sa.String()),
    sa.column("portfolio_name", sa.String()),
    sa.column("base_currency", sa.String()),
    sa.column("valuation_timezone", sa.String()),
    sa.column("valuation_cutoff_policy", sa.String()),
    sa.column("as_of_date", sa.Date()),
    sa.column("nav", sa.Float()),
    sa.column("day_change_value", sa.Float()),
    sa.column("day_change_pct", sa.Float()),
    sa.column("securities_count", sa.Integer()),
    sa.column("sort_order", sa.Integer()),
)


def upgrade() -> None:
    bind = op.get_bind()
    has_portfolios = bind.execute(sa.select(sa.func.count()).select_from(portfolio_record)).scalar_one()
    if has_portfolios:
        return

    op.bulk_insert(
        portfolio_record,
        [
            {
                "portfolio_id": "yungu",
                "portfolio_name": "Yungu",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": date(2026, 4, 16),
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
    )


def downgrade() -> None:
    op.execute(sa.delete(portfolio_record).where(portfolio_record.c.portfolio_id == "yungu"))
