from __future__ import annotations

import ast
from datetime import UTC, datetime
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


def test_recalc_source_event_index_metadata_matches_migration_contract() -> None:
    from watchlist_app.db.models.recalc import RecalcJob

    index = next(
        item
        for item in RecalcJob.__table__.indexes
        if item.name == "uq_recalc_job_source_event_identity"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "trigger_ref_type",
        "trigger_ref_id",
        "instrument_id",
        "job_type",
    ]
    for dialect_name in ("sqlite", "postgresql"):
        predicate = str(index.dialect_options[dialect_name]["where"]).lower()
        assert "trigger_ref_type is not null" in predicate
        assert "trim(trigger_ref_type) <> ''" in predicate
        assert "trigger_ref_id is not null" in predicate
        assert "trim(trigger_ref_id) <> ''" in predicate


def test_recalc_retry_metadata_matches_migration_contract() -> None:
    from watchlist_app.db.models.recalc import RecalcJob, RecalcWorkerRegistration

    table = RecalcJob.__table__
    assert table.c.attempt_count.nullable is False
    assert table.c.max_attempts.nullable is False
    assert table.c.available_at.nullable is False
    assert any(
        constraint.name == "ck_recalc_job_attempt_budget"
        for constraint in table.constraints
    )
    claim_index = next(
        index for index in table.indexes if index.name == "idx_recalc_job_claim"
    )
    assert [column.name for column in claim_index.columns] == [
        "job_status",
        "available_at",
        "priority",
        "enqueued_at",
    ]
    assert {
        "last_successful_poll_at",
        "last_poll_error",
        "worker_state",
        "stopped_at",
    }.issubset(RecalcWorkerRegistration.__table__.c.keys())
    assert any(
        constraint.name == "ck_recalc_worker_registration_lifecycle"
        for constraint in RecalcWorkerRegistration.__table__.constraints
    )


def test_recalc_inbox_generation_metadata_matches_migration_contract() -> None:
    from watchlist_app.db.models.recalc import (
        RecalcInvalidationState,
        RecalcJob,
        RecalcSourceEventInbox,
    )

    assert RecalcJob.__table__.c.claimed_generation.nullable is True
    assert any(
        constraint.name == "ck_recalc_job_claimed_generation"
        for constraint in RecalcJob.__table__.constraints
    )
    assert any(
        constraint.name == "uq_recalc_source_event_inbox_identity"
        for constraint in RecalcSourceEventInbox.__table__.constraints
    )
    assert any(
        constraint.name == "ck_recalc_source_event_inbox_lifecycle"
        for constraint in RecalcSourceEventInbox.__table__.constraints
    )
    assert set(RecalcInvalidationState.__table__.primary_key.columns.keys()) == {
        "instrument_id",
        "job_type",
    }
    assert any(
        constraint.name == "ck_recalc_invalidation_state_generation_order"
        for constraint in RecalcInvalidationState.__table__.constraints
    )


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
    assert inspector.has_table("instrument_research_rating")
    assert not inspector.has_table("instrument_score_snapshot")
    assert not inspector.has_table("instrument_rating_read_model")
    for removed_table in (
        "holding_position",
        "holding_snapshot",
        "exposure_analytics_snapshot",
        "instrument_exposure_holdings_read_model",
        "instrument_exposure_read_model",
    ):
        assert not inspector.has_table(removed_table)
    watchlist_row_columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert {
        "duration",
        "yield_to_worst",
        "avg_credit_rating",
        "exposure_updated_at",
    }.isdisjoint(watchlist_row_columns)
    recalc_columns = {column["name"] for column in inspector.get_columns("recalc_job")}
    assert {
        "heartbeat_at",
        "lease_token",
        "attempt_count",
        "max_attempts",
        "available_at",
        "claimed_generation",
    }.issubset(recalc_columns)
    assert inspector.has_table("recalc_worker_registration")
    assert inspector.has_table("recalc_source_event_inbox")
    assert inspector.has_table("recalc_invalidation_state")
    worker_columns = {
        column["name"]
        for column in inspector.get_columns("recalc_worker_registration")
    }
    assert worker_columns == {
        "worker_id",
        "instance_id",
        "worker_version",
        "started_at",
        "last_heartbeat_at",
        "last_successful_poll_at",
        "last_poll_error",
        "worker_state",
        "stopped_at",
        "metadata_json",
    }
    worker_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("recalc_worker_registration")
    }
    assert worker_indexes["idx_recalc_worker_registration_last_heartbeat"][
        "unique"
    ] == 0
    recalc_indexes = {
        index["name"]: index for index in inspector.get_indexes("recalc_job")
    }
    assert recalc_indexes["idx_recalc_job_claim"]["column_names"] == [
        "job_status",
        "available_at",
        "priority",
        "enqueued_at",
    ]
    assert not inspector.has_table("nav_fact")
    for table_name, canonical_timestamp in {
        "instrument_summary_read_model": "last_recalculated_at",
        "instrument_chart_read_model": "last_recalculated_at",
        "instrument_performance_read_model": "last_recalculated_at",
        "instrument_risk_read_model": "last_recalculated_at",
        "performance_snapshot": "calculated_at",
        "risk_snapshot": "calculated_at",
    }.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert canonical_timestamp in columns
        assert "market_data_input_watermark_at" in columns
        assert "source_cutoff_at" not in columns
        assert "materialized_at" not in columns
        assert next(
            column["nullable"]
            for column in inspector.get_columns(table_name)
            if column["name"] == "market_data_input_watermark_at"
        ) is True
    watchlist_row_columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert "last_recalculated_at" in watchlist_row_columns
    assert "market_data_input_watermark_at" in watchlist_row_columns
    assert "last_fact_update_at" not in watchlist_row_columns
    assert "aum" not in watchlist_row_columns
    with create_engine(database_url).connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM field_registry WHERE field_key = 'aum'")
        ) == 0

    expected_unique_indexes = (
        ("recalc_job", "uq_recalc_job_running_instrument"),
        ("recalc_job", "uq_recalc_job_source_event_identity"),
        ("performance_snapshot", "uq_performance_snapshot_current_instrument"),
        ("risk_snapshot", "uq_risk_snapshot_current_instrument"),
        ("instrument_research_rating", "uq_research_rating_current_instrument"),
    )
    for table_name, index_name in expected_unique_indexes:
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        assert indexes[index_name]["unique"] == 1


