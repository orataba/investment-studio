from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
SNAPSHOTS_DIR = BACKEND_ROOT / "watchlist_migration_snapshots"
RUNTIME_PACKAGE_NAMES = ("app", "watchlist_app")


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assert_no_runtime_imports(path: Path) -> None:
    imported_modules = _imported_modules(path.read_text(encoding="utf-8"))
    offenders = sorted(
        module
        for module in imported_modules
        if any(module == package or module.startswith(f"{package}.") for package in RUNTIME_PACKAGE_NAMES)
    )
    assert not offenders, f"{path.name} must remain independent of runtime packages: {offenders}"


def test_watchlist_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        _assert_no_runtime_imports(migration_path)


def test_watchlist_migration_snapshots_do_not_import_runtime_modules() -> None:
    snapshot_paths = sorted(SNAPSHOTS_DIR.glob("*.py"))
    assert snapshot_paths
    for snapshot_path in snapshot_paths:
        _assert_no_runtime_imports(snapshot_path)


def test_watchlist_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-migrations.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    get_settings.cache_clear()

    inspector = inspect(create_engine(database_url))
    recalc_columns = {column["name"] for column in inspector.get_columns("recalc_job")}
    assert {"heartbeat_at", "lease_token"}.issubset(recalc_columns)

    expected_unique_indexes = {
        "recalc_job": "uq_recalc_job_running_instrument",
        "performance_snapshot": "uq_performance_snapshot_current_instrument",
        "risk_snapshot": "uq_risk_snapshot_current_instrument",
        "exposure_analytics_snapshot": "uq_exposure_snapshot_current_instrument",
        "holding_snapshot": "uq_holding_snapshot_current_instrument",
    }
    for table_name, index_name in expected_unique_indexes.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        assert indexes[index_name]["unique"] == 1

    tables = set(inspector.get_table_names())
    assert "instrument_score_snapshot" not in tables
    assert "instrument_rating_read_model" not in tables
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'instrument_name' "
                "AND source_metric_code = "
                "'watchlist_row_read_model.instrument_name'"
            )
        ) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0


def test_holding_revision_migration_rejects_duplicate_input_hashes(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'duplicate-holdings.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260809_0031")

    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for snapshot_id in ("holding-revision-1", "holding-revision-2"):
            connection.execute(
                text(
                    """
                    INSERT INTO holding_snapshot (
                        holding_snapshot_id,
                        instrument_id,
                        as_of_date,
                        source_cutoff_at,
                        methodology_version,
                        input_hash,
                        calculated_at,
                        superseded_at,
                        is_current,
                        source_record_id
                    ) VALUES (
                        :snapshot_id,
                        'fund-duplicate-holding',
                        :as_of_date,
                        :source_cutoff_at,
                        'holding-v1',
                        'same-input',
                        :calculated_at,
                        :superseded_at,
                        0,
                        NULL
                    )
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "as_of_date": date(2026, 8, 1),
                    "source_cutoff_at": now,
                    "calculated_at": now,
                    "superseded_at": now,
                },
            )

    with pytest.raises(RuntimeError, match="duplicate .*instrument_id, input_hash"):
        command.upgrade(config, "20260809_0032")
    get_settings.cache_clear()


def test_primary_display_field_migration_reconciles_upgraded_seed_data(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'primary-display-field.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260809_0035")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO watchlist ("
                "watchlist_id, name, description, owner_type, owner_id, "
                "is_default, is_shared, sort_order, created_at, updated_at"
                ") VALUES ("
                "'migration-watchlist', 'Migration Watchlist', NULL, "
                "'user', 'migration-test', 1, 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_view ("
                "watchlist_view_id, watchlist_id, name, description, kind, "
                "default_sort_json, default_filters_json, "
                "default_advanced_filter_json, default_group_by, density, "
                "is_default, created_at"
                ") VALUES ("
                "'migration-view', 'migration-watchlist', 'Migration View', "
                "NULL, 'table', '[]', '{}', '{}', NULL, NULL, 1, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_view_column ("
                "watchlist_view_id, field_key, display_order, width, "
                "is_visible, pin_side"
                ") VALUES ("
                "'migration-view', 'instrument_name', 0, 320, 1, NULL)"
            )
        )
        connection.execute(
            text(
                "UPDATE field_registry "
                "SET field_key = 'asset_name', "
                "source_metric_code = 'watchlist_row_read_model.asset_name' "
                "WHERE field_key = 'instrument_name'"
            )
        )
        connection.execute(
            text(
                "UPDATE watchlist_view_column SET field_key = 'asset_name' "
                "WHERE field_key = 'instrument_name'"
            )
        )
        view_id = "migration-view"
        connection.execute(
            text(
                "UPDATE watchlist_view "
                "SET default_sort_json = :sort_json, "
                "default_filters_json = :filters_json, "
                "default_group_by = 'asset_name' "
                "WHERE watchlist_view_id = :view_id"
            ),
            {
                "sort_json": '[{"field":"asset_name","direction":"asc"}]',
                "filters_json": '{"asset_name":"Audit"}',
                "view_id": view_id,
            },
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'instrument_name' "
                "AND source_metric_code = "
                "'watchlist_row_read_model.instrument_name'"
            )
        ) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM watchlist_view_column "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0
        migrated_view = connection.execute(
            text(
                "SELECT default_sort_json, default_filters_json, default_group_by "
                "FROM watchlist_view WHERE watchlist_view_id = :view_id"
            ),
            {"view_id": view_id},
        ).mappings().one()
        default_sort = migrated_view["default_sort_json"]
        default_filters = migrated_view["default_filters_json"]
        if isinstance(default_sort, str):
            default_sort = json.loads(default_sort)
        if isinstance(default_filters, str):
            default_filters = json.loads(default_filters)
        assert default_sort == [
            {"field": "instrument_name", "direction": "asc"}
        ]
        assert default_filters == {"instrument_name": "Audit"}
        assert migrated_view["default_group_by"] == "instrument_name"
    get_settings.cache_clear()
