from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Literal

import exchange_calendars
from exchange_calendars.errors import CalendarError


CalculationFrequency = Literal["daily"]

_EXPECTED_MAX_GAP_DAYS: dict[CalculationFrequency, int] = {
    "daily": 4,
}


@lru_cache(maxsize=64)
def _market_calendar_sessions(
    calendar_name: str,
    start_date: date,
    end_date: date,
) -> tuple[date, ...] | None:
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
    event_driven = str(expected_frequency or "").strip().lower() == "event_driven"
    resolved_frequency: CalculationFrequency = "daily"
    calculation_points = raw_points

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
    elif event_driven:
        gap_count = 0
        gap_detection_basis = "event_driven"
    else:
        gap_count = len([gap for gap in gaps if gap > _EXPECTED_MAX_GAP_DAYS["daily"]])
        gap_detection_basis = "calendar_day_threshold"
    gap_label = "calendar gaps" if gap_count else "aligned observations"

    profile = {
        "requested_frequency": "daily",
        "expected_frequency": "daily",
        "resolved_frequency": resolved_frequency,
        "inferred_frequency": "daily",
        "source_frequency_counts": {"daily": len(raw_points), "unknown": 0},
        "frequency_source": "daily_policy",
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
        "status_label": f"Daily risk basis - {gap_label}",
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

    del resolved_frequency
    event_driven = str(expected_frequency or "").strip().lower() == "event_driven"
    try:
        normalized_release_lag = max(int(release_lag_days or 0), 0)
    except (TypeError, ValueError):
        normalized_release_lag = 0
    normalized_market_calendar = str(market_calendar or "").strip() or None
    expected_latest_date: date | None = None

    if not event_driven and normalized_market_calendar:
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

    if event_driven:
        return {
            "status": "fresh",
            "expected_latest_date": None,
            "lag_days": max((current_date - latest_observation_date).days, 0),
            "reason": None,
        }

    maximum_age_days = _EXPECTED_MAX_GAP_DAYS["daily"] + normalized_release_lag
    lag_days = max((current_date - latest_observation_date).days, 0)
    stale = lag_days > maximum_age_days
    return {
        "status": "stale" if stale else "fresh",
        "expected_latest_date": None,
        "lag_days": lag_days,
        "reason": (
            f"Latest observation is {lag_days} calendar days old; "
            f"the daily freshness allowance is {maximum_age_days} days."
            if stale
            else None
        ),
    }
