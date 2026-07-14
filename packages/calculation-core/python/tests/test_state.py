from __future__ import annotations

import pytest

from portfolio_ops_calculation_core.state import (
    JOB_STATUS_TRANSITIONS,
    MANIFEST_STATUS_TRANSITIONS,
    RECOMPUTE_INTENT_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    CalculationJobStatus,
    CalculationManifestStatus,
    CalculationRunStatus,
    InvalidStatusTransition,
    RecomputeIntentStatus,
    require_job_status_transition,
    require_manifest_status_transition,
    require_recompute_intent_status_transition,
    require_run_status_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current, targets in RUN_STATUS_TRANSITIONS.items()
        for target in targets
    ],
)
def test_all_declared_run_status_transitions_are_accepted(
    current: CalculationRunStatus,
    target: CalculationRunStatus,
) -> None:
    require_run_status_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in CalculationRunStatus
        for target in CalculationRunStatus
        if target not in RUN_STATUS_TRANSITIONS[current]
    ],
)
def test_all_undeclared_run_status_transitions_are_rejected(
    current: CalculationRunStatus,
    target: CalculationRunStatus,
) -> None:
    with pytest.raises(InvalidStatusTransition):
        require_run_status_transition(current, target)


def test_sealed_manifest_is_terminal() -> None:
    require_manifest_status_transition(
        CalculationManifestStatus.BUILDING,
        CalculationManifestStatus.SEALED,
    )
    assert not MANIFEST_STATUS_TRANSITIONS[CalculationManifestStatus.SEALED]
    with pytest.raises(InvalidStatusTransition):
        require_manifest_status_transition(
            CalculationManifestStatus.SEALED,
            CalculationManifestStatus.BUILDING,
        )


def test_retry_wait_can_only_be_released_or_terminated() -> None:
    assert JOB_STATUS_TRANSITIONS[CalculationJobStatus.RETRY_WAIT] == frozenset(
        {
            CalculationJobStatus.LEASED,
            CalculationJobStatus.FAILED,
            CalculationJobStatus.SUPERSEDED,
        }
    )
    require_job_status_transition(
        CalculationJobStatus.RETRY_WAIT,
        CalculationJobStatus.LEASED,
    )
    with pytest.raises(InvalidStatusTransition):
        require_job_status_transition(
            CalculationJobStatus.RETRY_WAIT,
            CalculationJobStatus.QUEUED,
        )


@pytest.mark.parametrize("status", list(CalculationJobStatus))
def test_job_self_transitions_are_rejected(status: CalculationJobStatus) -> None:
    with pytest.raises(InvalidStatusTransition):
        require_job_status_transition(status, status)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in CalculationJobStatus
        for target in CalculationJobStatus
    ],
)
def test_job_status_transition_contract_is_exhaustive(
    current: CalculationJobStatus,
    target: CalculationJobStatus,
) -> None:
    if target in JOB_STATUS_TRANSITIONS[current]:
        require_job_status_transition(current, target)
    else:
        with pytest.raises(InvalidStatusTransition):
            require_job_status_transition(current, target)


def test_capturing_run_can_fail_before_a_manifest_is_sealed() -> None:
    require_run_status_transition(
        CalculationRunStatus.CAPTURING,
        CalculationRunStatus.FAILED,
    )


def test_recompute_intent_has_one_way_terminal_transitions() -> None:
    for target in (
        RecomputeIntentStatus.MATERIALIZED,
        RecomputeIntentStatus.SUPERSEDED,
        RecomputeIntentStatus.FAILED,
    ):
        require_recompute_intent_status_transition(
            RecomputeIntentStatus.PENDING,
            target,
        )
        assert not RECOMPUTE_INTENT_STATUS_TRANSITIONS[target]
        with pytest.raises(InvalidStatusTransition):
            require_recompute_intent_status_transition(
                target,
                RecomputeIntentStatus.PENDING,
            )


@pytest.mark.parametrize(
    ("enum_type", "invalid_value"),
    [
        (CalculationRunStatus, "complete"),
        (CalculationManifestStatus, "mutable"),
        (CalculationJobStatus, "running"),
        (RecomputeIntentStatus, "consumed"),
    ],
)
def test_status_enums_reject_values_outside_the_contract(
    enum_type: type[
        CalculationRunStatus
        | CalculationManifestStatus
        | CalculationJobStatus
        | RecomputeIntentStatus
    ],
    invalid_value: str,
) -> None:
    with pytest.raises(ValueError):
        enum_type(invalid_value)
