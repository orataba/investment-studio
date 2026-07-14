from __future__ import annotations

import ast
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "portfolio_app")


@pytest.fixture(autouse=True)
def isolated_portfolio_store() -> None:
    """Migration tests own their database lifecycle and need no API store fixture."""


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_portfolio_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        imported_modules = _imported_modules(migration_path.read_text(encoding="utf-8"))
        offenders = sorted(
            module
            for module in imported_modules
            if any(
                module == package or module.startswith(f"{package}.")
                for package in RUNTIME_PACKAGE_NAMES
            )
        )
        assert not offenders, f"{migration_path.name} imports runtime packages: {offenders}"


def _migration_configs(database_url: str) -> tuple[Config, Config]:
    instrument_root = WORKSPACE_ROOT / "infra" / "instrument_registry"
    instrument_config = Config(str(instrument_root / "alembic.ini"))
    instrument_config.set_main_option(
        "script_location",
        str(instrument_root / "alembic"),
    )
    instrument_config.set_main_option("sqlalchemy.url", database_url)

    portfolio_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    portfolio_config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "alembic"),
    )
    portfolio_config.set_main_option("sqlalchemy.url", database_url)
    return instrument_config, portfolio_config


def _prepare_portfolio_database_at_0032(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Config, Engine]:
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv(
        "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA", "")

    from portfolio_app.core.settings import get_settings

    get_settings.cache_clear()
    instrument_config, portfolio_config = _migration_configs(database_url)
    command.upgrade(instrument_config, "head")
    # SQLite has no schemas, so both migration stacks otherwise share the
    # same default Alembic version table. Keep the instrument tables but hand
    # version ownership to the Portfolio migration stack.
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    command.upgrade(portfolio_config, "20260711_0032")
    return portfolio_config, engine


def _prepare_sqlite_exact_ledger_stamped_at_0042(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Config, Engine]:
    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        database_path,
        monkeypatch,
    )
    command.upgrade(portfolio_config, "20260713_0038")
    # 0039-0042 are PostgreSQL-only production storage migrations.  Stamp past
    # them to exercise 0043's explicitly supported SQLite shape gate.
    command.stamp(portfolio_config, "20260714_0042")
    return portfolio_config, engine


def test_0043_sqlite_current_shape_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_sqlite_exact_ledger_stamped_at_0042(
        tmp_path / "portfolio-0043-current.db",
        monkeypatch,
    )
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE name = 'transaction_revision_record'
                       OR tbl_name = 'transaction_revision_record'
                       OR name = 'transaction_current'
                    ORDER BY type, name
                    """
                )
            ).all()

        command.upgrade(portfolio_config, "20260714_0043")

        with engine.connect() as connection:
            after = connection.execute(
                text(
                    """
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE name = 'transaction_revision_record'
                       OR tbl_name = 'transaction_revision_record'
                       OR name = 'transaction_current'
                    ORDER BY type, name
                    """
                )
            ).all()
            assert after == before
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260714_0043"
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_0043_sqlite_partial_shape_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_sqlite_exact_ledger_stamped_at_0042(
        tmp_path / "portfolio-0043-partial.db",
        monkeypatch,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE transaction_revision_record "
                    "ADD COLUMN unreviewed_partial_drift TEXT"
                )
            )

        with pytest.raises(RuntimeError, match="partial or unknown"):
            command.upgrade(portfolio_config, "20260714_0043")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260714_0042"
            assert "unreviewed_partial_drift" in {
                column["name"]
                for column in inspect(connection).get_columns(
                    "transaction_revision_record"
                )
            }
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _seed_minimal_taxonomy(connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO portfolio_record (
                portfolio_id, portfolio_name, base_currency,
                valuation_timezone, valuation_cutoff_policy,
                securities_count, sort_order
            ) VALUES (
                'migration-test-portfolio', 'Migration Test Portfolio', 'USD',
                'UTC', 'end_of_day', 0, 0
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO taxonomy_record (
                taxonomy_id, portfolio_id, name, taxonomy_type, purpose,
                primary_assignment_scope, planning_enabled, budgeting_level,
                root_default_target_dimension, status, source_template_ref
            ) VALUES (
                'migration-test-taxonomy', 'migration-test-portfolio',
                'Migration Test Taxonomy', 'allocation', NULL, 'instrument',
                1, 'top_level', 'weight', 'active', NULL
            )
            """
        )
    )


