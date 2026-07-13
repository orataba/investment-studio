from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import (
    InstrumentChartReadModel,
    InstrumentPerformanceReadModel,
    InstrumentRiskReadModel,
    InstrumentSummaryReadModel,
)
from watchlist_app.db.models.watchlists import Watchlist, WatchlistItem
from portfolio_ops_instrument_core import instrument_store as shared_store


pytestmark = pytest.mark.postgresql_integration

DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
)

WATERMARK_MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "20260713_0028_rename_market_data_input_watermark.py"
)
AUDIT_LIVE_DATA_PATH = WORKSPACE_ROOT / "infra" / "scripts" / "audit_live_data.py"


def _run_instrument_registry_upgrade() -> None:
    config = Config(str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
    )
    command.upgrade(config, "head")


def _run_watchlist_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _run_watchlist_upgrade_until_fk(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "1b7d2e8c4f90")


def _seed_required_instrument_registry_rows(database_url: str, instrument_ids: list[str]) -> None:
    empty_json = json.dumps({})
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for instrument_id in instrument_ids:
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM instrument_registry.instrument WHERE instrument_id = :instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                ).scalar()
                if exists:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.instrument (
                            instrument_id,
                            instrument_name,
                            instrument_type,
                            currency,
                            quote_selection_policy_json,
                            source_settings_json,
                            refresh_status_json,
                            lifecycle_state_json
                        )
                        VALUES (
                            :instrument_id,
                            :instrument_name,
                            :instrument_type,
                            :currency,
                            CAST(:quote_selection_policy_json AS jsonb),
                            CAST(:source_settings_json AS jsonb),
                            CAST(:refresh_status_json AS jsonb),
                            CAST(:lifecycle_state_json AS jsonb)
                        )
                        """
                    ),
                    {
                        "instrument_id": instrument_id,
                        "instrument_name": instrument_id.replace("-", " ").title(),
                        "instrument_type": "fund",
                        "currency": "USD",
                        "quote_selection_policy_json": empty_json,
                        "source_settings_json": empty_json,
                        "refresh_status_json": empty_json,
                        "lifecycle_state_json": json.dumps({"status": "active"}),
                    },
                )
    finally:
        engine.dispose()


def _seed_instrument_ids_required_by_watchlist_baseline(database_url: str) -> None:
    _run_watchlist_upgrade_until_fk(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT instrument_id
                    FROM (
                        SELECT instrument_id FROM watchlist.instrument_detail
                        UNION
                        SELECT instrument_id FROM watchlist.watchlist_item
                    ) AS seeded_instruments
                    WHERE instrument_id IS NOT NULL
                    ORDER BY instrument_id
                    """
                )
            )
            instrument_ids = [str(row.instrument_id) for row in rows]
    finally:
        engine.dispose()
    _seed_required_instrument_registry_rows(database_url, instrument_ids)
    _run_watchlist_upgrade(database_url)


def _admin_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(database="postgres").render_as_string(hide_password=False)


def _is_postgres_server_unavailable(error: OperationalError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate is not None:
        return str(sqlstate).startswith("08") or sqlstate == "57P03"
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "connection timed out",
            "timeout expired",
            "could not translate host name",
            "name or service not known",
            "nodename nor servname provided",
            "network is unreachable",
            "no route to host",
        )
    )