def test_recalc_retry_migration_backfills_existing_jobs_without_deleting_audit(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-recalc-retry.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260714_0032")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES (
                    'retry-migration-fund', 'fund', 'fund',
                    'Retry Migration Fund', 1, '{}'
                )
                """
            )
        )
        for status in ("queued", "running", "completed", "failed"):
            connection.execute(
                text(
                    """
                    INSERT INTO recalc_job (
                        recalc_job_id, job_type, instrument_id, trigger_type,
                        trigger_ref_type, trigger_ref_id, job_status, priority,
                        dedupe_key, payload_json, enqueued_at
                    ) VALUES (
                        :job_id, 'all', 'retry-migration-fund', 'migration',
                        'migration_event', :trigger_ref_id, :status, 100,
                        :dedupe_key, '{}', '2026-07-14 03:00:00'
                    )
                    """
                ),
                {
                    "job_id": f"retry-{status}",
                    "trigger_ref_id": f"retry-{status}",
                    "status": status,
                    "dedupe_key": f"retry-migration:{status}",
                },
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT recalc_job_id, attempt_count, max_attempts, available_at,
                       claimed_generation
                FROM recalc_job
                WHERE instrument_id = 'retry-migration-fund'
                ORDER BY recalc_job_id
                """
            )
        ).mappings().all()
        inbox_rows = connection.execute(
            text(
                """
                SELECT source_event_inbox_id, generation, consumed_at
                FROM recalc_source_event_inbox
                WHERE instrument_id = 'retry-migration-fund'
                ORDER BY generation
                """
            )
        ).mappings().all()
        state = connection.execute(
            text(
                """
                SELECT requested_generation, completed_generation
                FROM recalc_invalidation_state
                WHERE instrument_id = 'retry-migration-fund'
                  AND job_type = 'all'
                """
            )
        ).one()
    assert {row["recalc_job_id"] for row in rows} == {
        "retry-queued",
        "retry-running",
        "retry-completed",
        "retry-failed",
    }
    assert {row["recalc_job_id"]: row["attempt_count"] for row in rows} == {
        "retry-completed": 1,
        "retry-failed": 3,
        "retry-queued": 0,
        "retry-running": 1,
    }
    assert all(row["max_attempts"] == 3 for row in rows)
    assert all(row["available_at"] is not None for row in rows)
    assert len(inbox_rows) == 4
    assert [row["generation"] for row in inbox_rows] == [1, 2, 3, 4]
    assert tuple(state) == (4, 1)
    assert sum(row["consumed_at"] is not None for row in inbox_rows) == 1
    assert {
        row["recalc_job_id"]: row["claimed_generation"] for row in rows
    } == {
        "retry-completed": 1,
        "retry-failed": 2,
        "retry-queued": None,
        "retry-running": 4,
    }
    get_settings.cache_clear()


