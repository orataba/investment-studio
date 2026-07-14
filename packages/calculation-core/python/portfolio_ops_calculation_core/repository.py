from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence, cast
from uuid import UUID, uuid4

from sqlalchemy import Interval, Table, and_, bindparam, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, Result, RowMapping
from sqlalchemy.orm import Session

from portfolio_ops_calculation_core.lifecycle import (
    ActiveDedupeConflict,
    ActiveJobLease,
    ActiveRunHandle,
    CalculationScope,
    CapturingRun,
    FailureReason,
    GenerationConflict,
    InvalidLifecycleArgument,
    JobAttemptFence,
    LifecycleErrorCode,
    LifecycleReason,
    LifecycleRepositoryError,
    ManifestSeal,
    PendingRecomputeIntent,
    PublicationCommand,
    PublicationLineage,
    QueuedJob,
    RecomputeIntentHandle,
    RunCompletion,
    RunLineage,
    RunRequest,
    RunTransition,
    ScopeGeneration,
    ScopeGenerationLock,
    StaleLease,
    StaleWorkerInstance,
    WorkerHeartbeat,
    WorkerRegistration,
    build_recompute_intent_dedupe_key,
    require_positive_duration,
)
from portfolio_ops_calculation_core.models import (
    CalculationCurrentPublication,
    CalculationInputManifest,
    CalculationJob,
    CalculationPublication,
    CalculationRecomputeIntent,
    CalculationRun,
    CalculationScopeGeneration,
    CalculationWorkerHeartbeat,
)
from portfolio_ops_calculation_core.state import (
    CalculationJobStatus,
    CalculationManifestStatus,
    CalculationRunStatus,
    RecomputeIntentStatus,
)


type LifecycleExecutor = Connection | Session

_scope_generation = cast(Table, CalculationScopeGeneration.__table__)
_run = cast(Table, CalculationRun.__table__)
_manifest = cast(Table, CalculationInputManifest.__table__)
_job = cast(Table, CalculationJob.__table__)
_publication = cast(Table, CalculationPublication.__table__)
_current_publication = cast(Table, CalculationCurrentPublication.__table__)
_intent = cast(Table, CalculationRecomputeIntent.__table__)
_worker = cast(Table, CalculationWorkerHeartbeat.__table__)

_ACTIVE_RUN_STATUSES = (
    CalculationRunStatus.CAPTURING,
    CalculationRunStatus.QUEUED,
    CalculationRunStatus.RUNNING,
    CalculationRunStatus.SUCCEEDED,
)
_ACTIVE_JOB_STATUSES = (
    CalculationJobStatus.QUEUED,
    CalculationJobStatus.LEASED,
    CalculationJobStatus.RETRY_WAIT,
)


def _one_or_none(result: Result[Any]) -> RowMapping | None:
    return result.mappings().one_or_none()


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _run_status(value: Any) -> CalculationRunStatus:
    return CalculationRunStatus(_status_value(value))


def _job_status(value: Any) -> CalculationJobStatus:
    return CalculationJobStatus(_status_value(value))


def _conflict(
    message: str,
    *,
    entity: str,
    entity_id: UUID | str,
    expected: str,
) -> LifecycleRepositoryError:
    return LifecycleRepositoryError(
        LifecycleErrorCode.LIFECYCLE_CONFLICT,
        message,
        details={
            "entity": entity,
            "entity_id": str(entity_id),
            "expected": expected,
        },
    )


def _validate_max_attempts(max_attempts: int) -> None:
    if max_attempts <= 0:
        raise InvalidLifecycleArgument(
            "max_attempts must be positive",
            field_name="max_attempts",
        )


def _validate_worker(
    lease_owner: str,
    calculation_kinds: Sequence[str],
    lease_duration: timedelta,
) -> tuple[str, ...]:
    if not lease_owner or lease_owner != lease_owner.strip() or len(lease_owner) > 255:
        raise InvalidLifecycleArgument(
            "lease_owner must be a non-empty trimmed string of at most 255 characters",
            field_name="lease_owner",
        )
    require_positive_duration(lease_duration, field_name="lease_duration")
    kinds = tuple(dict.fromkeys(calculation_kinds))
    if not kinds:
        raise InvalidLifecycleArgument(
            "calculation_kinds must not be empty",
            field_name="calculation_kinds",
        )
    if any(not kind or kind != kind.strip() or len(kind) > 64 for kind in kinds):
        raise InvalidLifecycleArgument(
            "calculation_kinds must contain trimmed non-empty values",
            field_name="calculation_kinds",
        )
    return kinds


def _scope_predicate(scope: CalculationScope):
    return and_(
        _scope_generation.c.calculation_kind == scope.calculation_kind,
        _scope_generation.c.scope_kind == scope.scope_kind,
        _scope_generation.c.scope_id == scope.scope_id,
    )


def _current_generation_exists(run_id: UUID, captured_generation: int):
    return exists(
        select(1)
        .select_from(
            _run.join(
                _scope_generation,
                and_(
                    _scope_generation.c.calculation_kind
                    == _run.c.calculation_kind,
                    _scope_generation.c.scope_kind == _run.c.scope_kind,
                    _scope_generation.c.scope_id == _run.c.scope_id,
                ),
            )
        )
        .where(
            _run.c.run_id == run_id,
            _run.c.captured_generation == captured_generation,
            _scope_generation.c.generation == captured_generation,
        )
    )


def create_scope_generation(
    executor: LifecycleExecutor,
    scope: CalculationScope,
) -> ScopeGeneration:
    """Create generation zero inside the caller's current transaction."""

    statement = (
        postgresql_insert(_scope_generation)
        .values(
            calculation_kind=scope.calculation_kind,
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
            generation=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                _scope_generation.c.calculation_kind,
                _scope_generation.c.scope_kind,
                _scope_generation.c.scope_id,
            ]
        )
        .returning(
            _scope_generation.c.generation,
            _scope_generation.c.updated_at,
        )
    )
    row = _one_or_none(executor.execute(statement))
    if row is None:
        raise LifecycleRepositoryError(
            LifecycleErrorCode.SCOPE_ALREADY_EXISTS,
            "calculation scope already exists",
            details={
                "calculation_kind": scope.calculation_kind,
                "scope_kind": scope.scope_kind,
                "scope_id": scope.scope_id,
            },
        )
    return ScopeGeneration(scope, int(row["generation"]), row["updated_at"])


def read_scope_generation(
    executor: LifecycleExecutor,
    scope: CalculationScope,
    *,
    lock: ScopeGenerationLock = ScopeGenerationLock.NONE,
) -> ScopeGeneration:
    """Read a scope generation in the caller's snapshot, optionally locking it.

    The repository does not choose an isolation level.  A manifest producer
    should call this inside its caller-owned ``REPEATABLE READ`` transaction and
    normally use ``SHARE`` (blocks generation updates) or ``UPDATE`` when it
    will mutate the row itself.
    """

    statement = select(
        _scope_generation.c.generation,
        _scope_generation.c.updated_at,
    ).where(_scope_predicate(scope))
    if lock is ScopeGenerationLock.SHARE:
        statement = statement.with_for_update(read=True)
    elif lock is ScopeGenerationLock.UPDATE:
        statement = statement.with_for_update()
    elif lock is not ScopeGenerationLock.NONE:
        raise InvalidLifecycleArgument(
            "lock must be a ScopeGenerationLock value",
            field_name="lock",
        )

    row = _one_or_none(executor.execute(statement))
    if row is None:
        raise LifecycleRepositoryError(
            LifecycleErrorCode.NOT_FOUND,
            "calculation scope generation was not found",
            details={
                "calculation_kind": scope.calculation_kind,
                "scope_kind": scope.scope_kind,
                "scope_id": scope.scope_id,
            },
        )
    return ScopeGeneration(scope, int(row["generation"]), row["updated_at"])


