from datetime import UTC, date, datetime, timedelta

import exchange_calendars

import pytest

from studio_market.calendars import closed_market_sessions


@pytest.mark.parametrize("market", ["XSHG", "XSHE"])
def test_bounded_calendar_padding_preserves_supported_full_history(market):
    start, end = date(2000, 1, 1), date(2026, 10, 9)
    calendar = exchange_calendars.get_calendar("XSHG", start="1999-12-01", end="2026-12-31")
    expected = tuple(day.date() for day in calendar.sessions_in_range(start, end))
    actual = closed_market_sessions(market, start, end, datetime(2026, 10, 11, tzinfo=UTC))
    assert actual == expected
    assert actual[0] == date(2000, 1, 4) and actual[-1] == end


def test_bounded_calendar_padding_does_not_certify_actual_out_of_bounds_requests():
    calendar = exchange_calendars.get_calendar("XSHG")
    first, last = calendar.bound_min().date(), calendar.bound_max().date()
    clock = datetime(2050, 1, 1, tzinfo=UTC)
    assert closed_market_sessions("XSHG", first, first, clock) == (first,)
    assert closed_market_sessions("XSHG", first - timedelta(days=1), first, clock) is None
    assert closed_market_sessions("XSHG", date(2000, 1, 4), last + timedelta(days=1), clock) is None


@pytest.mark.parametrize('market', ['XHKG', 'XNYS'])
def test_non_session_request_bounds_do_not_make_old_history_unverified(market):
    assert closed_market_sessions(market, date(2000, 1, 1), date(2000, 1, 9), datetime(2026, 10, 8, tzinfo=UTC)) == tuple(date(2000, 1, day) for day in range(3, 8))


@pytest.mark.parametrize('day,hour', [(date(2026, 10, 8), 8), (date(2026, 12, 24), 4)])
def test_hk_cash_close_waits_for_random_auction_end_even_on_half_day(day, hour):
    assert closed_market_sessions('XHKG', day, day, datetime(day.year, day.month, day.day, hour, 5, tzinfo=UTC)) == ()
    assert closed_market_sessions('XHKG', day, day, datetime(day.year, day.month, day.day, hour, 10, tzinfo=UTC)) == (day,)


@pytest.mark.parametrize('day,hour', [(date(2026, 3, 6), 21), (date(2026, 3, 9), 20), (date(2026, 11, 27), 18)])
def test_us_session_close_honors_dst_and_early_close(day, hour):
    assert closed_market_sessions('XNYS', day, day, datetime(day.year, day.month, day.day, hour - 1, 59, tzinfo=UTC)) == ()
    assert closed_market_sessions('XNYS', day, day, datetime(day.year, day.month, day.day, hour, tzinfo=UTC)) == (day,)


def test_confirmed_full_day_closure_and_utc_crypto_boundary():
    assert closed_market_sessions('XHKG', date(2023, 9, 1), date(2023, 9, 1), datetime(2026, 10, 8, tzinfo=UTC)) == ()
    assert closed_market_sessions('24/7', date(2026, 10, 7), date(2026, 10, 8), datetime(2026, 10, 8, 23, 59, tzinfo=UTC)) == (date(2026, 10, 7),)
    assert closed_market_sessions('invalid', date(2026, 10, 7), date(2026, 10, 8), datetime(2026, 10, 8, tzinfo=UTC)) is None
