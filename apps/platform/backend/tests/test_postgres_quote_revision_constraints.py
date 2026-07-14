from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.canonical_fx import (
    compose_effective_fx_rate,
    resolve_canonical_fx_window_book_in_session,
)
from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy
from portfolio_ops_instrument_core.quote_revisions import quote_revision_payload_hash
from platform_app.services.market_data_outbox import (
    MarketDataOutboxDeadLetterRequeueError,
    MarketDataOutboxWorker,
    MarketDataOutboxWorkerConfig,
    PostgresMarketDataOutboxRepository,
)
from platform_app.services.readiness import _read_outbox_readiness_snapshot


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
DEFAULT_POSTGRES_URL = "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"

pytestmark = pytest.mark.postgresql_integration


def _admin_database_url(database_url: str) -> str:
    return (
        make_url(database_url)
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )


def _server_unavailable(error: OperationalError) -> bool:
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
            "network is unreachable",
            "no route to host",
        )
    )


@pytest.fixture
def postgres_quote_registry(monkeypatch: pytest.MonkeyPatch):
    base_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_quote_revision_{uuid4().hex[:8]}"
    database_url = (
        make_url(base_url)
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )
    admin_engine = create_engine(
        _admin_database_url(base_url),
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    settings_module = None
    session_module = None
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as error:  # pragma: no cover - environment dependent
            if _server_unavailable(error):
                pytest.skip(f"PostgreSQL server is unavailable: {error}")
            pytest.fail(f"PostgreSQL credentials were rejected: {error}")

        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        except Exception as error:
            pytest.fail(
                "PostgreSQL test database creation failed; the dedicated test "
                f"role must have CREATEDB permission. Cause: {error}"
            )
        database_created = True

        monkeypatch.setenv("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE", database_name)
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL", database_url
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry"
        )
        monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", database_url)
        monkeypatch.setenv(
            "PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA", "instrument_registry"
        )

        from platform_app.core import settings as platform_settings
        from platform_app.db import session as platform_session

        settings_module = platform_settings
        session_module = platform_session
        settings_module.get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_session_factory.cache_clear()

        config = Config(
            str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini")
        )
        config.set_main_option(
            "script_location",
            str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
        )
        command.upgrade(config, "20260712_0007")
        seed_engine = create_engine(database_url)
        try:
            with seed_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.instrument (
                            instrument_id, instrument_name, instrument_type,
                            currency, quote_selection_policy_json,
                            source_settings_json, refresh_status_json,
                            lifecycle_state_json, market_data_updated_at
                        ) VALUES (
                            'legacy-cursor-probe', 'Legacy Cursor Probe', 'fund',
                            'USD', '{}', '{}', '{}', '{"status": "active"}',
                            '2026-07-13T00:00:00.000000Z'
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.instrument_market_data (
                            instrument_id, metric_family, quote_basis,
                            as_of_date, value, currency, provider, status
                        ) VALUES (
                            'legacy-cursor-probe', 'nav', 'official_nav',
                            '2026-07-12', '99.5000', 'USD',
                            'legacy-postgres-probe', 'complete'
                        )
                        """
                    )
                )
        finally:
            seed_engine.dispose()
        command.upgrade(config, "head")

        session_factory = session_module.get_session_factory()
        instrument = shared_store.create_instrument(
            session_factory,
            instrument_name="Quote Revision Constraint Asset",
            instrument_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": f"QUOTE-{uuid4().hex[:8]}",
                    "is_primary": True,
                }
            ],
        )
        yield database_url, session_factory, str(instrument["instrument_id"])
    finally:
        if session_module is not None:
            try:
                session_module.get_engine().dispose()
            finally:
                session_module.get_engine.cache_clear()
                session_module.get_session_factory.cache_clear()
        if settings_module is not None:
            settings_module.get_settings.cache_clear()
        admin_engine.dispose()
        if database_created:
            cleanup_engine = create_engine(
                _admin_database_url(base_url),
                isolation_level="AUTOCOMMIT",
            )
            try:
                with cleanup_engine.connect() as connection:
                    connection.execute(
                        text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
                    )
            finally:
                cleanup_engine.dispose()


def test_postgres_allows_only_one_current_revision_per_observation(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    migrated = shared_store.list_quote_observation_revisions(
        session_factory,
        instrument_id="legacy-cursor-probe",
        as_of_date=date(2026, 7, 12),
    )
    assert len(migrated) == 1
    assert migrated[0]["value"] == Decimal("99.5")
    assert migrated[0]["revision_number"] == 1
    assert migrated[0]["ingested_at"] is None
    assert migrated[0]["value_input_scale"] == 1
    assert migrated[0]["numeric_scale_state"] == "legacy_inferred"
    assert migrated[0]["payload_schema_version"] == 1
    assert migrated[0]["payload_hash"] == quote_revision_payload_hash(
        value="99.5",
        source_ref="legacy-postgres-probe",
        status="complete",
    )

    for value in ("100", "101"):
        result = shared_store.upsert_market_data(
            session_factory,
            instrument_id=instrument_id,
            metric_family="nav",
            quote_basis="official_nav",
            as_of_date=date(2026, 7, 13),
            value=value,
            currency="USD",
            source_ref="postgres-fixture",
            status="complete",
        )
        assert result is not None

    revisions = shared_store.list_quote_observation_revisions(
        session_factory,
        instrument_id=instrument_id,
        as_of_date=date(2026, 7, 13),
    )
    assert [revision["revision_number"] for revision in revisions] == [2, 1]
    assert [revision["is_current"] for revision in revisions] == [True, False]

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            index_definition = connection.scalar(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'instrument_registry'
                      AND indexname = 'uq_quote_observation_revision_current'
                    """
                )
            )
            uuid_columns = (
                connection.execute(
                    text(
                        """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'instrument_registry'
                      AND (
                        (table_name = 'quote_series'
                         AND column_name = 'quote_series_id')
                        OR (table_name = 'quote_observation'
                            AND column_name IN ('observation_id', 'quote_series_id'))
                        OR (table_name = 'quote_observation_revision'
                            AND column_name IN ('revision_id', 'observation_id'))
                      )
                    """
                    )
                )
                .mappings()
                .all()
            )
            value_source_columns = (
                connection.execute(
                    text(
                        """
                    SELECT column_name, data_type, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = 'instrument_registry'
                      AND table_name = 'quote_observation_revision'
                      AND column_name IN ('value', 'source_ref')
                    """
                    )
                )
                .mappings()
                .all()
            )
            quote_indexes = (
                connection.execute(
                    text(
                        """
                    SELECT tablename, indexname
                    FROM pg_indexes
                    WHERE schemaname = 'instrument_registry'
                      AND tablename IN (
                          'quote_series',
                          'quote_observation',
                          'quote_observation_revision'
                      )
                    """
                    )
                )
                .mappings()
                .all()
            )
            trigger_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT trigger_name
                        FROM information_schema.triggers
                        WHERE event_object_schema = 'instrument_registry'
                          AND event_object_table = 'quote_observation_revision'
                        """
                    )
                )
            )
        assert index_definition is not None
        assert "UNIQUE INDEX" in index_definition
        assert "WHERE is_current" in index_definition
        assert len(uuid_columns) == 5
        assert {row["data_type"] for row in uuid_columns} == {"uuid"}
        columns_by_name = {row["column_name"]: row for row in value_source_columns}
        assert columns_by_name["value"]["data_type"] == "numeric"
        assert columns_by_name["value"]["numeric_precision"] is None
        assert columns_by_name["value"]["numeric_scale"] is None
        assert columns_by_name["source_ref"]["data_type"] == "text"
        indexes_by_table = {
            table_name: {
                row["indexname"]
                for row in quote_indexes
                if row["tablename"] == table_name
            }
            for table_name in (
                "quote_series",
                "quote_observation",
                "quote_observation_revision",
            )
        }
        assert indexes_by_table["quote_series"] == {
            "pk_quote_series",
            "uq_quote_series_instrument_metric_basis_currency",
        }
        assert indexes_by_table["quote_observation"] == {
            "pk_quote_observation",
            "uq_quote_observation_series_date",
        }
        assert indexes_by_table["quote_observation_revision"] == {
            "pk_quote_observation_revision",
            "uq_quote_observation_revision_current",
            "uq_quote_observation_revision_number",
        }
        assert {
            "trg_quote_observation_revision_immutable",
            "trg_quote_observation_revision_insert_sequence",
            "trg_quote_observation_revision_current_cardinality",
        }.issubset(trigger_names)

        with pytest.raises(DBAPIError, match="quote_revision_immutable_violation"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE instrument_registry.quote_observation_revision
                        SET value = 999
                        WHERE revision_id = :revision_id
                        """
                    ),
                    {"revision_id": revisions[0]["revision_id"]},
                )
        with pytest.raises(DBAPIError, match="quote_revision_immutable_violation"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        DELETE FROM instrument_registry.quote_observation_revision
                        WHERE revision_id = :revision_id
                        """
                    ),
                    {"revision_id": revisions[0]["revision_id"]},
                )
        with pytest.raises(DBAPIError, match="market_data_outbox_event"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        DELETE FROM instrument_registry.instrument
                        WHERE instrument_id = :instrument_id
                        """
                    ),
                    {"instrument_id": instrument_id},
                )
        with pytest.raises(DBAPIError, match="quote_revision_cardinality_violation"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE instrument_registry.quote_observation_revision
                        SET is_current = FALSE, superseded_at = NOW()
                        WHERE revision_id = :revision_id
                        """
                    ),
                    {"revision_id": revisions[0]["revision_id"]},
                )
        with pytest.raises(DBAPIError, match="quote_revision_insert_violation"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.quote_observation_revision (
                            revision_id, observation_id, revision_number, value,
                            value_input_scale, numeric_scale_state,
                            payload_schema_version,
                            source_ref, status, source_published_at, ingested_at,
                            payload_hash, is_current, superseded_at
                        ) VALUES (
                            :revision_id, :observation_id, 3, '102',
                            0, 'declared', 2,
                            'unclosed', 'complete', NULL, NOW(),
                            :payload_hash, TRUE, NULL
                        )
                        """
                    ),
                    {
                        "revision_id": str(uuid4()),
                        "observation_id": revisions[0]["observation_id"],
                        "payload_hash": f"sha256:{uuid4().hex}",
                    },
                )

        exact_value = Decimal("102.123456789012345678901234567890123456789")
        long_source_ref = "issuer-document:" + "x" * 5000
        revision_3_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.quote_observation_revision
                    SET is_current = FALSE, superseded_at = NOW()
                    WHERE revision_id = :revision_id
                    """
                ),
                {"revision_id": revisions[0]["revision_id"]},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_observation_revision (
                        revision_id, observation_id, revision_number, value,
                        value_input_scale, numeric_scale_state,
                        payload_schema_version,
                        source_ref, status, source_published_at, ingested_at,
                        payload_hash, is_current, superseded_at
                    ) VALUES (
                        :revision_id, :observation_id, 3, :value,
                        39, 'declared', 2,
                        :source_ref, 'complete', NULL, NOW(),
                        :payload_hash, TRUE, NULL
                    )
                    """
                ),
                {
                    "revision_id": revision_3_id,
                    "observation_id": revisions[0]["observation_id"],
                    "value": exact_value,
                    "source_ref": long_source_ref,
                    "payload_hash": f"sha256:{uuid4().hex}",
                },
            )
        with engine.connect() as connection:
            inserted = (
                connection.execute(
                    text(
                        """
                    SELECT value, source_ref, is_current
                    FROM instrument_registry.quote_observation_revision
                    WHERE revision_id = :revision_id
                    """
                    ),
                    {"revision_id": revision_3_id},
                )
                .mappings()
                .one()
            )
            instrument_count = connection.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM instrument_registry.instrument
                    WHERE instrument_id = :instrument_id
                    """
                ),
                {"instrument_id": instrument_id},
            )
        assert inserted["value"] == exact_value
        assert inserted["source_ref"] == long_source_ref
        assert inserted["is_current"] is True
        assert instrument_count == 1

        with pytest.raises(DBAPIError, match="quote_series_metric_basis"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.quote_series (
                            quote_series_id, instrument_id, metric_family,
                            quote_basis, currency, data_updated_at
                        ) VALUES (
                            :quote_series_id, :instrument_id,
                            'nav', 'close', 'USD', NULL
                        )
                        """
                    ),
                    {
                        "quote_series_id": str(uuid4()),
                        "instrument_id": instrument_id,
                    },
                )
        with pytest.raises(DBAPIError, match="quote_observation_revision_value_status"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE instrument_registry.quote_observation_revision
                        SET is_current = FALSE, superseded_at = NOW()
                        WHERE revision_id = :revision_id
                        """
                    ),
                    {"revision_id": revision_3_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.quote_observation_revision (
                            revision_id, observation_id, revision_number, value,
                            value_input_scale, numeric_scale_state,
                            payload_schema_version,
                            source_ref, status, source_published_at, ingested_at,
                            payload_hash, is_current, superseded_at
                        ) VALUES (
                            :revision_id, :observation_id, 4, 0,
                            0, 'declared', 2,
                            'zero', 'complete', NULL, NOW(),
                            'sha256:zero', TRUE, NULL
                        )
                        """
                    ),
                    {
                        "revision_id": str(uuid4()),
                        "observation_id": revisions[0]["observation_id"],
                    },
                )
    finally:
        engine.dispose()