def advance_scope_generation_with_intent(
    executor: LifecycleExecutor,
    scope: CalculationScope,
    *,
    expected_generation: int,
    reason: LifecycleReason,
    intent_id: UUID | None = None,
) -> RecomputeIntentHandle:
    """CAS-increment a scope and insert its one intent in the same outer transaction."""

    if expected_generation < 0:
        raise InvalidLifecycleArgument(
            "expected_generation must be non-negative",
            field_name="expected_generation",
        )
    generation_row = _one_or_none(
        executor.execute(
            update(_scope_generation)
            .where(
                _scope_predicate(scope),
                _scope_generation.c.generation == expected_generation,
            )
            .values(
                generation=_scope_generation.c.generation + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(_scope_generation.c.generation)
        )
    )
    if generation_row is None:
        raise GenerationConflict(scope, expected_generation)

    requested_generation = int(generation_row["generation"])
    resolved_intent_id = intent_id or uuid4()
    dedupe_key = build_recompute_intent_dedupe_key(scope, requested_generation)
    intent_row = _one_or_none(
        executor.execute(
            postgresql_insert(_intent)
            .values(
                intent_id=resolved_intent_id,
                calculation_kind=scope.calculation_kind,
                scope_kind=scope.scope_kind,
                scope_id=scope.scope_id,
                requested_generation=requested_generation,
                dedupe_key=dedupe_key,
                reason_code=reason.code,
                reason_context=dict(reason.context),
                status=RecomputeIntentStatus.PENDING,
            )
            .returning(_intent.c.intent_id)
        )
    )
    if intent_row is None:
        raise _conflict(
            "recompute intent insertion returned no row",
            entity="scope",
            entity_id=scope.scope_id,
            expected=f"generation={requested_generation}",
        )
    return RecomputeIntentHandle(
        intent_id=intent_row["intent_id"],
        scope=scope,
        requested_generation=requested_generation,
        dedupe_key=dedupe_key,
    )


def materialize_recompute_intent(
    executor: LifecycleExecutor,
    intent: RecomputeIntentHandle,
    *,
    run_id: UUID,
) -> None:
    row = _one_or_none(
        executor.execute(
            update(_intent)
            .where(
                _intent.c.intent_id == intent.intent_id,
                _intent.c.status == RecomputeIntentStatus.PENDING,
                _intent.c.requested_generation == intent.requested_generation,
                _intent.c.run_id.is_(None),
            )
            .values(
                status=RecomputeIntentStatus.MATERIALIZED,
                run_id=run_id,
                completed_at=func.clock_timestamp(),
            )
            .returning(_intent.c.intent_id)
        )
    )
    if row is None:
        raise _conflict(
            "recompute intent is no longer pending",
            entity="recompute_intent",
            entity_id=intent.intent_id,
            expected="pending",
        )


def lock_next_pending_recompute_intent(
    executor: LifecycleExecutor,
    *,
    calculation_kinds: Sequence[str],
) -> PendingRecomputeIntent | None:
    """Lock one pending intent without choosing the caller's transaction policy.

    The dispatcher must call this inside the same ``REPEATABLE READ``
    transaction that captures and seals the replacement run.  Only the intent
    row is locked here.  The producer then acquires its domain/config locks
    before the scope-generation share lock, preserving the fact-writer lock
    order and avoiding a scope/config deadlock.
    """

    kinds = tuple(dict.fromkeys(calculation_kinds))
    if not kinds or any(
        not kind or kind != kind.strip() or len(kind) > 64 for kind in kinds
    ):
        raise InvalidLifecycleArgument(
            "calculation_kinds must contain trimmed non-empty values",
            field_name="calculation_kinds",
        )
    row = _one_or_none(
        executor.execute(
            select(
                _intent.c.intent_id,
                _intent.c.calculation_kind,
                _intent.c.scope_kind,
                _intent.c.scope_id,
                _intent.c.requested_generation,
                _scope_generation.c.generation.label("current_generation"),
                _intent.c.dedupe_key,
                _intent.c.reason_code,
                _intent.c.reason_context,
                _intent.c.created_at,
            )
            .select_from(
                _intent.join(
                    _scope_generation,
                    and_(
                        _scope_generation.c.calculation_kind
                        == _intent.c.calculation_kind,
                        _scope_generation.c.scope_kind == _intent.c.scope_kind,
                        _scope_generation.c.scope_id == _intent.c.scope_id,
                    ),
                )
            )
            .where(
                _intent.c.status == RecomputeIntentStatus.PENDING,
                _intent.c.calculation_kind.in_(kinds),
            )
            .order_by(_intent.c.created_at, _intent.c.intent_id)
            .limit(1)
            .with_for_update(of=_intent, skip_locked=True)
        )
    )
    if row is None:
        return None
    return PendingRecomputeIntent(
        intent_id=row["intent_id"],
        scope=CalculationScope(
            row["calculation_kind"],
            row["scope_kind"],
            row["scope_id"],
        ),
        requested_generation=int(row["requested_generation"]),
        current_generation=int(row["current_generation"]),
        dedupe_key=row["dedupe_key"],
        reason_code=row["reason_code"],
        reason_context=dict(row["reason_context"]),
        created_at=row["created_at"],
    )


def _terminalize_recompute_intent(
    executor: LifecycleExecutor,
    intent: RecomputeIntentHandle,
    *,
    status: RecomputeIntentStatus,
    reason_code: str,
) -> None:
    if status not in {
        RecomputeIntentStatus.SUPERSEDED,
        RecomputeIntentStatus.FAILED,
    }:
        raise InvalidLifecycleArgument(
            "terminal intent status must be superseded or failed",
            field_name="status",
        )
    if not reason_code or reason_code != reason_code.strip() or len(reason_code) > 64:
        raise InvalidLifecycleArgument(
            "reason_code must be a non-empty trimmed string of at most 64 characters",
            field_name="reason_code",
        )
    row = _one_or_none(
        executor.execute(
            update(_intent)
            .where(
                _intent.c.intent_id == intent.intent_id,
                _intent.c.status == RecomputeIntentStatus.PENDING,
                _intent.c.requested_generation == intent.requested_generation,
                _intent.c.run_id.is_(None),
            )
            .values(
                status=status,
                status_reason_code=reason_code,
                completed_at=func.clock_timestamp(),
            )
            .returning(_intent.c.intent_id)
        )
    )
    if row is None:
        raise _conflict(
            "recompute intent is no longer pending",
            entity="recompute_intent",
            entity_id=intent.intent_id,
            expected="pending",
        )


def supersede_recompute_intent(
    executor: LifecycleExecutor,
    intent: RecomputeIntentHandle,
    *,
    reason_code: str,
) -> None:
    _terminalize_recompute_intent(
        executor,
        intent,
        status=RecomputeIntentStatus.SUPERSEDED,
        reason_code=reason_code,
    )


def fail_recompute_intent(
    executor: LifecycleExecutor,
    intent: RecomputeIntentHandle,
    *,
    reason_code: str,
) -> None:
    _terminalize_recompute_intent(
        executor,
        intent,
        status=RecomputeIntentStatus.FAILED,
        reason_code=reason_code,
    )


def register_worker(
    executor: LifecycleExecutor,
    registration: WorkerRegistration,
) -> WorkerHeartbeat:
    """Register one process instance; a reused worker id fails closed."""

    row = _one_or_none(
        executor.execute(
            postgresql_insert(_worker)
            .values(
                worker_id=registration.worker_id,
                instance_id=registration.instance_id,
                worker_version=registration.worker_version,
                supported_calculation_kinds=list(
                    registration.supported_calculation_kinds
                ),
                metadata_json=dict(registration.metadata),
            )
            .on_conflict_do_nothing(index_elements=[_worker.c.worker_id])
            .returning(_worker.c.started_at, _worker.c.heartbeat_at)
        )
    )
    if row is None:
        raise StaleWorkerInstance(
            registration.worker_id,
            registration.instance_id,
            "register",
        )
    return WorkerHeartbeat(
        registration=registration,
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
    )


def heartbeat_worker(
    executor: LifecycleExecutor,
    registration: WorkerRegistration,
) -> WorkerHeartbeat:
    """Advance liveness only for the exact registered process instance."""

    row = _one_or_none(
        executor.execute(
            update(_worker)
            .where(
                _worker.c.worker_id == registration.worker_id,
                _worker.c.instance_id == registration.instance_id,
            )
            .values(
                worker_version=registration.worker_version,
                supported_calculation_kinds=list(
                    registration.supported_calculation_kinds
                ),
                heartbeat_at=func.clock_timestamp(),
                metadata_json=dict(registration.metadata),
            )
            .returning(_worker.c.started_at, _worker.c.heartbeat_at)
        )
    )
    if row is None:
        raise StaleWorkerInstance(
            registration.worker_id,
            registration.instance_id,
            "heartbeat",
        )
    return WorkerHeartbeat(
        registration=registration,
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
    )


def has_fresh_worker(
    executor: LifecycleExecutor,
    *,
    calculation_kind: str,
    max_age: timedelta,
) -> bool:
    if not calculation_kind or calculation_kind != calculation_kind.strip() or len(
        calculation_kind
    ) > 64:
        raise InvalidLifecycleArgument(
            "calculation_kind must be a non-empty trimmed value",
            field_name="calculation_kind",
        )
    require_positive_duration(max_age, field_name="max_age")
    age_interval = bindparam("worker_max_age", value=max_age, type_=Interval())
    return bool(
        executor.scalar(
            select(
                exists(
                    select(1).where(
                        _worker.c.heartbeat_at
                        >= func.clock_timestamp() - age_interval,
                        _worker.c.supported_calculation_kinds.contains(
                            [calculation_kind]
                        ),
                    )
                )
            )
        )
    )


def create_capturing_run(
    executor: LifecycleExecutor,
    request: RunRequest,
    *,
    run_id: UUID | None = None,
    manifest_id: UUID | None = None,
) -> CapturingRun:
    resolved_run_id = run_id or uuid4()
    resolved_manifest_id = manifest_id or uuid4()
    run_statement = (
        postgresql_insert(_run)
        .values(
            run_id=resolved_run_id,
            calculation_kind=request.scope.calculation_kind,
            scope_kind=request.scope.scope_kind,
            scope_id=request.scope.scope_id,
            requested_as_of=request.requested_as_of,
            effective_as_of=request.effective_as_of,
            cutoff_at=func.transaction_timestamp(),
            timezone=request.timezone_name,
            methodology_version=request.methodology_version,
            input_schema_version=request.input_schema_version,
            output_schema_version=request.output_schema_version,
            captured_generation=request.captured_generation,
            dedupe_key=request.dedupe_key,
            status=CalculationRunStatus.CAPTURING,
            requested_by=request.requested_by,
        )
        .on_conflict_do_nothing(
            index_elements=[_run.c.dedupe_key],
            index_where=_run.c.status.in_(_ACTIVE_RUN_STATUSES),
        )
        .returning(_run.c.run_id, _run.c.cutoff_at)
    )
    run_row = _one_or_none(executor.execute(run_statement))
    if run_row is None:
        raise ActiveDedupeConflict(
            request.dedupe_key,
            read_active_run_by_dedupe(executor, request),
        )

    manifest_row = _one_or_none(
        executor.execute(
            postgresql_insert(_manifest)
            .values(
                manifest_id=resolved_manifest_id,
                run_id=resolved_run_id,
                captured_generation=request.captured_generation,
                schema_version=request.input_schema_version,
                status=CalculationManifestStatus.BUILDING,
            )
            .returning(_manifest.c.manifest_id)
        )
    )
    if manifest_row is None:
        raise _conflict(
            "input manifest insertion returned no row",
            entity="run",
            entity_id=resolved_run_id,
            expected="capturing",
        )
    return CapturingRun(
        run_id=resolved_run_id,
        manifest_id=resolved_manifest_id,
        request=request,
        dedupe_key=request.dedupe_key,
        cutoff_at=run_row["cutoff_at"],
    )


def read_active_run_by_dedupe(
    executor: LifecycleExecutor,
    request: RunRequest,
    *,
    lock: ScopeGenerationLock = ScopeGenerationLock.SHARE,
) -> ActiveRunHandle | None:
    """Return the active owner of a run request's exact dedupe identity.

    ``None`` means no owner is visible in the caller's current snapshot.  It is
    not permission to ignore an ``ActiveDedupeConflict`` whose
    ``retry_transaction`` detail is true; that case requires a fresh outer
    transaction because PostgreSQL's unique check saw a row older snapshots
    cannot read.
    """

    active_manifest = _manifest.alias("active_manifest")
    statement = (
        select(
            _run.c.run_id,
            active_manifest.c.manifest_id,
            _run.c.calculation_kind,
            _run.c.scope_kind,
            _run.c.scope_id,
            _run.c.status,
            _run.c.requested_as_of,
            _run.c.effective_as_of,
            _run.c.cutoff_at,
            _run.c.timezone,
            _run.c.methodology_version,
            _run.c.input_schema_version,
            _run.c.output_schema_version,
            _run.c.captured_generation,
            _run.c.dedupe_key,
            _run.c.created_at,
        )
        .select_from(
            _run.outerjoin(
                active_manifest,
                active_manifest.c.run_id == _run.c.run_id,
            )
        )
        .where(
            _run.c.dedupe_key == request.dedupe_key,
            _run.c.status.in_(_ACTIVE_RUN_STATUSES),
            _run.c.calculation_kind == request.scope.calculation_kind,
            _run.c.scope_kind == request.scope.scope_kind,
            _run.c.scope_id == request.scope.scope_id,
        )
    )
    if lock is ScopeGenerationLock.SHARE:
        statement = statement.with_for_update(of=_run, read=True)
    elif lock is ScopeGenerationLock.UPDATE:
        statement = statement.with_for_update(of=_run)
    elif lock is not ScopeGenerationLock.NONE:
        raise InvalidLifecycleArgument(
            "lock must be a ScopeGenerationLock value",
            field_name="lock",
        )
    row = _one_or_none(executor.execute(statement))
    if row is None:
        return None
    return ActiveRunHandle(
        run_id=row["run_id"],
        manifest_id=row["manifest_id"],
        scope=CalculationScope(
            row["calculation_kind"],
            row["scope_kind"],
            row["scope_id"],
        ),
        status=_run_status(row["status"]),
        requested_as_of=row["requested_as_of"],
        effective_as_of=row["effective_as_of"],
        cutoff_at=row["cutoff_at"],
        timezone_name=row["timezone"],
        methodology_version=row["methodology_version"],
        input_schema_version=row["input_schema_version"],
        output_schema_version=row["output_schema_version"],
        captured_generation=int(row["captured_generation"]),
        dedupe_key=row["dedupe_key"],
        created_at=row["created_at"],
    )


def seal_manifest_and_queue_run(
    executor: LifecycleExecutor,
    capturing: CapturingRun,
    seal: ManifestSeal,
    *,
    max_attempts: int = 3,
    job_id: UUID | None = None,
) -> QueuedJob:
    _validate_max_attempts(max_attempts)
    manifest_row = _one_or_none(
        executor.execute(
            update(_manifest)
            .where(
                _manifest.c.manifest_id == capturing.manifest_id,
                _manifest.c.run_id == capturing.run_id,
                _manifest.c.status == CalculationManifestStatus.BUILDING,
                _manifest.c.captured_generation
                == capturing.request.captured_generation,
            )
            .values(
                status=CalculationManifestStatus.SEALED,
                canonical_manifest_hash=seal.canonical_manifest_hash,
                dependency_counts=dict(seal.dependency_counts),
                sealed_at=func.clock_timestamp(),
            )
            .returning(_manifest.c.manifest_id)
        )
    )
    if manifest_row is None:
        raise _conflict(
            "manifest could not be sealed",
            entity="manifest",
            entity_id=capturing.manifest_id,
            expected="building at captured generation",
        )

    run_row = _one_or_none(
        executor.execute(
            update(_run)
            .where(
                _run.c.run_id == capturing.run_id,
                _run.c.status == CalculationRunStatus.CAPTURING,
                _run.c.manifest_id.is_(None),
                _run.c.captured_generation == capturing.request.captured_generation,
            )
            .values(
                status=CalculationRunStatus.QUEUED,
                manifest_id=capturing.manifest_id,
            )
            .returning(_run.c.dedupe_key)
        )
    )
    if run_row is None:
        raise _conflict(
            "run could not transition to queued",
            entity="run",
            entity_id=capturing.run_id,
            expected="capturing with sealed manifest",
        )

    resolved_job_id = job_id or uuid4()
    job_row = _one_or_none(
        executor.execute(
            postgresql_insert(_job)
            .values(
                job_id=resolved_job_id,
                run_id=capturing.run_id,
                dedupe_key=capturing.dedupe_key,
                status=CalculationJobStatus.QUEUED,
                max_attempts=max_attempts,
            )
            .on_conflict_do_nothing(
                index_elements=[_job.c.dedupe_key],
                index_where=_job.c.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .returning(_job.c.job_id)
        )
    )
    if job_row is None:
        raise LifecycleRepositoryError(
            LifecycleErrorCode.ACTIVE_DEDUPE_CONFLICT,
            "an active job already owns the calculation dedupe key",
            details={"dedupe_key": capturing.dedupe_key},
        )
    return QueuedJob(
        job_id=resolved_job_id,
        run_id=capturing.run_id,
        manifest_id=capturing.manifest_id,
        dedupe_key=capturing.dedupe_key,
        max_attempts=max_attempts,
    )


def claim_next_job(
    executor: LifecycleExecutor,
    *,
    lease_owner: str,
    calculation_kinds: Sequence[str],
    lease_duration: timedelta,
) -> ActiveJobLease | None:
    """Claim one ready job using PostgreSQL row skipping.

    Queued jobs start their run. Retry-wait jobs and expired leased jobs keep
    the already-running run. Expired leases are always taken over with a new
    attempt and fencing token; the old token is never renewed.
    """

    kinds = _validate_worker(lease_owner, calculation_kinds, lease_duration)
    now = func.clock_timestamp()
    join_condition = and_(
        _scope_generation.c.calculation_kind == _run.c.calculation_kind,
        _scope_generation.c.scope_kind == _run.c.scope_kind,
        _scope_generation.c.scope_id == _run.c.scope_id,
    )
    claimable = or_(
        and_(
            _job.c.status == CalculationJobStatus.QUEUED,
            _run.c.status == CalculationRunStatus.QUEUED,
            _job.c.available_at <= now,
        ),
        and_(
            _job.c.status == CalculationJobStatus.RETRY_WAIT,
            _run.c.status == CalculationRunStatus.RUNNING,
            _job.c.available_at <= now,
        ),
        and_(
            _job.c.status == CalculationJobStatus.LEASED,
            _run.c.status == CalculationRunStatus.RUNNING,
            _job.c.lease_expires_at <= now,
        ),
    )
    candidate_statement = (
        select(
            _job.c.job_id,
            _job.c.run_id,
            _job.c.status.label("job_status"),
            _job.c.attempt,
            _job.c.fencing_token,
            _run.c.status.label("run_status"),
            _run.c.captured_generation,
        )
        .select_from(
            _job.join(_run, _run.c.run_id == _job.c.run_id).join(
                _scope_generation,
                join_condition,
            )
        )
        .where(
            _run.c.calculation_kind.in_(kinds),
            _job.c.attempt < _job.c.max_attempts,
            _scope_generation.c.generation == _run.c.captured_generation,
            claimable,
        )
        .order_by(_job.c.available_at, _job.c.created_at, _job.c.job_id)
        .limit(1)
        .with_for_update(
            of=[_job, _scope_generation],
            skip_locked=True,
        )
    )
    candidate = _one_or_none(executor.execute(candidate_statement))
    if candidate is None:
        return None

    old_job_status = _job_status(candidate["job_status"])
    old_attempt = int(candidate["attempt"])
    old_token = int(candidate["fencing_token"])
    job_conditions = [
        _job.c.job_id == candidate["job_id"],
        _job.c.run_id == candidate["run_id"],
        _job.c.status == old_job_status,
        _job.c.attempt == old_attempt,
        _job.c.fencing_token == old_token,
        _job.c.attempt < _job.c.max_attempts,
    ]
    if old_job_status is CalculationJobStatus.LEASED:
        job_conditions.append(_job.c.lease_expires_at <= now)
    else:
        job_conditions.append(_job.c.available_at <= now)

    lease_interval = bindparam(
        "lease_duration",
        value=lease_duration,
        type_=Interval(),
    )
    claimed = _one_or_none(
        executor.execute(
            update(_job)
            .where(*job_conditions)
            .values(
                status=CalculationJobStatus.LEASED,
                attempt=old_attempt + 1,
                fencing_token=old_token + 1,
                lease_owner=lease_owner,
                heartbeat_at=now,
                lease_expires_at=now + lease_interval,
                failure_code=None,
                failure_diagnostic=None,
                completed_at=None,
            )
            .returning(
                _job.c.job_id,
                _job.c.run_id,
                _job.c.attempt,
                _job.c.fencing_token,
                _job.c.lease_owner,
                _job.c.lease_expires_at,
            )
        )
    )
    if claimed is None:
        raise _conflict(
            "claim candidate changed after its row lock",
            entity="job",
            entity_id=candidate["job_id"],
            expected=f"{old_job_status.value} attempt={old_attempt} token={old_token}",
        )

    if old_job_status is CalculationJobStatus.QUEUED:
        started = _one_or_none(
            executor.execute(
                update(_run)
                .where(
                    _run.c.run_id == candidate["run_id"],
                    _run.c.status == CalculationRunStatus.QUEUED,
                    _run.c.captured_generation == candidate["captured_generation"],
                )
                .values(
                    status=CalculationRunStatus.RUNNING,
                    started_at=func.clock_timestamp(),
                )
                .returning(_run.c.run_id)
            )
        )
        if started is None:
            raise _conflict(
                "claimed job could not start its queued run",
                entity="run",
                entity_id=candidate["run_id"],
                expected="queued",
            )

    return ActiveJobLease(
        job_id=claimed["job_id"],
        run_id=claimed["run_id"],
        attempt=int(claimed["attempt"]),
        fencing_token=int(claimed["fencing_token"]),
        captured_generation=int(candidate["captured_generation"]),
        lease_owner=claimed["lease_owner"],
        lease_expires_at=claimed["lease_expires_at"],
    )


def lock_expired_job_for_failure(
    executor: LifecycleExecutor,
    *,
    calculation_kinds: Sequence[str],
) -> JobAttemptFence | None:
    """Lock one exhausted expired lease for fail-closed reaping.

    The returned fence is only valid in the same caller-owned transaction and
    must be passed to ``fail_expired_job`` before that transaction ends.
    """

    kinds = tuple(dict.fromkeys(calculation_kinds))
    if not kinds or any(
        not kind or kind != kind.strip() or len(kind) > 64 for kind in kinds
    ):
        raise InvalidLifecycleArgument(
            "calculation_kinds must contain trimmed non-empty values",
            field_name="calculation_kinds",
        )
    now = func.clock_timestamp()
    row = _one_or_none(
        executor.execute(
            select(
                _job.c.job_id,
                _job.c.run_id,
                _job.c.attempt,
                _job.c.fencing_token,
                _run.c.captured_generation,
            )
            .select_from(
                _job.join(_run, _run.c.run_id == _job.c.run_id).join(
                    _scope_generation,
                    and_(
                        _scope_generation.c.calculation_kind
                        == _run.c.calculation_kind,
                        _scope_generation.c.scope_kind == _run.c.scope_kind,
                        _scope_generation.c.scope_id == _run.c.scope_id,
                    ),
                )
            )
            .where(
                _run.c.calculation_kind.in_(kinds),
                _run.c.status == CalculationRunStatus.RUNNING,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.lease_expires_at <= now,
                _job.c.attempt >= _job.c.max_attempts,
                _scope_generation.c.generation == _run.c.captured_generation,
            )
            .order_by(_job.c.lease_expires_at, _job.c.job_id)
            .limit(1)
            .with_for_update(
                of=[_job, _scope_generation],
                skip_locked=True,
            )
        )
    )
    if row is None:
        return None
    return JobAttemptFence(
        job_id=row["job_id"],
        run_id=row["run_id"],
        attempt=int(row["attempt"]),
        fencing_token=int(row["fencing_token"]),
        captured_generation=int(row["captured_generation"]),
    )


def heartbeat_job(
    executor: LifecycleExecutor,
    lease: ActiveJobLease,
    *,
    lease_duration: timedelta,
) -> ActiveJobLease:
    require_positive_duration(lease_duration, field_name="lease_duration")
    now = func.clock_timestamp()
    lease_interval = bindparam(
        "lease_duration",
        value=lease_duration,
        type_=Interval(),
    )
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == lease.job_id,
                _job.c.run_id == lease.run_id,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.attempt == lease.attempt,
                _job.c.fencing_token == lease.fencing_token,
                _job.c.lease_owner == lease.lease_owner,
                _job.c.lease_expires_at > now,
                _current_generation_exists(
                    lease.run_id,
                    lease.captured_generation,
                ),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + lease_interval,
            )
            .returning(_job.c.lease_expires_at)
        )
    )
    if row is None:
        raise StaleLease(lease, "heartbeat")
    return ActiveJobLease(
        job_id=lease.job_id,
        run_id=lease.run_id,
        attempt=lease.attempt,
        fencing_token=lease.fencing_token,
        captured_generation=lease.captured_generation,
        lease_owner=lease.lease_owner,
        lease_expires_at=row["lease_expires_at"],
    )


