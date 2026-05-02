"""clean up research target solve configuration columns

Revision ID: 20260430_0016
Revises: 20260423_0015
Create Date: 2026-04-30 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0016"
down_revision = "20260423_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_column("rebalance_frequency")
        batch_op.drop_column("target_set_mode")
        batch_op.drop_column("run_template")
        batch_op.drop_column("benchmark_mode")
        batch_op.drop_column("start_date")

    with op.batch_alter_table("research_run_record") as batch_op:
        batch_op.drop_column("run_template")
        batch_op.drop_column("benchmark_mode")


def downgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("benchmark_mode", sa.String(), nullable=False, server_default="none"))
        batch_op.add_column(
            sa.Column("run_template", sa.String(), nullable=False, server_default="target_weight_solve")
        )
        batch_op.add_column(sa.Column("target_set_mode", sa.String(), nullable=False, server_default="taa_over_saa"))
        batch_op.add_column(sa.Column("rebalance_frequency", sa.String(), nullable=False, server_default="monthly"))

    with op.batch_alter_table("research_run_record") as batch_op:
        batch_op.add_column(sa.Column("benchmark_mode", sa.String(), nullable=False, server_default="none"))
        batch_op.add_column(
            sa.Column("run_template", sa.String(), nullable=False, server_default="target_weight_solve")
        )