def test_source_event_idempotency_migration_rejects_historical_duplicates(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-source-events.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260714_0031")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES (
                    'migration-source-event', 'fund', 'fund',
                    'Migration Source Event', 1, '{}'
                )
                """
            )
        )
        for job_id, enqueued_at, status in (
            ("source-event-earliest", "2026-07-14 01:00:00", "completed"),
            ("source-event-later", "2026-07-14 02:00:00", "failed"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO recalc_job (
                        recalc_job_id, job_type, instrument_id, trigger_type,
                        trigger_ref_type, trigger_ref_id, job_status, priority,
                        dedupe_key, payload_json, enqueued_at
                    ) VALUES (
                        :job_id, 'all', 'migration-source-event',
                        'market_data_refresh', 'instrument_registry_outbox',
                        'migration-event-1', :status, 100,
                        :dedupe_key, '{}', :enqueued_at
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "status": status,
                    "dedupe_key": f"migration-source-event:{job_id}",
                    "enqueued_at": enqueued_at,
                },
            )

    with pytest.raises(
        RuntimeError,
        match="Cannot enforce recalc source-event idempotency",
    ):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT recalc_job_id
                FROM recalc_job
                WHERE trigger_ref_type = 'instrument_registry_outbox'
                  AND trigger_ref_id = 'migration-event-1'
                  AND instrument_id = 'migration-source-event'
                  AND job_type = 'all'
                ORDER BY recalc_job_id
                """
            )
        ).scalars().all() == ["source-event-earliest", "source-event-later"]
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260714_0031"
        )

    assert "uq_recalc_job_source_event_identity" not in {
        index["name"] for index in inspect(engine).get_indexes("recalc_job")
    }

    get_settings.cache_clear()


def test_source_cutoff_rename_is_lossless_in_both_directions(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-source-cutoff.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0027")

    engine = create_engine(database_url)
    source_time = datetime(2026, 7, 12, 3, 4, 5, 678901, tzinfo=UTC)
    canonical_time = datetime(2026, 7, 13, 9, 8, 7, 123456, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES ('cutoff-fund', 'fund', 'fund', 'Cutoff Fund', 1, '{}')
                """
            )
        )
        for table_name in (
            "instrument_summary_read_model",
            "instrument_chart_read_model",
            "instrument_performance_read_model",
            "instrument_risk_read_model",
        ):
            connection.execute(
                text(
                    f"""
                    INSERT INTO {table_name} (
                        instrument_id, payload_json, data_freshness_status,
                        last_recalculated_at, source_cutoff_at
                    ) VALUES (
                        'cutoff-fund', '{{}}', 'fresh', :canonical_time, :source_time
                    )
                    """
                ),
                {
                    "canonical_time": canonical_time.isoformat(),
                    "source_time": source_time.isoformat(),
                },
            )
        for table_name, snapshot_id in (
            ("performance_snapshot", "performance-cutoff"),
            ("risk_snapshot", "risk-cutoff"),
        ):
            connection.execute(
                text(
                    f"""
                    INSERT INTO {table_name} (
                        snapshot_id, instrument_id, as_of_date, source_cutoff_at,
                        methodology_version, input_hash, calculated_at, is_current
                    ) VALUES (
                        :snapshot_id, 'cutoff-fund', '2026-07-13', :source_time,
                        'migration-test/v1', 'migration-input', :canonical_time, 1
                    )
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "canonical_time": canonical_time.isoformat(),
                    "source_time": source_time.isoformat(),
                },
            )

    table_names = (
        "instrument_summary_read_model",
        "instrument_chart_read_model",
        "instrument_performance_read_model",
        "instrument_risk_read_model",
        "performance_snapshot",
        "risk_snapshot",
    )

    def _normalized_rows(connection, table_name: str, column_name: str):
        primary_key = (
            "snapshot_id" if table_name.endswith("snapshot") else "instrument_id"
        )
        return {
            (
                str(row[0]),
                datetime.fromisoformat(str(row[1]).replace(" ", "T")),
            )
            for row in connection.execute(
                text(
                    f"SELECT {primary_key}, {column_name} FROM {table_name} "
                    "WHERE instrument_id = 'cutoff-fund'"
                )
            )
        }

    with engine.connect() as connection:
        before = {
            table_name: _normalized_rows(
                connection, table_name, "source_cutoff_at"
            )
            for table_name in table_names
        }

    command.upgrade(config, "20260713_0028")
    inspector = inspect(engine)
    with engine.connect() as connection:
        after_upgrade = {
            table_name: _normalized_rows(
                connection, table_name, "market_data_input_watermark_at"
            )
            for table_name in table_names
        }
        assert after_upgrade == before  # per-table EXCEPT = 0
        for table_name in table_names:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert "source_cutoff_at" not in columns
            assert "market_data_input_watermark_at" in columns
            assert next(
                column["nullable"]
                for column in inspector.get_columns(table_name)
                if column["name"] == "market_data_input_watermark_at"
            ) is True

    command.downgrade(config, "20260713_0027")
    inspector = inspect(engine)
    with engine.connect() as connection:
        after_downgrade = {
            table_name: _normalized_rows(
                connection, table_name, "source_cutoff_at"
            )
            for table_name in table_names
        }
        assert after_downgrade == before  # per-table EXCEPT = 0
        for table_name in table_names:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert "source_cutoff_at" in columns
            assert "market_data_input_watermark_at" not in columns
    get_settings.cache_clear()