def retry_job(
    executor: LifecycleExecutor,
    lease: ActiveJobLease,
    reason: FailureReason,
    *,
    retry_delay: timedelta,
) -> JobAttemptFence:
    require_positive_duration(retry_delay, field_name="retry_delay")
    now = func.clock_timestamp()
    retry_interval = bindparam(
        "retry_delay",
        value=retry_delay,
        type_=Interval(),
    )
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == lease.job_id,
                _job.c.run_id == lease.run_id,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.attempt == lease.attempt,
                _job.c.fencing_token == lease.fencing_token,
                _job.c.lease_owner == lease.lease_owner,
                _job.c.lease_expires_at > now,
                _job.c.attempt < _job.c.max_attempts,
                _current_generation_exists(
                    lease.run_id,
                    lease.captured_generation,
                ),
            )
            .values(
                status=CalculationJobStatus.RETRY_WAIT,
                available_at=now + retry_interval,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                failure_code=reason.code,
                failure_diagnostic=reason.diagnostic,
            )
            .returning(_job.c.job_id)
        )
    )
    if row is None:
        raise StaleLease(lease, "retry")
    return JobAttemptFence(
        job_id=lease.job_id,
        run_id=lease.run_id,
        attempt=lease.attempt,
        fencing_token=lease.fencing_token,
        captured_generation=lease.captured_generation,
    )


