from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from portfolio_ops_calculation_core.lifecycle import (
    ActiveDedupeConflict,
    CalculationScope,
    FailureReason,
    LifecycleErrorCode,
    LifecycleReason,
    LifecycleRepositoryError,
    ManifestSeal,
    PublicationCommand,
    RunRequest,
    ScopeGenerationLock,
    StaleLease,
    StaleWorkerInstance,
    WorkerRegistration,
)
from portfolio_ops_calculation_core.models import CalculationScopeGeneration
from portfolio_ops_calculation_core.repository import LifecycleRepository
from portfolio_ops_calculation_core.state import CalculationRunStatus


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
)
RUN_POSTGRES = os.getenv("PORTFOLIO_OPS_RUN_CALCULATION_CORE_POSTGRES") == "1"

pytestmark = [
    pytest.mark.postgresql_integration,
    pytest.mark.skipif(
        not RUN_POSTGRES,
        reason="set PORTFOLIO_OPS_RUN_CALCULATION_CORE_POSTGRES=1 explicitly",
    ),
]


def _admin_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="module")
def registry_engine() -> Engine:
    base_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_core_repo_{uuid4().hex[:10]}"
    database_url = make_url(base_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    admin_engine = create_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    prior_expected = os.getenv("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE")
    prior_url = os.getenv("PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL")
    engine: Engine | None = None
    try:
        os.environ["PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE"] = database_name
        os.environ["PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL"] = (
            database_url
        )
        config = Config(str(WORKSPACE_ROOT / "infra/calculation_registry/alembic.ini"))
        config.set_main_option(
            "script_location",
            str(WORKSPACE_ROOT / "infra/calculation_registry/alembic"),
        )
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        yield engine
    finally:
        if prior_expected is None:
            os.environ.pop("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE", None)
        else:
            os.environ["PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE"] = prior_expected
        if prior_url is None:
            os.environ.pop(
                "PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL",
                None,
            )
        else:
            os.environ[
                "PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL"
            ] = prior_url
        if engine is not None:
            engine.dispose()
        admin_engine.dispose()
        cleanup = create_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
        try:
            with cleanup.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                )
        finally:
            cleanup.dispose()


def _request(
    scope: CalculationScope,
    *,
    methodology: str,
    generation: int,
    as_of: date = date(2026, 7, 14),
) -> RunRequest:
    return RunRequest(
        scope=scope,
        requested_as_of=as_of,
        effective_as_of=as_of,
        timezone_name="UTC",
        methodology_version=methodology,
        input_schema_version="portfolio-daily-input.v1",
        output_schema_version="portfolio-daily-output.v1",
        captured_generation=generation,
        requested_by="postgres-repository-test",
    )


def _create_queued_run(
    engine: Engine,
    request: RunRequest,
    *,
    create_scope: bool = True,
    max_attempts: int = 4,
):  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        if create_scope:
            LifecycleRepository.create_scope_generation(connection, request.scope)
        capturing = LifecycleRepository.create_capturing_run(connection, request)
        assert capturing.cutoff_at == connection.scalar(select(func.transaction_timestamp()))
        queued = LifecycleRepository.seal_manifest_and_queue_run(
            connection,
            capturing,
            ManifestSeal("a" * 64, {"configuration": 1}),
            max_attempts=max_attempts,
        )
    return capturing, queued


def test_repository_never_commits_the_callers_transaction(
    registry_engine: Engine,
) -> None:
    scope = CalculationScope("transaction_owner", "portfolio", "rollback-scope")
    with registry_engine.connect() as owner:
        transaction = owner.begin()
        LifecycleRepository.create_scope_generation(owner, scope)
        with registry_engine.connect() as observer:
            count = observer.scalar(
                select(func.count())
                .select_from(CalculationScopeGeneration.__table__)
                .where(
                    CalculationScopeGeneration.calculation_kind
                    == scope.calculation_kind,
                    CalculationScopeGeneration.scope_kind == scope.scope_kind,
                    CalculationScopeGeneration.scope_id == scope.scope_id,
                )
            )
            assert count == 0
        transaction.rollback()

    session_scope = CalculationScope(
        "transaction_owner",
        "portfolio",
        "rollback-session-scope",
    )
    with Session(registry_engine) as session:
        transaction = session.begin()
        LifecycleRepository.create_scope_generation(session, session_scope)
        transaction.rollback()
    with registry_engine.connect() as observer:
        count = observer.scalar(
            select(func.count())
            .select_from(CalculationScopeGeneration.__table__)
            .where(
                CalculationScopeGeneration.calculation_kind
                == session_scope.calculation_kind,
                CalculationScopeGeneration.scope_kind == session_scope.scope_kind,
                CalculationScopeGeneration.scope_id == session_scope.scope_id,
            )
        )
        assert count == 0


