"""materialize portfolio daily snapshots

Revision ID: 20260502_0017
Revises: 20260430_0016
Create Date: 2026-05-02 18:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260502_0017"
down_revision = "20260430_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_daily_snapshot",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("coverage_state", sa.String(), nullable=False),
        sa.Column("nav", sa.Float(), nullable=True),
        sa.Column("beginning_nav", sa.Float(), nullable=True),
        sa.Column("ending_nav", sa.Float(), nullable=True),
        sa.Column("daily_twr", sa.Float(), nullable=True),
        sa.Column("cumulative_twr", sa.Float(), nullable=True),
        sa.Column("drawdown", sa.Float(), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("portfolio_id", "as_of_date"),
    )
    op.create_index(
        "ix_portfolio_daily_snapshot_portfolio_coverage",
        "portfolio_daily_snapshot",
        ["portfolio_id", "coverage_state", "as_of_date"],
    )

    op.create_table(
        "portfolio_calculation_state",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), nullable=False),
        sa.Column("daily_snapshot_status", sa.String(), nullable=False, server_default="stale"),
        sa.Column("dirty_from", sa.Date(), nullable=True),
        sa.Column("refreshed_from", sa.Date(), nullable=True),
        sa.Column("refreshed_to", sa.Date(), nullable=True),
        sa.Column("refreshed_at", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("portfolio_id"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_calculation_state")
    op.drop_index("ix_portfolio_daily_snapshot_portfolio_coverage", table_name="portfolio_daily_snapshot")
    op.drop_table("portfolio_daily_snapshot")
