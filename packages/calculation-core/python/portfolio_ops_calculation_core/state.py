from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import TypeVar


class CalculationRunStatus(StrEnum):
    CAPTURING = "capturing"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class CalculationManifestStatus(StrEnum):
    BUILDING = "building"
    SEALED = "sealed"


class CalculationJobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class RecomputeIntentStatus(StrEnum):
    PENDING = "pending"
    MATERIALIZED = "materialized"
    SUPERSEDED = "superseded"
    FAILED = "failed"


_StatusT = TypeVar("_StatusT", bound=StrEnum)


class InvalidStatusTransition(ValueError):
    def __init__(self, current: StrEnum, target: StrEnum) -> None:
        super().__init__(
            f"invalid {type(current).__name__} transition: "
            f"{current.value!r} -> {target.value!r}"
        )
        self.current = current
        self.target = target


RUN_STATUS_TRANSITIONS: Mapping[
    CalculationRunStatus,
    frozenset[CalculationRunStatus],
] = MappingProxyType(
    {
        CalculationRunStatus.CAPTURING: frozenset(
            {
                CalculationRunStatus.QUEUED,
                CalculationRunStatus.FAILED,
                CalculationRunStatus.SUPERSEDED,
            }
        ),
        CalculationRunStatus.QUEUED: frozenset(
            {
                CalculationRunStatus.RUNNING,
                CalculationRunStatus.FAILED,
                CalculationRunStatus.SUPERSEDED,
            }
        ),
        CalculationRunStatus.RUNNING: frozenset(
            {
                CalculationRunStatus.SUCCEEDED,
                CalculationRunStatus.FAILED,
                CalculationRunStatus.SUPERSEDED,
            }
        ),
        CalculationRunStatus.SUCCEEDED: frozenset(
            {
                CalculationRunStatus.PUBLISHED,
                CalculationRunStatus.SUPERSEDED,
            }
        ),
        CalculationRunStatus.PUBLISHED: frozenset(),
        CalculationRunStatus.SUPERSEDED: frozenset(),
        CalculationRunStatus.FAILED: frozenset(),
    }
)


MANIFEST_STATUS_TRANSITIONS: Mapping[
    CalculationManifestStatus,
    frozenset[CalculationManifestStatus],
] = MappingProxyType(
    {
        CalculationManifestStatus.BUILDING: frozenset(
            {CalculationManifestStatus.SEALED}
        ),
        CalculationManifestStatus.SEALED: frozenset(),
    }
)


JOB_STATUS_TRANSITIONS: Mapping[
    CalculationJobStatus,
    frozenset[CalculationJobStatus],
] = MappingProxyType(
    {
        CalculationJobStatus.QUEUED: frozenset(
            {
                CalculationJobStatus.LEASED,
                CalculationJobStatus.SUPERSEDED,
            }
        ),
        CalculationJobStatus.LEASED: frozenset(
            {
                CalculationJobStatus.RETRY_WAIT,
                CalculationJobStatus.SUCCEEDED,
                CalculationJobStatus.FAILED,
                CalculationJobStatus.SUPERSEDED,
            }
        ),
        CalculationJobStatus.RETRY_WAIT: frozenset(
            {
                CalculationJobStatus.LEASED,
                CalculationJobStatus.FAILED,
                CalculationJobStatus.SUPERSEDED,
            }
        ),
        CalculationJobStatus.SUCCEEDED: frozenset(),
        CalculationJobStatus.FAILED: frozenset(),
        CalculationJobStatus.SUPERSEDED: frozenset(),
    }
)


RECOMPUTE_INTENT_STATUS_TRANSITIONS: Mapping[
    RecomputeIntentStatus,
    frozenset[RecomputeIntentStatus],
] = MappingProxyType(
    {
        RecomputeIntentStatus.PENDING: frozenset(
            {
                RecomputeIntentStatus.MATERIALIZED,
                RecomputeIntentStatus.SUPERSEDED,
                RecomputeIntentStatus.FAILED,
            }
        ),
        RecomputeIntentStatus.MATERIALIZED: frozenset(),
        RecomputeIntentStatus.SUPERSEDED: frozenset(),
        RecomputeIntentStatus.FAILED: frozenset(),
    }
)


RUN_TERMINAL_STATUSES = frozenset(
    {
        CalculationRunStatus.PUBLISHED,
        CalculationRunStatus.SUPERSEDED,
        CalculationRunStatus.FAILED,
    }
)
JOB_TERMINAL_STATUSES = frozenset(
    {
        CalculationJobStatus.SUCCEEDED,
        CalculationJobStatus.FAILED,
        CalculationJobStatus.SUPERSEDED,
    }
)
RECOMPUTE_INTENT_TERMINAL_STATUSES = frozenset(
    {
        RecomputeIntentStatus.MATERIALIZED,
        RecomputeIntentStatus.SUPERSEDED,
        RecomputeIntentStatus.FAILED,
    }
)


def _require_transition(
    current: _StatusT,
    target: _StatusT,
    transitions: Mapping[_StatusT, frozenset[_StatusT]],
) -> None:
    if target not in transitions[current]:
        raise InvalidStatusTransition(current, target)


def require_run_status_transition(
    current: CalculationRunStatus,
    target: CalculationRunStatus,
) -> None:
    _require_transition(current, target, RUN_STATUS_TRANSITIONS)


def require_manifest_status_transition(
    current: CalculationManifestStatus,
    target: CalculationManifestStatus,
) -> None:
    _require_transition(current, target, MANIFEST_STATUS_TRANSITIONS)


def require_job_status_transition(
    current: CalculationJobStatus,
    target: CalculationJobStatus,
) -> None:
    _require_transition(current, target, JOB_STATUS_TRANSITIONS)


def require_recompute_intent_status_transition(
    current: RecomputeIntentStatus,
    target: RecomputeIntentStatus,
) -> None:
    _require_transition(current, target, RECOMPUTE_INTENT_STATUS_TRANSITIONS)
