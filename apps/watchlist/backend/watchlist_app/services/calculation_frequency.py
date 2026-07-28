from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Literal

try:
    import exchange_calendars
    from exchange_calendars.errors import CalendarError
except ImportError:  # pragma: no cover - dependency fallback for partial local envs.
    exchange_calendars = None
    CalendarError = ValueError


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


@lru_cache(maxsize=64)
def _market_calendar_sessions(
    calendar_name: str,
    start_date: date,
    end_date: date,
) -> tuple[date, ...] | None:
    if exchange_calendars is None:
        return None
    try:
        calendar = exchange_calendars.get_calendar(calendar_name)
        return tuple(
            session.date()
            for session in calendar.sessions_in_range(
                start_date.isoformat(),
                end_date.isoformat(),
            )
        )
    except (CalendarError, ValueError):
        return None


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
    *,
    expected_frequency: object = None,
    market_calendar: str | None = None,
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
    normalized_expected_frequency = normalize_frequency(expected_frequency)
    resolved_frequency = (
        normalized_expected_frequency
        or (
            max(declared_frequencies, key=lambda item: _FREQUENCY_RANK[item])
            if declared_frequencies
            else inferred_frequency
        )
    )
    calculation_points = resample_nav_points(raw_points, resolved_frequency)

    declared_counts = Counter(declared_frequencies)
    unknown_count = max(len(raw_points) - len(declared_frequencies), 0)
    source_counts = {
        frequency: int(declared_counts.get(frequency, 0))
        for frequency in CALCULATION_FREQUENCIES
    }
    source_counts["unknown"] = unknown_count

    gaps = [
        (calculation_points[index]["as_of_date"] - calculation_points[index - 1]["as_of_date"]).days
        for index in range(1, len(calculation_points))
    ]
    largest_gap_days = max(gaps) if gaps else None
    normalized_market_calendar = str(market_calendar or "").strip() or None
    calendar_sessions = (
        _market_calendar_sessions(
            normalized_market_calendar,
            calculation_points[0]["as_of_date"],
            calculation_points[-1]["as_of_date"],
        )
        if normalized_market_calendar
        and resolved_frequency == "daily"
        and calculation_points
        else None
    )
    missing_observation_dates: list[date] = []
    if calendar_sessions is not None:
        actual_dates = {point["as_of_date"] for point in calculation_points}
        missing_observation_dates = [
            session_date
            for session_date in calendar_sessions
            if session_date not in actual_dates
        ]
        gap_count = len(missing_observation_dates)
        gap_detection_basis = f"market_calendar:{normalized_market_calendar}"
    else:
        max_expected_gap = _EXPECTED_MAX_GAP_DAYS[resolved_frequency]
        gap_count = len([gap for gap in gaps if gap > max_expected_gap])
        gap_detection_basis = "calendar_day_threshold"
    source_label = (
        f"registry expected {CALCULATION_FREQUENCY_LABELS[resolved_frequency].lower()} data"
        if normalized_expected_frequency
        else "mixed declared data"
        if len([count for count in declared_counts.values() if count > 0]) > 1
        else f"declared {CALCULATION_FREQUENCY_LABELS[resolved_frequency].lower()} data"
        if declared_frequencies
        else f"inferred {CALCULATION_FREQUENCY_LABELS[inferred_frequency].lower()} data"
    )
    gap_label = "calendar gaps" if gap_count else "aligned observations"

    profile = {
        "requested_frequency": "auto",
        "expected_frequency": normalized_expected_frequency,
        "resolved_frequency": resolved_frequency,
        "inferred_frequency": inferred_frequency,
        "source_frequency_counts": source_counts,
        "frequency_source": (
            "registry_expected"
            if normalized_expected_frequency
            else "mixed_declared"
            if len([count for count in declared_counts.values() if count > 0]) > 1
            else "declared"
            if declared_frequencies
            else "inferred"
        ),
        "raw_observation_count": len(raw_points),
        "observation_count": len(calculation_points),
        "start_date": calculation_points[0]["as_of_date"].isoformat() if calculation_points else None,
        "end_date": calculation_points[-1]["as_of_date"].isoformat() if calculation_points else None,
        "annualization_periods_per_year": _annualization_periods_per_year(calculation_points),
        "largest_gap_days": largest_gap_days,
        "gap_count": gap_count,
        "gap_status": "calendar_gaps" if gap_count else "aligned",
        "gap_detection_basis": gap_detection_basis,
        "missing_observation_date_sample": [
            item.isoformat()
            for item in (
                missing_observation_dates
                if len(missing_observation_dates) <= 20
                else [
                    *missing_observation_dates[:10],
                    *missing_observation_dates[-10:],
                ]
            )
        ],
        "status_label": (
            f"{CALCULATION_FREQUENCY_LABELS[resolved_frequency]} risk basis - "
            f"{source_label}, {gap_label}"
        ),
    }
    return {
        "points": calculation_points,
        "profile": profile,
    }


