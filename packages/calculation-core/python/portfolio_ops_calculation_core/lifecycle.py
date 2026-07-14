from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from portfolio_ops_calculation_core.contracts import JsonValue
from portfolio_ops_calculation_core.state import CalculationRunStatus


_SHA256_HEX_LENGTH = 64


class LifecycleErrorCode(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    SCOPE_ALREADY_EXISTS = "scope_already_exists"
    GENERATION_CONFLICT = "generation_conflict"
    ACTIVE_DEDUPE_CONFLICT = "active_dedupe_conflict"
    LIFECYCLE_CONFLICT = "lifecycle_conflict"
    STALE_LEASE = "stale_lease"
    PUBLICATION_CAS_CONFLICT = "publication_cas_conflict"
    STALE_WORKER_INSTANCE = "stale_worker_instance"


class ScopeGenerationLock(StrEnum):
    """PostgreSQL row-lock strength for a scope-generation read."""

    NONE = "none"
    SHARE = "share"
    UPDATE = "update"


class LifecycleRepositoryError(RuntimeError):
    """Fail-closed repository error.

    The repository never commits or rolls back. A caller that receives this
    exception after a multi-statement operation must roll back its externally
    owned transaction; it must never catch the error and commit partial work.
    """

    def __init__(
        self,
        code: LifecycleErrorCode,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


class InvalidLifecycleArgument(LifecycleRepositoryError):
    def __init__(self, message: str, *, field_name: str) -> None:
        super().__init__(
            LifecycleErrorCode.INVALID_ARGUMENT,
            message,
            details={"field": field_name},
        )


class GenerationConflict(LifecycleRepositoryError):
    def __init__(self, scope: "CalculationScope", expected_generation: int) -> None:
        super().__init__(
            LifecycleErrorCode.GENERATION_CONFLICT,
            "scope generation did not match the expected value",
            details={
                "calculation_kind": scope.calculation_kind,
                "scope_kind": scope.scope_kind,
                "scope_id": scope.scope_id,
                "expected_generation": expected_generation,
            },
        )


class ActiveDedupeConflict(LifecycleRepositoryError):
    """An active run owns the requested dedupe identity.

    Under PostgreSQL ``REPEATABLE READ`` an ``ON CONFLICT`` check can observe a
    concurrently committed unique row that is not visible to the transaction's
    older snapshot.  In that narrow case ``active_run`` is ``None`` and the
    caller must roll back and retry the whole unit of work before looking it up.
    """

    def __init__(
        self,
        dedupe_key: str,
        active_run: "ActiveRunHandle | None",
    ) -> None:
        super().__init__(
            LifecycleErrorCode.ACTIVE_DEDUPE_CONFLICT,
            "an active run already owns the calculation dedupe key",
            details={
                "dedupe_key": dedupe_key,
                "active_run_id": (
                    str(active_run.run_id) if active_run is not None else None
                ),
                "retry_transaction": active_run is None,
            },
        )
        self.active_run = active_run


class StaleLease(LifecycleRepositoryError):
    def __init__(self, fence: "JobAttemptFence", operation: str) -> None:
        super().__init__(
            LifecycleErrorCode.STALE_LEASE,
            f"job lease is stale during {operation}",
            details={
                "job_id": str(fence.job_id),
                "run_id": str(fence.run_id),
                "attempt": fence.attempt,
                "fencing_token": fence.fencing_token,
                "operation": operation,
            },
        )


class StaleWorkerInstance(LifecycleRepositoryError):
    def __init__(self, worker_id: str, instance_id: UUID, operation: str) -> None:
        super().__init__(
            LifecycleErrorCode.STALE_WORKER_INSTANCE,
            f"worker instance is stale during {operation}",
            details={
                "worker_id": worker_id,
                "instance_id": str(instance_id),
                "operation": operation,
            },
        )


def _require_trimmed(value: str, *, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise InvalidLifecycleArgument(
            f"{field_name} must be a non-empty trimmed string of at most {maximum} characters",
            field_name=field_name,
        )


def _require_sha256(value: str, *, field_name: str) -> None:
    if (
        len(value) != _SHA256_HEX_LENGTH
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InvalidLifecycleArgument(
            f"{field_name} must be a lowercase SHA-256 hex digest",
            field_name=field_name,
        )


def _canonical_digest(domain: str, values: tuple[str, ...]) -> str:
    digest = sha256()
    for value in (domain, *values):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CalculationScope:
    calculation_kind: str
    scope_kind: str
    scope_id: str

    def __post_init__(self) -> None:
        _require_trimmed(
            self.calculation_kind,
            field_name="calculation_kind",
            maximum=64,
        )
        _require_trimmed(self.scope_kind, field_name="scope_kind", maximum=64)
        _require_trimmed(self.scope_id, field_name="scope_id", maximum=255)


@dataclass(frozen=True, slots=True)
class ScopeGeneration:
    scope: CalculationScope
    generation: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RecomputeIntentHandle:
    intent_id: UUID
    scope: CalculationScope
    requested_generation: int
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class PendingRecomputeIntent:
    """One pending intent locked by its caller-owned dispatcher transaction."""

    intent_id: UUID
    scope: CalculationScope
    requested_generation: int
    current_generation: int
    dedupe_key: str
    reason_code: str
    reason_context: Mapping[str, JsonValue]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.requested_generation < 0 or self.current_generation < 0:
            raise InvalidLifecycleArgument(
                "intent generations must be non-negative",
                field_name="requested_generation",
            )
        _require_sha256(self.dedupe_key, field_name="dedupe_key")
        _require_trimmed(self.reason_code, field_name="reason_code", maximum=64)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidLifecycleArgument(
                "intent created_at must be timezone-aware",
                field_name="created_at",
            )
        object.__setattr__(
            self,
            "reason_context",
            MappingProxyType(dict(self.reason_context)),
        )

    @property
    def handle(self) -> RecomputeIntentHandle:
        return RecomputeIntentHandle(
            intent_id=self.intent_id,
            scope=self.scope,
            requested_generation=self.requested_generation,
            dedupe_key=self.dedupe_key,
        )


@dataclass(frozen=True, slots=True)
class WorkerRegistration:
    worker_id: str
    instance_id: UUID
    worker_version: str
    supported_calculation_kinds: tuple[str, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_trimmed(self.worker_id, field_name="worker_id", maximum=255)
        _require_trimmed(
            self.worker_version,
            field_name="worker_version",
            maximum=128,
        )
        if (
            not isinstance(self.supported_calculation_kinds, tuple)
            or not self.supported_calculation_kinds
            or len(set(self.supported_calculation_kinds))
            != len(self.supported_calculation_kinds)
        ):
            raise InvalidLifecycleArgument(
                "supported_calculation_kinds must be a non-empty unique tuple",
                field_name="supported_calculation_kinds",
            )
        for calculation_kind in self.supported_calculation_kinds:
            _require_trimmed(
                calculation_kind,
                field_name="calculation_kind",
                maximum=64,
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    registration: WorkerRegistration
    started_at: datetime
    heartbeat_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("started_at", self.started_at),
            ("heartbeat_at", self.heartbeat_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise InvalidLifecycleArgument(
                    f"{field_name} must be timezone-aware",
                    field_name=field_name,
                )
        if self.heartbeat_at < self.started_at:
            raise InvalidLifecycleArgument(
                "heartbeat_at must not precede started_at",
                field_name="heartbeat_at",
            )


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Identity and methodology requested for one calculation run.

    The immutable knowledge cutoff is assigned by PostgreSQL when the run is
    inserted; callers cannot supply it. This prevents repeated commands for the
    same scope/as-of/generation from evading active dedupe with a new timestamp.
    """

    scope: CalculationScope
    requested_as_of: date
    effective_as_of: date
    timezone_name: str
    methodology_version: str
    input_schema_version: str
    output_schema_version: str
    captured_generation: int
    requested_by: str

    def __post_init__(self) -> None:
        if self.effective_as_of > self.requested_as_of:
            raise InvalidLifecycleArgument(
                "effective_as_of must not be after requested_as_of",
                field_name="effective_as_of",
            )
        _require_trimmed(self.timezone_name, field_name="timezone_name", maximum=64)
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise InvalidLifecycleArgument(
                "timezone_name must be a valid IANA timezone",
                field_name="timezone_name",
            ) from error
        _require_trimmed(
            self.methodology_version,
            field_name="methodology_version",
            maximum=128,
        )
        _require_trimmed(
            self.input_schema_version,
            field_name="input_schema_version",
            maximum=64,
        )
        _require_trimmed(
            self.output_schema_version,
            field_name="output_schema_version",
            maximum=64,
        )
        _require_trimmed(self.requested_by, field_name="requested_by", maximum=255)
        if self.captured_generation < 0:
            raise InvalidLifecycleArgument(
                "captured_generation must be non-negative",
                field_name="captured_generation",
            )

    @property
    def dedupe_key(self) -> str:
        return _canonical_digest(
            "calculation-run-dedupe.v1",
            (
                self.scope.calculation_kind,
                self.scope.scope_kind,
                self.scope.scope_id,
                self.requested_as_of.isoformat(),
                self.effective_as_of.isoformat(),
                self.timezone_name,
                self.methodology_version,
                self.input_schema_version,
                self.output_schema_version,
                str(self.captured_generation),
            ),
        )


@dataclass(frozen=True, slots=True)
class CapturingRun:
    run_id: UUID
    manifest_id: UUID
    request: RunRequest
    dedupe_key: str
    cutoff_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveRunHandle:
    """Stable identity and lifecycle fields for an active dedupe owner."""

    run_id: UUID
    manifest_id: UUID | None
    scope: CalculationScope
    status: CalculationRunStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    timezone_name: str
    methodology_version: str
    input_schema_version: str
    output_schema_version: str
    captured_generation: int
    dedupe_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ManifestSeal:
    canonical_manifest_hash: str
    dependency_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_sha256(
            self.canonical_manifest_hash,
            field_name="canonical_manifest_hash",
        )
        for dependency_kind, dependency_count in self.dependency_counts.items():
            _require_trimmed(
                dependency_kind,
                field_name="dependency_kind",
                maximum=255,
            )
            if (
                isinstance(dependency_count, bool)
                or not isinstance(dependency_count, int)
                or dependency_count < 0
            ):
                raise InvalidLifecycleArgument(
                    "dependency counts must be non-negative integers",
                    field_name="dependency_counts",
                )


@dataclass(frozen=True, slots=True)
class QueuedJob:
    job_id: UUID
    run_id: UUID
    manifest_id: UUID
    dedupe_key: str
    max_attempts: int


@dataclass(frozen=True, slots=True)
class JobAttemptFence:
    job_id: UUID
    run_id: UUID
    attempt: int
    fencing_token: int
    captured_generation: int

    def __post_init__(self) -> None:
        if self.attempt <= 0:
            raise InvalidLifecycleArgument(
                "attempt must be positive",
                field_name="attempt",
            )
        if self.fencing_token <= 0:
            raise InvalidLifecycleArgument(
                "fencing_token must be positive",
                field_name="fencing_token",
            )
        if self.captured_generation < 0:
            raise InvalidLifecycleArgument(
                "captured_generation must be non-negative",
                field_name="captured_generation",
            )


@dataclass(frozen=True, slots=True)
class ActiveJobLease(JobAttemptFence):
    lease_owner: str
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        JobAttemptFence.__post_init__(self)
        _require_trimmed(self.lease_owner, field_name="lease_owner", maximum=255)
        if (
            self.lease_expires_at.tzinfo is None
            or self.lease_expires_at.utcoffset() is None
        ):
            raise InvalidLifecycleArgument(
                "lease_expires_at must be timezone-aware",
                field_name="lease_expires_at",
            )


@dataclass(frozen=True, slots=True)
class LifecycleReason:
    code: str
    context: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_trimmed(self.code, field_name="reason_code", maximum=64)


@dataclass(frozen=True, slots=True)
class FailureReason:
    code: str
    diagnostic: str | None = None
    context: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_trimmed(self.code, field_name="failure_code", maximum=64)
        if self.diagnostic is not None:
            _require_trimmed(
                self.diagnostic,
                field_name="failure_diagnostic",
                maximum=2000,
            )


@dataclass(frozen=True, slots=True)
class RunCompletion:
    run_id: UUID
    job_id: UUID
    status: CalculationRunStatus
    attempt: int
    fencing_token: int
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class RunTransition:
    run_id: UUID
    status: CalculationRunStatus
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class PublicationCommand:
    run_id: UUID
    canonical_output_hash: str
    output_fencing_token: int
    expected_current_publication_id: UUID | None

    def __post_init__(self) -> None:
        _require_sha256(
            self.canonical_output_hash,
            field_name="canonical_output_hash",
        )
        if self.output_fencing_token <= 0:
            raise InvalidLifecycleArgument(
                "output_fencing_token must be positive",
                field_name="output_fencing_token",
            )


@dataclass(frozen=True, slots=True)
class RunLineage:
    run_id: UUID
    scope: CalculationScope
    status: CalculationRunStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    timezone_name: str
    methodology_version: str
    input_schema_version: str
    output_schema_version: str
    captured_generation: int
    manifest_id: UUID | None
    publication_id: UUID | None
    published_output_hash: str | None
    published_fencing_token: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublicationLineage:
    publication_id: UUID
    run_id: UUID
    manifest_id: UUID
    scope: CalculationScope
    methodology_version: str
    input_schema_version: str
    output_schema_version: str
    captured_generation: int
    effective_as_of: date
    canonical_output_hash: str
    published_fencing_token: int
    published_at: datetime


def build_recompute_intent_dedupe_key(
    scope: CalculationScope,
    requested_generation: int,
) -> str:
    if requested_generation < 0:
        raise InvalidLifecycleArgument(
            "requested_generation must be non-negative",
            field_name="requested_generation",
        )
    return _canonical_digest(
        "calculation-intent-dedupe.v1",
        (
            scope.calculation_kind,
            scope.scope_kind,
            scope.scope_id,
            str(requested_generation),
        ),
    )


def require_positive_duration(value: timedelta, *, field_name: str) -> None:
    if value <= timedelta(0):
        raise InvalidLifecycleArgument(
            f"{field_name} must be positive",
            field_name=field_name,
        )