def _complete_running_run(
    executor: LifecycleExecutor,
    fence: JobAttemptFence,
    *,
    target_status: CalculationRunStatus,
    reason: FailureReason | None,
) -> RunCompletion:
    values: dict[str, Any] = {
        "status": target_status,
        "completed_at": func.clock_timestamp(),
    }
    if reason is not None:
        values["status_reason_code"] = reason.code
        values["status_reason_context"] = dict(reason.context)
    row = _one_or_none(
        executor.execute(
            update(_run)
            .where(
                _run.c.run_id == fence.run_id,
                _run.c.status == CalculationRunStatus.RUNNING,
                _run.c.captured_generation == fence.captured_generation,
            )
            .values(**values)
            .returning(_run.c.completed_at)
        )
    )
    if row is None:
        raise _conflict(
            f"run could not transition to {target_status.value}",
            entity="run",
            entity_id=fence.run_id,
            expected=f"running generation={fence.captured_generation}",
        )
    return RunCompletion(
        run_id=fence.run_id,
        job_id=fence.job_id,
        status=target_status,
        attempt=fence.attempt,
        fencing_token=fence.fencing_token,
        completed_at=row["completed_at"],
    )


def succeed_job(
    executor: LifecycleExecutor,
    lease: ActiveJobLease,
) -> RunCompletion:
    now = func.clock_timestamp()
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == lease.job_id,
                _job.c.run_id == lease.run_id,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.attempt == lease.attempt,
                _job.c.fencing_token == lease.fencing_token,
                _job.c.lease_owner == lease.lease_owner,
                _job.c.lease_expires_at > now,
                _current_generation_exists(
                    lease.run_id,
                    lease.captured_generation,
                ),
            )
            .values(
                status=CalculationJobStatus.SUCCEEDED,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                completed_at=now,
            )
            .returning(_job.c.job_id)
        )
    )
    if row is None:
        raise StaleLease(lease, "succeed")
    return _complete_running_run(
        executor,
        lease,
        target_status=CalculationRunStatus.SUCCEEDED,
        reason=None,
    )


