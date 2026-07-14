from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from portfolio_ops_calculation_core import (
    CalculationScope,
    build_recompute_intent_dedupe_key,
)
from tests.test_postgres_portfolio_daily_schema import _postgres_database


pytestmark = pytest.mark.postgresql_integration


def _generation(connection, portfolio_id: str) -> int:
    return int(
        connection.scalar(
            text(
                """
                SELECT generation
                FROM calculation_registry.calculation_scope_generation
                WHERE calculation_kind='portfolio_daily'
                  AND scope_kind='portfolio' AND scope_id=:portfolio_id
                """
            ),
            {"portfolio_id": portfolio_id},
        )
    )


def _insert_instrument(connection, instrument_id: str, *, instrument_type: str = "fund") -> None:
    connection.execute(
        text(
            """
            INSERT INTO instrument_registry.instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json
            ) VALUES (
                :instrument_id, :instrument_id, :instrument_type, 'USD',
                '{"trading":["close"],"valuation":["close"],"total_return":[],"chart":["close"],"reference":["close"]}'::jsonb,
                '{}'::jsonb, '{}'::jsonb, '{"status":"active"}'::jsonb
            )
            """
        ),
        {"instrument_id": instrument_id, "instrument_type": instrument_type},
    )


def _insert_quote_identity(connection, instrument_id: str) -> tuple[str, str]:
    series_id = str(uuid4())
    observation_id = str(uuid4())
    parameters = {
        "series_id": series_id,
        "observation_id": observation_id,
        "instrument_id": instrument_id,
    }
    connection.execute(
        text(
            """
            INSERT INTO instrument_registry.quote_series (
                quote_series_id, instrument_id, metric_family,
                quote_basis, currency
            ) VALUES (:series_id, :instrument_id, 'price', 'close', 'USD')
            """
        ),
        parameters,
    )
    connection.execute(
        text(
            """
            INSERT INTO instrument_registry.quote_observation (
                observation_id, quote_series_id, as_of_date
            ) VALUES (:observation_id, :series_id, DATE '2026-07-14')
            """
        ),
        parameters,
    )
    return series_id, observation_id


def _insert_revision(connection, observation_id: str, revision_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO instrument_registry.quote_observation_revision (
                revision_id, observation_id, revision_number, value,
                value_input_scale, numeric_scale_state, payload_schema_version,
                status, payload_hash, is_current
            ) VALUES (
                :revision_id, :observation_id, 1, 100.25,
                2, 'declared', 2,
                'complete', :payload_hash, true
            )
            """
        ),
        {
            "revision_id": revision_id,
            "observation_id": observation_id,
            "payload_hash": "sha256:" + "a" * 64,
        },
    )


def _insert_portfolio_with_instrument_subscription(
    connection,
    *,
    portfolio_id: str,
    instrument_id: str,
    sort_order: int,
) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            """
            INSERT INTO portfolio.portfolio_record (
                portfolio_id, portfolio_name, base_currency,
                operating_profile, valuation_timezone,
                valuation_cutoff_policy, sort_order
            ) VALUES (
                :portfolio_id, :portfolio_id, 'USD', 'standard_taxonomy',
                'UTC', 'close', :sort_order
            )
            """
        ),
        {"portfolio_id": portfolio_id, "sort_order": sort_order},
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolio.portfolio_instrument_universe_record (
                portfolio_id, instrument_id, source, holding_state,
                transaction_count, status, created_at, updated_at
            ) VALUES (
                :portfolio_id, :instrument_id, 'manual', 'not_held',
                0, 'active', :now, :now
            )
            """
        ),
        {
            "portfolio_id": portfolio_id,
            "instrument_id": instrument_id,
            "now": "2026-07-14T00:00:00Z",
        },
    )


