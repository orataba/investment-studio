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
    selected_snapshots = [
        snapshot
        for snapshot in candidates
        if effective_end_date is not None
        and cast(date, snapshot["as_of_date"]) <= effective_end_date
        and (
            requested_start_date is None
            or cast(date, snapshot["as_of_date"]) >= requested_start_date
        )
    ]
    effective_start_date = (
        cast(date, selected_snapshots[0]["as_of_date"])
        if selected_snapshots
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
    start_is_close_boundary: bool = False,
    start_is_funded_segment: bool = False,
) -> str:
    if not visible_snapshots or effective_end_date is None:
        return "unavailable"
    normalized_visible = sorted(
        (
            snapshot
            for snapshot in visible_snapshots
            if isinstance(snapshot.get("as_of_date"), date)
        ),
        key=lambda item: cast(date, item["as_of_date"]),
    )
    if not normalized_visible:
        return "unavailable"

    first_snapshot = normalized_visible[0]
    first_date = cast(date, first_snapshot["as_of_date"])
    first_nav = _safe_float(first_snapshot.get("nav"))
    next_snapshot = normalized_visible[1] if len(normalized_visible) > 1 else None
    next_snapshot_starts_funded_segment = bool(
        next_snapshot is not None
        and cast(date, next_snapshot["as_of_date"]) == first_date + timedelta(days=1)
        and abs(_safe_float(next_snapshot.get("beginning_nav")) or 0.0) <= 1e-12
        and (_safe_float(next_snapshot.get("external_cash_in")) or 0.0) > 1e-9
        and (next_daily_twr := _safe_float(next_snapshot.get("daily_twr")))
        is not None
        and isfinite(next_daily_twr)
        and snapshot_coverage_state(next_snapshot, "return") == "complete"
    )
    explicit_close_boundary_anchor = bool(
        start_is_close_boundary
        and first_nav is not None
        and (
            abs(first_nav) > 1e-12
            or next_snapshot_starts_funded_segment
        )
    )
    inferred_initial_valuation_anchor = bool(
        has_complete_valuation(first_snapshot)
        and first_nav is not None
        and abs(first_nav) > 1e-12
        and _safe_float(first_snapshot.get("daily_twr")) is None
        and bool(first_snapshot.get("return_chain_continuous"))
    )
    initial_valuation_anchor = bool(
        explicit_close_boundary_anchor or inferred_initial_valuation_anchor
    )
    anchor_date = (
        requested_start_date
        if explicit_close_boundary_anchor and requested_start_date is not None
        else (first_date if initial_valuation_anchor else None)
    )
    start_boundary_complete = bool(
        (
            anchor_date is not None
            and first_date == anchor_date
            and has_complete_valuation(first_snapshot)
        )
        if initial_valuation_anchor
        else (
            (
                first_date == (inception_date or first_date)
                or (
                    start_is_funded_segment
                    and requested_start_date is not None
                    and first_date == requested_start_date
                )
            )
            and snapshot_coverage_state(first_snapshot, "return") == "complete"
            and (daily_twr := _safe_float(first_snapshot.get("daily_twr")))
            is not None
            and isfinite(daily_twr)
        )
    )
    return_snapshots = (
        normalized_visible[1:]
        if initial_valuation_anchor and first_date == anchor_date
        else normalized_visible
    )
    period_start_date = (
        cast(date, anchor_date) + timedelta(days=1)
        if initial_valuation_anchor and anchor_date is not None
        else first_date
    )
    has_any_return = any(
        _safe_float(snapshot.get("daily_twr")) is not None
        for snapshot in return_snapshots
    )

    expected_observation_count = max(
        (effective_end_date - period_start_date).days + 1,
        0,
    )
    observations_are_contiguous = bool(
        (
            expected_observation_count == 0
            and not return_snapshots
        )
        or (
            expected_observation_count > 0
            and len(return_snapshots) == expected_observation_count
            and cast(date, return_snapshots[0]["as_of_date"]) == period_start_date
            and cast(date, return_snapshots[-1]["as_of_date"]) == effective_end_date
            and all(
                cast(date, snapshot["as_of_date"])
                == period_start_date + timedelta(days=index)
                for index, snapshot in enumerate(return_snapshots)
            )
        )
    )
    returns_are_continuous = all(
        snapshot_coverage_state(snapshot, "return") == "complete"
        and (daily_twr := _safe_float(snapshot.get("daily_twr"))) is not None
        and isfinite(daily_twr)
        and bool(snapshot.get("return_chain_continuous"))
        for snapshot in return_snapshots
    )
    end_boundary_complete = bool(
        (
            expected_observation_count == 0
            and first_date == effective_end_date
            and has_complete_valuation(first_snapshot)
        )
        or (
            return_snapshots
            and cast(date, return_snapshots[-1]["as_of_date"])
            == effective_end_date
            and has_complete_valuation(return_snapshots[-1])
        )
    )
    if (
        start_boundary_complete
        and observations_are_contiguous
        and returns_are_continuous
        and end_boundary_complete
    ):
        return "complete"
    has_material_capital = any(
        (nav := _safe_float(snapshot.get("nav"))) is not None
        and abs(nav) > 1e-12
        for snapshot in normalized_visible
    )
    return "partial" if has_any_return or has_material_capital else "unavailable"


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
            else (0.0 if is_initial_valuation_anchor else None)
        )
        rendered_snapshots.append(rendered_snapshot)

    return rendered_snapshots
