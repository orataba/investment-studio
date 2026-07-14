from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from portfolio_app.calculations.portfolio_daily.published_repository import (
    read_current_portfolio_daily_publication,
)
from portfolio_ops_calculation_core.lifecycle import PublicationCommand
from portfolio_ops_calculation_core.repository import publish_run
from tests.test_postgres_portfolio_daily_schema import (
    _insert_unavailable_attempt,
    _lease,
    _postgres_database,
    _seed_sealed_manifest,
)


pytestmark = pytest.mark.postgresql_integration


def test_current_reader_excludes_abandoned_attempt_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-read-test-worker"
        with engine.begin() as connection:
            run_id_text, _, job_id = _seed_sealed_manifest(connection)
            run_id = UUID(run_id_text)

            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            _insert_unavailable_attempt(
                connection,
                run_id=run_id_text,
                token=1,
                worker_id=worker_id,
                output_hash="c" * 64,
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'retry_wait', available_at = clock_timestamp(),
                        lease_owner = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, failure_code = 'retryable_test',
                        failure_diagnostic = 'attempt one abandoned'
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            )

            _lease(connection, job_id=job_id, attempt=2, worker_id=worker_id)
            _insert_unavailable_attempt(
                connection,
                run_id=run_id_text,
                token=2,
                worker_id=worker_id,
                output_hash="d" * 64,
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'succeeded', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        failure_code = NULL, failure_diagnostic = NULL,
                        completed_at = clock_timestamp()
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'succeeded', completed_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )

            publication_id = uuid4()
            publish_run(
                connection,
                PublicationCommand(
                    run_id=run_id,
                    canonical_output_hash="d" * 64,
                    output_fencing_token=2,
                    expected_current_publication_id=None,
                ),
                publication_id=publication_id,
            )

            result = read_current_portfolio_daily_publication(
                connection,
                portfolio_id="attempt-isolation",
                range_start=date(2026, 7, 14),
                range_end=date(2026, 7, 14),
            )

            assert result is not None
            assert result.metadata.publication_id == publication_id
            assert result.metadata.run_id == run_id
            assert result.metadata.published_fencing_token == 2
            assert [row.output_fencing_token for row in result.snapshots] == [2]
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM portfolio.portfolio_daily_snapshot_output
                    WHERE run_id = :run_id AND output_fencing_token = 1
                    """
                ),
                {"run_id": run_id},
            ) == 1
