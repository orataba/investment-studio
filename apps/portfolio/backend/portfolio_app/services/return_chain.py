from __future__ import annotations

from datetime import date, timedelta
from math import isfinite
from typing import cast


_COVERAGE_STATES = {"complete", "partial", "unavailable"}


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def snapshot_coverage_state(snapshot: dict[str, object], dimension: str) -> str:
    value = str(snapshot.get(f"{dimension}_coverage_state") or "").strip()
    return value if value in _COVERAGE_STATES else "unavailable"


def incomplete_coverage_state(*, has_content: bool) -> str:
    return "partial" if has_content else "unavailable"


def merge_coverage_states(states: list[str]) -> str:
    normalized = [state if state in _COVERAGE_STATES else "unavailable" for state in states]
    if normalized and all(state == "complete" for state in normalized):
        return "complete"
    if any(state in {"complete", "partial"} for state in normalized):
        return "partial"
    return "unavailable"


def has_complete_valuation(snapshot: dict[str, object]) -> bool:
    return (
        snapshot_coverage_state(snapshot, "valuation") == "complete"
        and _safe_float(snapshot.get("nav")) is not None
    )


def is_reliable_valuation_snapshot(snapshot: dict[str, object]) -> bool:
    return has_complete_valuation(snapshot) and not bool(snapshot.get("stale_price_flag"))


def resolve_reliable_snapshot_window(
    snapshots: list[dict[str, object]],
    *,
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
    default_end_date: date | None = None,
    include_start_boundary: bool = False,
) -> dict[str, object]:
    normalized_snapshots: list[dict[str, object]] = []
    for snapshot in snapshots:
        normalized = dict(snapshot)
        snapshot_date = _parse_iso_date(normalized.get("as_of_date"))
        if snapshot_date is None:
            continue
        normalized["as_of_date"] = snapshot_date
        normalized_snapshots.append(normalized)
    normalized_snapshots.sort(key=lambda item: cast(date, item["as_of_date"]))

    resolved_requested_end = requested_end_date or default_end_date
    candidates = [
        snapshot
        for snapshot in normalized_snapshots
        if resolved_requested_end is None
        or cast(date, snapshot["as_of_date"]) <= resolved_requested_end
    ]
    latest_reliable = next(
        (
            snapshot
            for snapshot in reversed(candidates)
            if is_reliable_valuation_snapshot(snapshot)
        ),
        None,
    )
    effective_end_date = (
        cast(date, latest_reliable["as_of_date"])
        if latest_reliable is not None
        else None
    )
    lower_bound = (
        requested_start_date - timedelta(days=1)
        if include_start_boundary and requested_start_date is not None
        else requested_start_date
    )
    selected_snapshots = [
        snapshot
        for snapshot in candidates
        if effective_end_date is not None
        and cast(date, snapshot["as_of_date"]) <= effective_end_date
        and (
            lower_bound is None
            or cast(date, snapshot["as_of_date"]) >= lower_bound
        )
    ]
    visible_snapshots = [
        snapshot
        for snapshot in selected_snapshots
        if requested_start_date is None
        or cast(date, snapshot["as_of_date"]) >= requested_start_date
    ]
    effective_start_date = (
        cast(date, visible_snapshots[0]["as_of_date"])
        if visible_snapshots
        else None
    )

    trailing_candidates = [
        snapshot
        for snapshot in candidates
        if effective_end_date is None
        or cast(date, snapshot["as_of_date"]) > effective_end_date
    ]
    trailing_endpoint_is_stale = bool(
        trailing_candidates
        and has_complete_valuation(trailing_candidates[-1])
        and bool(trailing_candidates[-1].get("stale_price_flag"))
    )
    as_of_clamp_reason = None
    if (
        requested_end_date is not None
        and (
            effective_end_date is None
            or effective_end_date < requested_end_date
        )
    ):
        as_of_clamp_reason = (
            "requested_end_stale_price"
            if trailing_endpoint_is_stale
            else "requested_end_after_latest_reliable_endpoint"
        )
    elif requested_end_date is None and resolved_requested_end is not None and (
        effective_end_date is None or effective_end_date < resolved_requested_end
    ):
        as_of_clamp_reason = (
            "latest_snapshot_stale_price"
            if trailing_endpoint_is_stale
            else "latest_snapshot_unreliable"
        )
    elif candidates and effective_end_date is not None and (
        cast(date, candidates[-1]["as_of_date"]) > effective_end_date
    ):
        as_of_clamp_reason = (
            "latest_snapshot_stale_price"
            if trailing_endpoint_is_stale
            else "latest_snapshot_unreliable"
        )

    return {
        "snapshots": selected_snapshots,
        "requested_start_date": requested_start_date,
        "requested_end_date": resolved_requested_end,
        "effective_start_date": effective_start_date,
        "effective_end_date": effective_end_date,
        "as_of_clamp_reason": as_of_clamp_reason,
    }