def test_watchlist_row_watermark_rename_is_lossless_and_irreversible(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-row-watermark.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0028")

    engine = create_engine(database_url)
    watermark = datetime(2026, 7, 11, 3, 4, 5, 678901, tzinfo=UTC)
    recalculated = datetime(2026, 7, 13, 9, 8, 7, 123456, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, owner_type, owner_id, is_default,
                    is_shared, sort_order, created_at, updated_at
                ) VALUES (
                    'row-watermark-list', 'Rows', 'user', 'migration-test',
                    0, 0, 0, :recalculated, :recalculated
                )
                """
            ),
            {"recalculated": recalculated.isoformat()},
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_row_read_model (
                    watchlist_id, instrument_id, instrument_type,
                    instrument_name, attributes_json, data_freshness_status,
                    last_fact_update_at, last_recalculated_at
                ) VALUES (
                    'row-watermark-list', 'row-watermark-fund', 'fund',
                    'Row Fund', '{}', 'fresh', :watermark, :recalculated
                )
                """
            ),
            {
                "watermark": watermark.isoformat(),
                "recalculated": recalculated.isoformat(),
            },
        )

    command.upgrade(config, "20260713_0029")
    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert "market_data_input_watermark_at" in columns
    assert "last_fact_update_at" not in columns
    assert not inspector.has_table("nav_fact")
    with engine.connect() as connection:
        upgraded_value = connection.scalar(
            text(
                "SELECT market_data_input_watermark_at "
                "FROM watchlist_row_read_model "
                "WHERE instrument_id = 'row-watermark-fund'"
            )
        )
        return_ytd_description = connection.scalar(
            text(
                "SELECT description FROM field_registry "
                "WHERE field_key = 'return_ytd'"
            )
        )
    assert datetime.fromisoformat(str(upgraded_value).replace(" ", "T")) == watermark
    assert return_ytd_description == (
        "Derived from canonical total-return quote resolution."
    )

    with pytest.raises(RuntimeError, match="intentionally irreversible"):
        command.downgrade(config, "20260713_0028")
    assert not inspect(engine).has_table("nav_fact")
    get_settings.cache_clear()


