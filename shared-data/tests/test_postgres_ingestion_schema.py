from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from studio_data.core.settings import get_settings


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.postgresql_integration


@pytest.fixture
def ingestion_database(monkeypatch):
    base_url = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not base_url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    database_name = f"investment_studio_ingestion_{uuid4().hex[:8]}"
    url = make_url(base_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    admin = sa.create_engine(
        make_url(base_url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    try:
        for namespace in ("HOME", "INSTRUMENT_DATA", "DATA", "PORTFOLIO", "WATCHLIST", "MARKET", "BRIEFING"):
            monkeypatch.setenv(f"INVESTMENT_STUDIO_{namespace}_DATABASE_URL", url)
            monkeypatch.setenv(f"INVESTMENT_STUDIO_{namespace}_ALEMBIC_DATABASE_URL", url)
        for key, value in {
            "INSTRUMENT_DATA_SCHEMA": "instrument_data",
            "DATA_DATABASE_SCHEMA": "instrument_data",
            "DATA_OPERATIONS_DATABASE_SCHEMA": "data_ingestion",
            "PORTFOLIO_DATABASE_SCHEMA": "portfolio",
            "WATCHLIST_DATABASE_SCHEMA": "watchlist",
            "MIGRATION_EXPECTED_DATABASE": database_name,
        }.items():
            monkeypatch.setenv(f"INVESTMENT_STUDIO_{key}", value)
        get_settings.cache_clear()
        yield url
    finally:
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        admin.dispose()


def _migrate_all() -> None:
    subprocess.run(
        [str(ROOT / "infra/scripts/migrate_all.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PROJECT_ROOT": str(ROOT),
            "PYTHON_BIN": sys.executable,
            "ENV_ROOT": "",
        },
        check=True,
        capture_output=True,
        text=True,
    )


def _config() -> Config:
    migration_root = ROOT / "shared-data"
    config = Config(str(migration_root / "alembic.ini"))
    config.set_main_option("script_location", str(migration_root / "alembic"))
    return config


def _objects(connection, schema):
    return connection.execute(sa.text(
        "SELECT c.oid, c.relname, c.relacl::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        # Reference storage was introduced after the schema rename under test;
        # its own downgrades intentionally remove and recreate these objects.
        "WHERE n.nspname = :schema AND c.relname NOT LIKE '%instrument_reference_snapshot%' "
        "AND c.relname NOT LIKE '%reference_observation%' ORDER BY c.oid"
    ), {"schema": schema}).all()


def _assert_renamed_functions_and_writes(engine):
    with engine.connect() as connection:
        functions = connection.execute(sa.text(
            "SELECT n.nspname, p.oid::regprocedure::text, p.proconfig "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname IN ('instrument_data', 'data_ingestion') "
            "AND p.proconfig IS NOT NULL"
        )).all()
        assert len(functions) == 12
        for schema, name, config in functions:
            expected = (
                "search_path=instrument_data, public"
                if schema == "instrument_data"
                else "search_path=data_ingestion, instrument_data, public"
            )
            assert config == [expected], name

        # The caller path must not rescue stale paths captured by the triggers.
        connection.exec_driver_sql("SET LOCAL search_path TO public")
        connection.exec_driver_sql("""
            INSERT INTO instrument_data.instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json
            ) VALUES (
                'rename-write-test', 'Rename Write Test', 'private_fund', 'CNY',
                '{"trading":["official_nav"],"valuation":["official_nav"],
                  "total_return":["total_return_nav"],"chart":["total_return_nav"],
                  "reference":["official_nav"]}',
                '{"source_mode":"email","source_email":"nav@example.test"}',
                '{}', '{"status":"active"}'
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO instrument_data.instrument_market_data (
                instrument_id, metric_family, quote_basis, as_of_date,
                value, currency, price_unit, price_scale, provider, status,
                nav_lineage_kind, nav_lineage_evidence_json
            ) VALUES (
                'rename-write-test', 'nav', 'official_nav', '2026-09-04',
                '1.0', 'CNY', 'per_unit', 1, 'email', 'complete',
                'provider_explicit', '{"source":"migration-test"}'
            )
        """)
        connection.exec_driver_sql("""
            UPDATE instrument_data.instrument_market_data SET value='1.1'
            WHERE instrument_id='rename-write-test'
        """)
        connection.exec_driver_sql("""
            INSERT INTO instrument_data.fund_nav_projection_run (
                fund_nav_projection_run_id, instrument_id, input_fingerprint,
                source_observation_fingerprint, projection_kind, projection_status,
                method_version, source_provider, evidence_json, created_by, created_at
            ) VALUES (
                'fund-nav-projection-' || repeat('a', 64), 'rename-write-test',
                repeat('a', 64), repeat('b', 64), 'event_derived', 'unavailable',
                'test/v1', 'email', '{"unavailable_reason":"no anchor"}',
                'migration-test', '2026-09-04T00:00:00Z'
            )
        """)
        connection.rollback()


def test_fresh_install_and_populated_schema_rename_preserve_data(ingestion_database):
    _migrate_all()
    engine = sa.create_engine(ingestion_database)
    try:
        _assert_renamed_functions_and_writes(engine)
        with engine.begin() as connection:
            assert "platform" not in sa.inspect(connection).get_schema_names()
            connection.exec_driver_sql(
                "INSERT INTO data_ingestion.platform_metadata (metadata_key, value_json) "
                "VALUES ('rename-test', '{\"cursor\":123,\"status\":\"parsed\"}')"
            )
            objects = _objects(connection, "data_ingestion")
            asset_objects = _objects(connection, "instrument_data")
            assert asset_objects
            assert "instrument_registry" not in sa.inspect(connection).get_schema_names()
            foreign_key = connection.scalar(sa.text(
                "SELECT oid FROM pg_constraint "
                "WHERE conname = 'fk_email_nav_candidate_route_instrument_id_instrument'"
            ))
            assert foreign_key is not None

        registry_root = ROOT / "shared-data/instruments"
        registry_config = Config(str(registry_root / "alembic.ini"))
        registry_config.set_main_option("script_location", str(registry_root / "alembic"))
        command.downgrade(registry_config, "20260902_0029")
        with engine.connect() as connection:
            assert _objects(connection, "instrument_registry") == asset_objects

        # A populated pre-rename database exercises the actual upgrade path.
        command.downgrade(_config(), "20260823_0007")
        with engine.begin() as connection:
            # Reproduce function settings captured by a real pre-rename install,
            # not the dual-name path used when today's migrator builds from zero.
            functions = connection.scalars(sa.text(
                "SELECT p.oid::regprocedure::text FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname IN ('instrument_registry', 'platform')"
            )).all()
            for name in functions:
                connection.exec_driver_sql(
                    f"ALTER FUNCTION {name} "
                    "SET search_path TO platform, instrument_registry, public"
                )
        with engine.connect() as connection:
            assert "data_ingestion" not in sa.inspect(connection).get_schema_names()
            assert _objects(connection, "platform") == objects
        _migrate_all()
        _migrate_all()  # Reinstall must not recreate an empty legacy schema.
        _assert_renamed_functions_and_writes(engine)
        # Match the documented CLI path, which limits autogenerate reflection to
        # ingestion rather than including the shared Registry search path.
        subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=ROOT / "shared-data",
            env={
                **os.environ,
                "PYTHONPATH": (
                    f"{ROOT}/shared-data:"
                    f"{ROOT}/shared-data/instruments/python"
                ),
            },
            check=True,
        )
        with engine.connect() as connection:
            assert "platform" not in sa.inspect(connection).get_schema_names()
            assert _objects(connection, "data_ingestion") == objects
            assert _objects(connection, "instrument_data") == asset_objects
            assert connection.scalar(sa.text(
                "SELECT value_json FROM data_ingestion.platform_metadata WHERE metadata_key='rename-test'"
            )) == {"cursor": 123, "status": "parsed"}
            assert connection.scalar(sa.text(
                "SELECT version_num FROM data_ingestion.platform_alembic_version"
            )) == "20260904_0009"
            assert connection.scalar(sa.text(
                "SELECT oid FROM pg_constraint "
                "WHERE conname = 'fk_email_nav_candidate_route_instrument_id_instrument'"
            )) == foreign_key

        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE SCHEMA platform")
        with pytest.raises(RuntimeError, match="Both platform and data_ingestion"):
            command.upgrade(_config(), "head")
        with engine.connect() as connection:
            assert _objects(connection, "data_ingestion") == objects
    finally:
        engine.dispose()
