"""Actual absent quotes (continuous multi-day returns) cannot become daily zeros."""
from datetime import date
from math import sin

import pandas as pd
import pytest

from portfolio_app.services import research_solver as solver
from portfolio_app.services.market_data import market_calendar_sessions
from portfolio_app.services.risk_alignment import align_risk_navs
from portfolio_app.services.risk_basis import observation_coverage_from_dates
from portfolio_app.services.risk_model import enrich_holdings_forward_risk
from tests.test_global_research_solver import tree, solve
from tests.test_risk_model import AS_OF_DATE, _return_points, _holding, _workspace, _risk_policy


@pytest.mark.parametrize("missing_all", [False, True])
@pytest.mark.parametrize("policy", ["strict", "complete_case_drop"])
def test_forward_risk_preserves_real_missing_session_and_next_return(missing_all, policy):
    holdings = []
    for key, scale in (("alpha", 1), ("beta", -.7)):
        points = _return_points(scale)
        missing = points[10]["date"]
        following = points[11]["date"]
        if missing_all or key == "beta":
            removed = points.pop(10)
            points[10]["start_date"] = removed["start_date"]
            points[10]["value"] = (1 + removed["value"]) * (1 + points[10]["value"]) - 1
        row = _holding(key, .5, points=points)
        row["instrument_return_series_all"]["observation_coverage"] = {
            "gap_dates": [missing] if missing_all or key == "beta" else [],
            "gap_detection_basis": "market_calendar:XSHG",
        }
        holdings.append(row)
    result = enrich_holdings_forward_risk(_workspace(holdings), as_of_date=AS_OF_DATE,
        calculation_frequency="daily", risk_policy=_risk_policy(missing_return_policy=policy))["forward_risk"]
    assert result["status"] == ("unavailable" if policy == "strict" else "ok")
    coverage = result["coverage"]
    assert coverage["rows_before"] == 22
    assert coverage["missing_row_count"] == 2
    assert {row["date"] for row in coverage["missing_rows"]} == {missing, following}
    if policy == "complete_case_drop":
        assert result["observation_count"] == 20


def research_state(*, missing_all=False, outside=False):
    state = tree()
    days = market_calendar_sessions("XSHG", date(2026, 5, 4), state.as_of_date)
    missing = date(2026, 5, 12) if outside else date(2026, 6, 10)
    for k, key in enumerate(("x", "y", "z")):
        value, points = 100., []
        for i, day in enumerate(days):
            value *= 1 + .01 * sin(i + k)
            if day == missing and (missing_all or key == "y"):
                continue
            points.append(dict(metric_family="price", quote_basis="adjusted_close", as_of_date=str(day),
                               value=str(value), currency="CNY", price_unit="per_unit", price_scale="1", status="complete"))
        state.instrument_detail_cache[key] = dict(instrument_id=key, instrument_name=key, instrument_type="equity", currency="CNY",
            source_settings={"expected_frequency": "daily", "market_calendar": "XSHG"},
            quote_selection_policy={"total_return": ["adjusted_close"]}, market_data=points)
    return state


@pytest.mark.parametrize("missing_all", [False, True])
def test_research_real_sources_agree_with_forward_risk_policy(missing_all):
    with pytest.raises(ValueError, match="missing|incomplete"):
        solve(research_state(missing_all=missing_all))
    result = solve(research_state(missing_all=missing_all), missing_return_policy="complete_case_drop")
    assert result.solve_event["execution_ready"] is True
    assert result.solve_event["missing_return_row_count"] == 2


def test_gap_before_requested_window_is_diagnostic_not_a_global_veto():
    assert solve(research_state(outside=True)).solve_event["execution_ready"] is True
    row = _holding("alpha", 1)
    row["instrument_return_series_all"]["observation_coverage"] = {
        "gap_dates": ["2026-05-12"], "gap_detection_basis": "market_calendar:XSHG"}
    result = enrich_holdings_forward_risk(_workspace([row]), as_of_date=AS_OF_DATE,
        calculation_frequency="daily", risk_policy=_risk_policy())
    assert result["forward_risk"]["status"] == "ok"


def test_holidays_and_event_driven_marks_remain_valid_but_missing_fx_does_not(monkeypatch):
    days = [date(2026, 6, n) for n in (1, 2, 3, 4)]
    daily = pd.Series([100, 101, 102, 103], index=days)
    slow = pd.Series([100, 103], index=[days[0], days[-1]])
    for basis in ("event_driven", "market_calendar:HOLIDAY_EXAMPLE"):
        navs, returns = align_risk_navs({"daily": daily, "slow": slow},
            coverage_by_key={"slow": {"gap_dates": [], "gap_detection_basis": basis}})
        assert returns["slow"].iloc[1:].tolist() == pytest.approx([0, 0, .03])
    state = research_state(outside=True)
    original = solver._convert_price_to_base
    monkeypatch.setattr(solver, "_convert_price_to_base", lambda *args, **kw:
        None if kw["point_date"] == date(2026, 6, 10) else original(*args, **kw))
    with pytest.raises(ValueError, match="missing|incomplete"):
        solve(state)


def test_expected_missing_tail_is_present_even_if_every_member_stops_early():
    days = market_calendar_sessions("XSHG", date(2026, 6, 1), date(2026, 6, 5))
    series = pd.Series([100 + i for i in range(len(days) - 1)], index=days[:-1])
    coverage = observation_coverage_from_dates(list(series.index),
        source_settings={"expected_frequency": "daily", "market_calendar": "XSHG"}, end_date=days[-1])
    _, returns = align_risk_navs({"x": series, "y": series}, coverage_by_key={"x": coverage, "y": coverage}, end_date=days[-1])
    assert returns.index[-1] == days[-1]
    assert returns.loc[days[-1]].isna().all()


@pytest.mark.parametrize("navs,calendar", [({}, None), ({"x": pd.Series(dtype=float)}, None),
    ({"x": pd.Series([100.], index=[date(2026, 6, 1)])}, [])])
def test_empty_alignment_inputs_are_domain_errors(navs, calendar):
    with pytest.raises(ValueError, match="non-empty"):
        align_risk_navs(navs, calendar=calendar, coverage_by_key={})


def test_missing_quote_before_window_cannot_supply_its_opening_mark():
    # June 2 is before the selected boundary, but June 1 cannot legally stand
    # in for its missing mark at June 3. June 4's catch-up is not a daily return.
    series = pd.Series([100., 104., 105.], index=[date(2026, 6, d) for d in (1, 4, 5)])
    navs, returns = align_risk_navs({"x": series}, calendar=[date(2026, 6, d) for d in (3, 4, 5)],
        coverage_by_key={"x": {"gap_dates": ["2026-06-02"], "gap_detection_basis": "market_calendar:XSHG"}})
    assert pd.isna(navs.loc[date(2026, 6, 3), "x"])
    assert pd.isna(returns.loc[date(2026, 6, 4), "x"])
    assert returns.loc[date(2026, 6, 5), "x"] == pytest.approx(105 / 104 - 1)