@pytest.fixture
def postgres_watchlist_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    base_database_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_watchlist_fk_{uuid4().hex[:8]}"
    database_url = make_url(base_database_url).set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    database_created = False
    settings_module = None
    session_module = None
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as exc:  # pragma: no cover - environment-dependent skip
            if _is_postgres_server_unavailable(exc):
                pytest.skip(f"PostgreSQL server is unavailable for integration tests: {exc}")
            pytest.fail(
                "PostgreSQL integration test connection was rejected. Verify the "
                "portfolio_ops_test credentials created by "
                f"infra/launchd/bootstrap_local_database.sh. Cause: {exc}"
            )

        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        except Exception as exc:
            pytest.fail(
                "PostgreSQL integration test database creation failed. "
                "Ensure the dedicated portfolio_ops_test role exists and has CREATEDB "
                "permission (run infra/launchd/bootstrap_local_database.sh), or set "
                f"PORTFOLIO_OPS_TEST_POSTGRES_URL explicitly. Cause: {exc}"
            )
        database_created = True

        monkeypatch.setenv("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE", database_name)
        monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
        monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry")
        monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
        monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL", database_url)
        monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "watchlist")

        from watchlist_app.core import settings as watchlist_settings_module
        from watchlist_app.db import session as watchlist_session_module

        settings_module = watchlist_settings_module
        session_module = watchlist_session_module
        settings_module.get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_session_factory.cache_clear()

        try:
            _run_instrument_registry_upgrade()
            _seed_instrument_ids_required_by_watchlist_baseline(database_url)

            session_factory = session_module.get_session_factory()
            identifier_value = f"WATCHFK{uuid4().hex[:8].upper()}"
            instrument = shared_store.create_instrument(
                session_factory,
                instrument_name="Watchlist FK Integration Asset",
                instrument_type="fund",
                currency="USD",
                identifiers=[
                    {
                        "identifier_type": "ticker",
                        "identifier_value": identifier_value,
                        "is_primary": True,
                    }
                ],
            )
        except Exception as exc:
            pytest.fail(
                "PostgreSQL integration database setup or migration failed; "
                f"the temporary database will be removed. Cause: {exc}"
            )

        yield {
            "database_url": database_url,
            "database_schema": "watchlist",
            "instrument_id": str(instrument["instrument_id"]),
        }
    finally:
        admin_engine.dispose()
        if session_module is not None:
            try:
                session_module.get_engine().dispose()
            except Exception:
                # Database cleanup below is the stronger isolation guarantee.
                pass
            finally:
                session_module.get_engine.cache_clear()
                session_module.get_session_factory.cache_clear()
        if settings_module is not None:
            settings_module.get_settings.cache_clear()

        if database_created:
            cleanup_engine = create_engine(
                _admin_database_url(base_database_url),
                isolation_level="AUTOCOMMIT",
            )
            try:
                with cleanup_engine.connect() as connection:
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
            finally:
                cleanup_engine.dispose()


def test_watchlist_instrument_registry_foreign_keys_are_enforced(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module

    engine = create_engine(postgres_watchlist_env["database_url"])
    try:
        with engine.connect() as connection:
            constraints = connection.execute(
                text(
                    """
                    SELECT
                        con.conname AS constraint_name,
                        cls.relname AS table_name,
                        ref_ns.nspname AS referred_schema,
                        ref_cls.relname AS referred_table
                    FROM pg_constraint con
                    JOIN pg_class cls
                        ON cls.oid = con.conrelid
                    JOIN pg_namespace cls_ns
                        ON cls_ns.oid = cls.relnamespace
                    JOIN pg_class ref_cls
                        ON ref_cls.oid = con.confrelid
                    JOIN pg_namespace ref_ns
                        ON ref_ns.oid = ref_cls.relnamespace
                    WHERE cls_ns.nspname = :schema
                      AND cls.relname IN ('instrument_detail', 'watchlist_item')
                      AND con.conname IN (
                        'fk_instrument_detail_instrument_id_instrument',
                        'fk_watchlist_item_instrument_id_instrument'
                      )
                    """
                ),
                {"schema": postgres_watchlist_env["database_schema"]},
            ).mappings().all()
    finally:
        engine.dispose()

    by_name = {item["constraint_name"]: item for item in constraints}
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["table_name"] == "watchlist_item"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_schema"] == "instrument_registry"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_table"] == "instrument"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["table_name"] == "instrument_detail"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_schema"] == "instrument_registry"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_table"] == "instrument"

    session_factory = session_module.get_session_factory()
    watchlist_id = f"watchlist-fk-{uuid4().hex[:8]}"

    with session_factory() as session:
        session.add(
            Watchlist(
                watchlist_id=watchlist_id,
                name="Constraint Verification Watchlist",
                description=None,
                owner_type="user",
                owner_id="integration",
                is_default=False,
                is_shared=False,
                sort_order=0,
            )
        )
        session.add(
            InstrumentDetail(
                instrument_id=postgres_watchlist_env["instrument_id"],
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Watchlist FK Integration Asset",
                primary_identifier_type="ticker",
                primary_identifier_value="WATCHFK",
                is_active=True,
                metadata_json={},
            )
        )
        session.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=postgres_watchlist_env["instrument_id"],
                added_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        session.commit()

    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id=f"missing-{uuid4().hex[:8]}",
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Missing Shared Instrument",
                primary_identifier_type="ticker",
                primary_identifier_value="MISSINGFK",
                is_active=True,
                metadata_json={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_factory() as session:
        session.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=f"missing-{uuid4().hex[:8]}",
                added_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_recalc_instrument_advisory_lock_serializes_transactions(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository

    repository = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    with session_factory() as first, session_factory() as second:
        assert repository.acquire_instrument_lock(
            first,
            instrument_id=instrument_id,
            wait=False,
        ) is True
        assert repository.acquire_instrument_lock(
            second,
            instrument_id=instrument_id,
            wait=False,
        ) is False
        first.commit()
        assert repository.acquire_instrument_lock(
            second,
            instrument_id=instrument_id,
            wait=False,
        ) is True
        second.rollback()


def test_postgres_recalc_claim_serializes_job_types_for_one_instrument(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

    repository = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id=instrument_id,
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="PostgreSQL claim asset",
                primary_identifier_type="ticker",
                primary_identifier_value="CLAIMLOCK",
                is_active=True,
                metadata_json={},
            )
        )
        for job_type in ("performance", "all"):
            repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type=job_type,
                instrument_id=instrument_id,
                trigger_type="integration_test",
                trigger_ref_type=None,
                trigger_ref_id=None,
                job_status="queued",
                priority=90,
                dedupe_key=make_recalc_dedupe_key(
                    job_type=job_type,
                    instrument_id=instrument_id,
                ),
                payload_json={"requested_by": "integration_test"},
            )
        session.commit()

    with session_factory() as session:
        first = repository.claim_next_queued(session)
        assert first is not None
        first_job_id = first.recalc_job_id
        first_lease = str(first.lease_token)
        session.commit()

    with session_factory() as session:
        assert repository.claim_next_queued(session) is None
        running = repository.get(session, first_job_id)
        assert running is not None
        assert repository.mark_completed(
            session,
            running,
            lease_token=first_lease,
            payload_json={"integration_test": True},
        ) is True
        session.commit()

    with session_factory() as session:
        second = repository.claim_next_queued(session)
        assert second is not None
        assert second.recalc_job_id != first_job_id
        assert second.instrument_id == instrument_id
        session.rollback()