def fail_active_job(
    executor: LifecycleExecutor,
    lease: ActiveJobLease,
    reason: FailureReason,
) -> RunCompletion:
    now = func.clock_timestamp()
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == lease.job_id,
                _job.c.run_id == lease.run_id,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.attempt == lease.attempt,
                _job.c.fencing_token == lease.fencing_token,
                _job.c.lease_owner == lease.lease_owner,
                _job.c.lease_expires_at > now,
                _current_generation_exists(
                    lease.run_id,
                    lease.captured_generation,
                ),
            )
            .values(
                status=CalculationJobStatus.FAILED,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                failure_code=reason.code,
                failure_diagnostic=reason.diagnostic,
                completed_at=now,
            )
            .returning(_job.c.job_id)
        )
    )
    if row is None:
        raise StaleLease(lease, "fail")
    return _complete_running_run(
        executor,
        lease,
        target_status=CalculationRunStatus.FAILED,
        reason=reason,
    )


def fail_expired_job(
    executor: LifecycleExecutor,
    fence: JobAttemptFence,
    reason: FailureReason,
) -> RunCompletion:
    """Terminalize an expired lease without pretending the reaper owns it."""

    now = func.clock_timestamp()
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == fence.job_id,
                _job.c.run_id == fence.run_id,
                _job.c.status == CalculationJobStatus.LEASED,
                _job.c.attempt == fence.attempt,
                _job.c.fencing_token == fence.fencing_token,
                _job.c.lease_expires_at <= now,
                _current_generation_exists(
                    fence.run_id,
                    fence.captured_generation,
                ),
            )
            .values(
                status=CalculationJobStatus.FAILED,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                failure_code=reason.code,
                failure_diagnostic=reason.diagnostic,
                completed_at=now,
            )
            .returning(_job.c.job_id)
        )
    )
    if row is None:
        raise StaleLease(fence, "fail_expired")
    return _complete_running_run(
        executor,
        fence,
        target_status=CalculationRunStatus.FAILED,
        reason=reason,
    )