def test_worker_registration_and_heartbeat_are_database_fenced(
    registry_engine: Engine,
) -> None:
    registration = WorkerRegistration(
        worker_id="portfolio-daily-worker-db-fence",
        instance_id=uuid4(),
        worker_version="portfolio-daily-worker.v1",
        supported_calculation_kinds=("portfolio_daily",),
        metadata={"host": "repository-test"},
    )
    with registry_engine.begin() as connection:
        first = LifecycleRepository.register_worker(connection, registration)

    stale_registration = WorkerRegistration(
        worker_id=registration.worker_id,
        instance_id=uuid4(),
        worker_version=registration.worker_version,
        supported_calculation_kinds=registration.supported_calculation_kinds,
        metadata={"host": "stale-process"},
    )
    with pytest.raises(StaleWorkerInstance):
        with registry_engine.begin() as connection:
            LifecycleRepository.register_worker(connection, stale_registration)
    with pytest.raises(StaleWorkerInstance):
        with registry_engine.begin() as connection:
            LifecycleRepository.heartbeat_worker(connection, stale_registration)

    with registry_engine.begin() as connection:
        heartbeat = LifecycleRepository.heartbeat_worker(connection, registration)
        assert heartbeat.started_at == first.started_at
        assert heartbeat.heartbeat_at >= first.heartbeat_at
        assert LifecycleRepository.has_fresh_worker(
            connection,
            calculation_kind="portfolio_daily",
            max_age=timedelta(minutes=1),
        )
        assert not LifecycleRepository.has_fresh_worker(
            connection,
            calculation_kind="foreign_calculation",
            max_age=timedelta(minutes=1),
        )


def test_scope_generation_lock_and_active_dedupe_handle(
    registry_engine: Engine,
) -> None:
    scope = CalculationScope("portfolio_daily_read", "portfolio", "portfolio-read")
    request = _request(scope, methodology="portfolio-daily.read.v1", generation=0)
    with registry_engine.begin() as connection:
        LifecycleRepository.create_scope_generation(connection, scope)
        generation = LifecycleRepository.read_scope_generation(
            connection,
            scope,
            lock=ScopeGenerationLock.SHARE,
        )
        assert generation.generation == 0
        original = LifecycleRepository.create_capturing_run(connection, request)
        with pytest.raises(ActiveDedupeConflict) as conflict:
            LifecycleRepository.create_capturing_run(connection, request)
        assert conflict.value.active_run is not None
        assert conflict.value.active_run.run_id == original.run_id
        assert conflict.value.active_run.manifest_id == original.manifest_id
        assert conflict.value.details["retry_transaction"] is False


