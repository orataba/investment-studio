"""Real close-to-close interval identity survives covariance window selection."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from portfolio_app.services import research_solver as solver
from portfolio_app.services.risk_currency import RiskFxHistory, align_base_currency_navs
from portfolio_app.services.risk_model import enrich_holdings_forward_risk
from tests.test_risk_model import _holding, _risk_policy, _workspace


MODELS = ["sample_covariance", "ewma_covariance", "lw_covariance", "ewma_vol_shrinkage_corr_covariance"]


def return_frame(anchor):
    dates = [date(2026, 6, day) for day in (1, 2, 3, 4, 5)]
    frame = pd.DataFrame({"a": [.01, -.02, .015, .003, -.004],
                          "b": [.003, .002, -.005, .001, .006]}, index=dates)
    frame.attrs["return_period_starts"] = {
        end.isoformat(): start.isoformat() for start, end in zip([anchor, *dates[:-1]], dates)
    }
    return frame


@pytest.mark.parametrize("model_id", MODELS)
@pytest.mark.parametrize("first_start", [date(2026, 5, 29), date(2026, 5, 22)])
def test_every_covariance_model_uses_actual_first_interval(model_id, first_start):
    short = return_frame(date(2026, 5, 31))
    actual = return_frame(first_start)
    parameters = {"min_observations": 2, "corr_min_observations": 2}
    baseline = solver.estimate_covariance(short, model_id=model_id, lookback_days=30, parameters=parameters)
    covariance = solver.estimate_covariance(actual, model_id=model_id, lookback_days=30, parameters=parameters)
    # Identical observations/model, only genuine interval duration differs.
    ratio = 5 / (date(2026, 6, 5) - first_start).days
    np.testing.assert_allclose(covariance, baseline * ratio, rtol=1e-10, atol=1e-12)


@pytest.mark.parametrize("model_id", MODELS)
def test_missing_or_noncontiguous_period_start_cannot_be_inferred(model_id):
    frame = return_frame(date(2026, 5, 29))
    parameters = {"min_observations": 2, "corr_min_observations": 2}
    frame.attrs.clear()
    with pytest.raises(ValueError, match="actual return period starts"):
        solver.estimate_covariance(frame, model_id=model_id, lookback_days=30, parameters=parameters)
    frame = return_frame(date(2026, 5, 29))
    frame.attrs["return_period_starts"]["2026-06-03"] = "2026-06-01"
    with pytest.raises(ValueError, match="contiguous actual return intervals"):
        solver.estimate_covariance(frame, model_id=model_id, lookback_days=30, parameters=parameters)


def test_window_clipping_uses_selected_first_return_start_not_original_anchor():
    frame = return_frame(date(2026, 5, 22))
    window = frame.loc[frame.index >= date(2026, 6, 3)]
    assert solver._annualization_periods_per_year(window) == pytest.approx(3 / 3 * 365.25)
    # The mapping still contains May 22, but that excluded return contributes no time.
    assert window.attrs["return_period_starts"]["2026-06-01"] == "2026-05-22"


def test_research_weekend_window_preserves_actual_close_anchor():
    dates = [item.date() for item in pd.bdate_range("2026-05-29", "2026-06-30")]
    series = pd.Series(np.cumprod([100., *([1.002, .998] * 11)[:len(dates)-1]]), index=dates)
    series.attrs["observation_coverage"] = {"gap_detection_basis": "market_calendar:24/5", "gap_dates": []}
    member = solver.ScopeMemberRecord(member_type=solver.TARGET_MEMBER_INSTRUMENT, member_id="asset", label="Asset")
    frame = solver._solver_return_window(members=[member], nav_series_by_member={(member.member_type, member.member_id): series},
                                        as_of_date=dates[-1], lookback_days=30, calculation_frequency="daily")
    assert frame.attrs["nav_anchor_date"] == date(2026, 5, 29)
    assert date(2026, 5, 30) not in frame.index
    cleaned = solver._clean_return_frame(frame)
    assert cleaned.attrs["return_period_starts"]["2026-06-01"] == "2026-05-29"
    assert solver._annualization_periods_per_year(cleaned) == pytest.approx(22 / 32 * 365.25)


def test_forward_risk_matches_exact_sample_span_after_weekend_boundary():
    dates = [item.date() for item in pd.bdate_range("2026-05-29", "2026-06-30")]
    values = np.asarray([.003, -.002, .001] * 8)[:len(dates)-1]
    points = [{"start_date": start.isoformat(), "date": end.isoformat(), "value": float(value)}
              for start, end, value in zip(dates, dates[1:], values)]
    result = enrich_holdings_forward_risk(_workspace([_holding("asset", 1, points=points)]),
        as_of_date=dates[-1], calculation_frequency="daily", risk_policy=_risk_policy())
    assert result["forward_risk"]["status"] == "ok"
    expected = np.var(values, ddof=1) * len(values) / (dates[-1] - dates[0]).days * 365.25
    assert result["forward_risk"]["portfolio_variance"] == pytest.approx(expected)
    assert result["rows"][0]["risk_return_series"]["points"][0]["start_date"] == "2026-05-29"


def test_fx_union_intervals_survive_covariance_without_native_only_anchor():
    # The FX leg trades Monday while the native market's first new close is Tuesday.
    dates = [date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    native = pd.Series([100., 102., 101.], index=[dates[0], dates[2], dates[3]])
    coverage = {"gap_dates": [], "gap_detection_basis": "market_calendar:24/5"}
    history = RiskFxHistory("fx", "HKD", "USD", pd.Series([.125, .13, .128, .132], index=dates), coverage)
    navs, returns, _ = align_base_currency_navs({"asset": native}, currency_by_key={"asset": "HKD"},
        coverage_by_key={"asset": coverage}, base_currency="USD", fx_histories={"fx": history}, end_date=dates[-1])
    selected = returns.iloc[1:]
    expected_returns = navs["asset"].pct_change(fill_method=None).iloc[1:]
    assert selected.attrs["return_period_starts"]["2026-06-01"] == "2026-05-29"
    assert selected.attrs["return_period_starts"]["2026-06-02"] == "2026-06-01"
    actual = solver.estimate_covariance(selected, model_id="sample_covariance", lookback_days=30)
    expected = expected_returns.var(ddof=1) * 3 / 5 * 365.25
    assert actual.iloc[0, 0] == pytest.approx(expected)


def test_backtest_metrics_use_portfolio_price_anchor_not_estimated_market_interval():
    points = [{"date": "2026-05-30", "value": 1.},
              {"date": "2026-06-01", "value": 1.01},
              {"date": "2026-06-02", "value": .9999}]
    returns = {"2026-06-01": .01, "2026-06-02": -.01}
    actual = solver._build_backtest_metrics(points, returns)
    assert actual["start_date"] == "2026-05-30"
    assert actual["annualized_volatility"] == pytest.approx(np.std([.01, -.01], ddof=1) * np.sqrt(2 / 3 * 365.25))
