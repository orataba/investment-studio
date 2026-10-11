from copy import deepcopy
from datetime import date
from math import log1p
from math import sqrt

import pandas as pd
import pytest

from portfolio_app.services import instrument_charts as charts, research_solver as research
from portfolio_app.services.market_data import analytical_return_quote_bases, market_calendar_sessions, resolve_quote_point
from portfolio_app.services.risk_currency import RiskFxHistory, align_base_currency_navs
from portfolio_app.services.risk_model import _return_series_with_periods
from tests.test_market_history import _detail, _point
from tests.test_risk_model import _holding


DAYS = [date(2026, 7, day) for day in (6, 7, 8, 9)]


def security(*, segments=False):
    result = _detail([
        *[_point("close", str(day), str(value)) for day, value in zip(DAYS, (100, 90, 91, 92))],
        *[_point("adjusted_close", str(day), str(value)) for day, value in zip(DAYS, (100, 110, 500, 550))],
    ])
    result["source_settings"] = {"expected_frequency": "daily", "market_calendar": "XNYS"}
    if segments:
        result["source_settings"]["total_return_segments"] = [
            {"start_date": str(DAYS[0]), "end_date": str(DAYS[1])},
            {"start_date": str(DAYS[2]), "end_date": str(DAYS[3])},
        ]
    return result


def chart(detail, mode="return", range_key="all", as_of=DAYS[-1]):
    return charts.build_instrument_price_chart_from_detail(
        detail, instrument_id="equity-history", as_of_date=as_of, range_key=range_key, price_mode=mode,
    )


def test_raw_security_quotes_remain_valuation_facts_but_cannot_fund_research_or_risk():
    detail = _detail([_point("close", str(day), str(value)) for day, value in zip(DAYS, (100, 50, 55, 54))])
    assert analytical_return_quote_bases(detail) == ["adjusted_close"]
    assert resolve_quote_point(detail, candidate_bases=["close"], as_of_date=DAYS[-1]).point["value"] == 54
    assert chart(detail)["points"] == []
    assert [point["value"] for point in chart(detail, "raw")["points"]] == [100, 50, 55, 54]
    assert chart(detail, "front")["points"] == []
    assert chart(detail, "front")["available_price_modes"] == ["raw"]
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    with pytest.raises(ValueError, match="confirmed analytical return source"):
        research._build_instrument_nav_series(state, instrument_id="equity-history", start_date=DAYS[0], end_date=DAYS[-1])
    assert research._selected_price_points(detail, end_date=DAYS[-1], candidate_bases=["close"]) == []


@pytest.mark.parametrize("instrument_type,basis", [("index", "close"), ("crypto", "close"), ("fx", "spot")])
def test_confirmed_non_security_price_return_contracts_remain_available(instrument_type, basis):
    detail = _detail([_point(basis, str(day), str(value)) for day, value in zip(DAYS, (100, 101, 102, 103))],
                     instrument_type=instrument_type, return_semantics="price_return")
    detail["quote_selection_policy"] = {role: [basis] for role in ("total_return", "chart", "valuation")}
    detail["source_settings"]["market_calendar"] = {"index": "XNYS", "crypto": "24/7", "fx": "24/5"}[instrument_type]
    if basis == "spot":
        for point in detail["market_data"]:
            point.update(metric_family="fx", price_unit="rate")
    series = charts.build_instrument_risk_return_series_from_detail(detail, as_of_date=DAYS[-1])
    assert series["return_kind"] == "price_return"
    assert series["return_measure"] == "simple"
    assert series["quote_basis"] == basis
    assert series["currency"] == "USD"
    assert series["points"][0]["value"] == pytest.approx(.01)
    assert series["points"][0]["log_return"] == pytest.approx(log1p(.01))


