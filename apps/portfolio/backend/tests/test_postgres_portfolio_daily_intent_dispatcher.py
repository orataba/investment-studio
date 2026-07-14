from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from portfolio_app.calculations.portfolio_daily.commands import (
    PortfolioDailyCommandError,
    enqueue_portfolio_daily_unit_of_work,
)
from portfolio_app.calculations.portfolio_daily.intent_dispatcher import (
    IntentDispatchStatus,
    dispatch_next_portfolio_daily_intent,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.session import _configure_search_path
from portfolio_ops_calculation_core import (
    CalculationScope,
    LifecycleReason,
    LifecycleRepository,
)
from tests.test_postgres_portfolio_daily_schema import _postgres_database


pytestmark = pytest.mark.postgresql_integration


def test_future_portfolio_local_date_is_rejected_before_run_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        portfolio_id = "pd-future-as-of"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Future As Of', 'USD',
                        'standard_taxonomy', 'Asia/Shanghai', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
        with engine.connect() as connection:
            local_today = connection.scalar(
                text(
                    "SELECT "
                    "(transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')::date"
                )
            )
        assert type(local_today) is date

        with pytest.raises(
            PortfolioDailyCommandError,
            match="must not be after the portfolio-local transaction date",
        ):
            enqueue_portfolio_daily_unit_of_work(
                worker_engine,
                portfolio_id=portfolio_id,
                as_of_date=local_today + timedelta(days=1),
                requested_by="manual-api-test",
                settings=Settings(database_url=str(engine.url)),
            )

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM calculation_registry.calculation_run
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio' AND scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 0


def test_pending_intent_is_captured_sealed_queued_and_materialized_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        portfolio_id = "pd-dispatch"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Intent Dispatch', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'pd-dispatch-cash', :portfolio_id, 'Cash',
                        'deposit_account', 'USD', 'active'
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )

        outcome = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
            requested_as_of=date(2026, 7, 14),
        )
        assert outcome.status is IntentDispatchStatus.MATERIALIZED
        assert outcome.portfolio_id == portfolio_id
        assert outcome.requested_generation == 1
        assert outcome.run is not None
        assert not outcome.run.deduplicated

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT i.status AS intent_status, i.run_id AS intent_run_id,
                           r.status AS run_status, r.captured_generation,
                           r.requested_as_of,
                           m.status AS manifest_status,
                           m.canonical_manifest_hash,
                           j.status AS job_status
                    FROM calculation_registry.calculation_recompute_intent i
                    JOIN calculation_registry.calculation_run r
                      ON r.run_id=i.run_id
                    JOIN calculation_registry.calculation_input_manifest m
                      ON m.manifest_id=r.manifest_id
                    JOIN calculation_registry.calculation_job j
                      ON j.run_id=r.run_id
                    WHERE i.intent_id=:intent_id
                    """
                ),
                {"intent_id": outcome.intent_id},
            ).mappings().one()
            assert row["intent_status"] == "materialized"
            assert row["intent_run_id"] == outcome.run.run_id
            assert row["run_status"] == "queued"
            assert row["captured_generation"] == 1
            assert row["requested_as_of"] == date(2026, 7, 14)
            assert row["manifest_status"] == "sealed"
            assert len(row["canonical_manifest_hash"]) == 64
            assert row["job_status"] == "queued"

        idle = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
        )
        assert idle.status is IntentDispatchStatus.IDLE


def test_missing_portfolio_intent_is_terminalized_without_poisoning_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        orphan_scope = CalculationScope(
            "portfolio_daily",
            "portfolio",
            "pd-dispatch-orphan",
        )
        with engine.begin() as connection:
            LifecycleRepository.create_scope_generation(connection, orphan_scope)
            orphan_intent = LifecycleRepository.advance_scope_generation_with_intent(
                connection,
                orphan_scope,
                expected_generation=0,
                reason=LifecycleReason("orphan_portfolio"),
            )

        valid_portfolio_id = "pd-dispatch-after-orphan"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'After Orphan', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": valid_portfolio_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'pd-dispatch-after-orphan-cash', :portfolio_id, 'Cash',
                        'deposit_account', 'USD', 'active'
                    )
                    """
                ),
                {"portfolio_id": valid_portfolio_id},
            )

        failed = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
        )
        assert failed.status is IntentDispatchStatus.FAILED
        assert failed.intent_id == str(orphan_intent.intent_id)
        assert failed.reason_code == "manifest_capture_failed"

        materialized = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
        )
        assert materialized.status is IntentDispatchStatus.MATERIALIZED
        assert materialized.portfolio_id == valid_portfolio_id

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT status
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE intent_id=:intent_id
                    """
                ),
                {"intent_id": orphan_intent.intent_id},
            ) == "failed"


def test_older_generation_is_superseded_before_current_generation_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        portfolio_id = "pd-dispatch-generation-race"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Generation One', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'pd-dispatch-generation-race-cash', :portfolio_id,
                        'Cash', 'deposit_account', 'USD', 'active'
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE portfolio.portfolio_record
                    SET valuation_cutoff_policy='latest_complete_eod'
                    WHERE portfolio_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            )

        superseded = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
        )
        assert superseded.status is IntentDispatchStatus.SUPERSEDED
        assert superseded.requested_generation == 1
        assert superseded.reason_code == "newer_generation_pending"

        current = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=Settings(database_url=str(engine.url)),
            requested_by="portfolio-daily-worker-test",
        )
        assert current.status is IntentDispatchStatus.MATERIALIZED
        assert current.requested_generation == 2
        assert current.run is not None
        assert current.run.captured_generation == 2

        with engine.connect() as connection:
            states = connection.execute(
                text(
                    """
                    SELECT requested_generation, status, status_reason_code
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio' AND scope_id=:portfolio_id
                    ORDER BY requested_generation
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).all()
            assert states == [
                (1, "superseded", "newer_generation_pending"),
                (2, "materialized", None),
            ]


def test_existing_queued_run_is_linked_without_duplicate_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        portfolio_id = "pd-dispatch-existing-queue"
        settings = Settings(database_url=str(engine.url))
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        :portfolio_id, 'Existing Queue', 'USD',
                        'standard_taxonomy', 'UTC', 'close', 0
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'pd-dispatch-existing-queue-cash', :portfolio_id,
                        'Cash', 'deposit_account', 'USD', 'active'
                    )
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
        with engine.connect() as connection:
            as_of_date = connection.scalar(
                text("SELECT (transaction_timestamp() AT TIME ZONE 'UTC')::date")
            )

        existing = enqueue_portfolio_daily_unit_of_work(
            worker_engine,
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            requested_by="manual-api-test",
            settings=settings,
        )
        assert not existing.deduplicated

        outcome = dispatch_next_portfolio_daily_intent(
            worker_engine,
            settings=settings,
            requested_by="portfolio-daily-worker-test",
        )
        assert outcome.status is IntentDispatchStatus.MATERIALIZED
        assert outcome.run is not None
        assert outcome.run.deduplicated
        assert outcome.run.run_id == existing.run_id

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT count(*) FROM calculation_registry.calculation_run
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio' AND scope_id=:portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ) == 1