def assess_latest_observation_freshness(
    *,
    latest_observation_date: date | None,
    current_date: date,
    resolved_frequency: CalculationFrequency,
    expected_frequency: object = None,
    market_calendar: object = None,
    release_lag_days: object = 0,
) -> dict[str, object]:
    """Assess source-date freshness without imposing a shared Watchlist as-of.

    Daily exchange-calendar sources are compared with the latest completed
    session whose calendar-day release lag has fully elapsed.  The current day
    is deliberately excluded because an intraday Watchlist refresh must not
    require an observation scheduled to become available later that day.
    """

    if latest_observation_date is None:
        return {
            "status": "unavailable",
            "expected_latest_date": None,
            "lag_days": None,
            "reason": "No canonical calculation-series observation is available.",
        }
    if latest_observation_date > current_date:
        return {
            "status": "stale",
            "expected_latest_date": None,
            "lag_days": 0,
            "reason": (
                f"Latest observation {latest_observation_date.isoformat()} is future-dated "
                f"relative to {current_date.isoformat()}."
            ),
        }

    normalized_expected = normalize_frequency(expected_frequency) or resolved_frequency
    try:
        normalized_release_lag = max(int(release_lag_days or 0), 0)
    except (TypeError, ValueError):
        normalized_release_lag = 0
    normalized_market_calendar = str(market_calendar or "").strip() or None
    expected_latest_date: date | None = None

    if normalized_expected == "daily" and normalized_market_calendar:
        sessions = _market_calendar_sessions(
            normalized_market_calendar,
            current_date - timedelta(days=370),
            current_date,
        )
        available_sessions = [
            session_date
            for session_date in (sessions or ())
            if session_date + timedelta(days=normalized_release_lag) < current_date
        ]
        if available_sessions:
            expected_latest_date = available_sessions[-1]

    if expected_latest_date is not None:
        stale = latest_observation_date < expected_latest_date
        return {
            "status": "stale" if stale else "fresh",
            "expected_latest_date": expected_latest_date.isoformat(),
            "lag_days": max((current_date - latest_observation_date).days, 0),
            "reason": (
                f"Latest observation {latest_observation_date.isoformat()} is older "
                f"than expected completed session {expected_latest_date.isoformat()}."
                if stale
                else None
            ),
        }

    maximum_age_days = (
        _EXPECTED_MAX_GAP_DAYS[normalized_expected] + normalized_release_lag
    )
    lag_days = max((current_date - latest_observation_date).days, 0)
    stale = lag_days > maximum_age_days
    return {
        "status": "stale" if stale else "fresh",
        "expected_latest_date": None,
        "lag_days": lag_days,
        "reason": (
            f"Latest observation is {lag_days} calendar days old; "
            f"the {normalized_expected} freshness allowance is {maximum_age_days} days."
            if stale
            else None
        ),
    }