def test_price_modes_reanchor_one_multiplicative_chain_and_do_not_change_raw_observations():
    detail = security()
    before = deepcopy(detail)
    raw, front, back = (chart(detail, mode) for mode in ("raw", "front", "back"))
    assert detail == before
    assert raw["adjustment_method"] is None
    assert [point["value"] for point in raw["points"]] == [100, 90, 91, 92]
    assert front["adjustment_anchor_date"] == str(DAYS[-1])
    assert back["adjustment_anchor_date"] == str(DAYS[0])
    assert front["points"][-1]["value"] == pytest.approx(92)
    assert back["points"][0]["value"] == 100
    for display in (front, back):
        assert display["adjustment_method"] == "total_return_reinvestment"
        assert display["points"][1]["value"] / display["points"][0]["value"] - 1 == pytest.approx(.1)
    long_detail = _detail([_point("close", "2025-01-02", "80"), _point("adjusted_close", "2025-01-02", "50"),
                          *detail["market_data"]])
    assert chart(long_detail, "back", "1m")["adjustment_anchor_date"] == "2025-01-02"
    assert chart(long_detail, "back", "1m")["points"] == chart(long_detail, "back")["points"][1:]


def test_front_requires_latest_raw_quote_to_have_a_confirmed_tr_anchor_and_empty_ranges_stay_empty():
    detail = security()
    detail["market_data"].append(_point("close", "2026-07-10", "93"))
    front = chart(detail, "front", as_of=date(2026, 7, 10))
    assert front["points"] == []
    assert front["adjustment_anchor_date"] is None
    assert front["available_price_modes"] == ["raw", "back"]
    assert chart(detail, "raw", as_of=date(2026, 7, 10))["points"][-1]["value"] == 93
    for mode in ("return", "raw", "back"):
        assert chart(detail, mode, "1m", as_of=date(2026, 10, 8))["points"] == []


def test_expected_calendar_gap_withholds_path_risk_without_erasing_comparable_endpoint_return():
    days = market_calendar_sessions("XNYS", date(2026, 5, 1), DAYS[-1])
    detail = _detail([_point("adjusted_close", str(day), str(100 + index % 7))
                      for index, day in enumerate(days)])
    detail["exchange_code"] = "XNYS"
    complete = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=DAYS[-1])
    assert complete["instrument_volatility_1m"] is not None
    detail["market_data"] = [point for point in detail["market_data"] if point["as_of_date"] != "2026-06-15"]
    partial = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=DAYS[-1])
    assert partial["instrument_volatility_1m"] is None
    assert partial["instrument_current_drawdown"] is None
    assert partial["instrument_max_drawdown"] is None
    assert partial["instrument_return_1m"] == complete["instrument_return_1m"]
    invalid = next(point for point in partial["instrument_return_series_all"]["points"]
                   if point["date"] == "2026-06-16")
    assert invalid["value"] is None
    assert invalid["reason"] == "missing_expected_observation"
    display = chart(detail)
    assert "2026-06-15" in display["observation_coverage"]["gap_dates"]
    before = next(point for point in display["points"] if point["date"] == "2026-06-12")
    after = next(point for point in display["points"] if point["date"] == "2026-06-16")
    assert before["segment_id"] != after["segment_id"]


def test_trailing_expected_session_gap_withholds_path_risk_but_holiday_does_not():
    days = market_calendar_sessions("XNYS", date(2026, 5, 1), date(2026, 7, 2))
    detail = _detail([_point("adjusted_close", str(day), str(100 + index % 7))
                      for index, day in enumerate(days)])
    detail["exchange_code"] = "XNYS"
    holiday = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=date(2026, 7, 3))
    assert holiday["instrument_current_drawdown"] is not None
    missing = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=date(2026, 7, 6))
    assert missing["instrument_current_drawdown"] is None
    assert missing["instrument_volatility_1m"] is None
    assert missing["instrument_trend_reason"] == "missing_expected_observation"
    # Querying the independent earlier window retains its actual endpoint and risk.
    earlier = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=date(2026, 7, 2))
    assert earlier["instrument_trend_as_of_date"] == "2026-07-02"
    assert earlier["instrument_volatility_1m"] is not None