def period_return_coverage_state(
    snapshots: list[dict[str, object]],
    visible_snapshots: list[dict[str, object]],
    *,
    requested_start_date: date | None,
    effective_end_date: date | None,
    inception_date: date | None,
) -> str:
    if not visible_snapshots or effective_end_date is None:
        return "unavailable"
    first_date = cast(date, visible_snapshots[0]["as_of_date"])
    initial_valuation_anchor = bool(
        requested_start_date is None
        and inception_date == first_date
        and has_complete_valuation(visible_snapshots[0])
        and _safe_float(visible_snapshots[0].get("daily_twr")) is None
        and bool(visible_snapshots[0].get("return_chain_continuous"))
    )
    return_snapshots = visible_snapshots[1:] if initial_valuation_anchor else visible_snapshots
    period_start_date = (
        first_date + timedelta(days=1)
        if initial_valuation_anchor
        else (requested_start_date or first_date)
    )
    has_any_return = any(
        _safe_float(snapshot.get("daily_twr")) is not None
        for snapshot in return_snapshots
    )

    start_boundary_complete = True
    if requested_start_date is not None:
        boundary_date = requested_start_date - timedelta(days=1)
        boundary_snapshot = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.get("as_of_date") == boundary_date
            ),
            None,
        )
        starts_at_inception = (
            inception_date == requested_start_date
            and snapshot_coverage_state(visible_snapshots[0], "return") == "complete"
            and _safe_float(visible_snapshots[0].get("daily_twr")) is not None
        )
        start_boundary_complete = bool(
            first_date == requested_start_date
            and (
                (boundary_snapshot is not None and has_complete_valuation(boundary_snapshot))
                or starts_at_inception
            )
            and not (
                inception_date is not None
                and inception_date > requested_start_date
            )
        )

    expected_observation_count = (effective_end_date - period_start_date).days + 1
    observations_are_contiguous = bool(
        expected_observation_count > 0
        and len(return_snapshots) == expected_observation_count
        and cast(date, return_snapshots[0]["as_of_date"]) == period_start_date
        and cast(date, return_snapshots[-1]["as_of_date"]) == effective_end_date
        and all(
            cast(date, snapshot["as_of_date"]) == period_start_date + timedelta(days=index)
            for index, snapshot in enumerate(return_snapshots)
        )
    )
    returns_are_continuous = all(
        snapshot_coverage_state(snapshot, "return") == "complete"
        and (daily_twr := _safe_float(snapshot.get("daily_twr"))) is not None
        and isfinite(daily_twr)
        for snapshot in return_snapshots
    )
    end_boundary_complete = bool(
        return_snapshots
        and cast(date, return_snapshots[-1]["as_of_date"]) == effective_end_date
        and has_complete_valuation(return_snapshots[-1])
    )
    if (
        start_boundary_complete
        and observations_are_contiguous
        and returns_are_continuous
        and end_boundary_complete
    ):
        return "complete"
    return "partial" if has_any_return or visible_snapshots else "unavailable"


def aggregate_snapshot_coverage(
    snapshots: list[dict[str, object]],
    dimension: str,
) -> str:
    if not snapshots:
        return "unavailable"
    return merge_coverage_states(
        [snapshot_coverage_state(snapshot, dimension) for snapshot in snapshots]
    )


def summarize_daily_snapshots(
    snapshots: list[dict[str, object]],
    *,
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
    effective_start_date: date | None = None,
    effective_end_date: date | None = None,
    as_of_clamp_reason: str | None = None,
) -> dict[str, object]:
    latest_complete = next(
        (
            snapshot
            for snapshot in reversed(snapshots)
            if is_reliable_valuation_snapshot(snapshot)
        ),
        None,
    )
    return {
        "snapshot_count": len(snapshots),
        "complete_count": sum(
            1 for snapshot in snapshots if snapshot.get("coverage_state") == "complete"
        ),
        "partial_count": sum(
            1 for snapshot in snapshots if snapshot.get("coverage_state") == "partial"
        ),
        "unavailable_count": sum(
            1 for snapshot in snapshots if snapshot.get("coverage_state") == "unavailable"
        ),
        "latest_complete_as_of_date": latest_complete.get("as_of_date") if latest_complete else None,
        "requested_start_date": requested_start_date,
        "requested_end_date": requested_end_date,
        "effective_start_date": effective_start_date,
        "effective_end_date": effective_end_date,
        "as_of_clamp_reason": as_of_clamp_reason,
    }


def compound_daily_twr(snapshots: list[dict[str, object]]) -> float | None:
    growth_index = 1.0
    has_return = False
    for snapshot in snapshots:
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        if daily_twr is None or not isfinite(daily_twr):
            continue
        growth_index *= 1.0 + daily_twr
        has_return = True
    return (growth_index - 1.0) if has_return else None


def rebased_twr_series(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    growth_index = 1.0
    has_return_history = False
    return_chain_continuous = True
    rendered_snapshots: list[dict[str, object]] = []

    for snapshot in snapshots:
        rendered_snapshot = dict(snapshot)
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        point_return_complete = (
            snapshot_coverage_state(snapshot, "return") == "complete"
            and daily_twr is not None
            and isfinite(daily_twr)
        )
        is_initial_valuation_anchor = bool(
            not rendered_snapshots
            and not has_return_history
            and not point_return_complete
            and has_complete_valuation(snapshot)
            and bool(snapshot.get("return_chain_continuous"))
        )
        if not point_return_complete and not is_initial_valuation_anchor:
            return_chain_continuous = False
        elif point_return_complete and return_chain_continuous:
            has_return_history = True
            growth_index *= 1.0 + daily_twr

        rendered_snapshot["return_chain_continuous"] = return_chain_continuous
        rendered_snapshot["cumulative_twr"] = (
            (growth_index - 1.0)
            if has_return_history and return_chain_continuous
            else None
        )
        rendered_snapshots.append(rendered_snapshot)

    return rendered_snapshots
