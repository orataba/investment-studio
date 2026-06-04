"""add research backtest controls

Revision ID: 20260602_0028
Revises: 20260528_0027
Create Date: 2026-06-02 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260602_0028"
down_revision = "20260528_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(
            sa.Column("backtest_rebalance_frequency", sa.String(), nullable=False, server_default="1m")
        )
        batch_op.add_column(sa.Column("backtest_benchmark_instrument_id", sa.String(), nullable=True))
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.alter_column("backtest_rebalance_frequency", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_column("backtest_benchmark_instrument_id")
        batch_op.drop_column("backtest_rebalance_frequency")