@pytest.mark.parametrize("settings", [{}, {"expected_frequency": "daily"}, {"market_calendar": "UNVERIFIED"}])
def test_unverified_schedule_retains_actual_prices_but_withholds_daily_risk(settings):
    detail = security()
    detail["source_settings"] = settings
    detail.pop("exchange_code", None)
    result = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=DAYS[-1])
    assert result["instrument_trend_reason"] == "observation_schedule_unverified"
    assert result["instrument_current_drawdown"] is None
    assert result["instrument_max_drawdown"] is None
    for point in result["instrument_return_series_all"]["points"]:
        assert point["status"] == "unavailable"
        assert point["value"] is None
        assert point["reason"] == "observation_schedule_unverified"
    assert chart(detail, "raw")["points"][-1]["value"] == 92
    assert chart(detail)["summary"]["change_pct"] == pytest.approx(4.5)
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    with pytest.raises(ValueError, match="equity-history daily risk unavailable: observation schedule unverified"):
        research._build_instrument_nav_series(state, instrument_id="equity-history", start_date=DAYS[0], end_date=DAYS[-1])


def test_return_segments_survive_chart_intervals_metrics_and_display_modes():
    detail = security(segments=True)
    series = charts.build_instrument_risk_return_series_from_detail(detail, as_of_date=DAYS[-1])
    assert [point["status"] for point in series["points"]] == ["complete", "unavailable", "complete"]
    assert series["points"][1]["value"] is None
    assert series["points"][1]["reason"] == "total_return_segment_break"
    assert series["points"][2]["value"] == pytest.approx(.1)
    result = chart(detail)
    assert [point["segment_id"] for point in result["points"]] == [str(DAYS[0]), str(DAYS[0]), str(DAYS[2]), str(DAYS[2])]
    assert result["summary"]["change_pct"] is None
    assert result["summary"]["high"] is None
    assert len(chart(detail, "front")["points"]) == 2
    assert len(chart(detail, "back")["points"]) == 2
    trend = charts.build_instrument_trend_metrics_from_detail(detail, as_of_date=DAYS[-1])
    assert trend["instrument_max_drawdown"] is None
    assert trend["instrument_return_series_all"]["return_kind"] == "total_return"


def test_native_and_fx_alignment_masks_only_the_unconfirmed_return_interval():
    detail = security(segments=True)
    contract = charts.build_instrument_risk_return_series_from_detail(detail, as_of_date=DAYS[-1])
    native = pd.Series([100., 110., 500., 550.], index=DAYS)
    _, returns, metadata = align_base_currency_navs(
        {"asset": native}, currency_by_key={"asset": "USD"}, base_currency="USD",
        coverage_by_key={"asset": {"gap_dates": [], "gap_detection_basis": "market_calendar:XNYS"}},
        return_contracts_by_key={"asset": contract},
    )
    assert returns.loc[DAYS[1], "asset"] == pytest.approx(.1)
    assert pd.isna(returns.loc[DAYS[2], "asset"])
    assert returns.loc[DAYS[3], "asset"] == pytest.approx(.1)
    assert metadata["asset"]["return_measure"] == "simple"


@pytest.mark.parametrize("changed", [{"return_measure": "log"}, {"return_kind": "unknown"}, {"currency": "USD"}])
def test_forward_risk_rejects_log_or_ambiguous_inputs_instead_of_treating_them_as_simple(changed):
    holding = _holding("fund", 1)
    holding["instrument_return_series_all"].update(changed)
    with pytest.raises(ValueError, match="explicit simple"):
        _return_series_with_periods(holding)


def test_research_keeps_the_selected_return_contract_and_interval_dates():
    detail = security(segments=True)
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    series, _ = research._build_instrument_nav_series(state, instrument_id="equity-history", start_date=DAYS[0], end_date=DAYS[-1])
    assert series.attrs["return_contract"]["quote_basis"] == "adjusted_close"
    assert series.attrs["return_contract"]["return_kind"] == "total_return"
    assert series.attrs["return_contract"]["return_measure"] == "simple"
    assert series.attrs["return_contract"]["start_date"] == str(DAYS[0])
    assert series.attrs["return_contract"]["end_date"] == str(DAYS[-1])


