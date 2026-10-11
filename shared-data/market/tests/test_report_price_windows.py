from datetime import date, datetime, UTC

import pytest

from studio_market.numeric.reporting import select_report_price_window


def choose(days, *, kind="daily", calendar="XNYS", cutoff=datetime(2026, 9, 10, 0, tzinfo=UTC)):
    return select_report_price_window(
        [{"date": f"2026-09-{day:02}", "close": 100 + day, "observed_at": cutoff.isoformat()} for day in reversed(days)],
        report_kind=kind, period_start=date(2026, 9, 7) if kind == "weekly" else date(2026, 9, 10),
        period_end=date(2026, 9, 10), cutoff=cutoff, calendar_name=calendar)


def test_recent_two_prices_cannot_bridge_a_missing_us_session():
    base, final, state = choose([4, 9])
    assert base is final is None
    assert state["missing_dates"] == ["2026-09-08"]  # Sep 7 is a confirmed holiday.


def test_weekly_requires_internal_expected_sessions_and_real_week_anchor():
    base, final, state = choose([4, 9], kind="weekly")
    assert base is final is None
    assert state["missing_dates"] == ["2026-09-08"]


def test_unknown_schedule_is_not_assumed_complete():
    assert choose([8, 9], calendar=None)[2]["status"] == "observation_schedule_unverified"


def test_daily_pair_uses_closed_sessions_at_exact_cutoff():
    # An intraday Sep 9 row cannot replace Sep 8 close before Sep 9 has closed.
    base, final, state = choose([3, 4, 8, 9], cutoff=datetime(2026, 9, 9, 15, tzinfo=UTC))
    assert state["status"] == "available"
    assert (base["date"], final["date"]) == ("2026-09-04", "2026-09-08")


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf"), True])
def test_invalid_endpoint_prices_never_become_returns(bad):
    cutoff = datetime(2026, 9, 10, 0, tzinfo=UTC)
    result = select_report_price_window([
        {"date": "2026-09-08", "close": 100, "observed_at": cutoff.isoformat()},
        {"date": "2026-09-09", "close": bad, "observed_at": cutoff.isoformat()},
    ], report_kind="daily", period_start=date(2026, 9, 10), period_end=date(2026, 9, 10), cutoff=cutoff, calendar_name="XNYS")
    assert result[0] is result[1] is None
    assert result[2]["status"] == "missing_comparable_prices"