def test_nav_fact_removal_refuses_to_drop_nonempty_duplicate_store(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-nav-fact-guard.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0028")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES ('legacy-nav-fund', 'fund', 'fund', 'Legacy NAV Fund', 1, '{}')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO nav_fact (
                    instrument_id, as_of_date, nav_type, value, currency,
                    is_primary, adopted_at
                ) VALUES (
                    'legacy-nav-fund', '2026-07-13', 'nav_with_dividend',
                    101.25, 'USD', 1, :adopted_at
                )
                """
            ),
            {"adopted_at": datetime(2026, 7, 13, tzinfo=UTC).isoformat()},
        )

    with pytest.raises(RuntimeError, match="nav_fact still contains rows"):
        command.upgrade(config, "head")
    assert inspect(engine).has_table("nav_fact")
    get_settings.cache_clear()


def test_nav_fact_removal_refuses_missing_return_ytd_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-nav-metadata-guard.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0028")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM field_registry WHERE field_key = 'return_ytd'")
        )

    with pytest.raises(RuntimeError, match="exactly one return_ytd row"):
        command.upgrade(config, "head")

    inspector = inspect(engine)
    assert inspector.has_table("nav_fact")
    columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert "last_fact_update_at" in columns
    assert "market_data_input_watermark_at" not in columns
    get_settings.cache_clear()


def test_uncurried_aum_removal_cleans_schema_catalog_and_views(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-aum-removal.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0029")

    engine = create_engine(database_url)
    now = datetime(2026, 7, 13, tzinfo=UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, owner_type, owner_id, is_default,
                    is_shared, sort_order, created_at, updated_at
                ) VALUES (
                    'aum-watchlist', 'AUM Boundary', 'user', 'migration-test',
                    0, 0, 0, :now, :now
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_row_read_model (
                    watchlist_id, instrument_id, instrument_type,
                    instrument_name, aum, return_ytd, attributes_json,
                    data_freshness_status
                ) VALUES (
                    'aum-watchlist', 'aum-fund', 'fund', 'AUM Fund', NULL,
                    0.125, '{}', 'fresh'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view (
                    watchlist_view_id, watchlist_id, name, kind,
                    default_sort_json, default_filters_json,
                    default_advanced_filter_json, default_group_by,
                    is_default, created_at
                ) VALUES (
                    'aum-view', 'aum-watchlist', 'AUM View', 'table',
                    :sort_json, :filters_json, :advanced_json, 'aum', 1, :now
                )
                """
            ),
            {
                "sort_json": json.dumps(
                    [
                        {"field": "aum", "direction": "desc"},
                        {"field": "return_ytd", "direction": "asc"},
                    ]
                ),
                "filters_json": json.dumps(
                    {"aum": [100], "attr.coverage_status": ["Invested"]}
                ),
                "advanced_json": json.dumps(
                    {
                        "type": "group",
                        "logic": "and",
                        "conditions": [
                            {
                                "type": "rule",
                                "field": "aum",
                                "operator": "gte",
                                "value": 100,
                            },
                            {
                                "type": "rule",
                                "field": "return_ytd",
                                "operator": "gte",
                                "value": 0,
                            },
                        ],
                    }
                ),
                "now": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view_column (
                    watchlist_view_id, field_key, display_order, is_visible
                ) VALUES
                    ('aum-view', 'aum', 1, 1),
                    ('aum-view', 'return_ytd', 2, 1)
                """
            )
        )

    command.upgrade(config, "head")

    inspector = inspect(engine)
    row_columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert "aum" not in row_columns
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM field_registry WHERE field_key = 'aum'")
        ) == 0
        assert connection.scalar(
            text(
                "SELECT return_ytd FROM watchlist_row_read_model "
                "WHERE instrument_id = 'aum-fund'"
            )
        ) == pytest.approx(0.125)
        assert list(
            connection.execute(
                text(
                    "SELECT field_key FROM watchlist_view_column "
                    "WHERE watchlist_view_id = 'aum-view' ORDER BY display_order"
                )
            ).scalars()
        ) == ["return_ytd"]
        view = connection.execute(
            text(
                """
                SELECT default_group_by, default_sort_json,
                       default_filters_json, default_advanced_filter_json
                FROM watchlist_view WHERE watchlist_view_id = 'aum-view'
                """
            )
        ).mappings().one()

    def parsed(value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value

    assert view["default_group_by"] == "none"
    assert parsed(view["default_sort_json"]) == [
        {"field": "return_ytd", "direction": "asc"}
    ]
    assert parsed(view["default_filters_json"]) == {
        "attr.coverage_status": ["Invested"]
    }
    assert parsed(view["default_advanced_filter_json"]) == {
        "type": "group",
        "logic": "and",
        "conditions": [
            {
                "type": "rule",
                "field": "return_ytd",
                "operator": "gte",
                "value": 0,
            }
        ],
    }

    with pytest.raises(RuntimeError, match="intentionally irreversible"):
        command.downgrade(config, "20260713_0029")
    get_settings.cache_clear()


def test_uncurried_aum_removal_rejects_nonempty_values_before_ddl(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-aum-guard.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0029")

    engine = create_engine(database_url)
    now = datetime(2026, 7, 13, tzinfo=UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, owner_type, owner_id, is_default,
                    is_shared, sort_order, created_at, updated_at
                ) VALUES (
                    'aum-guard', 'AUM Guard', 'user', 'migration-test',
                    0, 0, 0, :now, :now
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_row_read_model (
                    watchlist_id, instrument_id, instrument_type,
                    instrument_name, aum, attributes_json,
                    data_freshness_status
                ) VALUES (
                    'aum-guard', 'nonempty-aum-fund', 'fund',
                    'Nonempty AUM Fund', 100, '{}', 'fresh'
                )
                """
            )
        )

    with pytest.raises(RuntimeError, match="contains non-null values"):
        command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "aum" in {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM field_registry WHERE field_key = 'aum'")
        ) == 1
    get_settings.cache_clear()