def test_statement_invalidation_is_typed_monotonic_and_once_per_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        portfolio_id = "pd-invalidation"
        instrument_ids = ("pd-fund-a", "pd-fund-b")
        with engine.begin() as connection:
            for instrument_id in instrument_ids:
                _insert_instrument(connection, instrument_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Invalidation', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            assert _generation(connection, portfolio_id) == 0
            subscriptions = set(
                connection.execute(
                    text(
                        """
                        SELECT dependency_kind, dependency_key
                        FROM portfolio.portfolio_daily_dependency_subscription
                        WHERE scope_id=:portfolio_id
                        """
                    ),
                    {"portfolio_id": portfolio_id},
                ).all()
            )
            assert {
                ("portfolio_config", portfolio_id),
                ("currency", "USD"),
                ("fx_market", "*"),
            } <= subscriptions

        # Two instruments in one SQL statement subscribe first and bump once.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, source, holding_state,
                        transaction_count, status, created_at, updated_at
                    ) VALUES
                        (:portfolio_id, :a, 'manual', 'not_held', 0, 'active', :now, :now),
                        (:portfolio_id, :b, 'manual', 'not_held', 0, 'active', :now, :now)
                    """
                ),
                {
                    "portfolio_id": portfolio_id,
                    "a": instrument_ids[0],
                    "b": instrument_ids[1],
                    "now": "2026-07-14T00:00:00Z",
                },
            )
            assert _generation(connection, portfolio_id) == 1
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM portfolio.portfolio_daily_dependency_subscription
                    WHERE scope_id=:portfolio_id AND dependency_kind='instrument'
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 2

        with engine.begin() as connection:
            _, observation_id = _insert_quote_identity(connection, instrument_ids[0])
        with engine.begin() as connection:
            _insert_revision(connection, observation_id, str(uuid4()))
            # Series identity + observation identity are one committed fact
            # transaction; the immutable revision is a second.  Generation is
            # transaction-coalesced, not inflated by internal SQL statements.
            assert _generation(connection, portfolio_id) == 3
            generation, dedupe_key, reason_context = connection.execute(
                text(
                    """
                    SELECT requested_generation, dedupe_key, reason_context
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE scope_id=:portfolio_id
                    ORDER BY requested_generation DESC LIMIT 1
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).one()
            assert generation == 3
            assert dedupe_key == build_recompute_intent_dedupe_key(
                CalculationScope("portfolio_daily", "portfolio", portfolio_id), 3
            )
            assert reason_context["source_transaction_id"].isdigit()

        # Two accounts in one statement are also one scope invalidation.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES
                      ('pd-account-a', :portfolio_id, 'A', 'deposit_account', 'USD', 'active'),
                      ('pd-account-b', :portfolio_id, 'B', 'deposit_account', 'USD', 'active')
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            assert _generation(connection, portfolio_id) == 4

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE portfolio.portfolio_record
                    SET portfolio_name='Invalidation Renamed',
                        sort_order=17,
                        lifecycle_status='archived'
                    WHERE portfolio_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            assert _generation(connection, portfolio_id) == 4

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE portfolio.portfolio_record
                    SET valuation_cutoff_policy='latest_complete_eod',
                        lifecycle_status='active'
                    WHERE portfolio_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            assert _generation(connection, portfolio_id) == 5

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.taxonomy_record (
                        taxonomy_id, portfolio_id, name, taxonomy_type,
                        primary_assignment_scope, planning_enabled,
                        root_default_target_dimension, status
                    ) VALUES (
                        'pd-taxonomy', :portfolio_id, 'Allocation', 'planning',
                        'instrument', true, 'weight', 'active'
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            assert _generation(connection, portfolio_id) == 6
            connection.execute(
                text(
                    """
                        INSERT INTO portfolio.taxonomy_node_record (
                            taxonomy_node_id, taxonomy_id, node_name, sort_order,
                            is_terminal, default_target_dimension, status
                        ) VALUES
                          ('pd-node-a', 'pd-taxonomy', 'A', 0, true, 'weight', 'active'),
                          ('pd-node-b', 'pd-taxonomy', 'B', 1, true, 'weight', 'active')
                    """
                )
            )
            assert _generation(connection, portfolio_id) == 6
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio' AND scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 6


def test_invalidation_coalesces_per_fact_transaction_and_rolls_back_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        portfolio_id = "pd-transaction-coalescing"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Transaction Coalescing', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )

        writer = engine.connect()
        transaction = writer.begin()
        try:
            for account_id in ("pd-rollback-a", "pd-rollback-b"):
                writer.execute(
                    text(
                        """
                        INSERT INTO portfolio.account_record (
                            account_id, portfolio_id, account_name,
                            account_type, currency, status
                        ) VALUES (
                            :account_id, :portfolio_id, :account_id,
                            'deposit_account', 'USD', 'active'
                        )
                        """
                    ),
                    {"account_id": account_id, "portfolio_id": portfolio_id},
                )
                assert _generation(writer, portfolio_id) == 1
            assert writer.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 1

            # Nothing in the fact transaction is externally visible before
            # commit, including generation, subscriptions, and intent.
            with engine.connect() as reader:
                assert _generation(reader, portfolio_id) == 0
                assert reader.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM calculation_registry.calculation_recompute_intent
                        WHERE scope_id=:portfolio_id
                        """
                    ),
                    {"portfolio_id": portfolio_id},
                ) == 0
            transaction.rollback()
        finally:
            if transaction.is_active:
                transaction.rollback()
            writer.close()

        with engine.connect() as connection:
            assert _generation(connection, portfolio_id) == 0
            assert connection.scalar(
                text(
                    """
                    SELECT count(*) FROM portfolio.account_record
                    WHERE portfolio_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 0
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 0

        with engine.begin() as connection:
            for account_id in ("pd-commit-a", "pd-commit-b"):
                connection.execute(
                    text(
                        """
                        INSERT INTO portfolio.account_record (
                            account_id, portfolio_id, account_name,
                            account_type, currency, status
                        ) VALUES (
                            :account_id, :portfolio_id, :account_id,
                            'deposit_account', 'USD', 'active'
                        )
                        """
                    ),
                    {"account_id": account_id, "portfolio_id": portfolio_id},
                )
            assert _generation(connection, portfolio_id) == 1

        with engine.connect() as connection:
            intent = connection.execute(
                text(
                    """
                    SELECT requested_generation, dedupe_key, reason_context
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).mappings().one()
            assert intent["requested_generation"] == 1
            assert intent["dedupe_key"] == build_recompute_intent_dedupe_key(
                CalculationScope("portfolio_daily", "portfolio", portfolio_id),
                1,
            )
            assert intent["reason_context"]["source_transaction_id"].isdigit()


def test_subscription_and_quote_revision_are_serialized_without_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        portfolio_id = "pd-race"
        instrument_id = "pd-race-fund"
        with engine.begin() as connection:
            _insert_instrument(connection, instrument_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :id, 'Race', 'USD', 'standard_taxonomy',
                        'UTC', 'close', 0
                    )
                    """
                ),
                {"id": portfolio_id},
            )
        with engine.begin() as connection:
            _, observation_id = _insert_quote_identity(connection, instrument_id)

        owner = engine.connect()
        transaction = owner.begin()
        try:
            owner.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, source, holding_state,
                        transaction_count, status, created_at, updated_at
                    ) VALUES (
                        :portfolio_id, :instrument_id, 'manual', 'not_held',
                        0, 'active', :now, :now
                    )
                    """
                ),
                {
                    "portfolio_id": portfolio_id,
                    "instrument_id": instrument_id,
                    "now": "2026-07-14T00:00:00Z",
                },
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _commit_revision,
                    engine,
                    observation_id,
                    str(uuid4()),
                )
                time.sleep(0.1)
                assert not future.done()
                transaction.commit()
                future.result(timeout=5)
        finally:
            if transaction.is_active:
                transaction.rollback()
            owner.close()

        with engine.connect() as connection:
            # universe subscription + quote revision; portfolio creation is generation zero.
            assert _generation(connection, portfolio_id) == 2


def test_mutable_instrument_and_quote_identities_invalidate_old_and_new_scopes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        instrument_a = "pd-identity-a"
        instrument_b = "pd-identity-b"
        fx_instrument = "pd-identity-fx"
        portfolio_a = "pd-identity-portfolio-a"
        portfolio_b = "pd-identity-portfolio-b"
        with engine.begin() as connection:
            _insert_instrument(connection, instrument_a)
            _insert_instrument(connection, instrument_b)
            _insert_instrument(connection, fx_instrument, instrument_type="fx")
            _insert_portfolio_with_instrument_subscription(
                connection,
                portfolio_id=portfolio_a,
                instrument_id=instrument_a,
                sort_order=0,
            )
            _insert_portfolio_with_instrument_subscription(
                connection,
                portfolio_id=portfolio_b,
                instrument_id=instrument_b,
                sort_order=1,
            )
            assert _generation(connection, portfolio_a) == 1
            assert _generation(connection, portfolio_b) == 1

        # One multi-row identifier statement is one invalidation for A.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.instrument_identifier (
                        instrument_id, identifier_type, identifier_value, is_primary
                    ) VALUES
                      (:a, 'ticker', 'PDIA', true),
                      (:a, 'internal', 'PDIA-INTERNAL', false)
                    """
                ),
                {"a": instrument_a},
            )
            assert _generation(connection, portfolio_a) == 2
            assert _generation(connection, portfolio_b) == 1

        # Moving both rows in one statement invalidates each old/new scope once.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.instrument_identifier
                    SET instrument_id=:b
                    WHERE instrument_id=:a
                    """
                ),
                {"a": instrument_a, "b": instrument_b},
            )
            assert _generation(connection, portfolio_a) == 3
            assert _generation(connection, portfolio_b) == 2
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM instrument_registry.instrument_identifier
                    WHERE instrument_id=:b
                    """
                ),
                {"b": instrument_b},
            )
            assert _generation(connection, portfolio_a) == 3
            assert _generation(connection, portfolio_b) == 3

        # Quote-series identity UPDATE must invalidate both the old and new
        # instrument subscribers, even before an observation exists.
        moved_series_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_series (
                        quote_series_id, instrument_id, metric_family,
                        quote_basis, currency
                    ) VALUES (:series_id, :a, 'price', 'close', 'USD')
                    """
                ),
                {"series_id": moved_series_id, "a": instrument_a},
            )
            assert _generation(connection, portfolio_a) == 4
            assert _generation(connection, portfolio_b) == 3
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.quote_series
                    SET instrument_id=:b
                    WHERE quote_series_id=:series_id
                    """
                ),
                {"series_id": moved_series_id, "b": instrument_b},
            )
            assert _generation(connection, portfolio_a) == 5
            assert _generation(connection, portfolio_b) == 4
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM instrument_registry.quote_series
                    WHERE quote_series_id=:series_id
                    """
                ),
                {"series_id": moved_series_id},
            )
            assert _generation(connection, portfolio_a) == 5
            assert _generation(connection, portfolio_b) == 5

        series_a = str(uuid4())
        series_b = str(uuid4())
        observation_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_series (
                        quote_series_id, instrument_id, metric_family,
                        quote_basis, currency
                    ) VALUES
                      (:series_a, :a, 'price', 'close', 'USD'),
                      (:series_b, :b, 'price', 'close', 'USD')
                    """
                ),
                {
                    "series_a": series_a,
                    "series_b": series_b,
                    "a": instrument_a,
                    "b": instrument_b,
                },
            )
            assert _generation(connection, portfolio_a) == 6
            assert _generation(connection, portfolio_b) == 6
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_observation (
                        observation_id, quote_series_id, as_of_date
                    ) VALUES (:observation_id, :series_a, DATE '2026-07-14')
                    """
                ),
                {"observation_id": observation_id, "series_a": series_a},
            )
            assert _generation(connection, portfolio_a) == 7
            assert _generation(connection, portfolio_b) == 6
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE instrument_registry.quote_observation
                    SET quote_series_id=:series_b
                    WHERE observation_id=:observation_id
                    """
                ),
                {"observation_id": observation_id, "series_b": series_b},
            )
            assert _generation(connection, portfolio_a) == 8
            assert _generation(connection, portfolio_b) == 7
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM instrument_registry.quote_observation
                    WHERE observation_id=:observation_id
                    """
                ),
                {"observation_id": observation_id},
            )
            assert _generation(connection, portfolio_a) == 8
            assert _generation(connection, portfolio_b) == 8
            connection.execute(
                text(
                    """
                    DELETE FROM instrument_registry.quote_series
                    WHERE quote_series_id IN (:series_a, :series_b)
                    """
                ),
                {"series_a": series_a, "series_b": series_b},
            )
            assert _generation(connection, portfolio_a) == 9
            assert _generation(connection, portfolio_b) == 8

        # An FX identity belongs to the conservative global FX dependency and
        # therefore invalidates every Portfolio Daily scope once per statement.
        fx_series = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_registry.quote_series (
                        quote_series_id, instrument_id, metric_family,
                        quote_basis, currency
                    ) VALUES (:series_id, :instrument_id, 'fx', 'spot', 'USD')
                    """
                ),
                {"series_id": fx_series, "instrument_id": fx_instrument},
            )
            assert _generation(connection, portfolio_a) == 10
            assert _generation(connection, portfolio_b) == 9


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE portfolio.portfolio_daily_dependency_subscription "
        "SET source_reason_code='tampered' WHERE scope_id='pd-immutable'",
        "DELETE FROM portfolio.portfolio_daily_dependency_subscription "
        "WHERE scope_id='pd-immutable'",
        "TRUNCATE portfolio.portfolio_daily_dependency_subscription",
    ),
)
def test_dependency_subscriptions_are_immutable_for_all_mutation_paths(
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        'pd-immutable', 'Immutable', 'USD', 'standard_taxonomy',
                        'UTC', 'close', 0
                    )
                    """
                )
            )
        with pytest.raises(DBAPIError, match="portfolio_daily_dependency_subscription"):
            with engine.begin() as connection:
                connection.execute(text(statement))


def _commit_revision(engine, observation_id: str, revision_id: str) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        _insert_revision(connection, observation_id, revision_id)