def test_postgres_market_data_watermark_rename_is_value_preserving(
    postgres_watchlist_env: dict[str, str],
) -> None:
    """Exercise the exact 0028 operations in an isolated PostgreSQL schema."""

    schema_name = f"watermark_migration_{uuid4().hex[:12]}"
    table_names = (
        "instrument_summary_read_model",
        "instrument_chart_read_model",
        "instrument_performance_read_model",
        "instrument_risk_read_model",
        "performance_snapshot",
        "risk_snapshot",
    )
    source_time = datetime(2026, 7, 12, 3, 4, 5, 678901, tzinfo=UTC)
    comparison_time = datetime(2026, 7, 13, 9, 8, 7, 123456, tzinfo=UTC)
    engine = create_engine(postgres_watchlist_env["database_url"])
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.execute(
                text(f'SET LOCAL search_path TO "{schema_name}"')
            )
            for table_name in table_names:
                connection.execute(
                    text(
                        f'CREATE TABLE "{table_name}" ('
                        "row_id text PRIMARY KEY, "
                        "source_cutoff_at timestamptz NOT NULL, "
                        "comparison_at timestamptz NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        f'INSERT INTO "{table_name}" '
                        "(row_id, source_cutoff_at, comparison_at) "
                        "VALUES ('row-1', :source_time, :comparison_time)"
                    ),
                    {
                        "source_time": source_time,
                        "comparison_time": comparison_time,
                    },
                )
                connection.execute(
                    text(
                        f'CREATE TABLE "{table_name}_before" AS '
                        f'SELECT row_id, source_cutoff_at FROM "{table_name}"'
                    )
                )

            spec = importlib.util.spec_from_file_location(
                f"watchlist_watermark_migration_{uuid4().hex}",
                WATERMARK_MIGRATION_PATH,
            )
            assert spec is not None and spec.loader is not None
            migration_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration_module)
            migration_module.op = Operations(
                MigrationContext.configure(connection)
            )

            migration_module.upgrade()
            for table_name in table_names:
                difference_count = connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM ("
                        f'(SELECT row_id, market_data_input_watermark_at AS value FROM "{table_name}" '
                        f' EXCEPT SELECT row_id, source_cutoff_at AS value FROM "{table_name}_before") '
                        "UNION ALL "
                        f'(SELECT row_id, source_cutoff_at AS value FROM "{table_name}_before" '
                        f' EXCEPT SELECT row_id, market_data_input_watermark_at AS value FROM "{table_name}")'
                        ") AS difference"
                    )
                )
                assert difference_count == 0
                nullable = connection.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :schema_name "
                        "AND table_name = :table_name "
                        "AND column_name = 'market_data_input_watermark_at'"
                    ),
                    {"schema_name": schema_name, "table_name": table_name},
                )
                assert nullable == "YES"

            migration_module.downgrade()
            for table_name in table_names:
                difference_count = connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM ("
                        f'(SELECT row_id, source_cutoff_at AS value FROM "{table_name}" '
                        f' EXCEPT SELECT row_id, source_cutoff_at AS value FROM "{table_name}_before") '
                        "UNION ALL "
                        f'(SELECT row_id, source_cutoff_at AS value FROM "{table_name}_before" '
                        f' EXCEPT SELECT row_id, source_cutoff_at AS value FROM "{table_name}")'
                        ") AS difference"
                    )
                )
                assert difference_count == 0
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
    finally:
        engine.dispose()


