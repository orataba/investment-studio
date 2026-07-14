from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"
VERSIONS_DIR = MIGRATIONS_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "platform_app", "portfolio_ops_instrument_core")


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_instrument_registry_revisions_do_not_import_runtime_modules() -> None:
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


def test_instrument_registry_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-migrations.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")

    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "head")


def test_corporate_action_migration_seeds_confirmed_semiconductor_etf_splits(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-populated.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260712_0006")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json, market_data_updated_at
                ) VALUES (
                    '159516-sz', '半导体设备ETF国泰', 'etf', 'CNY',
                    '{}', '{}', '{}', '{\"status\": \"active\"}', NULL
                )
                """
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT effective_date, record_date, new_units, old_units,
                       quantity_rounding, status, source
                FROM corporate_action_event
                WHERE instrument_id = '159516-sz'
                ORDER BY effective_date
                """
            )
        ).mappings().all()

    assert [str(row["effective_date"]) for row in rows] == ["2026-03-30", "2026-07-10"]
    assert [str(row["record_date"]) for row in rows] == ["2026-03-27", "2026-07-09"]
    assert all(row["new_units"] == "2" and row["old_units"] == "1" for row in rows)
    assert [row["quantity_rounding"] for row in rows] == ["exact", "truncate"]
    assert all(row["status"] == "confirmed" for row in rows)
    assert [row["source"] for row in rows] == [
        "fund_manager_announcement",
        "distributor_mirror",
    ]
