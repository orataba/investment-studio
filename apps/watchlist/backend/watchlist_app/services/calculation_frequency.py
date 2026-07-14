from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Literal


CalculationFrequency = Literal["daily", "weekly", "monthly"]

CALCULATION_FREQUENCIES: tuple[CalculationFrequency, ...] = ("daily", "weekly", "monthly")
CALCULATION_FREQUENCY_LABELS: dict[CalculationFrequency, str] = {
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
}
_FREQUENCY_RANK: dict[CalculationFrequency, int] = {"daily": 0, "weekly": 1, "monthly": 2}
_EXPECTED_MAX_GAP_DAYS: dict[CalculationFrequency, int] = {
    "daily": 4,
    "weekly": 10,
    "monthly": 45,
}


def normalize_frequency(value: object) -> CalculationFrequency | None:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"d", "day", "daily", "trading_day", "business_day"}:
        return "daily"
    if normalized in {"w", "week", "weekly"}:
        return "weekly"
    if normalized in {"m", "month", "monthly"}:
        return "monthly"
    return None


def infer_observation_frequency(dates: list[date]) -> CalculationFrequency:
    ordered_dates = sorted(set(item for item in dates if isinstance(item, date)))
    if len(ordered_dates) < 2:
        return "daily"

    gaps = [
        (ordered_dates[index] - ordered_dates[index - 1]).days
        for index in range(1, len(ordered_dates))
        if (ordered_dates[index] - ordered_dates[index - 1]).days > 0
    ]
    if not gaps:
        return "daily"

    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    daily_like_count = len([gap for gap in gaps if gap <= 3])
    weekly_like_count = len([gap for gap in gaps if 4 <= gap <= 10])
    monthly_like_count = len([gap for gap in gaps if 18 <= gap <= 45])

    if len(gaps) < 5:
        if daily_like_count:
            return "daily"
        if weekly_like_count == len(gaps):
            return "weekly"
        if monthly_like_count == len(gaps):
            return "monthly"
        return "daily"

    if median_gap <= 3:
        return "daily"
    if median_gap <= 10:
        return "weekly"
    if monthly_like_count >= len(gaps) * 0.6:
        return "monthly"
    return "daily"


def _period_key(observation_date: date, frequency: CalculationFrequency) -> str:
    if frequency == "daily":
        return observation_date.isoformat()
    if frequency == "weekly":
        week_start = observation_date - timedelta(days=observation_date.weekday())
        return week_start.isoformat()
    return observation_date.strftime("%Y-%m")


def resample_nav_points(
    nav_points: list[dict[str, Any]],
    frequency: CalculationFrequency,
) -> list[dict[str, Any]]:
    ordered_points = sorted(
        [point for point in nav_points if isinstance(point.get("as_of_date"), date)],
        key=lambda point: point["as_of_date"],
    )
    if frequency == "daily":
        return ordered_points

    buckets: dict[str, dict[str, Any]] = {}
    for point in ordered_points:
        buckets[_period_key(point["as_of_date"], frequency)] = point
    return sorted(buckets.values(), key=lambda point: point["as_of_date"])


def _annualization_periods_per_year(points: list[dict[str, Any]]) -> float | None:
    if len(points) < 2:
        return None
    elapsed_days = (points[-1]["as_of_date"] - points[0]["as_of_date"]).days
    if elapsed_days <= 0:
        return None
    return (len(points) - 1) / elapsed_days * 365.25


def build_calculation_frequency_context(
    nav_points: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_points = sorted(
        [point for point in nav_points if isinstance(point.get("as_of_date"), date)],
        key=lambda point: point["as_of_date"],
    )
    declared_frequencies = [
        frequency
        for frequency in (normalize_frequency(point.get("frequency")) for point in raw_points)
        if frequency is not None
    ]
    inferred_frequency = infer_observation_frequency([point["as_of_date"] for point in raw_points])
    resolved_frequency = (
        max(declared_frequencies, key=lambda item: _FREQUENCY_RANK[item])
        if declared_frequencies
        else inferred_frequency
    )
    calculation_points = resample_nav_points(raw_points, resolved_frequency)

    declared_counts = Counter(declared_frequencies)
    unknown_count = max(len(raw_points) - len(declared_frequencies), 0)
    source_counts = {
        frequency: (
            int(declared_counts.get(frequency, 0))
            if declared_frequencies
            else len(raw_points)
            if frequency == inferred_frequency
            else 0
        )
        for frequency in CALCULATION_FREQUENCIES
    }
    source_counts["unknown"] = unknown_count

    gaps = [
        (calculation_points[index]["as_of_date"] - calculation_points[index - 1]["as_of_date"]).days
        for index in range(1, len(calculation_points))
    ]
    largest_gap_days = max(gaps) if gaps else None
    max_expected_gap = _EXPECTED_MAX_GAP_DAYS[resolved_frequency]
    gap_count = len([gap for gap in gaps if gap > max_expected_gap])
    source_label = (
        "mixed declared data"
        if len([count for count in declared_counts.values() if count > 0]) > 1
        else f"declared {CALCULATION_FREQUENCY_LABELS[resolved_frequency].lower()} data"
        if declared_frequencies
        else f"inferred {CALCULATION_FREQUENCY_LABELS[inferred_frequency].lower()} data"
    )
    gap_label = "calendar gaps" if gap_count else "aligned observations"

    profile = {
        "requested_frequency": "auto",
        "resolved_frequency": resolved_frequency,
        "inferred_frequency": inferred_frequency,
        "source_frequency_counts": source_counts,
        "raw_observation_count": len(raw_points),
        "observation_count": len(calculation_points),
        "start_date": calculation_points[0]["as_of_date"].isoformat() if calculation_points else None,
        "end_date": calculation_points[-1]["as_of_date"].isoformat() if calculation_points else None,
        "annualization_periods_per_year": _annualization_periods_per_year(calculation_points),
        "largest_gap_days": largest_gap_days,
        "gap_count": gap_count,
        "gap_status": "calendar_gaps" if gap_count else "aligned",
        "status_label": (
            f"{CALCULATION_FREQUENCY_LABELS[resolved_frequency]} risk basis - "
            f"{source_label}, {gap_label}"
        ),
    }
    return {
        "points": calculation_points,
        "profile": profile,
    }
