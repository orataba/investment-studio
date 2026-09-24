"""Remove unused current research preferences, retaining saved runs.

The former training/test months only sliced an already-computed simulation.
They never controlled estimation or execution. Alternative-cost scenarios are
also retired. Historical request and result JSON remains untouched; a downgrade
restores only the old setting defaults, with no current scenario configuration.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260924_0069"
down_revision = "20260923_0068"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_constraint("research_backtest_walk_forward_windows", type_="check")
        batch_op.drop_column("backtest_walk_forward_training_months")
        batch_op.drop_column("backtest_walk_forward_test_months")
        batch_op.drop_column("backtest_robustness_scenarios_json")


def downgrade():
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(sa.Column(
            "backtest_walk_forward_training_months", sa.Integer(),
            nullable=False, server_default="24",
        ))
        batch_op.add_column(sa.Column(
            "backtest_walk_forward_test_months", sa.Integer(),
            nullable=False, server_default="6",
        ))
        batch_op.add_column(sa.Column(
            "backtest_robustness_scenarios_json", sa.JSON(), nullable=True,
        ))
        batch_op.create_check_constraint(
            "research_backtest_walk_forward_windows",
            "backtest_walk_forward_training_months > 0 AND backtest_walk_forward_test_months > 0",
        )