def fail_retry_wait_job(
    executor: LifecycleExecutor,
    fence: JobAttemptFence,
    reason: FailureReason,
) -> RunCompletion:
    row = _one_or_none(
        executor.execute(
            update(_job)
            .where(
                _job.c.job_id == fence.job_id,
                _job.c.run_id == fence.run_id,
                _job.c.status == CalculationJobStatus.RETRY_WAIT,
                _job.c.attempt == fence.attempt,
                _job.c.fencing_token == fence.fencing_token,
                _current_generation_exists(
                    fence.run_id,
                    fence.captured_generation,
                ),
            )
            .values(
                status=CalculationJobStatus.FAILED,
                failure_code=reason.code,
                failure_diagnostic=reason.diagnostic,
                completed_at=func.clock_timestamp(),
            )
            .returning(_job.c.job_id)
        )
    )
    if row is None:
        raise StaleLease(fence, "fail_retry_wait")
    return _complete_running_run(
        executor,
        fence,
        target_status=CalculationRunStatus.FAILED,
        reason=reason,
    )


def fail_capturing_run(
    executor: LifecycleExecutor,
    capturing: CapturingRun,
    reason: FailureReason,
) -> RunTransition:
    row = _one_or_none(
        executor.execute(
            update(_run)
            .where(
                _run.c.run_id == capturing.run_id,
                _run.c.status == CalculationRunStatus.CAPTURING,
                _run.c.captured_generation == capturing.request.captured_generation,
            )
            .values(
                status=CalculationRunStatus.FAILED,
                status_reason_code=reason.code,
                status_reason_context=dict(reason.context),
                completed_at=func.clock_timestamp(),
            )
            .returning(_run.c.completed_at)
        )
    )
    if row is None:
        raise _conflict(
            "capturing run could not fail",
            entity="run",
            entity_id=capturing.run_id,
            expected="capturing",
        )
    return RunTransition(
        run_id=capturing.run_id,
        status=CalculationRunStatus.FAILED,
        completed_at=row["completed_at"],
    )


def supersede_run(
    executor: LifecycleExecutor,
    *,
    run_id: UUID,
    replacement_run_id: UUID,
    expected_status: CalculationRunStatus,
    reason: FailureReason,
    fence: JobAttemptFence | None = None,
    lease_owner: str | None = None,
) -> RunTransition:
    if run_id == replacement_run_id:
        raise InvalidLifecycleArgument(
            "replacement_run_id must differ from run_id",
            field_name="replacement_run_id",
        )
    if expected_status not in {
        CalculationRunStatus.CAPTURING,
        CalculationRunStatus.QUEUED,
        CalculationRunStatus.RUNNING,
        CalculationRunStatus.SUCCEEDED,
    }:
        raise InvalidLifecycleArgument(
            "expected_status cannot be terminal",
            field_name="expected_status",
        )

    if expected_status is CalculationRunStatus.QUEUED:
        job_row = _one_or_none(
            executor.execute(
                update(_job)
                .where(
                    _job.c.run_id == run_id,
                    _job.c.status == CalculationJobStatus.QUEUED,
                    _job.c.attempt == 0,
                    _job.c.fencing_token == 0,
                )
                .values(
                    status=CalculationJobStatus.SUPERSEDED,
                    failure_code=reason.code,
                    failure_diagnostic=reason.diagnostic,
                    completed_at=func.clock_timestamp(),
                )
                .returning(_job.c.job_id)
            )
        )
        if job_row is None:
            raise _conflict(
                "queued job could not be superseded",
                entity="run",
                entity_id=run_id,
                expected="queued job attempt=0 token=0",
            )
    elif expected_status is CalculationRunStatus.RUNNING:
        if fence is None or fence.run_id != run_id:
            raise InvalidLifecycleArgument(
                "running supersede requires the exact job attempt fence",
                field_name="fence",
            )
        now = func.clock_timestamp()
        lease_branch = and_(
            _job.c.status == CalculationJobStatus.LEASED,
            or_(
                _job.c.lease_expires_at <= now,
                and_(
                    _job.c.lease_expires_at > now,
                    _job.c.lease_owner == lease_owner,
                ),
            ),
        )
        retry_branch = _job.c.status == CalculationJobStatus.RETRY_WAIT
        job_row = _one_or_none(
            executor.execute(
                update(_job)
                .where(
                    _job.c.job_id == fence.job_id,
                    _job.c.run_id == run_id,
                    _job.c.attempt == fence.attempt,
                    _job.c.fencing_token == fence.fencing_token,
                    or_(lease_branch, retry_branch),
                )
                .values(
                    status=CalculationJobStatus.SUPERSEDED,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    failure_code=reason.code,
                    failure_diagnostic=reason.diagnostic,
                    completed_at=now,
                )
                .returning(_job.c.job_id)
            )
        )
        if job_row is None:
            raise StaleLease(fence, "supersede")

    row = _one_or_none(
        executor.execute(
            update(_run)
            .where(
                _run.c.run_id == run_id,
                _run.c.status == expected_status,
            )
            .values(
                status=CalculationRunStatus.SUPERSEDED,
                status_reason_code=reason.code,
                status_reason_context=dict(reason.context),
                superseded_by_run_id=replacement_run_id,
                completed_at=func.clock_timestamp(),
            )
            .returning(_run.c.completed_at)
        )
    )
    if row is None:
        raise _conflict(
            "run could not be superseded",
            entity="run",
            entity_id=run_id,
            expected=expected_status.value,
        )
    return RunTransition(
        run_id=run_id,
        status=CalculationRunStatus.SUPERSEDED,
        completed_at=row["completed_at"],
    )


