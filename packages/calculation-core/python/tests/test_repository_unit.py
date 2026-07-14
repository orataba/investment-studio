from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from portfolio_ops_calculation_core.lifecycle import (
    ActiveJobLease,
    CalculationScope,
    LifecycleErrorCode,
    LifecycleReason,
    LifecycleRepositoryError,
    PublicationCommand,
    ScopeGenerationLock,
    StaleLease,
    StaleWorkerInstance,
    WorkerRegistration,
)
from portfolio_ops_calculation_core.repository import (
    advance_scope_generation_with_intent,
    claim_next_job,
    fail_recompute_intent,
    heartbeat_job,
    heartbeat_worker,
    lock_next_pending_recompute_intent,
    publish_run,
    read_scope_generation,
    register_worker,
    supersede_recompute_intent,
)


class _MappingsResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> "_MappingsResult":
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row


class _ExecutorWithoutTransactionMethods:
    def __init__(self, rows: list[dict[str, Any] | None]) -> None:
        self.rows = deque(rows)
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _MappingsResult:
        self.statements.append(statement)
        return _MappingsResult(self.rows.popleft())


def _sql(statement: Any) -> str:
    return " ".join(
        str(statement.compile(dialect=postgresql.dialect())).split()
    )


def _scope() -> CalculationScope:
    return CalculationScope("portfolio_daily", "portfolio", "portfolio-1")


def test_generation_and_intent_use_external_transaction_without_commit() -> None:
    now = datetime.now(timezone.utc)
    intent_id = uuid4()
    executor = _ExecutorWithoutTransactionMethods(
        [{"generation": 3}, {"intent_id": intent_id}]
    )
    result = advance_scope_generation_with_intent(
        executor,  # type: ignore[arg-type]
        _scope(),
        expected_generation=2,
        reason=LifecycleReason("transaction_revision"),
        intent_id=intent_id,
    )
    assert result.requested_generation == 3
    assert len(executor.statements) == 2
    assert "generation =" in _sql(executor.statements[0])
    assert ".generation = %(generation_" in _sql(executor.statements[0])
    assert now.tzinfo is not None


@pytest.mark.parametrize(
    ("lock", "clause"),
    [
        (ScopeGenerationLock.NONE, ""),
        (ScopeGenerationLock.SHARE, "FOR SHARE"),
        (ScopeGenerationLock.UPDATE, "FOR UPDATE"),
    ],
)
def test_scope_generation_read_has_explicit_lock_contract(
    lock: ScopeGenerationLock,
    clause: str,
) -> None:
    updated_at = datetime.now(timezone.utc)
    executor = _ExecutorWithoutTransactionMethods(
        [{"generation": 4, "updated_at": updated_at}]
    )
    result = read_scope_generation(
        executor,  # type: ignore[arg-type]
        _scope(),
        lock=lock,
    )
    assert result.generation == 4
    sql = _sql(executor.statements[0])
    if clause:
        assert clause in sql
    else:
        assert "FOR SHARE" not in sql and "FOR UPDATE" not in sql


def test_scope_generation_read_not_found_is_typed() -> None:
    executor = _ExecutorWithoutTransactionMethods([None])
    with pytest.raises(LifecycleRepositoryError) as captured:
        read_scope_generation(executor, _scope())  # type: ignore[arg-type]
    assert captured.value.code is LifecycleErrorCode.NOT_FOUND


def test_claim_sql_uses_skip_locked_and_includes_expired_takeover() -> None:
    executor = _ExecutorWithoutTransactionMethods([None])
    assert (
        claim_next_job(
            executor,  # type: ignore[arg-type]
            lease_owner="worker-1",
            calculation_kinds=("portfolio_daily",),
            lease_duration=timedelta(minutes=5),
        )
        is None
    )
    sql = _sql(executor.statements[0])
    assert "FOR UPDATE OF calculation_job, calculation_scope_generation SKIP LOCKED" in sql
    assert "calculation_job.lease_expires_at <= clock_timestamp()" in sql
    assert ".calculation_job.attempt < calculation_registry.calculation_job.max_attempts" in sql


def test_pending_intent_lock_skips_only_the_intent_row() -> None:
    intent_id = uuid4()
    created_at = datetime.now(timezone.utc)
    executor = _ExecutorWithoutTransactionMethods(
        [
            {
                "intent_id": intent_id,
                "calculation_kind": "portfolio_daily",
                "scope_kind": "portfolio",
                "scope_id": "portfolio-1",
                "requested_generation": 4,
                "current_generation": 5,
                "dedupe_key": "a" * 64,
                "reason_code": "quote_revision",
                "reason_context": {"source": "quote"},
                "created_at": created_at,
            }
        ]
    )
    intent = lock_next_pending_recompute_intent(
        executor,  # type: ignore[arg-type]
        calculation_kinds=("portfolio_daily",),
    )
    assert intent is not None
    assert intent.intent_id == intent_id
    assert intent.requested_generation == 4
    assert intent.current_generation == 5
    sql = _sql(executor.statements[0])
    assert "FOR UPDATE OF calculation_recompute_intent SKIP LOCKED" in sql
    assert "FOR UPDATE OF calculation_scope_generation" not in sql


