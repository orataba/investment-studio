from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from collections import Counter
from typing import Any, Literal

import exchange_calendars
from exchange_calendars.errors import CalendarError
from studio_market.calendars import confirmed_market_closures, closed_market_sessions


CalculationFrequency = Literal["daily", "weekly", "monthly", "event_driven", "unknown"]


def listed_session_points(points, *, instrument_type, market_calendar):
    """Historical listed observations must belong to their listing's sessions.

    Fund NAVs are publisher observations, not exchange closes; crypto has its
    own 24/7 contract. Neither inherits the listed-security filter.
    """
    if instrument_type not in {"equity", "etf"} or not points:
        return points
    dates = [point["as_of_date"] for point in points]
    sessions = _market_calendar_sessions(str(market_calendar or ""), min(dates), max(dates))
    # Unknown schedules retain the existing unverified-frequency semantics;
    # absence of a calendar is not evidence that an observed date was closed.
    if sessions is None:
        return points
    allowed = set(sessions)
    return [point for point in points if point["as_of_date"] in allowed]

_MARKET_CALENDAR_ALIASES = {
    # Shenzhen and Shanghai share the mainland trading-session calendar.  The
    # exchange_calendars package currently exposes XSHG but not XSHE.
    "XSHE": "XSHG",
}


@lru_cache(maxsize=64)
def _market_calendar_sessions(
    calendar_name: str,
    start_date: date,
    end_date: date,
) -> tuple[date, ...] | None:
    if calendar_name == "24/7":
        return tuple(start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1))
    try:
        name = _MARKET_CALENDAR_ALIASES.get(calendar_name, calendar_name)
        calendar = exchange_calendars.get_calendar(name)
        if start_date < calendar.first_session.date() or end_date > calendar.last_session.date():
            # Defaults only cover a rolling twenty-year history. Clipping an
            # older price history to that window would silently certify gaps as
            # aligned; request the actual sample instead, within known bounds.
            calendar = exchange_calendars.get_calendar(name,
                start=min(start_date, calendar.first_session.date()).isoformat(),
                end=max(end_date, calendar.last_session.date()).isoformat())
        covered_start = max(start_date, calendar.first_session.date())
        covered_end = min(end_date, calendar.last_session.date())
        if covered_start > covered_end:
            return ()
        return tuple(
            session.date()
            for session in calendar.sessions_in_range(
                covered_start.isoformat(),
                covered_end.isoformat(),
            )
            if session.date() not in confirmed_market_closures(name)
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


def source_calendar_date(now: datetime, market_calendar: object) -> date:
    """Use the source market's local day when evaluating a publication schedule."""
    name = str(market_calendar or "").strip()
    if name == "24/7":
        return now.astimezone(UTC).date()
    if name:
        try:
            calendar = exchange_calendars.get_calendar(_MARKET_CALENDAR_ALIASES.get(name, name))
            return now.astimezone(calendar.tz).date()
        except (CalendarError, ValueError):
            pass
    return now.date()


def build_calculation_frequency_context(
    nav_points: list[dict[str, Any]],
    *,
    expected_frequency: object = None,
    market_calendar: str | None = None,
    instrument_type: object = None,
) -> dict[str, Any]:
    raw_points = sorted(
        [point for point in nav_points if isinstance(point.get("as_of_date"), date)],
        key=lambda point: point["as_of_date"],
    )
    declared_frequency = str(expected_frequency or "").strip().lower()
    event_driven = declared_frequency == "event_driven"
    source_counts = Counter(str(point.get("frequency") or "unknown").strip().lower() for point in raw_points)
    known_source_frequencies = set(source_counts) - {"unknown"}
    resolved_frequency = (declared_frequency if declared_frequency in {"daily", "weekly", "monthly", "event_driven"}
        else next(iter(known_source_frequencies)) if len(known_source_frequencies) == 1
        and next(iter(known_source_frequencies)) in {"daily", "weekly", "monthly"} else "daily")
    calculation_points = listed_session_points(raw_points, instrument_type=instrument_type,
                                              market_calendar=market_calendar)

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
    elif resolved_frequency != "daily":
        gap_count = 0
        gap_detection_basis = "event_driven" if event_driven else "observed_intervals"
    else:
        gap_count = None
        gap_detection_basis = "schedule_unverified"
    gap_status = ("unverified" if gap_count is None else "calendar_gaps" if gap_count else "aligned")
    gap_label = ("observation schedule unverified" if gap_count is None
                 else "calendar gaps" if gap_count else "aligned observations")

    profile = {
        "requested_frequency": declared_frequency or resolved_frequency,
        "expected_frequency": declared_frequency or resolved_frequency,
        "resolved_frequency": resolved_frequency,
        "inferred_frequency": resolved_frequency,
        "source_frequency_counts": dict(source_counts),
        "frequency_source": "source_contract" if declared_frequency else "observation_metadata",
        "return_interval_basis": "actual_observation_endpoints",
        "raw_observation_count": len(raw_points),
        "excluded_non_session_count": len(raw_points) - len(calculation_points),
        "observation_count": len(calculation_points),
        "start_date": calculation_points[0]["as_of_date"].isoformat() if calculation_points else None,
        "end_date": calculation_points[-1]["as_of_date"].isoformat() if calculation_points else None,
        "annualization_periods_per_year": _annualization_periods_per_year(calculation_points),
        "largest_gap_days": largest_gap_days,
        "gap_count": gap_count,
        "gap_status": gap_status,
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
        "status_label": f"{resolved_frequency.replace('_', ' ').title()} observations - {gap_label}",
    }
    return {
        "points": calculation_points,
        "profile": profile,
        "missing_observation_dates": missing_observation_dates,
    }


def assess_latest_observation_freshness(
    *,
    latest_observation_date: date | None,
    current_date: date,
    resolved_frequency: CalculationFrequency,
    expected_frequency: object = None,
    market_calendar: object = None,
    release_lag_days: object = None,
    source_mode: object = None,
    instrument_type: object = None,
    confirmed_no_trade_dates: tuple[date, ...] = (),
    now: datetime | None = None,
) -> dict[str, object]:
    """Assess source-date freshness without imposing a shared Watchlist as-of.

    Daily exchange-calendar sources are compared with the latest completed
    session whose trading-session release lag has fully elapsed. An aware now
    includes today's session only after its actual exchange close. Date-only
    evaluations represent the start of the specified source-local date.
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

    declared_frequency = str(expected_frequency or "").strip().lower()
    event_driven = declared_frequency == "event_driven"
    non_daily = declared_frequency in {"weekly", "monthly"} or resolved_frequency in {"weekly", "monthly"}
    if release_lag_days is None and source_mode == "email" and instrument_type == "private_fund":
        # User-confirmed email private-fund convention; explicit source settings win.
        release_lag_days = 1
    try:
        normalized_release_lag = max(int(release_lag_days or 0), 0)
    except (TypeError, ValueError):
        normalized_release_lag = 0
    normalized_market_calendar = str(market_calendar or "").strip() or None
    expected_latest_date: date | None = None

    if not event_driven and not non_daily and normalized_market_calendar:
        sessions = _market_calendar_sessions(
            normalized_market_calendar,
            current_date - timedelta(days=370),
            current_date,
        )
        completed_sessions = (list(closed_market_sessions(normalized_market_calendar,
            current_date - timedelta(days=370), current_date, now) or ()) if now is not None
            else [session_date for session_date in (sessions or ()) if session_date < current_date])
        available_sessions = completed_sessions[:-normalized_release_lag] if normalized_release_lag else completed_sessions
        if available_sessions:
            expected_latest_date = available_sessions[-1]

    if expected_latest_date is not None:
        stale = latest_observation_date < expected_latest_date
        confirmed = []
        if stale and normalized_market_calendar == "XHKG" and confirmed_no_trade_dates:
            missing = _market_calendar_sessions(normalized_market_calendar,
                latest_observation_date + timedelta(days=1), expected_latest_date)
            if missing and set(missing).issubset(confirmed_no_trade_dates):
                stale = False
                confirmed = [day.isoformat() for day in missing]
        return {
            "status": "stale" if stale else "fresh",
            "expected_latest_date": expected_latest_date.isoformat(),
            "release_lag_trading_days": normalized_release_lag,
            "lag_days": max((current_date - latest_observation_date).days, 0),
            "confirmed_no_trade_dates": confirmed,
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

    return {
        "status": "unavailable", "expected_latest_date": None,
        "lag_days": max((current_date - latest_observation_date).days, 0),
        "reason": ("Publication schedule for this non-daily source is unverified."
                   if non_daily else "Daily observation calendar is unverified; freshness cannot be established."),
    }