def publish_run(
    executor: LifecycleExecutor,
    command: PublicationCommand,
    *,
    publication_id: UUID | None = None,
) -> PublicationLineage:
    """Publish through one caller-owned transaction.

    The function inserts the immutable publication, CAS-switches the current
    pointer, then marks the run published. The registry's deferred constraint
    trigger validates the final state at the caller's commit. This function
    deliberately does not commit, begin, roll back, or open a savepoint.
    """

    context_statement = (
        select(
            _run.c.run_id,
            _run.c.manifest_id,
            _run.c.calculation_kind,
            _run.c.scope_kind,
            _run.c.scope_id,
            _run.c.methodology_version,
            _run.c.input_schema_version,
            _run.c.output_schema_version,
            _run.c.captured_generation,
            _run.c.effective_as_of,
            _job.c.fencing_token,
        )
        .select_from(
            _run.join(_job, _job.c.run_id == _run.c.run_id)
            .join(_manifest, _manifest.c.manifest_id == _run.c.manifest_id)
            .join(
                _scope_generation,
                and_(
                    _scope_generation.c.calculation_kind
                    == _run.c.calculation_kind,
                    _scope_generation.c.scope_kind == _run.c.scope_kind,
                    _scope_generation.c.scope_id == _run.c.scope_id,
                ),
            )
        )
        .where(
            _run.c.run_id == command.run_id,
            _run.c.status == CalculationRunStatus.SUCCEEDED,
            _run.c.published_output_hash.is_(None),
            _job.c.status == CalculationJobStatus.SUCCEEDED,
            _manifest.c.status == CalculationManifestStatus.SEALED,
            _manifest.c.run_id == _run.c.run_id,
            _scope_generation.c.generation == _run.c.captured_generation,
        )
        .with_for_update(of=[_run, _job, _scope_generation])
    )
    context = _one_or_none(executor.execute(context_statement))
    if context is None:
        raise _conflict(
            "run is not publication-ready at the current generation",
            entity="run",
            entity_id=command.run_id,
            expected="succeeded job + sealed manifest + current generation",
        )
    if int(context["fencing_token"]) != command.output_fencing_token:
        raise LifecycleRepositoryError(
            LifecycleErrorCode.STALE_LEASE,
            "publication output token does not match the final succeeded job token",
            details={
                "run_id": str(command.run_id),
                "expected_token": int(context["fencing_token"]),
                "received_token": command.output_fencing_token,
            },
        )

    resolved_publication_id = publication_id or uuid4()
    publication_row = _one_or_none(
        executor.execute(
            postgresql_insert(_publication)
            .values(
                publication_id=resolved_publication_id,
                run_id=command.run_id,
                manifest_id=context["manifest_id"],
                calculation_kind=context["calculation_kind"],
                scope_kind=context["scope_kind"],
                scope_id=context["scope_id"],
                output_schema_version=context["output_schema_version"],
                published_fencing_token=command.output_fencing_token,
                canonical_output_hash=command.canonical_output_hash,
            )
            .returning(_publication.c.published_at)
        )
    )
    if publication_row is None:
        raise _conflict(
            "publication insertion returned no row",
            entity="run",
            entity_id=command.run_id,
            expected="one immutable publication",
        )

    scope_columns = {
        "calculation_kind": context["calculation_kind"],
        "scope_kind": context["scope_kind"],
        "scope_id": context["scope_id"],
    }
    pointer_statement: Any
    if command.expected_current_publication_id is None:
        pointer_statement = (
            postgresql_insert(_current_publication)
            .values(
                **scope_columns,
                publication_id=resolved_publication_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    _current_publication.c.calculation_kind,
                    _current_publication.c.scope_kind,
                    _current_publication.c.scope_id,
                ]
            )
            .returning(_current_publication.c.publication_id)
        )
    else:
        prior_publication = _publication.alias("prior_publication")
        prior_run = _run.alias("prior_run")
        prior_is_not_later = exists(
            select(1)
            .select_from(
                prior_publication.join(
                    prior_run,
                    prior_run.c.run_id == prior_publication.c.run_id,
                )
            )
            .where(
                prior_publication.c.publication_id
                == command.expected_current_publication_id,
                prior_run.c.effective_as_of <= context["effective_as_of"],
            )
        )
        pointer_statement = (
            update(_current_publication)
            .where(
                _current_publication.c.calculation_kind
                == context["calculation_kind"],
                _current_publication.c.scope_kind == context["scope_kind"],
                _current_publication.c.scope_id == context["scope_id"],
                _current_publication.c.publication_id
                == command.expected_current_publication_id,
                prior_is_not_later,
            )
            .values(publication_id=resolved_publication_id)
            .returning(_current_publication.c.publication_id)
        )
    pointer_row = _one_or_none(executor.execute(pointer_statement))
    if pointer_row is None:
        raise LifecycleRepositoryError(
            LifecycleErrorCode.PUBLICATION_CAS_CONFLICT,
            "current publication pointer did not match the expected value",
            details={
                "run_id": str(command.run_id),
                "expected_current_publication_id": (
                    str(command.expected_current_publication_id)
                    if command.expected_current_publication_id is not None
                    else None
                ),
            },
        )

    published_run = _one_or_none(
        executor.execute(
            update(_run)
            .where(
                _run.c.run_id == command.run_id,
                _run.c.status == CalculationRunStatus.SUCCEEDED,
                _run.c.manifest_id == context["manifest_id"],
                _run.c.captured_generation == context["captured_generation"],
                _run.c.published_output_hash.is_(None),
            )
            .values(
                status=CalculationRunStatus.PUBLISHED,
                published_output_hash=command.canonical_output_hash,
            )
            .returning(_run.c.run_id)
        )
    )
    if published_run is None:
        raise _conflict(
            "run could not complete the atomic publication transition",
            entity="run",
            entity_id=command.run_id,
            expected="succeeded with matching manifest and generation",
        )

    return PublicationLineage(
        publication_id=resolved_publication_id,
        run_id=command.run_id,
        manifest_id=context["manifest_id"],
        scope=CalculationScope(
            context["calculation_kind"],
            context["scope_kind"],
            context["scope_id"],
        ),
        methodology_version=context["methodology_version"],
        input_schema_version=context["input_schema_version"],
        output_schema_version=context["output_schema_version"],
        captured_generation=int(context["captured_generation"]),
        effective_as_of=context["effective_as_of"],
        canonical_output_hash=command.canonical_output_hash,
        published_fencing_token=command.output_fencing_token,
        published_at=publication_row["published_at"],
    )