def test_research_alignment_keeps_each_segment_but_benchmark_wealth_stops_at_break():
    detail = security(segments=True)
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    series, _ = research._build_instrument_nav_series(state, instrument_id="equity-history", start_date=DAYS[0], end_date=DAYS[-1])
    member = research.ScopeMemberRecord(research.TARGET_MEMBER_INSTRUMENT, "equity-history", "Equity")
    aligned, _, _ = research._align_member_series([member], {(member.member_type, member.member_id): series},
                                                start_date=DAYS[0], end_date=DAYS[-1])
    assert aligned[0].returns.loc[DAYS[1]] == pytest.approx(.1)
    assert pd.isna(aligned[0].returns.loc[DAYS[2]])
    assert aligned[0].returns.loc[DAYS[3]] == pytest.approx(.1)
    comparison = research._build_backtest_benchmark_comparison_from_state(
        state, benchmark_instrument_id="equity-history",
        portfolio_points=[{"date": str(day), "value": 1.0} for day in DAYS],
    )
    benchmark = comparison["backtest_benchmark"]
    assert [point["date"] for point in benchmark["points"]] == [str(DAYS[0]), str(DAYS[1])]
    assert benchmark["return_contract"]["return_kind"] == "total_return"
    assert "continuously confirmed" in benchmark["warnings"][-1]


def test_benchmark_carry_on_same_source_date_does_not_create_a_new_economic_return():
    nav = pd.Series({date(2026, 7, 10): 100., date(2026, 7, 13): 101.})
    nav.attrs["return_contract"] = {"quote_basis": "adjusted_close", "total_return_segments": [
        {"start_date": "2026-07-10", "end_date": "2026-07-13"},
    ]}
    points, returns = research._build_sampled_benchmark_points(nav, [
        {"date": day, "value": 1.0} for day in ("2026-07-10", "2026-07-11", "2026-07-13")
    ])
    assert returns["2026-07-11"] == 0
    assert points[-1]["value"] == pytest.approx(1.01)


def test_native_benchmark_missing_expected_session_does_not_become_zero_then_multiday_return():
    detail = security()
    detail["market_data"] = [point for point in detail["market_data"] if point["as_of_date"] != str(DAYS[2])]
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    comparison = research._build_backtest_benchmark_comparison_from_state(
        state, benchmark_instrument_id="equity-history",
        portfolio_points=[{"date": str(day), "value": 1 + index * .01} for index, day in enumerate(DAYS)],
    )
    benchmark = comparison["backtest_benchmark"]
    assert [point["date"] for point in benchmark["points"]] == [str(DAYS[0]), str(DAYS[1])]
    assert benchmark["metrics"]["end_date"] == str(DAYS[1])
    assert comparison["backtest_relative_metrics"]["end_date"] == str(DAYS[1])
    assert "continuously confirmed" in benchmark["warnings"][-1]


def test_benchmark_cannot_start_from_a_mark_missing_a_required_session():
    nav = pd.Series({DAYS[0]: 100., DAYS[2]: 110., DAYS[3]: 111.})
    nav.attrs["observation_coverage"] = {"gap_dates": [str(DAYS[1])], "gap_detection_basis": "market_calendar:XNYS"}
    points, returns = research._build_sampled_benchmark_points(nav, [
        {"date": str(day), "value": 1.0} for day in DAYS[1:]
    ])
    assert [point["date"] for point in points] == [str(DAYS[2]), str(DAYS[3])]
    assert returns == {str(DAYS[3]): pytest.approx(111 / 110 - 1)}


def test_benchmark_gap_inside_a_sparse_comparison_interval_is_not_bridged():
    nav = pd.Series({DAYS[0]: 100., DAYS[2]: 110., DAYS[3]: 111.})
    nav.attrs["observation_coverage"] = {"gap_dates": [str(DAYS[1])], "gap_detection_basis": "market_calendar:XNYS"}
    points, returns = research._build_sampled_benchmark_points(nav, [
        {"date": str(day), "value": 1.0} for day in (DAYS[0], DAYS[3])
    ])
    assert points == [{"date": str(DAYS[0]), "value": 1.0}]
    assert returns == {}


def test_relative_research_metrics_start_at_the_first_comparable_benchmark_observation():
    detail = security()
    detail["market_data"] = [point for point in detail["market_data"] if point["as_of_date"] != str(DAYS[0])]
    state = research.ResearchMarketState("USD", DAYS[-1], {"equity-history": detail}, {})
    comparison = research._build_backtest_benchmark_comparison_from_state(
        state, benchmark_instrument_id="equity-history",
        portfolio_points=[{"date": str(day), "value": 1 + index * .01} for index, day in enumerate(DAYS)],
    )
    assert comparison["backtest_benchmark"]["metrics"]["start_date"] == str(DAYS[1])
    assert comparison["backtest_relative_metrics"]["start_date"] == str(DAYS[1])


