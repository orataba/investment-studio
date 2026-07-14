"""Database orchestration used by the durable Portfolio Daily worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import Table, and_, func, or_, select
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    SCOPE_KIND,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_run_output,
)
from portfolio_ops_calculation_core import (
    ActiveJobLease,
    CalculationJob,
    CalculationJobStatus,
    CalculationRun,
    CalculationRunStatus,
    CalculationScope,
    CalculationScopeGeneration,
    FailureReason,
    JobAttemptFence,
    LifecycleRepository,
    PublicationCommand,
    PublicationLineage,
)


_run = cast(Table, CalculationRun.__table__)
_job = cast(Table, CalculationJob.__table__)
_generation = cast(Table, CalculationScopeGeneration.__table__)


class PublicationRecoveryStatus(StrEnum):
    IDLE = "idle"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class PublicationRecoveryOutcome:
    status: PublicationRecoveryStatus
    run_id: UUID | None = None
    publication: PublicationLineage | None = None
    replacement_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _ReadyPublication:
    run_id: UUID
    scope: CalculationScope
    effective_as_of: date
    output_fencing_token: int
    canonical_output_hash: str


def _next_ready_publication(session: Session) -> _ReadyPublication | None:
    row = session.execute(
        select(
            _run.c.run_id,
            _run.c.scope_id,
            _run.c.effective_as_of,
            _job.c.fencing_token,
            portfolio_daily_run_output.c.canonical_output_hash,
        )
        .select_from(
            _run.join(_job, _job.c.run_id == _run.c.run_id)
            .join(
                _generation,
                and_(
                    _generation.c.calculation_kind == _run.c.calculation_kind,
                    _generation.c.scope_kind == _run.c.scope_kind,
                    _generation.c.scope_id == _run.c.scope_id,
                ),
            )
            .join(
                portfolio_daily_run_output,
                and_(
                    portfolio_daily_run_output.c.run_id == _run.c.run_id,
                    portfolio_daily_run_output.c.output_fencing_token
                    == _job.c.fencing_token,
                    portfolio_daily_run_output.c.portfolio_id == _run.c.scope_id,
                ),
            )
        )
        .where(
            _run.c.calculation_kind == CALCULATION_KIND,
            _run.c.scope_kind == SCOPE_KIND,
            _run.c.status == CalculationRunStatus.SUCCEEDED,
            _run.c.published_output_hash.is_(None),
            _job.c.status == CalculationJobStatus.SUCCEEDED,
            _generation.c.generation == _run.c.captured_generation,
            portfolio_daily_run_output.c.canonical_output_hash.is_not(None),
        )
        .order_by(_run.c.completed_at, _run.c.run_id)
        .limit(1)
    ).mappings().one_or_none()
    if row is None:
        return None
    return _ReadyPublication(
        run_id=row["run_id"],
        scope=CalculationScope(CALCULATION_KIND, SCOPE_KIND, row["scope_id"]),
        effective_as_of=row["effective_as_of"],
        output_fencing_token=int(row["fencing_token"]),
        canonical_output_hash=str(row["canonical_output_hash"]),
    )


def recover_next_portfolio_daily_publication(
    session: Session,
) -> PublicationRecoveryOutcome:
    """Publish one succeeded attempt in the caller's atomic transaction."""

    candidate = _next_ready_publication(session)
    if candidate is None:
        return PublicationRecoveryOutcome(PublicationRecoveryStatus.IDLE)
    current = LifecycleRepository.read_current_publication_lineage(
        session,
        candidate.scope,
    )
    if current is not None and current.effective_as_of > candidate.effective_as_of:
        LifecycleRepository.supersede_run(
            session,
            run_id=candidate.run_id,
            replacement_run_id=current.run_id,
            expected_status=CalculationRunStatus.SUCCEEDED,
            reason=FailureReason(
                "newer_publication_exists",
                context={"replacement_run_id": str(current.run_id)},
            ),
        )
        return PublicationRecoveryOutcome(
            PublicationRecoveryStatus.SUPERSEDED,
            run_id=candidate.run_id,
            replacement_run_id=current.run_id,
        )
    publication = LifecycleRepository.publish_run(
        session,
        PublicationCommand(
            run_id=candidate.run_id,
            canonical_output_hash=candidate.canonical_output_hash,
            output_fencing_token=candidate.output_fencing_token,
            expected_current_publication_id=(
                current.publication_id if current is not None else None
            ),
        ),
    )
    return PublicationRecoveryOutcome(
        PublicationRecoveryStatus.PUBLISHED,
        run_id=candidate.run_id,
        publication=publication,
    )


