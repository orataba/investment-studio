from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.worker_repository import (
    PublicationRecoveryStatus,
    recover_next_portfolio_daily_publication,
    supersede_next_stale_generation_run,
)
from portfolio_app.calculations.portfolio_daily.commands import (
    enqueue_portfolio_daily_unit_of_work,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.session import _configure_search_path
from portfolio_ops_calculation_core import CalculationRunStatus
from tests.test_postgres_portfolio_daily_schema import (
    _insert_unavailable_attempt,
    _lease,
    _postgres_database,
    _seed_sealed_manifest,
)


pytestmark = pytest.mark.postgresql_integration


def _seed_current_generation_replacement(engine, *, generation: int) -> UUID:
    worker_engine = _configure_search_path(engine, "portfolio")
    handle = enqueue_portfolio_daily_unit_of_work(
        worker_engine,
        portfolio_id="attempt-isolation",
        as_of_date=date(2026, 7, 14),
        requested_by="worker-repository-test",
        settings=Settings(database_url=str(engine.url)),
    )
    assert handle.captured_generation == generation
    assert not handle.deduplicated
    return handle.run_id


def test_succeeded_output_is_recovered_into_one_atomic_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        worker_id = "portfolio-daily-worker-recovery"
        with engine.begin() as connection:
            run_id_text, _, job_id = _seed_sealed_manifest(
                connection,
                operating_profile="standard_taxonomy",
            )
            run_id = UUID(run_id_text)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status='running', started_at=clock_timestamp()
                    WHERE run_id=:run_id
                    """
                ),
                {"run_id": run_id},
            )
            _insert_unavailable_attempt(
                connection,
                run_id=run_id_text,
                token=1,
                worker_id=worker_id,
                output_hash="e" * 64,
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_job
                    SET status='succeeded', lease_owner=NULL,
                        lease_expires_at=NULL, heartbeat_at=NULL,
                        completed_at=clock_timestamp()
                    WHERE job_id=:job_id
                    """
                ),
                {"job_id": job_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status='succeeded', completed_at=clock_timestamp()
                    WHERE run_id=:run_id
                    """
                ),
                {"run_id": run_id},
            )

        with engine.connect().execution_options(
            isolation_level="SERIALIZABLE"
        ) as connection:
            with Session(bind=connection, expire_on_commit=False) as session:
                with session.begin():
                    outcome = recover_next_portfolio_daily_publication(session)
        assert outcome.status is PublicationRecoveryStatus.PUBLISHED
        assert outcome.run_id == run_id
        assert outcome.publication is not None
        assert outcome.publication.published_fencing_token == 1

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT status FROM calculation_registry.calculation_run
                    WHERE run_id=:run_id
                    """
                ),
                {"run_id": run_id},
            ) == "published"
            assert connection.scalar(
                text(
                    """
                    SELECT cp.publication_id
                    FROM calculation_registry.calculation_current_publication cp
                    WHERE cp.calculation_kind='portfolio_daily'
                      AND cp.scope_kind='portfolio'
                      AND cp.scope_id='attempt-isolation'
                    """
                )
            ) == outcome.publication.publication_id


def test_stale_queued_generation_is_superseded_only_after_replacement_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        with engine.begin() as connection:
            stale_run_text, _, _ = _seed_sealed_manifest(
                connection,
                operating_profile="standard_taxonomy",
            )
            stale_run_id = UUID(stale_run_text)
            replacement_generation = connection.scalar(
                text(
                    """
                    UPDATE calculation_registry.calculation_scope_generation
                    SET generation=generation + 1
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio'
                      AND scope_id='attempt-isolation'
                    RETURNING generation
                    """
                )
            )

        with Session(bind=engine, expire_on_commit=False) as session:
            with session.begin():
                assert (
                    supersede_next_stale_generation_run(
                        session,
                        worker_id="generation-reaper",
                    )
                    is None
                )

        replacement_run_id = _seed_current_generation_replacement(
            engine,
            generation=int(replacement_generation),
        )

        with Session(bind=engine, expire_on_commit=False) as session:
            with session.begin():
                outcome = supersede_next_stale_generation_run(
                    session,
                    worker_id="generation-reaper",
                )

        assert outcome is not None
        assert outcome.run_id == stale_run_id
        assert outcome.replacement_run_id == replacement_run_id
        assert outcome.previous_status is CalculationRunStatus.QUEUED
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT run.status, run.superseded_by_run_id,
                           job.status, job.failure_code
                    FROM calculation_registry.calculation_run AS run
                    JOIN calculation_registry.calculation_job AS job
                      ON job.run_id=run.run_id
                    WHERE run.run_id=:run_id
                    """
                ),
                {"run_id": stale_run_id},
            ).one() == (
                "superseded",
                replacement_run_id,
                "superseded",
                "generation_superseded",
            )


def test_stale_running_generation_respects_live_foreign_lease_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="head") as (engine, _):
        lease_owner = "owner-worker"
        with engine.begin() as connection:
            stale_run_text, _, job_id = _seed_sealed_manifest(
                connection,
                operating_profile="standard_taxonomy",
            )
            stale_run_id = UUID(stale_run_text)
            _lease(
                connection,
                job_id=job_id,
                attempt=1,
                worker_id=lease_owner,
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status='running', started_at=clock_timestamp()
                    WHERE run_id=:run_id
                    """
                ),
                {"run_id": stale_run_id},
            )
            replacement_generation = connection.scalar(
                text(
                    """
                    UPDATE calculation_registry.calculation_scope_generation
                    SET generation=generation + 1
                    WHERE calculation_kind='portfolio_daily'
                      AND scope_kind='portfolio'
                      AND scope_id='attempt-isolation'
                    RETURNING generation
                    """
                )
            )
        replacement_run_id = _seed_current_generation_replacement(
            engine,
            generation=int(replacement_generation),
        )

        with Session(bind=engine, expire_on_commit=False) as session:
            with session.begin():
                assert (
                    supersede_next_stale_generation_run(
                        session,
                        worker_id="different-worker",
                    )
                    is None
                )

        with Session(bind=engine, expire_on_commit=False) as session:
            with session.begin():
                outcome = supersede_next_stale_generation_run(
                    session,
                    worker_id=lease_owner,
                )

        assert outcome is not None
        assert outcome.run_id == stale_run_id
        assert outcome.replacement_run_id == replacement_run_id
        assert outcome.previous_status is CalculationRunStatus.RUNNING
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT run.status, run.superseded_by_run_id,
                           job.status, job.lease_owner
                    FROM calculation_registry.calculation_run AS run
                    JOIN calculation_registry.calculation_job AS job
                      ON job.run_id=run.run_id
                    WHERE run.run_id=:run_id
                    """
                ),
                {"run_id": stale_run_id},
            ).one() == (
                "superseded",
                replacement_run_id,
                "superseded",
                None,
            )