def _append_outbox_event(
    session_factory,
    *,
    instrument_id: str,
    point_date: date,
    value: str,
) -> str:
    result = shared_store.upsert_market_data(
        session_factory,
        instrument_id=instrument_id,
        metric_family="nav",
        quote_basis="official_nav",
        as_of_date=point_date,
        value=value,
        currency="USD",
        source_ref="postgres-outbox-test",
        status="complete",
    )
    assert result is not None
    revisions = shared_store.list_quote_observation_revisions(
        session_factory,
        instrument_id=instrument_id,
        as_of_date=point_date,
    )
    assert revisions
    return str(revisions[0]["revision_id"])


def test_postgres_outbox_trigger_is_atomic_changed_only_and_excludes_fx(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE instrument_id = 'legacy-cursor-probe'
                    """
                    )
                )
                == 0
            )

        revision_id = _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 14),
            value="100.0000",
        )
        with engine.connect() as connection:
            event = (
                connection.execute(
                    text(
                        """
                        SELECT event_id, event_type, instrument_id,
                               quote_revision_id, source_kind, source_version,
                               status, attempt_count, max_attempts
                        FROM instrument_registry.market_data_outbox_event
                        WHERE event_id = :event_id
                        """
                    ),
                    {"event_id": revision_id},
                )
                .mappings()
                .one()
            )
            trigger_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT trigger_name
                        FROM information_schema.triggers
                        WHERE event_object_schema = 'instrument_registry'
                          AND event_object_table = 'quote_observation_revision'
                        """
                    )
                )
            )
        assert str(event["event_id"]) == revision_id
        assert str(event["quote_revision_id"]) == revision_id
        assert event["source_kind"] == "quote_revision"
        assert event["source_version"] == revision_id
        assert event["event_type"] == "watchlist_market_data_refresh_requested"
        assert event["instrument_id"] == instrument_id
        assert event["status"] == "pending"
        assert event["attempt_count"] == 0
        assert event["max_attempts"] == 8
        assert "trg_quote_revision_market_data_outbox" in trigger_names

        _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 14),
            value="100.0000",
        )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE instrument_id = :instrument_id
                    """
                    ),
                    {"instrument_id": instrument_id},
                )
                == 1
            )

        fx_result = shared_store.upsert_market_data(
            session_factory,
            instrument_id="fx-usd-hkd",
            metric_family="fx",
            quote_basis="spot",
            as_of_date=date(2026, 7, 14),
            value="7.8000",
            currency="HKD",
            source_ref="postgres-outbox-fx-test",
            status="complete",
        )
        assert fx_result is not None
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE instrument_id = 'fx-usd-hkd'
                    """
                    )
                )
                == 0
            )

        equity = shared_store.create_instrument(
            session_factory,
            instrument_name="Outbox Unsupported Equity",
            instrument_type="equity",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": f"OUTBOX-EQUITY-{uuid4().hex[:8]}",
                    "is_primary": True,
                }
            ],
        )
        equity_id = str(equity["instrument_id"])
        equity_result = shared_store.upsert_market_data(
            session_factory,
            instrument_id=equity_id,
            metric_family="price",
            quote_basis="close",
            as_of_date=date(2026, 7, 14),
            value="25.0000",
            currency="USD",
            source_ref="postgres-outbox-equity-test",
            status="complete",
        )
        assert equity_result is not None
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE instrument_id = :instrument_id
                    """
                    ),
                    {"instrument_id": equity_id},
                )
                == 0
            )

        rolled_back_revision_id = str(uuid4())
        rolled_back_observation_id = str(uuid4())
        rolled_back_series_id = str(uuid4())
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_series (
                        quote_series_id, instrument_id, metric_family,
                        quote_basis, currency, data_updated_at
                    ) VALUES (
                        :series_id, :instrument_id, 'price',
                        'close', 'USD', NULL
                    )
                    """
                ),
                {
                    "series_id": rolled_back_series_id,
                    "instrument_id": instrument_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_observation (
                        observation_id, quote_series_id, as_of_date
                    ) VALUES (:observation_id, :series_id, '2026-07-15')
                    """
                ),
                {
                    "observation_id": rolled_back_observation_id,
                    "series_id": rolled_back_series_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_observation_revision (
                        revision_id, observation_id, revision_number, value,
                        value_input_scale, numeric_scale_state,
                        payload_schema_version, source_ref, status,
                        source_published_at, ingested_at, payload_hash,
                        is_current, superseded_at
                    ) VALUES (
                        :revision_id, :observation_id, 1, 101,
                        0, 'declared', 2, 'rollback-probe', 'complete',
                        NULL, NOW(), 'sha256:rollback-probe', TRUE, NULL
                    )
                    """
                ),
                {
                    "revision_id": rolled_back_revision_id,
                    "observation_id": rolled_back_observation_id,
                },
            )
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE event_id = :event_id
                    """
                    ),
                    {"event_id": rolled_back_revision_id},
                )
                == 1
            )
            transaction.rollback()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_event
                    WHERE event_id = :event_id
                    """
                    ),
                    {"event_id": rolled_back_revision_id},
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM instrument_registry.quote_observation_revision
                    WHERE revision_id = :revision_id
                    """
                    ),
                    {"revision_id": rolled_back_revision_id},
                )
                == 0
            )
    finally:
        engine.dispose()


def test_postgres_quote_policy_trigger_is_changed_only_scoped_and_lineaged(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    engine = create_engine(database_url)
    changed_fund_policy = {
        "trading": ["official_nav"],
        "valuation": ["official_nav"],
        "total_return": ["total_return_nav"],
        "chart": ["total_return_nav", "official_nav"],
        "reference": ["official_nav"],
    }
    try:
        updated = shared_store.upsert_quote_selection_policy(
            session_factory,
            instrument_id=instrument_id,
            quote_selection_policy=changed_fund_policy,
        )
        assert updated is not None
        source_version = str(updated["market_data_updated_at"])

        with engine.connect() as connection:
            event = (
                connection.execute(
                    text(
                        """
                        SELECT instrument_id, quote_revision_id, source_kind,
                               source_version, status
                        FROM instrument_registry.market_data_outbox_event
                        WHERE instrument_id = :instrument_id
                          AND source_kind = 'quote_selection_policy'
                        """
                    ),
                    {"instrument_id": instrument_id},
                )
                .mappings()
                .one()
            )
            trigger_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT trigger_name
                        FROM information_schema.triggers
                        WHERE event_object_schema = 'instrument_registry'
                          AND event_object_table = 'instrument'
                        """
                    )
                )
            )
        assert event == {
            "instrument_id": instrument_id,
            "quote_revision_id": None,
            "source_kind": "quote_selection_policy",
            "source_version": source_version,
            "status": "pending",
        }
        assert "trg_quote_policy_market_data_outbox" in trigger_names

        unchanged = shared_store.upsert_quote_selection_policy(
            session_factory,
            instrument_id=instrument_id,
            quote_selection_policy={
                "reference": ["official_nav"],
                "chart": ["total_return_nav", "official_nav"],
                "total_return": ["total_return_nav"],
                "valuation": ["official_nav"],
                "trading": ["official_nav"],
            },
        )
        assert unchanged is not None
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM instrument_registry.market_data_outbox_event
                        WHERE instrument_id = :instrument_id
                          AND source_kind = 'quote_selection_policy'
                        """
                    ),
                    {"instrument_id": instrument_id},
                )
                == 1
            )

        equity = shared_store.create_instrument(
            session_factory,
            instrument_name="Policy Unsupported Equity",
            instrument_type="equity",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": f"POLICY-EQUITY-{uuid4().hex[:8]}",
                    "is_primary": True,
                }
            ],
        )
        equity_id = str(equity["instrument_id"])
        equity_updated = shared_store.upsert_quote_selection_policy(
            session_factory,
            instrument_id=equity_id,
            quote_selection_policy={
                "trading": ["close"],
                "valuation": ["close"],
                "total_return": ["adjusted_close"],
                "chart": ["adjusted_close", "close"],
                "reference": ["close"],
            },
        )
        assert equity_updated is not None
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM instrument_registry.market_data_outbox_event
                        WHERE instrument_id = :instrument_id
                        """
                    ),
                    {"instrument_id": equity_id},
                )
                == 0
            )
    finally:
        engine.dispose()


