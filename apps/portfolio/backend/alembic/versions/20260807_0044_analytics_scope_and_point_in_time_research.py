"""Add analytics scope policy and point-in-time research identity.

Revision ID: 20260807_0044
Revises: 20260806_0043
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260807_0044"
down_revision = "20260806_0043"
branch_labels = None
depends_on = None


TRANSACTION_SEQUENCE_INDEX = "uq_transaction_record_transaction_sequence"


def _backfill_transaction_sequence() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT transaction_id FROM transaction_record "
            "ORDER BY created_at, transaction_id"
        )
    ).fetchall()
    used: set[int] = set()
    parsed_by_id: dict[str, int] = {}
    for row in rows:
        transaction_id = str(row[0])
        suffix = transaction_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            candidate = int(suffix)
            if candidate > 0 and candidate not in used:
                parsed_by_id[transaction_id] = candidate
                used.add(candidate)

    next_sequence = max(used, default=0) + 1
    for row in rows:
        transaction_id = str(row[0])
        sequence = parsed_by_id.get(transaction_id)
        if sequence is None:
            while next_sequence in used:
                next_sequence += 1
            sequence = next_sequence
            used.add(sequence)
            next_sequence += 1
        connection.execute(
            sa.text(
                "UPDATE transaction_record "
                "SET transaction_sequence = :sequence "
                "WHERE transaction_id = :transaction_id"
            ),
            {"sequence": sequence, "transaction_id": transaction_id},
        )


def upgrade() -> None:
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.add_column(sa.Column("transaction_sequence", sa.Integer(), nullable=True))
    _backfill_transaction_sequence()
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.alter_column("transaction_sequence", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(
            TRANSACTION_SEQUENCE_INDEX,
            ["transaction_sequence"],
            unique=True,
        )
        batch_op.drop_index("ix_transaction_record_portfolio_trade_sort")
        batch_op.create_index(
            "ix_transaction_record_portfolio_trade_sort",
            [
                "portfolio_id",
                "trade_date",
                "trade_at",
                "created_at",
                "transaction_sequence",
            ],
        )

    op.create_table(
        "portfolio_analytics_policy_state",
        sa.Column(
            "portfolio_id",
            sa.String(),
            sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("current_version >= 0", name="ck_analytics_policy_state_version"),
    )
    op.create_table(
        "analytics_scope_policy_record",
        sa.Column("analytics_scope_policy_id", sa.String(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.String(),
            sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "taxonomy_id",
            sa.String(),
            nullable=False,
        ),
        sa.Column("taxonomy_node_id", sa.String(), nullable=False),
        sa.Column("risk_eligible", sa.Boolean(), nullable=False),
        sa.Column("risk_budget_eligible", sa.Boolean(), nullable=False),
        sa.Column("performance_scope", sa.String(), nullable=False),
        sa.Column("valuation_basis", sa.String(), nullable=False),
        sa.Column("exclusion_reason", sa.String(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("superseded_by_policy_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_analytics_scope_policy_effective_range",
        ),
        sa.CheckConstraint(
            "performance_scope IN ('ordinary', 'derivative_lifecycle', 'operational_only', 'unallocated')",
            name="ck_analytics_scope_policy_performance_scope",
        ),
        sa.CheckConstraint(
            "valuation_basis IN ('market', 'fair_value', 'carrying', 'event', 'obligation', 'cash', 'unknown')",
            name="ck_analytics_scope_policy_valuation_basis",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "policy_version",
            name="uq_analytics_scope_policy_portfolio_version",
        ),
    )
    op.create_index(
        "ix_analytics_scope_policy_resolve",
        "analytics_scope_policy_record",
        [
            "portfolio_id",
            "taxonomy_id",
            "taxonomy_node_id",
            "effective_from",
            "effective_to",
            "superseded_by_policy_id",
        ],
    )

    op.create_table(
        "analytics_taxonomy_selection_record",
        sa.Column("analytics_taxonomy_selection_id", sa.String(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.String(),
            sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("taxonomy_id", sa.String(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("superseded_by_selection_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_analytics_taxonomy_selection_effective_range",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "selection_version",
            name="uq_analytics_taxonomy_selection_portfolio_version",
        ),
    )
    op.create_index(
        "ix_analytics_taxonomy_selection_resolve",
        "analytics_taxonomy_selection_record",
        [
            "portfolio_id",
            "effective_from",
            "effective_to",
            "superseded_by_selection_id",
        ],
    )

    op.create_table(
        "taxonomy_configuration_revision",
        sa.Column("taxonomy_configuration_revision_id", sa.String(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.String(),
            sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "taxonomy_id",
            sa.String(),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("superseded_by_revision_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_taxonomy_configuration_revision_effective_range",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "configuration_version",
            name="uq_taxonomy_configuration_revision_portfolio_version",
        ),
    )
    op.create_index(
        "ix_taxonomy_configuration_revision_resolve",
        "taxonomy_configuration_revision",
        [
            "portfolio_id",
            "taxonomy_id",
            "effective_from",
            "effective_to",
            "superseded_by_revision_id",
        ],
    )

    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(
            sa.Column("backtest_cash_yield_annual", sa.Float(), nullable=False, server_default="0.02")
        )
        batch_op.add_column(
            sa.Column("backtest_commission_bps", sa.Float(), nullable=False, server_default="2")
        )
        batch_op.add_column(
            sa.Column("backtest_tax_bps", sa.Float(), nullable=False, server_default="10")
        )
        batch_op.add_column(
            sa.Column("backtest_slippage_bps", sa.Float(), nullable=False, server_default="5")
        )
        batch_op.add_column(
            sa.Column("backtest_implementation_delay_days", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("backtest_robustness_scenarios_json", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("backtest_walk_forward_training_months", sa.Integer(), nullable=False, server_default="24")
        )
        batch_op.add_column(
            sa.Column("backtest_walk_forward_test_months", sa.Integer(), nullable=False, server_default="6")
        )
        batch_op.create_check_constraint(
            "research_backtest_execution_costs",
            "backtest_cash_yield_annual >= -1 AND "
            "backtest_commission_bps >= 0 AND backtest_tax_bps >= 0 AND "
            "backtest_slippage_bps >= 0 AND backtest_implementation_delay_days >= 0",
        )
        batch_op.create_check_constraint(
            "research_backtest_walk_forward_windows",
            "backtest_walk_forward_training_months > 0 AND backtest_walk_forward_test_months > 0",
        )


def _guard_downgrade() -> None:
    connection = op.get_bind()
    policy_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM analytics_scope_policy_record")).scalar()
        or 0
    )
    revision_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM taxonomy_configuration_revision")).scalar()
        or 0
    )
    selection_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM analytics_taxonomy_selection_record")).scalar()
        or 0
    )
    configured_research_count = int(
        connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM research_settings_record WHERE "
                "backtest_cash_yield_annual != 0.02 OR backtest_commission_bps != 2 OR "
                "backtest_tax_bps != 10 OR backtest_slippage_bps != 5 OR "
                "backtest_implementation_delay_days != 1 OR "
                "backtest_robustness_scenarios_json IS NOT NULL OR "
                "backtest_walk_forward_training_months != 24 OR "
                "backtest_walk_forward_test_months != 6"
            )
        ).scalar()
        or 0
    )
    if policy_count or revision_count or selection_count or configured_research_count:
        raise RuntimeError(
            "Cannot downgrade 20260807_0044 while analytics policies, taxonomy "
            "selection/configuration history, or non-default backtest settings exist."
        )


def downgrade() -> None:
    _guard_downgrade()
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_constraint("research_backtest_walk_forward_windows", type_="check")
        batch_op.drop_constraint("research_backtest_execution_costs", type_="check")
        batch_op.drop_column("backtest_walk_forward_test_months")
        batch_op.drop_column("backtest_walk_forward_training_months")
        batch_op.drop_column("backtest_robustness_scenarios_json")
        batch_op.drop_column("backtest_implementation_delay_days")
        batch_op.drop_column("backtest_slippage_bps")
        batch_op.drop_column("backtest_tax_bps")
        batch_op.drop_column("backtest_commission_bps")
        batch_op.drop_column("backtest_cash_yield_annual")

    op.drop_index(
        "ix_taxonomy_configuration_revision_resolve",
        table_name="taxonomy_configuration_revision",
    )
    op.drop_table("taxonomy_configuration_revision")
    op.drop_index(
        "ix_analytics_taxonomy_selection_resolve",
        table_name="analytics_taxonomy_selection_record",
    )
    op.drop_table("analytics_taxonomy_selection_record")
    op.drop_index(
        "ix_analytics_scope_policy_resolve",
        table_name="analytics_scope_policy_record",
    )
    op.drop_table("analytics_scope_policy_record")
    op.drop_table("portfolio_analytics_policy_state")

    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_index("ix_transaction_record_portfolio_trade_sort")
        batch_op.create_index(
            "ix_transaction_record_portfolio_trade_sort",
            [
                "portfolio_id",
                "trade_date",
                "trade_at",
                "created_at",
                "transaction_id",
            ],
        )
        batch_op.drop_index(TRANSACTION_SEQUENCE_INDEX)
        batch_op.drop_column("transaction_sequence")