def lease_has_retry_remaining(session: Session, lease: ActiveJobLease) -> bool:
    """Lock and inspect the exact attempt before choosing retry vs terminal fail."""

    row = session.execute(
        select(_job.c.attempt, _job.c.max_attempts)
        .where(
            _job.c.job_id == lease.job_id,
            _job.c.run_id == lease.run_id,
            _job.c.status == CalculationJobStatus.LEASED,
            _job.c.attempt == lease.attempt,
            _job.c.fencing_token == lease.fencing_token,
            _job.c.lease_owner == lease.lease_owner,
            _job.c.lease_expires_at > func.clock_timestamp(),
        )
        .with_for_update(of=_job)
    ).one_or_none()
    if row is None:
        return False
    return int(row.attempt) < int(row.max_attempts)


@dataclass(frozen=True, slots=True)
class SupersededGenerationOutcome:
    run_id: UUID
    replacement_run_id: UUID
    previous_status: CalculationRunStatus


def supersede_next_stale_generation_run(
    session: Session,
    *,
    worker_id: str,
) -> SupersededGenerationOutcome | None:
    """Terminalize one old-generation active run after a replacement is durable."""

    replacement = _run.alias("replacement_run")
    old_job = _job.alias("old_job")
    replacement_exists = (
        select(replacement.c.run_id)
        .where(
            replacement.c.calculation_kind == _run.c.calculation_kind,
            replacement.c.scope_kind == _run.c.scope_kind,
            replacement.c.scope_id == _run.c.scope_id,
            replacement.c.captured_generation == _generation.c.generation,
            replacement.c.status.in_(
                (
                    CalculationRunStatus.QUEUED,
                    CalculationRunStatus.RUNNING,
                    CalculationRunStatus.SUCCEEDED,
                    CalculationRunStatus.PUBLISHED,
                )
            ),
        )
        .order_by(replacement.c.created_at.desc(), replacement.c.run_id.desc())
        .limit(1)
        .scalar_subquery()
    )
    now = func.clock_timestamp()
    running_reapable = and_(
        _run.c.status == CalculationRunStatus.RUNNING,
        or_(
            old_job.c.status == CalculationJobStatus.RETRY_WAIT,
            and_(
                old_job.c.status == CalculationJobStatus.LEASED,
                or_(
                    old_job.c.lease_expires_at <= now,
                    old_job.c.lease_owner == worker_id,
                ),
            ),
        ),
    )
    row = session.execute(
        select(
            _run.c.run_id,
            _run.c.status,
            replacement_exists.label("replacement_run_id"),
            old_job.c.job_id,
            old_job.c.attempt,
            old_job.c.fencing_token,
            _run.c.captured_generation,
        )
        .select_from(
            _run.join(
                _generation,
                and_(
                    _generation.c.calculation_kind == _run.c.calculation_kind,
                    _generation.c.scope_kind == _run.c.scope_kind,
                    _generation.c.scope_id == _run.c.scope_id,
                ),
            ).join(old_job, old_job.c.run_id == _run.c.run_id)
        )
        .where(
            _run.c.calculation_kind == CALCULATION_KIND,
            _run.c.scope_kind == SCOPE_KIND,
            _run.c.captured_generation < _generation.c.generation,
            replacement_exists.is_not(None),
            or_(
                and_(
                    _run.c.status == CalculationRunStatus.QUEUED,
                    old_job.c.status == CalculationJobStatus.QUEUED,
                ),
                running_reapable,
                and_(
                    _run.c.status == CalculationRunStatus.SUCCEEDED,
                    old_job.c.status == CalculationJobStatus.SUCCEEDED,
                ),
            ),
        )
        .order_by(_run.c.created_at, _run.c.run_id)
        .limit(1)
        .with_for_update(of=[_run, old_job], skip_locked=True)
    ).mappings().one_or_none()
    if row is None:
        return None
    status = CalculationRunStatus(str(row["status"]))
    replacement_run_id = row["replacement_run_id"]
    fence = None
    if status is CalculationRunStatus.RUNNING:
        fence = JobAttemptFence(
            job_id=row["job_id"],
            run_id=row["run_id"],
            attempt=int(row["attempt"]),
            fencing_token=int(row["fencing_token"]),
            captured_generation=int(row["captured_generation"]),
        )
    LifecycleRepository.supersede_run(
        session,
        run_id=row["run_id"],
        replacement_run_id=replacement_run_id,
        expected_status=status,
        reason=FailureReason(
            "generation_superseded",
            context={"replacement_run_id": str(replacement_run_id)},
        ),
        fence=fence,
        lease_owner=worker_id if fence is not None else None,
    )
    return SupersededGenerationOutcome(
        run_id=row["run_id"],
        replacement_run_id=replacement_run_id,
        previous_status=status,
    )


__all__ = [
    "PublicationRecoveryOutcome",
    "PublicationRecoveryStatus",
    "SupersededGenerationOutcome",
    "lease_has_retry_remaining",
    "recover_next_portfolio_daily_publication",
    "supersede_next_stale_generation_run",
]