def test_postgres_outbox_worker_success_retry_reclaim_dead_and_heartbeat(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    engine = create_engine(database_url)
    repository = PostgresMarketDataOutboxRepository(session_factory)
    with engine.connect() as connection:
        now = connection.scalar(text("SELECT clock_timestamp()")) + timedelta(minutes=1)
    worker_id = "postgres-outbox-worker"
    repository.register_worker(
        worker_id=worker_id,
        hostname="pytest-host",
        process_id=123,
        now=now,
    )
    config = MarketDataOutboxWorkerConfig(
        lease_seconds=30,
        retry_base_seconds=5,
        retry_max_seconds=20,
    )
    try:
        with engine.connect() as connection:
            registered_poll_state = (
                connection.execute(
                    text(
                        """
                        SELECT last_successful_poll_at, last_poll_error
                        FROM instrument_registry.market_data_outbox_worker_heartbeat
                        WHERE worker_id = :worker_id
                        """
                    ),
                    {"worker_id": worker_id},
                )
                .mappings()
                .one()
            )
        assert registered_poll_state == {
            "last_successful_poll_at": None,
            "last_poll_error": None,
        }

        success_id = _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 16),
            value="101",
        )
        sent: list[tuple[str, str]] = []
        success_worker = MarketDataOutboxWorker(
            repository=repository,
            sender=lambda **kwargs: sent.append(
                (kwargs["event_id"], kwargs["instrument_id"])
            ),
            worker_id=worker_id,
            config=config,
            clock=lambda: now,
            hostname="pytest-host",
            process_id=123,
        )
        success_result = success_worker.run_once()
        assert success_result.delivered_count == 1
        assert sent == [(success_id, instrument_id)]

        retry_id = _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 17),
            value="102",
        )

        def fail_sender(**_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("Watchlist unavailable")

        retry_worker = MarketDataOutboxWorker(
            repository=repository,
            sender=fail_sender,
            worker_id=worker_id,
            config=config,
            clock=lambda: now,
            hostname="pytest-host",
            process_id=123,
        )
        retry_result = retry_worker.run_once()
        assert retry_result.retry_count == 1
        with engine.begin() as connection:
            retry_row = (
                connection.execute(
                    text(
                        """
                        SELECT status, attempt_count, available_at, last_error
                        FROM instrument_registry.market_data_outbox_event
                        WHERE event_id = :event_id
                        """
                    ),
                    {"event_id": retry_id},
                )
                .mappings()
                .one()
            )
            assert retry_row["status"] == "pending"
            assert retry_row["attempt_count"] == 1
            assert retry_row["available_at"] == now + timedelta(seconds=5)
            assert "Watchlist unavailable" in retry_row["last_error"]
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_event
                    SET available_at = :available_at
                    WHERE event_id = :event_id
                    """
                ),
                {
                    "event_id": retry_id,
                    "available_at": now - timedelta(seconds=1),
                },
            )
        retried: list[str] = []
        delivered_retry_worker = MarketDataOutboxWorker(
            repository=repository,
            sender=lambda **kwargs: retried.append(kwargs["event_id"]),
            worker_id=worker_id,
            config=config,
            clock=lambda: now,
            hostname="pytest-host",
            process_id=123,
        )
        delivered_retry_worker.run_once()
        assert retried == [retry_id]

        reclaimed_id = _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 18),
            value="103",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_event
                    SET status = 'processing',
                        attempt_count = 1,
                        lease_owner = 'crashed-worker',
                        lease_expires_at = :expired_at,
                        updated_at = :expired_at
                    WHERE event_id = :event_id
                    """
                ),
                {
                    "event_id": reclaimed_id,
                    "expired_at": now - timedelta(seconds=1),
                },
            )
        reclaimed: list[str] = []
        reclaim_worker = MarketDataOutboxWorker(
            repository=repository,
            sender=lambda **kwargs: reclaimed.append(kwargs["event_id"]),
            worker_id=worker_id,
            config=config,
            clock=lambda: now,
            hostname="pytest-host",
            process_id=123,
        )
        reclaim_worker.run_once()
        assert reclaimed == [reclaimed_id]
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT attempt_count
                    FROM instrument_registry.market_data_outbox_event
                    WHERE event_id = :event_id
                    """
                    ),
                    {"event_id": reclaimed_id},
                )
                == 2
            )

        dead_id = _append_outbox_event(
            session_factory,
            instrument_id=instrument_id,
            point_date=date(2026, 7, 19),
            value="104",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_event
                    SET max_attempts = 1
                    WHERE event_id = :event_id
                    """
                ),
                {"event_id": dead_id},
            )
        dead_worker = MarketDataOutboxWorker(
            repository=repository,
            sender=fail_sender,
            worker_id=worker_id,
            config=config,
            clock=lambda: now,
            hostname="pytest-host",
            process_id=123,
        )
        dead_result = dead_worker.run_once()
        assert dead_result.dead_count == 1

        repository.record_poll_failure(
            worker_id=worker_id,
            now=now + timedelta(seconds=1),
            error="RuntimeError: database unavailable",
        )
        with engine.connect() as connection:
            failed_poll_state = (
                connection.execute(
                    text(
                        """
                        SELECT last_successful_poll_at, last_poll_error
                        FROM instrument_registry.market_data_outbox_worker_heartbeat
                        WHERE worker_id = :worker_id
                        """
                    ),
                    {"worker_id": worker_id},
                )
                .mappings()
                .one()
            )
        assert failed_poll_state == {
            "last_successful_poll_at": now,
            "last_poll_error": "RuntimeError: database unavailable",
        }

        repository.record_poll_success(
            worker_id=worker_id,
            now=now + timedelta(seconds=2),
        )
        repository.stop_worker(worker_id=worker_id, now=now + timedelta(seconds=3))
        with engine.connect() as connection:
            statuses = dict(
                connection.execute(
                    text(
                        """
                        SELECT CAST(event_id AS TEXT), status
                        FROM instrument_registry.market_data_outbox_event
                        WHERE event_id IN (
                            :success_id, :retry_id, :reclaimed_id, :dead_id
                        )
                        """
                    ),
                    {
                        "success_id": success_id,
                        "retry_id": retry_id,
                        "reclaimed_id": reclaimed_id,
                        "dead_id": dead_id,
                    },
                ).all()
            )
            heartbeat = (
                connection.execute(
                    text(
                        """
                        SELECT worker_state, heartbeat_at, last_claimed_at,
                               last_delivered_at, last_successful_poll_at,
                               last_poll_error, stopped_at
                        FROM instrument_registry.market_data_outbox_worker_heartbeat
                        WHERE worker_id = :worker_id
                        """
                    ),
                    {"worker_id": worker_id},
                )
                .mappings()
                .one()
            )
        assert statuses == {
            success_id: "delivered",
            retry_id: "delivered",
            reclaimed_id: "delivered",
            dead_id: "dead",
        }
        assert heartbeat["worker_state"] == "stopped"
        assert heartbeat["last_claimed_at"] is not None
        assert heartbeat["last_delivered_at"] is not None
        assert heartbeat["last_successful_poll_at"] == now + timedelta(seconds=2)
        assert heartbeat["last_poll_error"] is None
        assert heartbeat["stopped_at"] == now + timedelta(seconds=3)
    finally:
        engine.dispose()


