from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
        "instrument_score_snapshot": "uq_score_snapshot_current_instrument",
        "holding_snapshot": "uq_holding_snapshot_current_instrument",
    }
    for table_name, index_name in expected_unique_indexes.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        assert indexes[index_name]["unique"] == 1