def test_generic_exposure_removal_migration_cleans_persisted_references(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-exposure-removal.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0026")

    engine = create_engine(database_url)
    now = datetime(2026, 7, 13, tzinfo=UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES (
                    'exposure-fund', 'fund', 'fund', 'Exposure Fund', 1, '{}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, description, owner_type, owner_id,
                    is_default, is_shared, sort_order, created_at, updated_at
                ) VALUES (
                    'exposure-watchlist', 'Exposure Watchlist', NULL, 'user',
                    'test', 0, 0, 0, :now, :now
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view (
                    watchlist_view_id, watchlist_id, name, description, kind,
                    default_sort_json, default_filters_json,
                    default_advanced_filter_json, default_group_by, density,
                    is_default, created_at
                ) VALUES (
                    'exposure-view', 'exposure-watchlist', 'Exposure View', NULL,
                    'table', :sort_json, :filters_json, :advanced_json,
                    'exposure_updated_at', 'comfortable', 1, :now
                )
                """
            ),
            {
                "sort_json": json.dumps(
                    [
                        {"field": "duration", "direction": "desc"},
                        {"field": "return_ytd", "direction": "asc"},
                    ]
                ),
                "filters_json": json.dumps(
                    {
                        "avg_credit_rating": ["AAA"],
                        "attr.coverage_status": ["Invested"],
                    }
                ),
                "advanced_json": json.dumps(
                    {
                        "type": "group",
                        "logic": "and",
                        "conditions": [
                            {
                                "type": "rule",
                                "field": "yield_to_worst",
                                "operator": "gte",
                                "value": 0.03,
                            },
                            {
                                "type": "group",
                                "logic": "or",
                                "conditions": [
                                    {
                                        "type": "rule",
                                        "field": "exposure_updated_at",
                                        "operator": "exists",
                                        "value": None,
                                    },
                                    {
                                        "type": "rule",
                                        "field": "return_ytd",
                                        "operator": "gte",
                                        "value": 0,
                                    },
                                ],
                            },
                        ],
                    }
                ),
                "now": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view_column (
                    watchlist_view_id, field_key, display_order, is_visible
                ) VALUES
                    ('exposure-view', 'duration', 1, 1),
                    ('exposure-view', 'return_ytd', 2, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_summary_read_model (
                    instrument_id, payload_json, data_freshness_status
                ) VALUES ('exposure-fund', :payload, 'fresh')
                """
            ),
            {
                "payload": json.dumps(
                    {
                        "tabs": ["overview", "exposure", "portfolio", "risk"],
                        "key_stats": [
                            {"label": "Holdings", "value": "12"},
                            {"label": "MTD Return", "value": "1.25%"},
                        ],
                        "unrelated": "preserved",
                    }
                )
            },
        )
        for table_name in (
            "instrument_exposure_read_model",
            "instrument_exposure_holdings_read_model",
        ):
            connection.execute(
                text(
                    f"""
                    INSERT INTO {table_name} (
                        instrument_id, payload_json, data_freshness_status
                    ) VALUES ('exposure-fund', '{{}}', 'fresh')
                    """
                )
            )
        connection.execute(
            text(
                """
                INSERT INTO holding_snapshot (
                    holding_snapshot_id, instrument_id, as_of_date,
                    source_cutoff_at, methodology_version, input_hash,
                    calculated_at, is_current
                ) VALUES (
                    'holding-exposure-fund', 'exposure-fund', '2026-07-13',
                    :now, 'legacy-holdings/v1', 'holding-input', :now, 1
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO holding_position (
                    holding_snapshot_id, holding_name, holding_type,
                    portfolio_weight
                ) VALUES (
                    'holding-exposure-fund', 'Legacy Holding', 'equity', 0.5
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO exposure_analytics_snapshot (
                    snapshot_id, instrument_id, as_of_date, source_cutoff_at,
                    methodology_version, input_hash, calculated_at, is_current,
                    asset_allocation_json, sector_allocation_json,
                    country_allocation_json, currency_allocation_json,
                    credit_rating_allocation_json, duration_bucket_json,
                    maturity_bucket_json, yield_bucket_json
                ) VALUES (
                    'exposure-exposure-fund', 'exposure-fund', '2026-07-13',
                    :now, 'legacy-exposure/v1', 'exposure-input', :now, 1,
                    '[]', '[]', '[]', '[]', '[]', '[]', '[]', '[]'
                )
                """
            ),
            {"now": now},
        )
        for job_type in ("exposure", "all"):
            connection.execute(
                text(
                    """
                    INSERT INTO recalc_job (
                        recalc_job_id, job_type, instrument_id, trigger_type,
                        job_status, priority, dedupe_key, payload_json, enqueued_at
                    ) VALUES (
                        :job_id, :job_type, 'exposure-fund', 'migration-test',
                        'completed', 50, :dedupe_key, '{}', :now
                    )
                    """
                ),
                {
                    "job_id": f"job-{job_type}",
                    "job_type": job_type,
                    "dedupe_key": f"exposure-fund:{job_type}",
                    "now": now,
                },
            )

    command.upgrade(config, "head")

    inspector = inspect(engine)
    for removed_table in (
        "holding_position",
        "holding_snapshot",
        "exposure_analytics_snapshot",
        "instrument_exposure_holdings_read_model",
        "instrument_exposure_read_model",
    ):
        assert not inspector.has_table(removed_table)
    watchlist_row_columns = {
        column["name"]
        for column in inspector.get_columns("watchlist_row_read_model")
    }
    assert {
        "duration",
        "yield_to_worst",
        "avg_credit_rating",
        "exposure_updated_at",
    }.isdisjoint(watchlist_row_columns)

    with engine.connect() as connection:
        remaining_field_keys = set(
            connection.execute(
                text(
                    """
                    SELECT field_key
                    FROM field_registry
                    WHERE field_key IN (
                        'duration', 'yield_to_worst', 'avg_credit_rating',
                        'exposure_updated_at'
                    )
                    """
                )
            ).scalars()
        )
        assert not remaining_field_keys
        assert connection.execute(
            text(
                """
                SELECT COUNT(*) FROM field_category
                WHERE category_code = 'exposure'
                """
            )
        ).scalar_one() == 0
        assert dict(
            connection.execute(
                text(
                    """
                    SELECT category_code, display_order FROM field_category
                    WHERE category_code IN ('ratings_analysis', 'monitoring')
                    """
                )
            ).all()
        ) == {"ratings_analysis": 8, "monitoring": 9}
        assert list(
            connection.execute(
                text(
                    """
                    SELECT field_key FROM watchlist_view_column
                    WHERE watchlist_view_id = 'exposure-view'
                    ORDER BY display_order
                    """
                )
            ).scalars()
        ) == ["return_ytd"]

        view = connection.execute(
            text(
                """
                SELECT default_group_by, default_sort_json,
                       default_filters_json, default_advanced_filter_json
                FROM watchlist_view
                WHERE watchlist_view_id = 'exposure-view'
                """
            )
        ).mappings().one()
        default_sort = view["default_sort_json"]
        default_filters = view["default_filters_json"]
        advanced_filter = view["default_advanced_filter_json"]
        if isinstance(default_sort, str):
            default_sort = json.loads(default_sort)
        if isinstance(default_filters, str):
            default_filters = json.loads(default_filters)
        if isinstance(advanced_filter, str):
            advanced_filter = json.loads(advanced_filter)
        assert view["default_group_by"] == "none"
        assert default_sort == [{"field": "return_ytd", "direction": "asc"}]
        assert default_filters == {"attr.coverage_status": ["Invested"]}
        assert advanced_filter == {
            "type": "group",
            "logic": "and",
            "conditions": [
                {
                    "type": "group",
                    "logic": "or",
                    "conditions": [
                        {
                            "type": "rule",
                            "field": "return_ytd",
                            "operator": "gte",
                            "value": 0,
                        }
                    ],
                }
            ],
        }

        summary_payload = connection.execute(
            text(
                """
                SELECT payload_json FROM instrument_summary_read_model
                WHERE instrument_id = 'exposure-fund'
                """
            )
        ).scalar_one()
        if isinstance(summary_payload, str):
            summary_payload = json.loads(summary_payload)
        assert summary_payload == {
            "tabs": ["overview", "risk"],
            "key_stats": [{"label": "MTD Return", "value": "1.25%"}],
            "unrelated": "preserved",
        }
        assert list(
            connection.execute(
                text(
                    """
                    SELECT job_type FROM recalc_job
                    WHERE instrument_id = 'exposure-fund'
                    ORDER BY job_type
                    """
                )
            ).scalars()
        ) == ["all"]


def test_research_rating_migration_keeps_only_manual_rating(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-rating-migration.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260712_0025")

    engine = create_engine(database_url)
    now = datetime(2026, 7, 13, tzinfo=UTC)
    research_payload = {
        "overview": {
            "current_view": "Constructive",
            "research_view": "Repeatable process",
        },
        "manual_rating": 3,
        "timeline_notes": [],
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES (
                    :instrument_id, 'fund', 'fund', 'Migration Fund', 1, '{}'
                )
                """
            ),
            {"instrument_id": "migration-fund"},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_manual_profile (
                    instrument_id, people_payload_json, strategy_payload_json,
                    price_payload_json, documents_payload_json,
                    research_payload_json, nav_settings_json, updated_at, updated_by
                ) VALUES (
                    :instrument_id, '{}', '{}', '{}', '{}', :research_payload,
                    '{}', :updated_at, 'migration-analyst'
                )
                """
            ),
            {
                "instrument_id": "migration-fund",
                "research_payload": json.dumps(research_payload),
                "updated_at": now.isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_score_snapshot (
                    snapshot_id, instrument_id, as_of_date, source_cutoff_at,
                    methodology_version, input_hash, calculated_at, is_current,
                    overall_score, overall_rating, analyst_stance
                ) VALUES (
                    'auto-score', :instrument_id, '2026-07-13', :updated_at,
                    'canonical-score/v2', 'invalid-auto-score', :updated_at, 1,
                    99, 5, 'High Conviction'
                )
                """
            ),
            {"instrument_id": "migration-fund", "updated_at": now.isoformat()},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_summary_read_model (
                    instrument_id, payload_json, data_freshness_status
                ) VALUES (
                    :instrument_id, :payload, 'fresh'
                )
                """
            ),
            {
                "instrument_id": "migration-fund",
                "payload": json.dumps(
                    {
                        "overall_rating": 5,
                        "analyst_stance": "High Conviction",
                        "rating_as_of": "2026-07-13",
                    }
                ),
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                """
                SELECT rating_value, confidence, rationale, author, is_current
                FROM instrument_research_rating
                WHERE instrument_id = 'migration-fund'
                """
            )
        ).mappings().one()
        assert migrated["rating_value"] == 3
        assert migrated["confidence"] == "unassessed"
        assert migrated["author"] == "migration-analyst"
        assert migrated["is_current"] == 1
        assert "legacy manual rating" in migrated["rationale"]

        cleaned_research = connection.execute(
            text(
                """
                SELECT research_payload_json
                FROM instrument_manual_profile
                WHERE instrument_id = 'migration-fund'
                """
            )
        ).scalar_one()
        if isinstance(cleaned_research, str):
            cleaned_research = json.loads(cleaned_research)
        assert "manual_rating" not in cleaned_research
        assert "current_view" not in cleaned_research["overview"]
        assert cleaned_research["overview"]["research_view"] == "Repeatable process"

        summary_payload = connection.execute(
            text(
                """
                SELECT payload_json
                FROM instrument_summary_read_model
                WHERE instrument_id = 'migration-fund'
                """
            )
        ).scalar_one()
        if isinstance(summary_payload, str):
            summary_payload = json.loads(summary_payload)
        assert "overall_rating" not in summary_payload
        assert "analyst_stance" not in summary_payload
        assert "rating_as_of" not in summary_payload
        assert summary_payload["research_rating"]["rating"] == 3

    inspector = inspect(engine)
    assert not inspector.has_table("instrument_score_snapshot")
    assert not inspector.has_table("instrument_rating_read_model")


def test_research_rating_migration_rejects_invalid_legacy_rating_before_ddl(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'invalid-rating-migration.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260712_0025")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES ('invalid-rating-fund', 'fund', 'fund', 'Invalid', 1, '{}')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_manual_profile (
                    instrument_id, people_payload_json, strategy_payload_json,
                    price_payload_json, documents_payload_json,
                    research_payload_json, nav_settings_json, updated_at, updated_by
                ) VALUES (
                    'invalid-rating-fund', '{}', '{}', '{}', '{}',
                    :research_payload, '{}', '2026-07-13T00:00:00+00:00', 'test'
                )
                """
            ),
            {"research_payload": json.dumps({"manual_rating": 9})},
        )

    with pytest.raises(RuntimeError, match="Invalid legacy manual_rating"):
        command.upgrade(config, "head")

    assert not inspect(engine).has_table("instrument_research_rating")