@pytest.mark.parametrize(
    ("drift_case", "error_pattern"),
    (
        ("effective_window", "effective windows contain real values"),
        ("duplicate_assignment", "one assignment row"),
        ("duplicate_target_set", "Only one active TargetSet"),
    ),
)
def test_portfolio_schema_convergence_fails_closed_without_deleting_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_case: str,
    error_pattern: str,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / f"portfolio-schema-fail-closed-{drift_case}.db",
        monkeypatch,
    )
    try:
        with engine.begin() as connection:
            _seed_minimal_taxonomy(connection)
            if drift_case == "effective_window":
                for table_name in (
                    "taxonomy_record",
                    "taxonomy_assignment_record",
                    "target_set_record",
                ):
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN effective_from DATE"
                        )
                    )
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN effective_to DATE")
                    )
                connection.execute(
                    text(
                        "UPDATE taxonomy_record SET effective_from = '2026-01-01' "
                        "WHERE taxonomy_id = 'migration-test-taxonomy'"
                    )
                )
            elif drift_case == "duplicate_assignment":
                connection.execute(
                    text(
                        """
                        INSERT INTO taxonomy_assignment_record (
                            assignment_id, taxonomy_id, target_scope,
                            target_entity_id, taxonomy_node_id, status
                        ) VALUES
                            ('assignment-current', 'migration-test-taxonomy',
                             'instrument', 'same-instrument', 'node-a', 'active'),
                            ('assignment-archived', 'migration-test-taxonomy',
                             'instrument', 'same-instrument', 'node-b', 'archived')
                        """
                    )
                )
            else:
                connection.execute(
                    text(
                        """
                        INSERT INTO target_set_record (
                            target_set_id, taxonomy_id,
                            comparator_taxonomy_node_id, target_set_type, name,
                            weight_enabled, risk_budget_enabled, status, notes
                        ) VALUES
                            ('target-set-a', 'migration-test-taxonomy', NULL,
                             'saa', 'Target A', 1, 0, 'active', NULL),
                            ('target-set-b', 'migration-test-taxonomy', NULL,
                             'saa', 'Target B', 1, 0, 'active', NULL)
                        """
                    )
                )

        with pytest.raises(RuntimeError, match=error_pattern):
            command.upgrade(portfolio_config, "head")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260711_0032"
            assert connection.scalar(
                text("SELECT count(*) FROM taxonomy_record")
            ) == 1
            if drift_case == "duplicate_assignment":
                assert connection.scalar(
                    text("SELECT count(*) FROM taxonomy_assignment_record")
                ) == 2
            if drift_case == "duplicate_target_set":
                assert connection.scalar(
                    text("SELECT count(*) FROM target_set_record")
                ) == 2
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_portfolio_schema_convergence_repairs_deployed_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / "portfolio-schema-drift.db",
        monkeypatch,
    )
    try:
        with engine.begin() as connection:
            for table_name in (
                "taxonomy_record",
                "taxonomy_assignment_record",
                "target_set_record",
            ):
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN effective_from DATE")
                )
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN effective_to DATE")
                )
            connection.execute(
                text("DROP INDEX ix_portfolio_daily_holding_instrument_date")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_portfolio_daily_holding_asset_date "
                    "ON portfolio_daily_holding_snapshot "
                    "(portfolio_id, instrument_id, as_of_date)"
                )
            )
            connection.execute(
                text("DROP INDEX ix_transaction_record_portfolio_instrument_trade")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_transaction_record_portfolio_asset_trade "
                    "ON transaction_record "
                    "(portfolio_id, instrument_id, trade_date, trade_at)"
                )
            )
            connection.execute(
                text("DROP INDEX ix_target_set_record_taxonomy_scope_type")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_target_set_record_taxonomy_scope_type_effective "
                    "ON target_set_record "
                    "(taxonomy_id, comparator_taxonomy_node_id, "
                    "target_set_type, effective_from, target_set_id)"
                )
            )

        command.upgrade(portfolio_config, "20260713_0033")
        inspector = inspect(engine)
        for table_name in (
            "taxonomy_record",
            "taxonomy_assignment_record",
            "target_set_record",
        ):
            assert {
                column["name"] for column in inspector.get_columns(table_name)
            }.isdisjoint({"effective_from", "effective_to"})
        indexes = {
            table_name: {
                index["name"]: index
                for index in inspector.get_indexes(table_name)
            }
            for table_name in (
                "portfolio_daily_holding_snapshot",
                "transaction_record",
                "taxonomy_assignment_record",
            )
        }
        assert "ix_portfolio_daily_holding_instrument_date" in indexes[
            "portfolio_daily_holding_snapshot"
        ]
        assert "ix_transaction_record_portfolio_instrument_trade" in indexes[
            "transaction_record"
        ]
        assignment_index = indexes["taxonomy_assignment_record"][
            "uq_taxonomy_assignment_target"
        ]
        assert bool(assignment_index["unique"])
        with engine.connect() as connection:
            target_indexes = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type = 'index' AND tbl_name = 'target_set_record'"
                    )
                )
            }
        assert "ix_target_set_record_taxonomy_scope_type" in target_indexes
        active_scope_index = str(target_indexes["uq_target_set_active_scope"])
        assert "UNIQUE INDEX" in active_scope_index
        assert "coalesce(comparator_taxonomy_node_id, '')" in active_scope_index
        assert "WHERE status = 'active'" in active_scope_index

        with engine.begin() as connection:
            _seed_minimal_taxonomy(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO taxonomy_assignment_record (
                        assignment_id, taxonomy_id, target_scope,
                        target_entity_id, taxonomy_node_id, status
                    ) VALUES (
                        'assignment-current', 'migration-test-taxonomy',
                        'instrument', 'same-instrument', 'node-a', 'active'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO target_set_record (
                        target_set_id, taxonomy_id,
                        comparator_taxonomy_node_id, target_set_type, name,
                        weight_enabled, risk_budget_enabled, status, notes
                    ) VALUES (
                        'target-set-current', 'migration-test-taxonomy', NULL,
                        'saa', 'Current Target', 1, 0, 'active', NULL
                    )
                    """
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO taxonomy_assignment_record (
                            assignment_id, taxonomy_id, target_scope,
                            target_entity_id, taxonomy_node_id, status
                        ) VALUES (
                            'assignment-archived', 'migration-test-taxonomy',
                            'instrument', 'same-instrument', 'node-b', 'archived'
                        )
                        """
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO target_set_record (
                            target_set_id, taxonomy_id,
                            comparator_taxonomy_node_id, target_set_type, name,
                            weight_enabled, risk_budget_enabled, status, notes
                        ) VALUES (
                            'target-set-duplicate', 'migration-test-taxonomy', NULL,
                            'saa', 'Duplicate Target', 1, 0, 'active', NULL
                        )
                        """
                    )
                )
        engine.dispose()
    finally:
        get_settings.cache_clear()


def _assert_split_coverage_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    expected_columns = {
        "nav_coverage_state",
        "nav_coverage_reason_codes",
        "book_pnl_coverage_state",
        "book_pnl_coverage_reason_codes",
    }
    for table_name in (
        "portfolio_daily_snapshot",
        "portfolio_daily_contribution_slice",
    ):
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        assert "coverage_state" not in columns
        assert expected_columns <= columns.keys()
        for column_name in expected_columns:
            assert columns[column_name]["nullable"] is False
            assert columns[column_name]["default"] is None

        check_sql = " ".join(
            str(constraint.get("sqltext") or "")
            for constraint in inspector.get_check_constraints(table_name)
        )
        assert "nav_coverage_state" in check_sql
        assert "book_pnl_coverage_state" in check_sql

    snapshot_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("portfolio_daily_snapshot")
    }
    assert "ix_portfolio_daily_snapshot_portfolio_coverage" not in snapshot_indexes
    assert snapshot_indexes[
        "ix_portfolio_daily_snapshot_portfolio_nav_coverage"
    ]["column_names"] == ["portfolio_id", "nav_coverage_state", "as_of_date"]


def test_split_coverage_migration_reaches_last_sqlite_revision_on_empty_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / "portfolio-split-coverage-empty.db",
        monkeypatch,
    )
    try:
        command.upgrade(portfolio_config, "20260713_0038")
        _assert_split_coverage_schema(engine)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260713_0038"
            assert connection.scalar(
                text("SELECT count(*) FROM portfolio_daily_snapshot")
            ) == 0
            assert connection.scalar(
                text("SELECT count(*) FROM portfolio_daily_contribution_slice")
            ) == 0
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_split_coverage_migration_invalidates_seeded_legacy_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / "portfolio-split-coverage-seeded.db",
        monkeypatch,
    )
    try:
        command.upgrade(portfolio_config, "20260713_0033")
        legacy_snapshot = {
            "as_of_date": "2026-01-02",
            "coverage_state": "complete",
            "calculation_version": "legacy-mixed-coverage",
            "nav": 100.0,
            "realized_pnl": 1.0,
            "total_pnl": 2.0,
        }
        legacy_slice = {
            "as_of_date": "2026-01-02",
            "axis": "instrument",
            "group_key": "cash:USD",
            "coverage_state": "complete",
            "total_pnl": 2.0,
        }
        with engine.begin() as connection:
            _seed_minimal_taxonomy(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_calculation_state (
                        portfolio_id, daily_snapshot_status, dirty_from,
                        refresh_request_id, refresh_started_at,
                        refresh_completed_at, error_message
                    ) VALUES (
                        'migration-test-portfolio', 'current', '2026-01-02',
                        'legacy-request', '2026-01-02T01:00:00Z',
                        '2026-01-02T01:01:00Z', 'legacy-error'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_daily_snapshot (
                        portfolio_id, as_of_date, coverage_state, nav,
                        beginning_nav, ending_nav, daily_twr, cumulative_twr,
                        drawdown, snapshot_json, calculated_at
                    ) VALUES (
                        'migration-test-portfolio', '2026-01-02', 'complete',
                        100.0, 98.0, 100.0, 0.02, 0.02, 0.0,
                        :snapshot_json, '2026-01-02T01:01:00Z'
                    )
                    """
                ),
                {"snapshot_json": json.dumps(legacy_snapshot)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_daily_contribution_slice (
                        portfolio_id, as_of_date, axis, group_key, group_label,
                        coverage_state, total_pnl, daily_contribution,
                        slice_json, calculated_at
                    ) VALUES (
                        'migration-test-portfolio', '2026-01-02', 'instrument',
                        'cash:USD', 'USD Cash', 'complete', 2.0, 0.02,
                        :slice_json, '2026-01-02T01:01:00Z'
                    )
                    """
                ),
                {"slice_json": json.dumps(legacy_slice)},
            )

        command.upgrade(portfolio_config, "20260713_0034")
        _assert_split_coverage_schema(engine)

        with engine.connect() as connection:
            snapshot_row = connection.execute(
                text(
                    """
                    SELECT nav_coverage_state, nav_coverage_reason_codes,
                           book_pnl_coverage_state,
                           book_pnl_coverage_reason_codes, snapshot_json
                    FROM portfolio_daily_snapshot
                    WHERE portfolio_id = 'migration-test-portfolio'
                      AND as_of_date = '2026-01-02'
                    """
                )
            ).mappings().one()
            slice_row = connection.execute(
                text(
                    """
                    SELECT nav_coverage_state, nav_coverage_reason_codes,
                           book_pnl_coverage_state,
                           book_pnl_coverage_reason_codes, slice_json
                    FROM portfolio_daily_contribution_slice
                    WHERE portfolio_id = 'migration-test-portfolio'
                      AND as_of_date = '2026-01-02'
                    """
                )
            ).mappings().one()
            calculation_state = connection.execute(
                text(
                    """
                    SELECT daily_snapshot_status, dirty_from,
                           refresh_request_id, refresh_started_at,
                           refresh_completed_at, error_message
                    FROM portfolio_calculation_state
                    WHERE portfolio_id = 'migration-test-portfolio'
                    """
                )
            ).mappings().one()

        for row, payload_column in (
            (snapshot_row, "snapshot_json"),
            (slice_row, "slice_json"),
        ):
            assert row["nav_coverage_state"] == "unavailable"
            assert json.loads(row["nav_coverage_reason_codes"]) == [
                "coverage_contract_rebuild_required"
            ]
            assert row["book_pnl_coverage_state"] == "unavailable"
            assert json.loads(row["book_pnl_coverage_reason_codes"]) == [
                "coverage_contract_rebuild_required"
            ]
            payload = json.loads(row[payload_column])
            assert "coverage_state" not in payload
            assert payload["nav_coverage_state"] == "unavailable"
            assert payload["nav_coverage_reason_codes"] == [
                "coverage_contract_rebuild_required"
            ]
            assert payload["book_pnl_coverage_state"] == "unavailable"
            assert payload["book_pnl_coverage_reason_codes"] == [
                "coverage_contract_rebuild_required"
            ]

        migrated_snapshot = json.loads(snapshot_row["snapshot_json"])
        assert migrated_snapshot["calculation_version"] == (
            "invalidated-by-20260713-0034"
        )
        assert calculation_state == {
            "daily_snapshot_status": "stale",
            "dirty_from": None,
            "refresh_request_id": "coverage-contract-20260713-0034",
            "refresh_started_at": None,
            "refresh_completed_at": None,
            "error_message": None,
        }

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE portfolio_daily_snapshot
                        SET nav_coverage_state = 'legacy-mixed'
                        WHERE portfolio_id = 'migration-test-portfolio'
                        """
                    )
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_research_metrics_migration_invalidates_unversioned_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / "portfolio-research-metrics-invalidation.db",
        monkeypatch,
    )
    try:
        command.upgrade(portfolio_config, "20260713_0034")
        legacy_metrics = {
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "period_return": 0.02,
            "annualized_return": 0.083,
            "sharpe_ratio": 1.1,
            "calmar_ratio": 2.0,
        }
        with engine.begin() as connection:
            _seed_minimal_taxonomy(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO research_run_record (
                        research_run_id, portfolio_id, job_type, status,
                        requested_at, as_of_date, planning_taxonomy_id,
                        lookback_days, requested_by, headline, detail_json,
                        artifacts_json, request_payload_json
                    ) VALUES (
                        'legacy-research-run', 'migration-test-portfolio',
                        'target_weight_solve', 'completed',
                        '2026-04-01T00:00:00Z', '2026-03-31',
                        'migration-test-taxonomy', 90, 'migration-test',
                        'Legacy metrics', :detail_json, :artifacts_json, '{}'
                    )
                    """
                ),
                {
                    "detail_json": json.dumps(
                        {
                            "backtest": {
                                "metrics": legacy_metrics,
                                "warnings": [],
                                "points": [],
                            },
                            "backtest_benchmark": {
                                "metrics": dict(legacy_metrics),
                                "warnings": [],
                                "points": [],
                            },
                            "backtest_relative_metrics": dict(legacy_metrics),
                        }
                    ),
                    "artifacts_json": json.dumps(
                        [
                            {"artifact_id": "report", "path": "report.md"},
                            {"artifact_id": "summary", "path": "summary.json"},
                            {"artifact_id": "holdings", "path": "holdings.csv"},
                        ]
                    ),
                },
            )

        command.upgrade(portfolio_config, "20260713_0038")

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT detail_json, artifacts_json
                    FROM research_run_record
                    WHERE research_run_id = 'legacy-research-run'
                    """
                )
            ).mappings().one()
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260713_0038"

        detail = json.loads(row["detail_json"])
        assert detail["backtest"]["metrics"] is None
        assert detail["backtest_benchmark"]["metrics"] is None
        assert detail["backtest_relative_metrics"] is None
        assert detail["backtest"]["warnings"] == [
            "legacy_research_backtest_metrics_invalidated_v2"
        ]
        assert detail["backtest_benchmark"]["warnings"] == [
            "legacy_research_backtest_metrics_invalidated_v2"
        ]
        assert json.loads(row["artifacts_json"]) == []
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_revisioned_decimal_transaction_ledger_migrates_legacy_rows_on_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_portfolio_database_at_0032(
        tmp_path / "portfolio-revisioned-transaction-ledger.db",
        monkeypatch,
    )
    try:
        command.upgrade(portfolio_config, "20260713_0035")
        with engine.begin() as connection:
            _seed_minimal_taxonomy(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'migration-test-account', 'migration-test-portfolio',
                        'Migration Test Account', 'securities_account', 'USD', 'active'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_calculation_state (
                        portfolio_id, daily_snapshot_status, dirty_from,
                        refresh_request_id, refresh_started_at,
                        refresh_completed_at, error_message
                    ) VALUES (
                        'migration-test-portfolio', 'current', '2026-01-10',
                        'legacy-request', '2026-01-10T01:00:00Z',
                        '2026-01-10T01:01:00Z', 'legacy-error'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO transaction_record (
                        transaction_id, portfolio_id, transaction_type,
                        trade_date, trade_time, trade_at, trade_timezone,
                        trade_time_is_estimated, settlement_date,
                        entitlement_date, acquisition_date, account_id,
                        settlement_cash_account_id, instrument_id,
                        instrument_ref_json, quantity, price, gross_amount,
                        counter_amount, fx_rate, fees, taxes, currency,
                        transfer_scope, transfer_object_type, transfer_group_id,
                        counterparty_account_id, note, created_at
                    ) VALUES (
                        'txn-migration-0036', 'migration-test-portfolio', 'buy',
                        '2026-01-02', '09:30:00',
                        '2026-01-02T09:30:00+00:00', 'UTC', 0,
                        '2026-01-05', '2026-01-02', '2026-01-02',
                        'migration-test-account', NULL,
                        'instrument-migration-0036', :instrument_ref_json,
                        1.234567890123, 12.3456789012344, 15.24157875,
                        NULL, NULL, 0.12345678, 0.00000001, 'USD',
                        NULL, NULL, NULL, NULL, '  audit note  ',
                        '2026-01-02T10:00:00+00:00'
                    )
                    """
                ),
                {
                    "instrument_ref_json": json.dumps(
                        {
                            "instrument_id": "instrument-migration-0036",
                            "instrument_name": "Precision Fund",
                            "instrument_type": "fund",
                            "currency": "USD",
                            "identifiers": [],
                        }
                    )
                },
            )

        command.upgrade(portfolio_config, "20260713_0037")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260713_0037"
            before_0038 = {
                "identity": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_identity_record")
                    ).mappings().one()
                ),
                "group": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_revision_group_record")
                    ).mappings().one()
                ),
                "revision": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_revision_record")
                    ).mappings().one()
                ),
                "current": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_current")
                    ).mappings().one()
                ),
                "counts": {
                    relation_name: connection.scalar(
                        text(f"SELECT count(*) FROM {relation_name}")
                    )
                    for relation_name in (
                        "transaction_identity_record",
                        "transaction_revision_group_record",
                        "transaction_revision_record",
                        "transaction_current",
                    )
                },
            }
        assert before_0038["group"]["actor_source"] == "alembic"
        assert before_0038["current"]["actor_source"] == "alembic"

        command.upgrade(portfolio_config, "20260713_0038")

        inspector = inspect(engine)
        assert {
            "transaction_identity_record",
            "transaction_revision_group_record",
            "transaction_revision_record",
        } <= set(inspector.get_table_names())
        assert "transaction_record" not in inspector.get_table_names()
        assert "transaction_current" in inspector.get_view_names()

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260713_0038"
            identity = connection.execute(
                text(
                    """
                    SELECT transaction_id, portfolio_id, created_by
                    FROM transaction_identity_record
                    """
                )
            ).mappings().one()
            group = connection.execute(
                text(
                    """
                    SELECT revision_group_id, portfolio_id, source_kind,
                           actor_type, actor_id, actor_source, request_id,
                           idempotency_key, source_ref
                    FROM transaction_revision_group_record
                    """
                )
            ).mappings().one()
            revision_table = Table(
                "transaction_revision_record",
                MetaData(),
                autoload_with=connection,
            )
            revision = connection.execute(
                revision_table.select()
            ).mappings().one()
            current = connection.execute(
                text("SELECT * FROM transaction_current")
            ).mappings().one()
            after_0038 = {
                "identity": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_identity_record")
                    ).mappings().one()
                ),
                "group": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_revision_group_record")
                    ).mappings().one()
                ),
                "revision": dict(
                    connection.execute(
                        text("SELECT * FROM transaction_revision_record")
                    ).mappings().one()
                ),
                "current": dict(current),
                "counts": {
                    relation_name: connection.scalar(
                        text(f"SELECT count(*) FROM {relation_name}")
                    )
                    for relation_name in (
                        "transaction_identity_record",
                        "transaction_revision_group_record",
                        "transaction_revision_record",
                        "transaction_current",
                    )
                },
            }
            calculation_state = connection.execute(
                text(
                    """
                    SELECT daily_snapshot_status, dirty_from,
                           refresh_request_id, refresh_started_at,
                           refresh_completed_at, error_message
                    FROM portfolio_calculation_state
                    WHERE portfolio_id = 'migration-test-portfolio'
                    """
                )
            ).mappings().one()
            trigger_names = set(
                connection.execute(
                    text(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'trigger'
                          AND name LIKE 'trg_transaction_%'
                        """
                    )
                ).scalars()
            )

        expected_group_after_0038 = {
            **before_0038["group"],
            "actor_source": "migration",
        }
        expected_current_after_0038 = {
            **before_0038["current"],
            "actor_source": "migration",
        }
        assert after_0038 == {
            **before_0038,
            "group": expected_group_after_0038,
            "current": expected_current_after_0038,
        }
        assert after_0038["counts"] == {
            "transaction_identity_record": 1,
            "transaction_revision_group_record": 1,
            "transaction_revision_record": 1,
            "transaction_current": 1,
        }
        assert after_0038["group"]["source_ref"] == before_0038["group"]["source_ref"]
        assert after_0038["revision"]["payload_hash"] == before_0038["revision"][
            "payload_hash"
        ]

        assert identity == {
            "transaction_id": "txn-migration-0036",
            "portfolio_id": "migration-test-portfolio",
            "created_by": "system:migration:20260713_0036",
        }
        assert group["portfolio_id"] == "migration-test-portfolio"
        assert group["source_kind"] == "migration"
        assert group["actor_type"] == "migration"
        assert group["actor_id"] == "system:migration:20260713_0036"
        assert group["actor_source"] == "migration"
        assert group["request_id"] == "migration:20260713_0036"
        assert group["idempotency_key"] == (
            "migration:20260713_0036:baseline:migration-test-portfolio"
        )
        assert json.loads(group["source_ref"]) == {
            "legacy_transaction_count": 1,
            "entitlement_date_default_to_null": 1,
            "entitlement_date_default_transaction_ids": ["txn-migration-0036"],
            "acquisition_date_default_to_null": 1,
            "acquisition_date_default_transaction_ids": ["txn-migration-0036"],
            "decimal_rounding": {"price_rounded_to_scale_12": 1},
            "decimal_rounding_transaction_ids": {
                "price_rounded_to_scale_12": ["txn-migration-0036"],
            },
        }

        assert revision["transaction_id"] == "txn-migration-0036"
        assert revision["portfolio_id"] == "migration-test-portfolio"
        assert revision["revision_number"] == 1
        assert revision["revision_group_id"] == group["revision_group_id"]
        assert revision["revision_kind"] == "baseline"
        assert revision["is_tombstone"] is False
        assert revision["supersedes_revision_id"] is None
        assert revision["supersedes_revision_number"] is None
        assert revision["payload_schema_version"] == "transaction-revision.v1"
        assert revision["entitlement_date"] is None
        assert revision["acquisition_date"] is None
        assert revision["note"] == "  audit note  "
        assert {
            field_name: revision[field_name]
            for field_name in ("quantity", "price", "gross_amount", "fees", "taxes")
        } == {
            "quantity": Decimal("1.234567890123"),
            "price": Decimal("12.345678901234"),
            "gross_amount": Decimal("15.24157875"),
            "fees": Decimal("0.12345678"),
            "taxes": Decimal("0.00000001"),
        }

        canonical_facts = {
            "transaction_type": "buy",
            "trade_date": "2026-01-02",
            "trade_time": "09:30:00.000000",
            "trade_at": "2026-01-02T09:30:00.000000Z",
            "trade_timezone": "UTC",
            "trade_time_is_estimated": False,
            "settlement_date": "2026-01-05",
            "entitlement_date": None,
            "acquisition_date": None,
            "account_id": "migration-test-account",
            "settlement_cash_account_id": None,
            "instrument_id": "instrument-migration-0036",
            "instrument_snapshot_json": {
                "instrument_id": "instrument-migration-0036",
                "instrument_name": "Precision Fund",
                "instrument_type": "fund",
                "currency": "USD",
                "identifiers": [],
            },
            "quantity": "1.234567890123",
            "price": "12.345678901234",
                "gross_amount": "15.24157875",
                "counter_amount": None,
                "quoted_fx_rate": None,
                "fees": "0.12345678",
                "taxes": "0.00000001",
                "consideration_basis": "source_reported",
                "numeric_scale_state": "legacy_inferred",
                "quantity_input_scale": 12,
                "price_input_scale": 12,
                "gross_amount_input_scale": 8,
                "counter_amount_input_scale": None,
                "quoted_fx_rate_input_scale": None,
                "fees_input_scale": 8,
                "taxes_input_scale": 8,
                "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": "  audit note  ",
        }
        expected_payload_hash = "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    "payload_schema_version": "transaction-revision.v1",
                    "is_tombstone": False,
                    "facts": canonical_facts,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert revision["payload_hash"] == expected_payload_hash

        assert current["transaction_id"] == "txn-migration-0036"
        assert current["current_revision_id"] == revision["revision_id"]
        assert current["current_revision_number"] == 1
        assert current["revision_group_id"] == group["revision_group_id"]
        assert current["revision_kind"] == "baseline"
        assert current["payload_hash"] == expected_payload_hash
        assert current["created_by"] == "system:migration:20260713_0036"
        assert current["source_kind"] == "migration"
        assert current["entitlement_date"] is None
        assert current["acquisition_date"] is None
        assert current["note"] == "  audit note  "
        assert calculation_state == {
            "daily_snapshot_status": "stale",
            "dirty_from": "2026-01-02",
            "refresh_request_id": "ledger-contract:20260713_0036",
            "refresh_started_at": None,
            "refresh_completed_at": None,
            "error_message": None,
        }

        expected_triggers = {
            f"trg_{table_name}_{operation}_forbidden"
            for table_name in (
                "transaction_identity_record",
                "transaction_revision_group_record",
                "transaction_revision_record",
            )
            for operation in ("update", "delete")
        }
        assert expected_triggers <= trigger_names
        assert "trg_transaction_revision_record_transition" in trigger_names

        mutation_statements = (
            "UPDATE transaction_identity_record SET created_by = created_by "
            "WHERE transaction_id = 'txn-migration-0036'",
            "DELETE FROM transaction_identity_record "
            "WHERE transaction_id = 'txn-migration-0036'",
            "UPDATE transaction_revision_group_record "
            "SET change_reason = change_reason "
            f"WHERE revision_group_id = '{group['revision_group_id']}'",
            "DELETE FROM transaction_revision_group_record "
            f"WHERE revision_group_id = '{group['revision_group_id']}'",
            "UPDATE transaction_revision_record SET note = note "
            f"WHERE revision_id = '{revision['revision_id']}'",
            "DELETE FROM transaction_revision_record "
            f"WHERE revision_id = '{revision['revision_id']}'",
        )
        for statement in mutation_statements:
            with pytest.raises(IntegrityError, match="append-only"):
                with engine.begin() as connection:
                    connection.execute(text(statement))
    finally:
        engine.dispose()
        get_settings.cache_clear()
