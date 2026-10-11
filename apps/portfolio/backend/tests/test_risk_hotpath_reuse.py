"""Reuse deterministic intermediates without changing risk dates or missingness."""
from datetime import date

import pandas as pd

from portfolio_app.services import market_data, risk_currency


def test_calendar_reuses_covering_history_across_holdings_and_aliases(monkeypatch):
    market_data.market_calendar_sessions.cache_clear()
    monkeypatch.setattr(market_data, "_MARKET_CALENDARS", {})
    get_calendar = market_data.exchange_calendars.get_calendar
    calls = []

    def counted(name, **kwargs):
        calls.append((name, kwargs))
        return get_calendar(name, **kwargs)

    monkeypatch.setattr(market_data.exchange_calendars, "get_calendar", counted)
    end = date(2026, 10, 8)
    full = market_data.market_calendar_sessions("XNYS", date(2000, 1, 3), end)
    assert full is not None and full[0] == date(2000, 1, 3) and full[-1] == end
    initial_calls = len(calls)
    assert initial_calls <= 2  # Default instance, then its historical extension.
    for name, start in [("XNYS", date(2010, 1, 4)), ("NYSE", date(2003, 1, 3))]:
        assert market_data.market_calendar_sessions(name, start, end) == tuple(
            day for day in full if day >= start
        )
    assert len(calls) == initial_calls
    earlier = market_data.market_calendar_sessions("XNYS", date(1999, 1, 4), end)
    assert earlier is not None and earlier[0] == date(1999, 1, 4)
    assert len(calls) == initial_calls + 1
    # Expanding the private instance never changes its previously computed range.
    market_data.market_calendar_sessions.cache_clear()
    assert market_data.market_calendar_sessions("XNYS", date(2000, 1, 3), end) == full
    assert len(calls) == initial_calls + 1
    market_data.market_calendar_sessions.cache_clear()


def test_calendar_unsupported_range_does_not_poison_valid_schedule(monkeypatch):
    market_data.market_calendar_sessions.cache_clear()
    monkeypatch.setattr(market_data, "_MARKET_CALENDARS", {})
    args = (date(2026, 6, 1), date(2026, 6, 5))
    expected = market_data.market_calendar_sessions("XSHG", *args)
    assert expected is not None
    assert market_data.market_calendar_sessions("XSHG", date(2026, 6, 1), date(2050, 1, 1)) is None
    assert market_data.market_calendar_sessions("UNKNOWN", *args) is None
    assert market_data.market_calendar_sessions("XSHE", *args) == expected
    market_data.market_calendar_sessions.cache_clear()


def test_fx_composition_does_not_deepcopy_full_interval_history_per_holding(monkeypatch):
    class IntervalHistory(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Pandas copied full interval metadata into an intermediate frame")

    align_components = risk_currency.align_risk_navs
    history = None

    def aligned(*args, **kwargs):
        nonlocal history
        navs, returns = align_components(*args, **kwargs)
        history = IntervalHistory(returns.attrs["return_period_starts"])
        returns.attrs["return_period_starts"] = history
        return navs, returns

    monkeypatch.setattr(risk_currency, "align_risk_navs", aligned)
    days = [date(2026, 7, day) for day in (6, 7, 8, 9)]
    native = pd.Series([100., 101., 102., 103.], index=days)
    coverage = {"gap_dates": [], "gap_detection_basis": "market_calendar:24/5"}
    fx = risk_currency.RiskFxHistory("fx", "HKD", "USD", pd.Series([.125, .13, .14], index=[days[0], *days[2:]]),
                                    {**coverage, "gap_dates": [str(days[1])]})
    _, returns, _ = risk_currency.align_base_currency_navs(
        {"a": native, "b": native * 2}, currency_by_key={"a": "HKD", "b": "USD"},
        coverage_by_key={"a": coverage, "b": coverage}, base_currency="USD", fx_histories={"fx": fx},
    )
    assert returns.attrs["return_period_starts"] is history
    assert history == {str(end): str(start) for start, end in zip(days, days[1:])}
    # Inspect numeric values without slicing the published frame's metadata.
    values = returns.to_numpy()
    assert pd.isna(values[1:3, 0]).all()
    assert pd.notna(values[1:, 1]).all()
    assert native.tolist() == [100., 101., 102., 103.]