def test_arithmetic_active_returns_do_not_create_a_negative_wealth_or_drawdown_series():
    from portfolio_app.api.contracts import ResearchBacktestBenchmarkComparisonResponse

    detail = _detail([_point("adjusted_close", str(day), str(value))
                      for day, value in zip(DAYS[:3], (100, 300, 270))])
    detail["source_settings"] = {"expected_frequency": "daily", "market_calendar": "XNYS"}
    state = research.ResearchMarketState("USD", DAYS[2], {"equity-history": detail}, {})
    comparison = research._build_backtest_benchmark_comparison_from_state(
        state, benchmark_instrument_id="equity-history",
        portfolio_points=[{"date": str(day), "value": value} for day, value in zip(DAYS[:3], (1., .9, .99))],
    )
    relative = comparison["backtest_relative_metrics"]
    assert relative["excess_return"] == pytest.approx(-.01 - 1.7)
    assert relative["tracking_error"] == pytest.approx(sqrt((1.15 ** 2 + 1.15 ** 2) * 365.25))
    assert relative["information_ratio"] == pytest.approx(-.95 * 365.25 / relative["tracking_error"])
    assert relative["observation_count"] == 2
    assert not {"period_return", "max_drawdown", "current_drawdown", "sharpe_ratio"} & relative.keys()
    serialized = ResearchBacktestBenchmarkComparisonResponse.model_validate(comparison).model_dump()
    assert serialized["backtest_relative_metrics"] == relative


def test_calendar_covers_long_history_and_confirmed_hkex_weather_closures():
    assert market_calendar_sessions("XHKG", date(2023, 8, 31), date(2023, 9, 8)) == (
        date(2023, 8, 31), date(2023, 9, 4), date(2023, 9, 5), date(2023, 9, 6), date(2023, 9, 7),
    )
    sessions = market_calendar_sessions("XNYS", date(2000, 1, 3), date(2026, 7, 9))
    assert sessions is not None
    assert sessions[0] == date(2000, 1, 3)
    assert sessions[-1] == date(2026, 7, 9)


def test_confirmed_holiday_carry_keeps_fx_return_beyond_last_native_segment_date():
    native_days = [date(2026, 6, 29), date(2026, 6, 30)]
    days = [*native_days, date(2026, 7, 1)]  # HK holiday; observed FX continues.
    contract = {"quote_basis": "adjusted_close", "return_kind": "total_return", "return_measure": "simple",
                "total_return_segments": [{"start_date": str(native_days[0]), "end_date": str(native_days[-1])}]}
    native = pd.Series([100., 101.], index=native_days)
    fx = RiskFxHistory("fx-hkd-usd", "HKD", "USD", pd.Series([.125, .126, .127], index=days),
                      {"gap_dates": [], "gap_detection_basis": "market_calendar:24/5"})
    navs, returns, metadata = align_base_currency_navs(
        {"asset": native}, currency_by_key={"asset": "HKD"}, base_currency="USD",
        coverage_by_key={"asset": {"gap_dates": [], "gap_detection_basis": "market_calendar:XHKG"}},
        return_contracts_by_key={"asset": contract}, fx_histories={fx.instrument_id: fx}, end_date=days[-1],
    )
    assert returns.at[days[-1], "asset"] == pytest.approx(.127 / .126 - 1)
    converted = navs["asset"].copy()
    converted.attrs.update(return_contract=metadata["asset"], observation_coverage=metadata["asset"]["observation_coverage"])
    member = research.ScopeMemberRecord(research.TARGET_MEMBER_INSTRUMENT, "asset", "Asset")
    aligned, _, _ = research._align_member_series([member], {(member.member_type, member.member_id): converted},
                                                start_date=days[0], end_date=days[-1])
    assert aligned[0].returns.loc[days[-1]] == pytest.approx(.127 / .126 - 1)
