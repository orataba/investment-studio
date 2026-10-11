"""Historical one-session losses for an unlevered long asset in its quote currency.

Use canonical simple economic returns, never display-price differences. Missing
sessions and economic breaks make the requested sample unavailable, never deleted.
"""
from datetime import UTC, date, datetime, timedelta
from math import isfinite

from investment_studio_instrument_core.risk_statistics import empirical_tail
from studio_market.calendars import closed_market_sessions
from watchlist_app.services.calculation_frequency import (
    _market_calendar_sessions, assess_latest_observation_freshness, source_calendar_date,
)


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def instrument_tail_risk(chart, *, instrument_type, confidence=.95, lookback_days=1095, source_settings=None, today=None, now=None):
    if confidence not in {.95, .99} or lookback_days not in {365, 1095, 1825}:
        raise ValueError("Unsupported confidence or lookback window")
    series = chart.get("return_series") or {}
    metadata, frequency = series.get("metadata") or {}, series.get("frequency") or {}
    source_settings = source_settings or {}
    basis = str(frequency.get("gap_detection_basis") or "")
    calendar = basis.removeprefix("market_calendar:") if basis.startswith("market_calendar:") else None
    now = now or (datetime.now(UTC) if today is None else None)
    today = today or source_calendar_date(now, calendar)
    result = {
        "instrument_id": chart.get("instrument_id"), "status": "unavailable", "reason": None,
        "confidence": confidence, "lookback_days": lookback_days, "horizon": "one_session",
        "currency": series.get("currency"), "return_kind": metadata.get("return_kind"),
        "return_measure": "simple", "quote_basis": metadata.get("quote_basis"),
        "calendar_basis": basis or None, "window_start_date": None, "window_end_date": None,
        "first_observation_start_date": None, "last_observation_end_date": None,
        "excluded_interval_count": 0, "expected_observation_count": None,
        "missing_intervals": [], "missing_return_dates": [], "unavailable_intervals": [],
        "uncovered_leading_count": None, "missing_internal_count": None, "uncovered_trailing_count": None,
        "history_coverage_status": "unavailable", "freshness": chart.get("freshness"),
        "latest_expected_date": None, "latest_observation_date": None, "source_stale": False,
        **empirical_tail([], confidence),
    }

    def unavailable(reason):
        return {**result, "status": "unavailable", "reason": reason, "var": None, "expected_shortfall": None}

    kind = metadata.get("return_kind")
    if (kind not in {"total_return", "price_return"}
        or (instrument_type in {"equity", "etf", "public_fund", "private_fund"} and kind != "total_return")
        or metadata.get("return_measure") != "simple"
        or metadata.get("return_series_status") not in {"ready", "complete", "partial"}):
        return unavailable("unconfirmed_economic_returns")
    if frequency.get("resolved_frequency") != "daily" or (source_settings.get("expected_frequency") or frequency.get("expected_frequency")) in {"weekly", "monthly", "event_driven"}:
        return unavailable("non_daily_source")
    if not calendar:
        return unavailable("unverified_daily_calendar")
    recent_sessions = _market_calendar_sessions(calendar, today - timedelta(days=370), today)
    completed = (closed_market_sessions(calendar, today - timedelta(days=370), today, now)
                 if now is not None else tuple(day for day in (recent_sessions or ()) if day < today))
    if not completed:
        return unavailable("unverified_daily_calendar")
    intervals = series.get("intervals") or []
    ends = [_day(point.get("date")) for point in intervals if isinstance(point, dict)]
    if any(day is None or day > today for day in ends) or len(ends) != len(intervals):
        return unavailable("invalid_return_interval")
    latest = max(ends, default=None)
    if latest and latest > completed[-1]:
        return unavailable("incomplete_market_session")
    def observation_freshness(day):
        return assess_latest_observation_freshness(
            latest_observation_date=day, current_date=today, now=now, resolved_frequency="daily",
            expected_frequency="daily", market_calendar=calendar,
            release_lag_days=source_settings.get("release_lag_days"),
            source_mode=source_settings.get("source_mode"), instrument_type=instrument_type,
        )

    expected_end = _day(observation_freshness(latest or completed[-1])["expected_latest_date"])
    if expected_end is None:
        return unavailable("unverified_daily_calendar")
    # A published close dated today may already be available. Otherwise anchor
    # the requested window to the last completed session, including stale gaps.
    end = max(expected_end, latest) if latest else expected_end
    start = end - timedelta(days=lookback_days)
    result.update(window_start_date=start.isoformat(), window_end_date=end.isoformat(),
                  latest_expected_date=expected_end.isoformat())
    sessions = _market_calendar_sessions(calendar, start, end)
    if sessions is None or not sessions:
        return unavailable("unverified_daily_calendar")
    expected = set(zip(sessions, sessions[1:]))
    periods, seen_ends = {}, set()
    for point in intervals:
        finish, begin = _day(point.get("date")), _day(point.get("start_date"))
        if finish <= start or finish > end:
            continue
        if begin is None or begin >= finish or finish in seen_ends:
            return unavailable("invalid_return_interval")
        seen_ends.add(finish)
        if begin < start:
            continue
        value = point.get("value")
        if point.get("status") == "unavailable":
            result["excluded_interval_count"] += 1
            result["unavailable_intervals"].append({"start_date": begin.isoformat(), "end_date": finish.isoformat(),
                                                   "reason": point.get("reason") or "unavailable_return_interval"})
            continue
        if (point.get("status") != "complete" or isinstance(value, bool)
            or not isinstance(value, (float, int)) or not isfinite(value) or value < -1):
            return unavailable("invalid_return_interval")
        if (begin, finish) not in expected:
            result["excluded_interval_count"] += 1
            result["unavailable_intervals"].append({"start_date": begin.isoformat(), "end_date": finish.isoformat(),
                                                   "reason": "non_daily_return_interval"})
            continue
        periods[begin, finish] = value
    ordered = sorted(periods, key=lambda pair: pair[1])
    missing = expected - periods.keys()
    first, last = (ordered[0][0], ordered[-1][1]) if ordered else (None, None)
    leading = sum(finish <= first for _, finish in missing) if first else len(missing)
    trailing = sum(begin >= last for begin, _ in missing) if last else 0
    result.update(
        first_observation_start_date=first.isoformat() if first else None,
        last_observation_end_date=last.isoformat() if last else None,
        latest_observation_date=last.isoformat() if last else None,
        source_stale=observation_freshness(last)["status"] != "fresh",
        expected_observation_count=len(expected), uncovered_leading_count=leading,
        missing_internal_count=len(missing) - leading - trailing, uncovered_trailing_count=trailing,
        history_coverage_status="partial" if missing else "complete",
        missing_intervals=[{"start_date": begin.isoformat(), "end_date": finish.isoformat()}
                           for begin, finish in sorted(missing) if first is None or finish > first],
        missing_return_dates=[finish.isoformat() for begin, finish in sorted(missing) if first is None or finish > first],
        observation_count=len(periods),
    )
    if len(missing) - leading or result["excluded_interval_count"]:
        return unavailable("missing_daily_return_intervals")
    result.update(empirical_tail([-value for value in periods.values()], confidence))
    if result["var"] is None:
        return unavailable("less_than_one_tail_observation" if periods else "no_verified_daily_returns")
    return {**result, "status": "partial" if missing or result["excluded_interval_count"] else "ready"}
