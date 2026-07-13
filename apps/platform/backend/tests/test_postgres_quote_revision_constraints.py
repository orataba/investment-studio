from __future__ import annotations

from datetime import date
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
    resolve_canonical_fx_window_book_in_session,
)
from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
)

pytestmark = pytest.mark.postgresql_integration


def _admin_database_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(
        hide_password=False
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
    database_url = make_url(base_url).set(database=database_name).render_as_string(
        hide_password=False
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
            uuid_columns = connection.execute(
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
            ).mappings().all()
            value_source_columns = connection.execute(
                text(
                    """
                    SELECT column_name, data_type, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = 'instrument_registry'
                      AND table_name = 'quote_observation_revision'
                      AND column_name IN ('value', 'source_ref')
                    """
                )
            ).mappings().all()
            quote_indexes = connection.execute(
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
            ).mappings().all()
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
        with pytest.raises(DBAPIError, match="quote_revision_immutable_violation"):
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
                            source_ref, status, source_published_at, ingested_at,
                            payload_hash, is_current, superseded_at
                        ) VALUES (
                            :revision_id, :observation_id, 3, '102',
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

        exact_value = Decimal(
            "102.123456789012345678901234567890123456789"
        )
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
                        source_ref, status, source_published_at, ingested_at,
                        payload_hash, is_current, superseded_at
                    ) VALUES (
                        :revision_id, :observation_id, 3, :value,
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
            inserted = connection.execute(
                text(
                    """
                    SELECT value, source_ref, is_current
                    FROM instrument_registry.quote_observation_revision
                    WHERE revision_id = :revision_id
                    """
                ),
                {"revision_id": revision_3_id},
            ).mappings().one()
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
                            source_ref, status, source_published_at, ingested_at,
                            payload_hash, is_current, superseded_at
                        ) VALUES (
                            :revision_id, :observation_id, 4, 0,
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


def test_postgres_migration_seeds_fx_identities_without_quote_facts(
    postgres_quote_registry,
) -> None:
    database_url, _, _ = postgres_quote_registry
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                text(
                    "SELECT version_num "
                    "FROM instrument_registry.alembic_version"
                )
            )
            instruments = connection.execute(
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
            ).mappings().all()
            identifiers = connection.execute(
                text(
                    """
                    SELECT instrument_id, identifier_type, identifier_value,
                           is_primary
                    FROM instrument_registry.instrument_identifier
                    WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')
                    ORDER BY instrument_id
                    """
                )
            ).mappings().all()
            quote_fact_counts = connection.execute(
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
            ).mappings().one()
    finally:
        engine.dispose()

    strict_policy = {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": [],
        "chart": ["spot"],
        "reference": ["spot"],
    }
    assert revision == "20260713_0011"
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
    assert cross.rate == Decimal("7.2") / Decimal("7.8")
    assert cross.effective_as_of_date == date(2026, 7, 11)
    assert len(cross.calculation_dependency.legs) == 2
    assert inverse.rate == Decimal("1") / Decimal("7.8")