def test_postgres_outbox_readiness_requires_fresh_successful_poll(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, _ = postgres_quote_registry
    engine = create_engine(database_url)
    repository = PostgresMarketDataOutboxRepository(session_factory)
    worker_id = "postgres-readiness-worker"
    try:
        with engine.connect() as connection:
            now = connection.scalar(text("SELECT clock_timestamp()"))
        repository.register_worker(
            worker_id=worker_id,
            hostname="pytest-host",
            process_id=789,
            now=now,
        )
        with session_factory() as session:
            registered = _read_outbox_readiness_snapshot(
                session,
                worker_max_age_seconds=90,
                event_max_age_seconds=900,
            )
        assert registered.fresh_successful_worker is False

        repository.record_poll_success(worker_id=worker_id, now=now)
        with session_factory() as session:
            successful = _read_outbox_readiness_snapshot(
                session,
                worker_max_age_seconds=90,
                event_max_age_seconds=900,
            )
        assert successful.fresh_successful_worker is True

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_worker_heartbeat
                    SET heartbeat_at = clock_timestamp(),
                        last_successful_poll_at = clock_timestamp() - INTERVAL '91 seconds'
                    WHERE worker_id = :worker_id
                    """
                ),
                {"worker_id": worker_id},
            )
        with session_factory() as session:
            stale_success = _read_outbox_readiness_snapshot(
                session,
                worker_max_age_seconds=90,
                event_max_age_seconds=900,
            )
        assert stale_success.fresh_successful_worker is False
    finally:
        engine.dispose()


def test_postgres_outbox_claim_skips_locked_event(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    locked_id = _append_outbox_event(
        session_factory,
        instrument_id=instrument_id,
        point_date=date(2026, 7, 21),
        value="106",
    )
    available_id = _append_outbox_event(
        session_factory,
        instrument_id=instrument_id,
        point_date=date(2026, 7, 22),
        value="107",
    )
    repository = PostgresMarketDataOutboxRepository(session_factory)
    now = datetime(2026, 7, 14, 10, tzinfo=UTC)
    repository.register_worker(
        worker_id="skip-locked-worker",
        hostname="pytest-host",
        process_id=456,
        now=now,
    )
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_event
                    SET available_at = CASE
                        WHEN event_id = :locked_id THEN :first_at
                        ELSE :second_at
                    END
                    WHERE event_id IN (:locked_id, :available_id)
                    """
                ),
                {
                    "locked_id": locked_id,
                    "available_id": available_id,
                    "first_at": now - timedelta(seconds=2),
                    "second_at": now - timedelta(seconds=1),
                },
            )

        lock_connection = engine.connect()
        lock_transaction = lock_connection.begin()
        try:
            lock_connection.execute(
                text(
                    """
                    SELECT event_id
                    FROM instrument_registry.market_data_outbox_event
                    WHERE event_id = :event_id
                    FOR UPDATE
                    """
                ),
                {"event_id": locked_id},
            )
            claimed = repository.claim(
                worker_id="skip-locked-worker",
                now=now,
                lease_seconds=30,
                batch_size=1,
            )
        finally:
            lock_transaction.rollback()
            lock_connection.close()

        assert [event.event_id for event in claimed] == [available_id]
    finally:
        engine.dispose()


