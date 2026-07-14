from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from portfolio_app.calculations.portfolio_daily import worker as worker_subject
from portfolio_app.calculations.portfolio_daily.commands import (
    enqueue_portfolio_daily_unit_of_work,
)
from portfolio_app.calculations.portfolio_daily.constants import CALCULATION_KIND
from portfolio_app.calculations.portfolio_daily.worker import (
    PortfolioDailyWorker,
    WorkerCycleStatus,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.session import _configure_search_path
from portfolio_ops_calculation_core import WorkerRegistration
from tests.test_postgres_portfolio_daily_schema import (
    _postgres_database,
    _seed_sealed_manifest,
)


pytestmark = pytest.mark.postgresql_integration


def test_worker_consumes_sealed_manifest_and_atomically_publishes_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        settings = Settings(database_url=str(engine.url))
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                        INSERT INTO portfolio.portfolio_record (
                            portfolio_id, portfolio_name, base_currency,
                            valuation_timezone, valuation_cutoff_policy, sort_order,
                            operating_profile
                        ) VALUES (
                            'worker-e2e', 'Worker E2E', 'USD', 'UTC', 'close', 0,
                            'standard_taxonomy'
                        )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'worker-e2e-cash', 'worker-e2e', 'Cash',
                        'deposit_account', 'USD', 'active'
                    )
                    """
                )
            )

        queued = enqueue_portfolio_daily_unit_of_work(
            worker_engine,
            portfolio_id="worker-e2e",
            as_of_date=date(2026, 7, 14),
            requested_by="worker-e2e-test",
            settings=settings,
        )
        registration = WorkerRegistration(
            worker_id="portfolio-daily-worker-e2e",
            instance_id=uuid4(),
            worker_version="test",
            supported_calculation_kinds=(CALCULATION_KIND,),
        )
        worker = PortfolioDailyWorker(
            engine=worker_engine,
            settings=settings,
            registration=registration,
        )
        worker.start()
        try:
            outcome = worker.run_once()
        finally:
            worker.close()

        assert outcome.status is WorkerCycleStatus.SUCCEEDED
        assert outcome.run_id == queued.run_id
        assert outcome.canonical_output_hash is not None
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT run.status AS run_status,
                           job.status AS job_status,
                           run.published_output_hash,
                           output.canonical_output_hash,
                           output.snapshot_count,
                           current.publication_id IS NOT NULL AS is_current
                    FROM calculation_registry.calculation_run AS run
                    JOIN calculation_registry.calculation_job AS job
                      ON job.run_id=run.run_id
                    JOIN portfolio.portfolio_daily_run_output AS output
                      ON output.run_id=run.run_id
                     AND output.output_fencing_token=job.fencing_token
                    LEFT JOIN calculation_registry.calculation_current_publication
                        AS current
                      ON current.calculation_kind=run.calculation_kind
                     AND current.scope_kind=run.scope_kind
                     AND current.scope_id=run.scope_id
                    WHERE run.run_id=:run_id
                    """
                ),
                {"run_id": queued.run_id},
            ).one()
            assert row.run_status == "published"
            assert row.job_status == "succeeded"
        assert row.published_output_hash == outcome.canonical_output_hash
        assert row.canonical_output_hash == outcome.canonical_output_hash
        assert row.snapshot_count == 1
        assert row.is_current


def test_worker_terminalizes_real_postgres_constraint_failure_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_engine = _configure_search_path(engine, "portfolio")
        settings = Settings(database_url=str(engine.url))
        with engine.begin() as connection:
            run_id, _, _ = _seed_sealed_manifest(
                connection,
                operating_profile="standard_taxonomy",
            )

        with pytest.raises(DBAPIError) as error_info:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TEMPORARY TABLE worker_exactness_check (
                            value integer CHECK (value > 0)
                        ) ON COMMIT DROP
                        """
                    )
                )
                connection.execute(
                    text("INSERT INTO worker_exactness_check (value) VALUES (-1)")
                )
        database_error = error_info.value
        assert getattr(database_error.orig, "sqlstate", None) == "23514"

        def fail_with_constraint_error(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise database_error

        monkeypatch.setattr(
            worker_subject,
            "calculate_portfolio_daily_attempt",
            fail_with_constraint_error,
        )
        registration = WorkerRegistration(
            worker_id="portfolio-daily-worker-constraint",
            instance_id=uuid4(),
            worker_version="test",
            supported_calculation_kinds=(CALCULATION_KIND,),
        )
        worker = PortfolioDailyWorker(
            engine=worker_engine,
            settings=settings,
            registration=registration,
        )
        worker.start()
        try:
            outcome = worker.run_once()
        finally:
            worker.close()

        assert outcome.status is WorkerCycleStatus.FAILED
        assert outcome.reason_code == "database_integrity_contract_failed"
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT run.status AS run_status,
                           run.status_reason_code,
                           job.status AS job_status,
                           job.attempt, job.max_attempts, job.failure_code
                    FROM calculation_registry.calculation_run AS run
                    JOIN calculation_registry.calculation_job AS job
                      ON job.run_id=run.run_id
                    WHERE run.run_id=:run_id
                    """
                ),
                {"run_id": run_id},
            ).one()
        assert row.run_status == "failed"
        assert row.job_status == "failed"
        assert row.attempt == 1
        assert row.max_attempts > row.attempt
        assert row.status_reason_code == "database_integrity_contract_failed"
        assert row.failure_code == "database_integrity_contract_failed"