def read_run_lineage(
    executor: LifecycleExecutor,
    run_id: UUID,
) -> RunLineage | None:
    row = _one_or_none(
        executor.execute(
            select(
                _run.c.run_id,
                _run.c.calculation_kind,
                _run.c.scope_kind,
                _run.c.scope_id,
                _run.c.status,
                _run.c.requested_as_of,
                _run.c.effective_as_of,
                _run.c.cutoff_at,
                _run.c.timezone,
                _run.c.methodology_version,
                _run.c.input_schema_version,
                _run.c.output_schema_version,
                _run.c.captured_generation,
                _run.c.manifest_id,
                _run.c.published_output_hash,
                _run.c.created_at,
                _run.c.started_at,
                _run.c.completed_at,
                _publication.c.publication_id,
                _publication.c.published_fencing_token,
            )
            .select_from(
                _run.outerjoin(
                    _publication,
                    _publication.c.run_id == _run.c.run_id,
                )
            )
            .where(_run.c.run_id == run_id)
        )
    )
    if row is None:
        return None
    return RunLineage(
        run_id=row["run_id"],
        scope=CalculationScope(
            row["calculation_kind"],
            row["scope_kind"],
            row["scope_id"],
        ),
        status=_run_status(row["status"]),
        requested_as_of=row["requested_as_of"],
        effective_as_of=row["effective_as_of"],
        cutoff_at=row["cutoff_at"],
        timezone_name=row["timezone"],
        methodology_version=row["methodology_version"],
        input_schema_version=row["input_schema_version"],
        output_schema_version=row["output_schema_version"],
        captured_generation=int(row["captured_generation"]),
        manifest_id=row["manifest_id"],
        publication_id=row["publication_id"],
        published_output_hash=row["published_output_hash"],
        published_fencing_token=(
            int(row["published_fencing_token"])
            if row["published_fencing_token"] is not None
            else None
        ),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def read_current_publication_lineage(
    executor: LifecycleExecutor,
    scope: CalculationScope,
) -> PublicationLineage | None:
    row = _one_or_none(
        executor.execute(
            select(
                _publication.c.publication_id,
                _publication.c.run_id,
                _publication.c.manifest_id,
                _publication.c.calculation_kind,
                _publication.c.scope_kind,
                _publication.c.scope_id,
                _run.c.methodology_version,
                _run.c.input_schema_version,
                _publication.c.output_schema_version,
                _run.c.captured_generation,
                _run.c.effective_as_of,
                _publication.c.canonical_output_hash,
                _publication.c.published_fencing_token,
                _publication.c.published_at,
            )
            .select_from(
                _current_publication.join(
                    _publication,
                    _publication.c.publication_id
                    == _current_publication.c.publication_id,
                ).join(_run, _run.c.run_id == _publication.c.run_id)
            )
            .where(
                _current_publication.c.calculation_kind == scope.calculation_kind,
                _current_publication.c.scope_kind == scope.scope_kind,
                _current_publication.c.scope_id == scope.scope_id,
            )
        )
    )
    if row is None:
        return None
    return PublicationLineage(
        publication_id=row["publication_id"],
        run_id=row["run_id"],
        manifest_id=row["manifest_id"],
        scope=CalculationScope(
            row["calculation_kind"],
            row["scope_kind"],
            row["scope_id"],
        ),
        methodology_version=row["methodology_version"],
        input_schema_version=row["input_schema_version"],
        output_schema_version=row["output_schema_version"],
        captured_generation=int(row["captured_generation"]),
        effective_as_of=row["effective_as_of"],
        canonical_output_hash=row["canonical_output_hash"],
        published_fencing_token=int(row["published_fencing_token"]),
        published_at=row["published_at"],
    )


class LifecycleRepository:
    """Stateless PostgreSQL lifecycle repository.

    Every method takes a caller-owned ``Connection`` or ``Session``. The class
    has no transaction methods by design and never commits, rolls back, begins,
    or opens a nested transaction. Fact revision plus generation/intent must be
    one caller transaction. Manifest capture through seal must be one caller
    ``REPEATABLE READ`` transaction. Publication must keep publication insert,
    current-pointer CAS, and run transition in one transaction; row locks, CAS,
    and the registry's deferred constraint provide the serializable equivalent.

    Any ``LifecycleRepositoryError`` from a multi-statement method requires the
    caller to roll back the outer transaction. Catching it and committing is a
    caller contract violation.
    """

    create_scope_generation = staticmethod(create_scope_generation)
    read_scope_generation = staticmethod(read_scope_generation)
    advance_scope_generation_with_intent = staticmethod(
        advance_scope_generation_with_intent
    )
    materialize_recompute_intent = staticmethod(materialize_recompute_intent)
    lock_next_pending_recompute_intent = staticmethod(
        lock_next_pending_recompute_intent
    )
    supersede_recompute_intent = staticmethod(supersede_recompute_intent)
    fail_recompute_intent = staticmethod(fail_recompute_intent)
    register_worker = staticmethod(register_worker)
    heartbeat_worker = staticmethod(heartbeat_worker)
    has_fresh_worker = staticmethod(has_fresh_worker)
    create_capturing_run = staticmethod(create_capturing_run)
    read_active_run_by_dedupe = staticmethod(read_active_run_by_dedupe)
    seal_manifest_and_queue_run = staticmethod(seal_manifest_and_queue_run)
    claim_next_job = staticmethod(claim_next_job)
    lock_expired_job_for_failure = staticmethod(lock_expired_job_for_failure)
    heartbeat_job = staticmethod(heartbeat_job)
    retry_job = staticmethod(retry_job)
    succeed_job = staticmethod(succeed_job)
    fail_active_job = staticmethod(fail_active_job)
    fail_expired_job = staticmethod(fail_expired_job)
    fail_retry_wait_job = staticmethod(fail_retry_wait_job)
    fail_capturing_run = staticmethod(fail_capturing_run)
    supersede_run = staticmethod(supersede_run)
    publish_run = staticmethod(publish_run)
    read_run_lineage = staticmethod(read_run_lineage)
    read_current_publication_lineage = staticmethod(
        read_current_publication_lineage
    )
