"""Exchange-confirmed exceptional full-day closures missing from calendar data.

These are dated observations, not a continuing severe-weather trading rule.
HKEX changed that rule in 2024; future weather must not imply market closure.
"""
from datetime import date, datetime


HKEX_CONFIRMED_CLOSURES = {
    date(2023, 9, 1): "https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en",
    date(2023, 9, 8): "https://www.hkex.com.hk/News/Market-Communications/2023/2309083news?sc_lang=en",
}


class MarketCalendarUnavailable(ValueError):
    """A safe, public calendar diagnosis; contains no provider response data."""


def confirmed_market_closures(calendar_name: str) -> frozenset[date]:
    return frozenset(HKEX_CONFIRMED_CLOSURES) if calendar_name.upper() in {"XHKG", "HKEX", "HK"} else frozenset()


def closed_market_sessions(calendar_name: str, start: date, end: date, now: datetime) -> tuple[date, ...] | None:
    """Known completed sessions as of an aware instant; None means unverified.

    Exchange calendars supply actual regular/early closes and time zones. Crypto
    daily bars use complete UTC days. No local fixed-hour or weekday inference.
    """
    from datetime import UTC, timedelta
    import exchange_calendars
    from exchange_calendars.errors import CalendarError

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A timezone-aware market cutoff is required")
    if start > end:
        return ()
    name = str(calendar_name or "").upper()
    name = {"XSHE": "XSHG", "HKEX": "XHKG", "HK": "XHKG"}.get(name, name)
    if name == "24/7":
        through = min(end, now.astimezone(UTC).date() - timedelta(days=1))
        return tuple(start + timedelta(days=offset) for offset in range((through - start).days + 1))
    try:
        calendar = exchange_calendars.get_calendar(name)
        if start < calendar.first_session.date() or end > calendar.last_session.date():
            expanded_start = min(start, calendar.first_session.date()) - timedelta(days=31)
            expanded_end = max(end, calendar.last_session.date()) + timedelta(days=31)
            # Padding is only for closed-day endpoints, not a request for new
            # calendar coverage. Bounded schedules (notably XSHG) must not fail
            # valid historical reads because this padding crosses their bounds.
            if calendar.bound_min() is not None:
                expanded_start = max(expanded_start, calendar.bound_min().date())
            if calendar.bound_max() is not None:
                expanded_end = min(expanded_end, calendar.bound_max().date())
            calendar = exchange_calendars.get_calendar(name,
                # Calendar bounds are first/last *sessions*. Include a full
                # month around requested dates so weekend/holiday endpoints do
                # not masquerade as unsupported history.
                start=expanded_start.isoformat(), end=expanded_end.isoformat())
        if start < calendar.first_session.date() or end > calendar.last_session.date():
            return None
        closures = confirmed_market_closures(name)
        def close(session):
            instant = calendar.session_close(session).to_pydatetime()
            # XHKG's library close omits the random closing auction. All ETF
            # shares entered CAS from 2016-07-25; the whole-market cutoff must
            # wait for its latest possible end, including half-day sessions.
            # https://www.hkex.com.hk/News/Market-Communications/2016/160720news?sc_lang=en
            if name == "XHKG" and session.date() >= date(2016, 7, 25):
                instant += timedelta(minutes=10)
            return instant
        return tuple(session.date() for session in calendar.sessions_in_range(start.isoformat(), end.isoformat())
                     if session.date() not in closures and close(session) <= now)
    except (CalendarError, ValueError, TypeError):
        return None


def latest_closed_market_session(calendar_name: str, now: datetime) -> date:
    """Last verified close, or an explicit failure when no schedule is known.

    Search one calendar year, without fabricating a weekday for an unknown or
    inactive exchange. The extra UTC date covers markets east of Greenwich;
    ``closed_market_sessions`` still requires the actual close to precede now.
    """
    from datetime import UTC, timedelta

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A timezone-aware market cutoff is required")
    today = now.astimezone(UTC).date()
    sessions = closed_market_sessions(calendar_name, today - timedelta(days=366),
                                      today + timedelta(days=1), now)
    if sessions is None:
        raise MarketCalendarUnavailable(f"No verified session calendar for {calendar_name!r}")
    if not sessions:
        raise MarketCalendarUnavailable(f"No verified closed session in the past year for {calendar_name!r}")
    return sessions[-1]


def daily_publication_sessions(
    calendar_name: str, start: date, end: date, now: datetime, *, release_lag_days: int = 0,
) -> tuple[tuple[date, ...], date | None] | None:
    """Due daily observations and a closed same-day session awaiting publication.

    A market close is not a publisher's delivery deadline. Without a positive
    declared trading-session lag, today's market-local daily observation becomes
    overdue on the next local date. Explicit positive lags retain their existing
    completed-session contract; the same-day allowance is not added again.
    This changes availability expectations, never the exchange calendar or data.
    """
    from datetime import UTC
    import exchange_calendars

    sessions = closed_market_sessions(calendar_name, start, end, now)
    if sessions is None:
        return None
    lag = max(int(release_lag_days), 0)
    if lag:
        return sessions[:-lag], None
    name = str(calendar_name or "").upper()
    name = {"XSHE": "XSHG", "HKEX": "XHKG", "HK": "XHKG"}.get(name, name)
    zone = UTC if name == "24/7" else exchange_calendars.get_calendar(name).tz
    today = now.astimezone(zone).date()
    pending = sessions[-1] if sessions and sessions[-1] == today else None
    return (sessions[:-1] if pending else sessions), pending