def test_full_fenced_lifecycle_retry_publish_and_pointer_cas(
    registry_engine: Engine,
) -> None:
    scope = CalculationScope("portfolio_daily_full", "portfolio", "portfolio-full")
    with registry_engine.begin() as connection:
        LifecycleRepository.create_scope_generation(connection, scope)
        intent = LifecycleRepository.advance_scope_generation_with_intent(
            connection,
            scope,
            expected_generation=0,
            reason=LifecycleReason("transaction_revision"),
        )
        request = _request(scope, methodology="portfolio-daily.v1", generation=1)
        capturing = LifecycleRepository.create_capturing_run(connection, request)
        LifecycleRepository.materialize_recompute_intent(
            connection,
            intent,
            run_id=capturing.run_id,
        )
        LifecycleRepository.seal_manifest_and_queue_run(
            connection,
            capturing,
            ManifestSeal("b" * 64, {"transaction": 1, "configuration": 1}),
            max_attempts=4,
        )

    with registry_engine.begin() as connection:
        first_lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-old",
            calculation_kinds=(scope.calculation_kind,),
            lease_duration=timedelta(milliseconds=80),
        )
        assert first_lease is not None
        assert first_lease.fencing_token == 1
    time.sleep(0.12)

    with registry_engine.begin() as connection:
        second_lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-takeover",
            calculation_kinds=(scope.calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert second_lease is not None
        assert second_lease.attempt == 2
        assert second_lease.fencing_token == 2

    with pytest.raises(StaleLease):
        with registry_engine.begin() as connection:
            LifecycleRepository.heartbeat_job(
                connection,
                first_lease,
                lease_duration=timedelta(seconds=5),
            )

    with registry_engine.begin() as connection:
        second_lease = LifecycleRepository.heartbeat_job(
            connection,
            second_lease,
            lease_duration=timedelta(seconds=5),
        )
        LifecycleRepository.retry_job(
            connection,
            second_lease,
            FailureReason("provider_timeout", "temporary quote timeout"),
            retry_delay=timedelta(milliseconds=20),
        )
    time.sleep(0.04)

    with registry_engine.begin() as connection:
        final_lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-final",
            calculation_kinds=(scope.calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert final_lease is not None
        assert final_lease.fencing_token == 3
        completion = LifecycleRepository.succeed_job(connection, final_lease)
        assert completion.status is CalculationRunStatus.SUCCEEDED

    with registry_engine.begin() as connection:
        first_publication = LifecycleRepository.publish_run(
            connection,
            PublicationCommand(capturing.run_id, "c" * 64, 3, None),
        )
    with registry_engine.connect() as connection:
        current = LifecycleRepository.read_current_publication_lineage(connection, scope)
        assert current == first_publication
        run = LifecycleRepository.read_run_lineage(connection, capturing.run_id)
        assert run is not None
        assert run.status is CalculationRunStatus.PUBLISHED
        assert run.published_fencing_token == 3

    second_request = _request(scope, methodology="portfolio-daily.v2", generation=1)
    second_run, _ = _create_queued_run(
        registry_engine,
        second_request,
        create_scope=False,
    )
    with registry_engine.begin() as connection:
        lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-v2",
            calculation_kinds=(scope.calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert lease is not None and lease.run_id == second_run.run_id
        LifecycleRepository.succeed_job(connection, lease)

    with pytest.raises(LifecycleRepositoryError) as captured_cas:
        with registry_engine.begin() as connection:
            LifecycleRepository.publish_run(
                connection,
                PublicationCommand(second_run.run_id, "d" * 64, 1, uuid4()),
            )
    assert captured_cas.value.code is LifecycleErrorCode.PUBLICATION_CAS_CONFLICT
    with registry_engine.connect() as connection:
        current = LifecycleRepository.read_current_publication_lineage(connection, scope)
        assert current is not None
        assert current.publication_id == first_publication.publication_id

    with registry_engine.begin() as connection:
        second_publication = LifecycleRepository.publish_run(
            connection,
            PublicationCommand(
                second_run.run_id,
                "d" * 64,
                1,
                first_publication.publication_id,
            ),
        )
    assert second_publication.run_id == second_run.run_id

    older_request = _request(
        scope,
        methodology="portfolio-daily-historical.v1",
        generation=1,
        as_of=date(2026, 7, 13),
    )
    older_run, _ = _create_queued_run(
        registry_engine,
        older_request,
        create_scope=False,
    )
    with registry_engine.begin() as connection:
        older_lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-historical",
            calculation_kinds=(scope.calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert older_lease is not None and older_lease.run_id == older_run.run_id
        LifecycleRepository.succeed_job(connection, older_lease)
    with pytest.raises(LifecycleRepositoryError) as time_regression:
        with registry_engine.begin() as connection:
            LifecycleRepository.publish_run(
                connection,
                PublicationCommand(
                    older_run.run_id,
                    "e" * 64,
                    1,
                    second_publication.publication_id,
                ),
            )
    assert time_regression.value.code is LifecycleErrorCode.PUBLICATION_CAS_CONFLICT
    with registry_engine.connect() as connection:
        current = LifecycleRepository.read_current_publication_lineage(connection, scope)
        assert current is not None
        assert current.publication_id == second_publication.publication_id


def test_conditional_failure_and_supersede_paths(registry_engine: Engine) -> None:
    fail_scope = CalculationScope("portfolio_daily_fail", "portfolio", "portfolio-fail")
    fail_run, _ = _create_queued_run(
        registry_engine,
        _request(fail_scope, methodology="failure.v1", generation=0),
    )
    with registry_engine.begin() as connection:
        lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-fail",
            calculation_kinds=(fail_scope.calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert lease is not None
        failed = LifecycleRepository.fail_active_job(
            connection,
            lease,
            FailureReason("calculation_invariant", "NAV did not close"),
        )
        assert failed.status is CalculationRunStatus.FAILED
    with registry_engine.connect() as connection:
        lineage = LifecycleRepository.read_run_lineage(connection, fail_run.run_id)
        assert lineage is not None and lineage.status is CalculationRunStatus.FAILED

    expired_scope = CalculationScope(
        "portfolio_daily_expired",
        "portfolio",
        "portfolio-expired",
    )
    expired_run, _ = _create_queued_run(
        registry_engine,
        _request(expired_scope, methodology="expired.v1", generation=0),
        max_attempts=1,
    )
    with registry_engine.begin() as connection:
        expired_lease = LifecycleRepository.claim_next_job(
            connection,
            lease_owner="worker-will-expire",
            calculation_kinds=(expired_scope.calculation_kind,),
            lease_duration=timedelta(milliseconds=60),
        )
        assert expired_lease is not None
    time.sleep(0.09)
    with registry_engine.begin() as connection:
        assert (
            LifecycleRepository.claim_next_job(
                connection,
                lease_owner="worker-cannot-exceed-max",
                calculation_kinds=(expired_scope.calculation_kind,),
                lease_duration=timedelta(seconds=5),
            )
            is None
        )
        expired_fence = LifecycleRepository.lock_expired_job_for_failure(
            connection,
            calculation_kinds=(expired_scope.calculation_kind,),
        )
        assert expired_fence is not None
        expired_failure = LifecycleRepository.fail_expired_job(
            connection,
            expired_fence,
            FailureReason("max_attempts_exhausted"),
        )
        assert expired_failure.run_id == expired_run.run_id

    supersede_scope = CalculationScope(
        "portfolio_daily_supersede",
        "portfolio",
        "portfolio-supersede",
    )
    with registry_engine.begin() as connection:
        LifecycleRepository.create_scope_generation(connection, supersede_scope)
        original = LifecycleRepository.create_capturing_run(
            connection,
            _request(supersede_scope, methodology="supersede.v1", generation=0),
        )
        with pytest.raises(LifecycleRepositoryError) as duplicate:
            LifecycleRepository.create_capturing_run(
                connection,
                _request(
                    supersede_scope,
                    methodology="supersede.v1",
                    generation=0,
                ),
            )
        assert duplicate.value.code is LifecycleErrorCode.ACTIVE_DEDUPE_CONFLICT
        replacement = LifecycleRepository.create_capturing_run(
            connection,
            _request(supersede_scope, methodology="supersede.v2", generation=0),
        )
        transition = LifecycleRepository.supersede_run(
            connection,
            run_id=original.run_id,
            replacement_run_id=replacement.run_id,
            expected_status=CalculationRunStatus.CAPTURING,
            reason=FailureReason("input_replaced"),
        )
        assert transition.status is CalculationRunStatus.SUPERSEDED


def test_concurrent_claim_skips_a_locked_job(registry_engine: Engine) -> None:
    calculation_kind = "portfolio_daily_skip_locked"
    first_scope = CalculationScope(calculation_kind, "portfolio", "skip-locked-1")
    second_scope = CalculationScope(calculation_kind, "portfolio", "skip-locked-2")
    _create_queued_run(
        registry_engine,
        _request(first_scope, methodology="skip-locked.v1", generation=0),
    )
    _create_queued_run(
        registry_engine,
        _request(second_scope, methodology="skip-locked.v1", generation=0),
    )

    first_connection = registry_engine.connect()
    second_connection = registry_engine.connect()
    first_transaction = first_connection.begin()
    second_transaction = second_connection.begin()
    try:
        first_lease = LifecycleRepository.claim_next_job(
            first_connection,
            lease_owner="worker-lock-holder",
            calculation_kinds=(calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        second_lease = LifecycleRepository.claim_next_job(
            second_connection,
            lease_owner="worker-skip-locked",
            calculation_kinds=(calculation_kind,),
            lease_duration=timedelta(seconds=5),
        )
        assert first_lease is not None
        assert second_lease is not None
        assert first_lease.job_id != second_lease.job_id
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_connection.close()
        first_connection.close()