def test_postgres_dead_letter_requeue_is_atomic_auditable_and_dead_only(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    event_id = _append_outbox_event(
        session_factory,
        instrument_id=instrument_id,
        point_date=date(2026, 7, 23),
        value="108",
    )
    engine = create_engine(database_url)
    repository = PostgresMarketDataOutboxRepository(session_factory)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.market_data_outbox_event
                    SET status = 'dead',
                        attempt_count = 2,
                        max_attempts = 2,
                        available_at = clock_timestamp() - INTERVAL '1 hour',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error = 'Watchlist transient failure',
                        updated_at = clock_timestamp() - INTERVAL '30 minutes',
                        delivered_at = NULL,
                        dead_at = clock_timestamp() - INTERVAL '30 minutes'
                    WHERE event_id = :event_id
                    """
                ),
                {"event_id": event_id},
            )
        with engine.connect() as connection:
            database_time_before = connection.scalar(text("SELECT clock_timestamp()"))

        result = repository.requeue_dead_event(
            event_id=event_id,
            additional_attempts=3,
        )

        with engine.connect() as connection:
            database_time_after = connection.scalar(text("SELECT clock_timestamp()"))
            row = (
                connection.execute(
                    text(
                        """
                        SELECT status, attempt_count, max_attempts, available_at,
                               lease_owner, lease_expires_at, last_error,
                               updated_at, delivered_at, dead_at
                        FROM instrument_registry.market_data_outbox_event
                        WHERE event_id = :event_id
                        """
                    ),
                    {"event_id": event_id},
                )
                .mappings()
                .one()
            )

        assert result.event_id == event_id
        assert result.attempt_count == 2
        assert result.previous_max_attempts == 2
        assert result.max_attempts == 5
        assert result.last_error == "Watchlist transient failure"
        assert result.available_at == result.updated_at
        assert database_time_before <= result.available_at <= database_time_after
        assert row == {
            "status": "pending",
            "attempt_count": 2,
            "max_attempts": 5,
            "available_at": result.available_at,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error": "Watchlist transient failure",
            "updated_at": result.updated_at,
            "delivered_at": None,
            "dead_at": None,
        }

        with pytest.raises(
            MarketDataOutboxDeadLetterRequeueError,
            match="does not exist or is not dead",
        ):
            repository.requeue_dead_event(
                event_id=event_id,
                additional_attempts=3,
            )

        with engine.connect() as connection:
            unchanged = (
                connection.execute(
                    text(
                        """
                        SELECT status, attempt_count, max_attempts, last_error,
                               available_at, updated_at
                        FROM instrument_registry.market_data_outbox_event
                        WHERE event_id = :event_id
                        """
                    ),
                    {"event_id": event_id},
                )
                .mappings()
                .one()
            )
        assert unchanged == {
            "status": "pending",
            "attempt_count": 2,
            "max_attempts": 5,
            "last_error": "Watchlist transient failure",
            "available_at": result.available_at,
            "updated_at": result.updated_at,
        }
    finally:
        engine.dispose()


def test_postgres_controlled_registry_reset_replaces_outbox_state(
    postgres_quote_registry,
) -> None:
    database_url, session_factory, instrument_id = postgres_quote_registry
    old_event_id = _append_outbox_event(
        session_factory,
        instrument_id=instrument_id,
        point_date=date(2026, 7, 20),
        value="105",
    )
    repository = PostgresMarketDataOutboxRepository(session_factory)
    repository.register_worker(
        worker_id="reset-worker",
        hostname="pytest-host",
        process_id=321,
        now=datetime(2026, 7, 14, 10, tzinfo=UTC),
    )

    shared_store.reset_store(
        session_factory,
        {
            "registry_name": "Reset Registry",
            "instruments": [
                {
                    "instrument_id": "reset-fund",
                    "instrument_name": "Reset Fund",
                    "instrument_type": "fund",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "internal",
                            "identifier_value": "RESET-FUND",
                            "is_primary": True,
                        }
                    ],
                    "market_data": [
                        {
                            "metric_family": "nav",
                            "quote_basis": "official_nav",
                            "as_of_date": "2026-07-14",
                            "value": "1.0000",
                            "currency": "USD",
                            "source_ref": "reset-seed",
                            "status": "complete",
                        }
                    ],
                }
            ],
        },
    )

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT CAST(event_id AS TEXT) AS event_id,
                               instrument_id, status
                        FROM instrument_registry.market_data_outbox_event
                        ORDER BY created_at, event_id
                        """
                    )
                )
                .mappings()
                .all()
            )
            heartbeat_count = connection.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM instrument_registry.market_data_outbox_worker_heartbeat
                    """
                )
            )
        assert len(rows) == 1
        assert rows[0]["event_id"] != old_event_id
        assert rows[0]["instrument_id"] == "reset-fund"
        assert rows[0]["status"] == "pending"
        assert heartbeat_count == 0
    finally:
        engine.dispose()


def test_postgres_migration_seeds_fx_identities_without_quote_facts(
    postgres_quote_registry,
) -> None:
    database_url, _, _ = postgres_quote_registry
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM instrument_registry.alembic_version")
            )
            instruments = (
                connection.execute(
                    text(
                        """
                    SELECT instrument_id, instrument_name, instrument_type,
                           currency, quote_selection_policy_json,
                           lifecycle_state_json
                    FROM instrument_registry.instrument
                    WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')
                    ORDER BY instrument_id
                    """
                    )
                )
                .mappings()
                .all()
            )
            identifiers = (
                connection.execute(
                    text(
                        """
                    SELECT instrument_id, identifier_type, identifier_value,
                           is_primary
                    FROM instrument_registry.instrument_identifier
                    WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')
                    ORDER BY instrument_id
                    """
                    )
                )
                .mappings()
                .all()
            )
            quote_fact_counts = (
                connection.execute(
                    text(
                        """
                    SELECT
                        (SELECT COUNT(*)
                         FROM instrument_registry.quote_series
                         WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny'))
                            AS series_count,
                        (SELECT COUNT(*)
                         FROM instrument_registry.quote_observation observation
                         JOIN instrument_registry.quote_series series
                           USING (quote_series_id)
                         WHERE series.instrument_id IN
                               ('fx-usd-hkd', 'fx-usd-cny'))
                            AS observation_count,
                        (SELECT COUNT(*)
                         FROM instrument_registry.quote_observation_revision revision
                         JOIN instrument_registry.quote_observation observation
                           USING (observation_id)
                         JOIN instrument_registry.quote_series series
                           USING (quote_series_id)
                         WHERE series.instrument_id IN
                               ('fx-usd-hkd', 'fx-usd-cny'))
                            AS revision_count
                    """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    strict_policy = {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": [],
        "chart": ["spot"],
        "reference": ["spot"],
    }
    assert revision == "20260714_0013"
    assert [row["instrument_id"] for row in instruments] == [
        "fx-usd-cny",
        "fx-usd-hkd",
    ]
    expected = {
        "fx-usd-cny": ("USD/CNY Spot", "CNY"),
        "fx-usd-hkd": ("USD/HKD Spot", "HKD"),
    }
    for row in instruments:
        assert row["instrument_type"] == "fx"
        assert (row["instrument_name"], row["currency"]) == expected[
            row["instrument_id"]
        ]
        assert row["quote_selection_policy_json"] == strict_policy
        assert row["lifecycle_state_json"] == {"status": "active"}
    assert [
        (
            row["instrument_id"],
            row["identifier_type"],
            row["identifier_value"],
            row["is_primary"],
        )
        for row in identifiers
    ] == [
        ("fx-usd-cny", "ticker", "USDCNY", True),
        ("fx-usd-hkd", "ticker", "USDHKD", True),
    ]
    assert dict(quote_fact_counts) == {
        "series_count": 0,
        "observation_count": 0,
        "revision_count": 0,
    }


def test_postgres_canonical_fx_window_is_decimal_and_query_bounded(
    postgres_quote_registry,
) -> None:
    _, session_factory, _ = postgres_quote_registry
    for quote_currency in ("HKD", "CNY"):
        instrument_id = f"fx-usd-{quote_currency.lower()}"
        seeded = shared_store.get_instrument(session_factory, instrument_id)
        assert seeded is not None
        assert seeded["instrument_type"] == "fx"
        assert seeded["currency"] == quote_currency
        inserted = shared_store.upsert_market_data(
            session_factory,
            instrument_id=instrument_id,
            metric_family="fx",
            quote_basis="spot",
            as_of_date=date(2026, 7, 11),
            value="7.8" if quote_currency == "HKD" else "7.2",
            currency=quote_currency,
            source_ref=f"postgres:{instrument_id}",
            status="complete",
        )
        assert inserted is not None

    statements: list[str] = []

    def capture_statement(*args) -> None:
        statement = str(args[2])
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with session_factory() as session:
            book = resolve_canonical_fx_window_book_in_session(
                session,
                currency_pairs=[
                    ("USD", "HKD"),
                    ("HKD", "USD"),
                    ("HKD", "CNY"),
                    ("CNY", "HKD"),
                ],
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 13),
                freshness_policy=QuoteFreshnessPolicy(
                    policy_version="canonical_quote_freshness.v1",
                    mode="calendar_day_carry_forward",
                    max_age_days=5,
                ),
                consumer_policy_version="postgres-canonical-fx-test.v1",
            )
        construction_query_count = len(statements)
        cross = book.rate_at("HKD", "CNY", date(2026, 7, 13))
        inverse = book.rate_at("HKD", "USD", date(2026, 7, 13))
        assert len(statements) == construction_query_count
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert construction_query_count == 5
    assert cross.resolution_status == "resolved"
    assert cross.rate == compose_effective_fx_rate(
        ((Decimal("7.8"), True), (Decimal("7.2"), False))
    )
    assert cross.effective_as_of_date == date(2026, 7, 11)
    assert len(cross.calculation_dependency.legs) == 2
    assert inverse.rate == compose_effective_fx_rate(((Decimal("7.8"), True),))