def test_live_audit_rejects_unbounded_materialized_quote_lineage(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module

    module_name = f"portfolio_ops_live_audit_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, AUDIT_LIVE_DATA_PATH)
    assert spec is not None and spec.loader is not None
    audit_module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = audit_module
    try:
        spec.loader.exec_module(audit_module)
    finally:
        sys.modules.pop(module_name, None)

    instrument_id = postgres_watchlist_env["instrument_id"]

    def bounded_resolution(scope: str) -> dict[str, object]:
        return {
            "schema_version": "watchlist_quote_resolution_summary.v1",
            "resolution_status": "resolved",
            "scope": scope,
            "observation_count": 12,
            "adopted_point_count": 10,
            "first_observation_date": "2025-01-31",
            "last_observation_date": "2026-01-31",
            "reason_codes": ["partial_series"],
            "calculation_dependency": {
                "fingerprint": f"bounded-{scope}",
                "revision_count": 12,
                "excluded_revision_count": 2,
            },
            "consumer_dependency": {
                "fingerprint": f"consumer-bounded-{scope}",
            },
        }

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id=instrument_id,
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Bounded lineage audit asset",
                primary_identifier_type="ticker",
                primary_identifier_value="BOUNDEDAUDIT",
                is_active=True,
                metadata_json={},
            )
        )
        session.add_all(
            [
                InstrumentChartReadModel(
                    instrument_id=instrument_id,
                    payload_json={
                        "resolution": bounded_resolution("chart-current"),
                        # Chart presentation points are outside the resolution
                        # lineage object and therefore outside this audit boundary.
                        "series": [{"points": [{"date": "2026-01-31", "value": 1}]}],
                    },
                    data_freshness_status="fresh",
                    last_recalculated_at=None,
                    market_data_input_watermark_at=None,
                ),
                InstrumentSummaryReadModel(
                    instrument_id=instrument_id,
                    payload_json={
                        "quote_resolution": bounded_resolution("summary-current")
                    },
                    data_freshness_status="fresh",
                    last_recalculated_at=None,
                    market_data_input_watermark_at=None,
                ),
                InstrumentPerformanceReadModel(
                    instrument_id=instrument_id,
                    payload_json={
                        "quote_resolution": bounded_resolution(
                            "performance-current"
                        ),
                        "historical_quote_resolution": bounded_resolution(
                            "performance-historical"
                        ),
                    },
                    data_freshness_status="fresh",
                    last_recalculated_at=None,
                    market_data_input_watermark_at=None,
                ),
                InstrumentRiskReadModel(
                    instrument_id=instrument_id,
                    payload_json={
                        "quote_resolution": bounded_resolution("risk-current"),
                        "historical_quote_resolution": bounded_resolution(
                            "risk-historical"
                        ),
                    },
                    data_freshness_status="fresh",
                    last_recalculated_at=None,
                    market_data_input_watermark_at=None,
                ),
            ]
        )
        session.commit()

    engine = create_engine(postgres_watchlist_env["database_url"])
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                text(audit_module.WATCHLIST_BOUNDED_READ_MODEL_LINEAGE_QUERY)
            ) == 0

        with session_factory() as session:
            chart = session.get(InstrumentChartReadModel, instrument_id)
            summary = session.get(InstrumentSummaryReadModel, instrument_id)
            performance = session.get(InstrumentPerformanceReadModel, instrument_id)
            risk = session.get(InstrumentRiskReadModel, instrument_id)
            assert all(
                record is not None
                for record in (chart, summary, performance, risk)
            )
            chart.payload_json = {"resolution": {"observations": []}}
            summary.payload_json = {"quote_resolution": {"points": []}}
            performance.payload_json = {
                "quote_resolution": {
                    "calculation_dependency": {"revision_ids": []}
                },
                "historical_quote_resolution": {
                    "calculation_dependency": {"payload_hashes": []}
                },
            }
            risk.payload_json = {
                "quote_resolution": {
                    "calculation_dependency": {"excluded_revision_ids": []}
                },
                "historical_quote_resolution": {
                    "calculation_dependency": {"excluded_payload_hashes": []}
                },
            }
            session.commit()

        with engine.connect() as connection:
            assert connection.scalar(
                text(audit_module.WATCHLIST_BOUNDED_READ_MODEL_LINEAGE_QUERY)
            ) == 6
    finally:
        engine.dispose()
