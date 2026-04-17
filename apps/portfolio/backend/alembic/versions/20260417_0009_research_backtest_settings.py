"""extend research settings for taxonomy backtest

Revision ID: 20260417_0009
Revises: 20260417_0008
Create Date: 2026-04-17 19:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260417_0009"
down_revision = "20260417_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_settings_record", sa.Column("comparator_taxonomy_node_id", sa.String(), nullable=True))
    op.add_column("research_settings_record", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column(
        "research_settings_record",
        sa.Column("target_set_mode", sa.String(), nullable=False, server_default="taa_over_saa"),
    )
    op.add_column(
        "research_settings_record",
        sa.Column("target_dimension", sa.String(), nullable=False, server_default="scope_default"),
    )
    op.add_column(
        "research_settings_record",
        sa.Column("rebalance_frequency", sa.String(), nullable=False, server_default="monthly"),
    )

    op.execute(
        "UPDATE research_settings_record "
        "SET run_template = 'taxonomy_backtest' "
        "WHERE run_template IS NULL OR run_template = ''"
    )
    op.execute(
        "UPDATE research_settings_record "
        "SET target_set_mode = 'taa_over_saa', target_dimension = 'scope_default', rebalance_frequency = 'monthly' "
        "WHERE target_set_mode IS NULL OR target_dimension IS NULL OR rebalance_frequency IS NULL"
    )

def downgrade() -> None:
    op.drop_column("research_settings_record", "rebalance_frequency")
    op.drop_column("research_settings_record", "target_dimension")
    op.drop_column("research_settings_record", "target_set_mode")
    op.drop_column("research_settings_record", "start_date")
    op.drop_column("research_settings_record", "comparator_taxonomy_node_id")
