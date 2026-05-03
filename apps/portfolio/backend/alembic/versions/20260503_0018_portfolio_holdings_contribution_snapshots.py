"""materialize portfolio holdings and contribution slices

Revision ID: 20260503_0018
Revises: 20260502_0017
Create Date: 2026-05-03 00:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260503_0018"
down_revision = "20260502_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_daily_holding_snapshot",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("cost_basis", sa.Float(), nullable=True),
        sa.Column("cost_basis_base", sa.Float(), nullable=True),
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("market_value_base", sa.Float(), nullable=True),
        sa.Column("portfolio_weight", sa.Float(), nullable=True),
        sa.Column("holding_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("portfolio_id", "as_of_date", "account_id", "asset_id"),
    )
    op.create_index(
        "ix_portfolio_daily_holding_portfolio_date",
        "portfolio_daily_holding_snapshot",
        ["portfolio_id", "as_of_date"],
    )
    op.create_index(
        "ix_portfolio_daily_holding_asset_date",
        "portfolio_daily_holding_snapshot",
        ["portfolio_id", "asset_id", "as_of_date"],
    )
    op.create_index(
        "ix_portfolio_daily_holding_account_date",
        "portfolio_daily_holding_snapshot",
        ["portfolio_id", "account_id", "as_of_date"],
    )

    op.create_table(
        "portfolio_daily_contribution_slice",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("axis", sa.String(), nullable=False),
        sa.Column("group_key", sa.String(), nullable=False),
        sa.Column("group_label", sa.String(), nullable=False),
        sa.Column("coverage_state", sa.String(), nullable=False),
        sa.Column("total_pnl", sa.Float(), nullable=True),
        sa.Column("daily_contribution", sa.Float(), nullable=True),
        sa.Column("slice_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("portfolio_id", "as_of_date", "axis", "group_key"),
    )
    op.create_index(
        "ix_portfolio_daily_contribution_axis_date",
        "portfolio_daily_contribution_slice",
        ["portfolio_id", "axis", "as_of_date"],
    )
    op.create_index(
        "ix_portfolio_daily_contribution_group_date",
        "portfolio_daily_contribution_slice",
        ["portfolio_id", "axis", "group_key", "as_of_date"],
    )

    op.execute("UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', error_message = NULL")


def downgrade() -> None:
    op.drop_index("ix_portfolio_daily_contribution_group_date", table_name="portfolio_daily_contribution_slice")
    op.drop_index("ix_portfolio_daily_contribution_axis_date", table_name="portfolio_daily_contribution_slice")
    op.drop_table("portfolio_daily_contribution_slice")
    op.drop_index("ix_portfolio_daily_holding_account_date", table_name="portfolio_daily_holding_snapshot")
    op.drop_index("ix_portfolio_daily_holding_asset_date", table_name="portfolio_daily_holding_snapshot")
    op.drop_index("ix_portfolio_daily_holding_portfolio_date", table_name="portfolio_daily_holding_snapshot")
    op.drop_table("portfolio_daily_holding_snapshot")
