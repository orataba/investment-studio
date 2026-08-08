from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260806_0043")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "20260807_0044"
MIGRATION_PARENT = "20260806_0043"
NEW_TABLES = {
    "portfolio_analytics_policy_state",
    "analytics_scope_policy_record",
    "analytics_taxonomy_selection_record",
    "taxonomy_configuration_revision",
}
NEW_RESEARCH_COLUMNS = {
    "backtest_cash_yield_annual",
    "backtest_commission_bps",
    "backtest_tax_bps",
    "backtest_slippage_bps",
    "backtest_implementation_delay_days",
    "backtest_robustness_scenarios_json",
    "backtest_walk_forward_training_months",
    "backtest_walk_forward_test_months",
}


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_analytics_scope_research_migration_is_constrained_guarded_and_reversible() -> None:
    engine = get_engine()
    config = _alembic_config()

    with engine.connect() as connection:
        before_inspector = sa.inspect(connection)
        before_tables = set(before_inspector.get_table_names())
        before_transaction_columns = {
            str(column["name"])
            for column in before_inspector.get_columns("transaction_record")
        }
        before_research_columns = {
            str(column["name"])
            for column in before_inspector.get_columns("research_settings_record")
        }
        transaction_count = int(
            connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record")) or 0
        )

    assert transaction_count > 0
    assert "transaction_sequence" not in before_transaction_columns
    assert NEW_TABLES.isdisjoint(before_tables)
    assert NEW_RESEARCH_COLUMNS.isdisjoint(before_research_columns)

    try:
        command.upgrade(config, MIGRATION_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            table_names = set(inspector.get_table_names())
            transaction_columns = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            research_columns = {
                str(column["name"])
                for column in inspector.get_columns("research_settings_record")
            }
            research_checks = {
                str(check["name"])
                for check in inspector.get_check_constraints("research_settings_record")
            }
            transaction_indexes = {
                str(index["name"]): index
                for index in inspector.get_indexes("transaction_record")
                if index.get("name")
            }
            sequence_rows = connection.execute(
                sa.text(
                    "SELECT transaction_id, transaction_sequence "
                    "FROM transaction_record ORDER BY transaction_sequence"
                )
            ).all()

        assert NEW_TABLES.issubset(table_names)
        assert "transaction_sequence" in transaction_columns
        assert NEW_RESEARCH_COLUMNS.issubset(research_columns)
        assert any(
            check.endswith("research_backtest_execution_costs")
            for check in research_checks
        )
        assert any(
            check.endswith("research_backtest_walk_forward_windows")
            for check in research_checks
        )
        assert transaction_indexes["uq_transaction_record_transaction_sequence"][
            "unique"
        ]
        assert len(sequence_rows) == transaction_count
        sequences = [int(row.transaction_sequence) for row in sequence_rows]
        assert all(sequence > 0 for sequence in sequences)
        assert len(set(sequences)) == transaction_count

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO analytics_scope_policy_record ("
                    "analytics_scope_policy_id, portfolio_id, taxonomy_id, "
                    "taxonomy_node_id, risk_eligible, risk_budget_eligible, "
                    "performance_scope, valuation_basis, exclusion_reason, "
                    "effective_from, effective_to, policy_version, "
                    "superseded_by_policy_id, created_at"
                    ") VALUES ("
                    "'migration-policy-1', 'portfolio-ops', 'migration-taxonomy', "
                    "'migration-node', 1, 1, 'ordinary', 'market', NULL, "
                    "'2026-01-01', NULL, 1, NULL, '2026-08-07T00:00:00Z'"
                    ")"
                )
            )

        with pytest.raises(RuntimeError, match="analytics policies"):
            command.downgrade(config, MIGRATION_PARENT)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM analytics_scope_policy_record "
                    "WHERE analytics_scope_policy_id = 'migration-policy-1'"
                )
            )
        command.downgrade(config, MIGRATION_PARENT)

        with engine.connect() as connection:
            downgraded_inspector = sa.inspect(connection)
            downgraded_tables = set(downgraded_inspector.get_table_names())
            downgraded_transaction_columns = {
                str(column["name"])
                for column in downgraded_inspector.get_columns("transaction_record")
            }
            downgraded_research_columns = {
                str(column["name"])
                for column in downgraded_inspector.get_columns(
                    "research_settings_record"
                )
            }
            downgraded_transaction_count = int(
                connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record"))
                or 0
            )

        assert NEW_TABLES.isdisjoint(downgraded_tables)
        assert "transaction_sequence" not in downgraded_transaction_columns
        assert NEW_RESEARCH_COLUMNS.isdisjoint(downgraded_research_columns)
        assert downgraded_transaction_count == transaction_count
    finally:
        if set(sa.inspect(engine).get_table_names()).issuperset(NEW_TABLES):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "DELETE FROM analytics_scope_policy_record "
                        "WHERE analytics_scope_policy_id = 'migration-policy-1'"
                    )
                )
        command.upgrade(config, "head")