@pytest.mark.parametrize(
    ("transition", "target"),
    [
        (supersede_recompute_intent, "superseded"),
        (fail_recompute_intent, "failed"),
    ],
)
def test_pending_intent_terminalization_is_caller_transaction_owned(
    transition: Any,
    target: str,
) -> None:
    handle = lock_next_pending_recompute_intent(
        _ExecutorWithoutTransactionMethods(
            [
                {
                    "intent_id": uuid4(),
                    "calculation_kind": "portfolio_daily",
                    "scope_kind": "portfolio",
                    "scope_id": "portfolio-1",
                    "requested_generation": 4,
                    "current_generation": 4,
                    "dedupe_key": "b" * 64,
                    "reason_code": "quote_revision",
                    "reason_context": {},
                    "created_at": datetime.now(timezone.utc),
                }
            ]
        ),  # type: ignore[arg-type]
        calculation_kinds=("portfolio_daily",),
    )
    assert handle is not None
    executor = _ExecutorWithoutTransactionMethods([{"intent_id": handle.intent_id}])
    transition(
        executor,
        handle.handle,
        reason_code=f"dispatcher_{target}",
    )
    sql = _sql(executor.statements[0])
    assert "UPDATE calculation_registry.calculation_recompute_intent" in sql
    assert "completed_at=clock_timestamp()" in sql
    assert executor.statements[0].compile().params["status"] == target


def test_stale_worker_heartbeat_fails_closed_on_owner_and_token_predicate() -> None:
    lease = ActiveJobLease(
        job_id=uuid4(),
        run_id=uuid4(),
        attempt=2,
        fencing_token=2,
        captured_generation=4,
        lease_owner="worker-old",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    executor = _ExecutorWithoutTransactionMethods([None])
    with pytest.raises(StaleLease):
        heartbeat_job(
            executor,  # type: ignore[arg-type]
            lease,
            lease_duration=timedelta(minutes=5),
        )
    sql = _sql(executor.statements[0])
    assert "calculation_job.status =" in sql
    assert "calculation_job.fencing_token =" in sql
    assert "calculation_job.lease_owner =" in sql
    assert "calculation_job.lease_expires_at > clock_timestamp()" in sql
    assert "calculation_scope_generation.generation =" in sql


def test_worker_registration_and_heartbeat_are_instance_fenced() -> None:
    registration = WorkerRegistration(
        worker_id="portfolio-daily-worker-1",
        instance_id=uuid4(),
        worker_version="portfolio-worker.v1",
        supported_calculation_kinds=("portfolio_daily",),
        metadata={"host": "test"},
    )
    started_at = datetime.now(timezone.utc)
    registered = _ExecutorWithoutTransactionMethods(
        [{"started_at": started_at, "heartbeat_at": started_at}]
    )
    heartbeat = register_worker(
        registered,  # type: ignore[arg-type]
        registration,
    )
    assert heartbeat.registration is registration
    register_sql = _sql(registered.statements[0])
    assert "ON CONFLICT (worker_id) DO NOTHING" in register_sql

    stale = _ExecutorWithoutTransactionMethods([None])
    with pytest.raises(StaleWorkerInstance):
        heartbeat_worker(stale, registration)  # type: ignore[arg-type]
    heartbeat_sql = _sql(stale.statements[0])
    assert "calculation_worker_heartbeat.instance_id =" in heartbeat_sql
    assert "heartbeat_at=clock_timestamp()" in heartbeat_sql


def test_publish_is_three_mutations_in_one_external_transaction() -> None:
    run_id = uuid4()
    manifest_id = uuid4()
    publication_id = uuid4()
    published_at = datetime.now(timezone.utc)
    context = {
        "run_id": run_id,
        "manifest_id": manifest_id,
        "calculation_kind": "portfolio_daily",
        "scope_kind": "portfolio",
        "scope_id": "portfolio-1",
        "methodology_version": "portfolio-daily.v1",
        "input_schema_version": "portfolio-daily-input.v1",
        "output_schema_version": "portfolio-daily-output.v1",
        "captured_generation": 5,
        "effective_as_of": date(2026, 7, 14),
        "fencing_token": 3,
    }
    executor = _ExecutorWithoutTransactionMethods(
        [
            context,
            {"published_at": published_at},
            {"publication_id": publication_id},
            {"run_id": run_id},
        ]
    )
    result = publish_run(
        executor,  # type: ignore[arg-type]
        PublicationCommand(run_id, "b" * 64, 3, None),
        publication_id=publication_id,
    )
    assert result.publication_id == publication_id
    assert len(executor.statements) == 4
    sql = [_sql(statement) for statement in executor.statements]
    assert sql[1].startswith("INSERT INTO calculation_registry.calculation_publication")
    assert sql[2].startswith(
        "INSERT INTO calculation_registry.calculation_current_publication"
    )
    assert sql[3].startswith("UPDATE calculation_registry.calculation_run")
    assert "published_fencing_token" in sql[1]


def test_publish_pointer_rowcount_zero_is_a_typed_cas_failure() -> None:
    run_id = uuid4()
    context = {
        "run_id": run_id,
        "manifest_id": uuid4(),
        "calculation_kind": "portfolio_daily",
        "scope_kind": "portfolio",
        "scope_id": "portfolio-1",
        "methodology_version": "portfolio-daily.v1",
        "input_schema_version": "portfolio-daily-input.v1",
        "output_schema_version": "portfolio-daily-output.v1",
        "captured_generation": 5,
        "effective_as_of": date(2026, 7, 14),
        "fencing_token": 3,
    }
    executor = _ExecutorWithoutTransactionMethods(
        [context, {"published_at": datetime.now(timezone.utc)}, None]
    )
    with pytest.raises(LifecycleRepositoryError) as captured:
        publish_run(
            executor,  # type: ignore[arg-type]
            PublicationCommand(run_id, "b" * 64, 3, UUID(int=1)),
        )
    assert captured.value.code is LifecycleErrorCode.PUBLICATION_CAS_CONFLICT
    assert len(executor.statements) == 3
