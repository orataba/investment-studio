"""Shared economic-return arithmetic; apps own calendars, metrics and portfolios.

Inputs are positive canonical levels. Outputs are decimal period returns, never
market prices. Missing inputs and action boundaries remain explicit intervals;
dropping a bad level before differencing would invent a return across the gap.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from math import isfinite, log1p


def _date(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def simple_to_log_return(value: float) -> float | None:
    """Log returns are undefined at/below a complete capital loss."""
    if not isfinite(value):
        raise ValueError("A return must be finite.")
    return log1p(value) if value > -1 else None


def return_interval_unavailable_reason(
    start_date: object,
    end_date: object,
    *,
    source_settings: Mapping[str, object] | None = None,
    quote_basis: object = None,
) -> str | None:
    """Reject intervals crossing incomplete or separately rebased TR segments.

    An unavailable interval describes (start_date, end_date]. A segment includes
    both endpoint levels: returns are valid only when both lie in one segment.
    Absence of segment metadata leaves pre-existing canonical source contracts
    intact; an explicitly empty segment list means no confirmed coverage.
    """
    start, end = _date(start_date), _date(end_date)
    if end <= start:
        raise ValueError("A return interval must have increasing dates.")
    if str(quote_basis) not in {"adjusted_close", "total_return_nav"}:
        return None
    settings = source_settings or {}
    for interval in settings.get("total_return_unavailable_intervals", []) or []:
        if not isinstance(interval, Mapping):
            return "invalid_total_return_coverage"
        try:
            left, right = _date(interval["start_date"]), _date(interval["end_date"])
        except (KeyError, TypeError, ValueError):
            return "invalid_total_return_coverage"
        if right < left:
            return "invalid_total_return_coverage"
        if start < right and end > left:
            return str(interval.get("reason") or "total_return_interval_unavailable")
    if "total_return_segments" in settings:
        segments = settings["total_return_segments"]
        if not isinstance(segments, list):
            return "invalid_total_return_coverage"
        for segment in segments:
            if not isinstance(segment, Mapping):
                return "invalid_total_return_coverage"
            try:
                left, right = _date(segment["start_date"]), _date(segment["end_date"])
            except (KeyError, TypeError, ValueError):
                return "invalid_total_return_coverage"
            if left > right:
                return "invalid_total_return_coverage"
            if left <= start < end <= right:
                return None
        return "total_return_segment_break"
    return None


def build_return_intervals(
    points: Iterable[Mapping[str, object]],
    *,
    date_key: str = "date",
    value_key: str = "value",
    break_dates: Iterable[object] = (),
    source_settings: Mapping[str, object] | None = None,
    quote_basis: object = None,
) -> list[dict[str, object]]:
    """Derive simple and log returns from the same observed interval endpoints.

    Callers pass missing expected sessions as break_dates. This function does
    not assume daily frequency, fill missing observations or annualize returns.
    It preserves unusable adjacent intervals instead of bridging their levels.
    """
    observations = sorted(
        ((_date(point[date_key]), point.get(value_key)) for point in points),
        key=lambda point: point[0],
    )
    dates = [item[0] for item in observations]
    if len(dates) != len(set(dates)):
        raise ValueError("Return observations must have unique dates.")
    breaks = sorted({_date(day) for day in break_dates})
    result: list[dict[str, object]] = []
    for (start, previous), (end, current) in zip(observations, observations[1:]):
        reason = return_interval_unavailable_reason(
            start, end, source_settings=source_settings, quote_basis=quote_basis,
        )
        if reason is None and any(start < boundary <= end for boundary in breaks):
            reason = "missing_observation_or_segment_break"
        simple = None
        logarithmic = None
        if reason is None:
            try:
                if isinstance(previous, bool) or isinstance(current, bool):
                    raise ValueError("Invalid level")
                before, after = float(previous), float(current)
                if not (isfinite(before) and isfinite(after) and before > 0 and after > 0):
                    raise ValueError("Invalid level")
                simple = after / before - 1
                if not isfinite(simple):
                    raise ValueError("Non-finite return")
                logarithmic = simple_to_log_return(simple)
            except (TypeError, ValueError, OverflowError):
                simple = None
                reason = "invalid_level"
        result.append({
            "start_date": start, "date": end, "value": simple,
            "log_return": logarithmic,
            "status": "complete" if reason is None else "unavailable", "reason": reason,
        })
    return result
