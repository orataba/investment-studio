from datetime import date
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from portfolio_app.services.risk_currency import RiskFxHistory, align_base_currency_navs
from portfolio_app.services.risk_alignment import align_risk_navs
from portfolio_app.services.risk_model import enrich_holdings_forward_risk
from tests.test_risk_model import AS_OF_DATE, _holding, _risk_policy, _return_points, _workspace


DAYS = [date(2026, 7, day) for day in (6, 7, 8, 9)]
CALENDAR = {"gap_dates": [], "gap_detection_basis": "market_calendar:WEEKDAYS"}


def fx(source="USD", target="HKD", values=(8., 7.9, 7.8, 7.7), days=DAYS, gaps=()):
    iid = f"fx-{source.lower()}-{target.lower()}"
    return RiskFxHistory(iid, source, target, pd.Series(values, index=days, dtype=float),
                         {**CALENDAR, "gap_dates": [str(day) for day in gaps]})


def converted(native, currency="HKD", base="USD", histories=(), coverage=CALENDAR, **kwargs):
    return align_base_currency_navs({"asset": native}, currency_by_key={"asset": currency},
        coverage_by_key={"asset": coverage}, base_currency=base,
        fx_histories={item.instrument_id: item for item in histories}, **kwargs)


@pytest.mark.parametrize("inverse", [False, True])
def test_base_return_compounds_exact_same_interval_native_and_fx(inverse):
    native = pd.Series([100., 110., 99., 105.], index=DAYS)
    rates = np.array([.125, .13, .12, .14])
    history = fx("USD", "HKD", 1 / rates) if inverse else fx("HKD", "USD", rates)
    navs, returns, metadata = converted(native, histories=[history], end_date=DAYS[-1])
    expected = native * rates
    np.testing.assert_allclose(navs["asset"], expected)
    np.testing.assert_allclose(returns["asset"].iloc[1:], expected.pct_change().iloc[1:])
    assert metadata["asset"]["currency"] == "USD"
    assert metadata["asset"]["source_instrument_ids"] == [history.instrument_id]


def test_fx_moves_during_native_exchange_holiday_and_native_gap_cannot_be_carried():
    native = pd.Series([100., 103., 104.], index=[DAYS[0], DAYS[2], DAYS[3]])
    history = fx()
    _, returns, _ = converted(native, histories=[history])
    assert returns.at[DAYS[1], "asset"] == pytest.approx(8 / 7.9 - 1)
    assert returns.at[DAYS[2], "asset"] == pytest.approx(1.03 * 7.9 / 7.8 - 1)
    _, missing, metadata = converted(native, histories=[history],
        coverage={**CALENDAR, "gap_dates": [str(DAYS[1])]})
    assert missing.loc[DAYS[1:3], "asset"].isna().all()
    assert str(DAYS[1]) in metadata["asset"]["observation_coverage"]["gap_dates"]


def test_fx_missing_session_invalidates_both_adjacent_returns_but_holiday_carry_is_legal():
    native = pd.Series([100., 101., 102., 103.], index=DAYS)
    observed = fx(values=[8., 7.8, 7.7], days=[DAYS[0], DAYS[2], DAYS[3]], gaps=[DAYS[1]])
    _, returns, _ = converted(native, histories=[observed])
    assert returns.loc[DAYS[1:3], "asset"].isna().all()
    holiday = fx(values=[8., 7.8, 7.7], days=[DAYS[0], DAYS[2], DAYS[3]])
    _, returns, _ = converted(native, histories=[holiday])
    assert returns.at[DAYS[1], "asset"] == pytest.approx(.01)


def test_usd_pivot_uses_both_leg_calendars_and_prevents_lookahead():
    native = pd.Series([100., 102., 103., 104.], index=DAYS)
    hkd = fx()
    eur = fx("USD", "EUR", [.9, .91, .92, .93])
    navs, _, metadata = converted(native, base="EUR", histories=[hkd, eur], end_date=DAYS[2])
    assert list(navs.index) == DAYS[:3]
    np.testing.assert_allclose(navs["asset"], (native * eur.levels / hkd.levels).iloc[:3])
    assert metadata["asset"]["source_instrument_ids"] == ["fx-usd-eur", "fx-usd-hkd"]
    broken = fx("USD", "EUR", [.9, .92, .93], days=[DAYS[0], DAYS[2], DAYS[3]], gaps=[DAYS[1]])
    _, returns, _ = converted(native, base="EUR", histories=[hkd, broken])
    assert returns.loc[DAYS[1:3], "asset"].isna().all()


def test_missing_path_and_invalid_selected_path_never_fall_back_to_native_returns():
    native = pd.Series([100., 101., 102., 103.], index=DAYS)
    with pytest.raises(ValueError, match="requires observed FX history"):
        converted(native)
    bad = fx("HKD", "USD", [.125, -1, .13, .14])
    with pytest.raises(ValueError, match="positive"):
        converted(native, histories=[bad, fx()])


def test_threshold_missing_interval_survives_base_currency_composition():
    native = pd.Series([100., 101., 102., 103.], index=DAYS)
    _, returns, _ = converted(native, histories=[fx()],
        coverage={"gap_detection_basis": "calendar_day_threshold", "gap_dates": [str(DAYS[2])]})
    assert pd.isna(returns.at[DAYS[2], "asset"])


def risk_fx_history(*, missing=None):
    days = [date.fromisoformat(_return_points()[0]["start_date"])] + [date.fromisoformat(p["date"]) for p in _return_points()]
    values = [8. * (1 + .003 * np.sin(index)) for index in range(len(days))]
    if missing is not None:
        values.pop(days.index(missing)); days.remove(missing)
    return fx(days=days, values=values, gaps=[missing] if missing else [])


def monetary(iid, exposure, kind="settled_cash"):
    return {"line_id": iid, "holding_kind": kind,
            "instrument_core": {"instrument_id": iid, "instrument_name": iid, "instrument_type": "cash", "currency": "HKD"},
            "risk_eligible": False, "market_value_base": exposure, "quantity": 100}


def test_forward_risk_models_foreign_securities_and_signed_monetary_exposures_in_one_currency():
    rows = [_holding("foreign", .7, currency="HKD"), monetary("cash:HKD", 200_000),
            _holding("domestic", .4, currency="USD"), monetary("pending:HKD", -300_000, "pending_payable")]
    native_before = deepcopy(rows[0]["instrument_return_series_all"])
    history = risk_fx_history()
    result = enrich_holdings_forward_risk(_workspace(rows, base_currency="USD"),
        as_of_date=AS_OF_DATE, calculation_frequency="daily", risk_policy=_risk_policy(),
        fx_histories={history.instrument_id: history})
    assert result["forward_risk"]["status"] == "ok"
    assert rows[0]["instrument_return_series_all"] == native_before
    assert all(row["risk_return_series"]["currency"] == "USD" for row in rows)
    assert all(row["forward_risk_status"] == "ok" for row in rows)
    assert rows[1]["risk_eligible"] is False and rows[3]["risk_eligible"] is False
    assert rows[1]["forward_contribution_to_variance"] / rows[3]["forward_contribution_to_variance"] == pytest.approx(-2 / 3)
    returns = pd.DataFrame({row["line_id"]: {p["date"]: p["value"] for p in row["risk_return_series"]["points"]} for row in rows})
    returns = returns.loc[returns.index > result["forward_risk"]["coverage"]["window_start_date"]]
    # Existing model annualizes by observed periods / elapsed span, including
    # the median opening interval: 22 daily observations across 30 days here.
    assert len(returns) == 22
    covariance = returns.cov().to_numpy() * (22 / 30 * 365.25)
    weights = np.array([.7, .2, .4, -.3])
    assert result["forward_risk"]["portfolio_variance"] == pytest.approx(weights @ covariance @ weights)


@pytest.mark.parametrize("policy,status", [("strict", "unavailable"), ("complete_case_drop", "ok")])
def test_forward_fx_gap_preserves_null_intervals_and_covariance_policy(policy, status):
    missing = date.fromisoformat(_return_points()[10]["date"])
    history = risk_fx_history(missing=missing)
    result = enrich_holdings_forward_risk(_workspace([_holding("foreign", 1, currency="HKD")], base_currency="USD"),
        as_of_date=AS_OF_DATE, calculation_frequency="daily", risk_policy=_risk_policy(missing_return_policy=policy),
        fx_histories={history.instrument_id: history})
    assert result["forward_risk"]["status"] == status
    assert result["forward_risk"]["coverage"]["missing_row_count"] == 2
    assert sum(point["value"] is None for point in result["rows"][0]["risk_return_series"]["points"]) == 2


def test_pure_foreign_cash_portfolio_has_fx_risk_and_full_observed_history():
    history = risk_fx_history()
    row = monetary("cash:HKD", 1_000_000)
    result = enrich_holdings_forward_risk(_workspace([row], base_currency="USD"),
        as_of_date=AS_OF_DATE, calculation_frequency="daily", risk_policy=_risk_policy(),
        fx_histories={history.instrument_id: history})
    assert result["forward_risk"]["status"] == "ok"
    assert row["forward_risk_share"] == pytest.approx(1.)
    assert row["risk_return_series"]["first_return_start_date"] == history.levels.index[0].isoformat()


@pytest.mark.parametrize("gap_kind", ["native", "fx", "threshold", "none"])
def test_converted_research_second_alignment_preserves_production_return_intervals(gap_kind):
    native = pd.Series([100., 101., 102., 103.], index=DAYS)
    coverage = dict(CALENDAR)
    history = fx()
    if gap_kind == "native":
        native = native.drop(DAYS[1]); coverage["gap_dates"] = [str(DAYS[1])]
    if gap_kind == "fx":
        history = fx(values=[8., 7.8, 7.7], days=[DAYS[0], DAYS[2], DAYS[3]], gaps=[DAYS[1]])
    if gap_kind == "threshold":
        coverage = {"gap_detection_basis": "calendar_day_threshold", "gap_dates": [str(DAYS[1])]}
    navs, first_returns, metadata = converted(native, histories=[history], coverage=coverage)
    _, second_returns = align_risk_navs({"asset": navs["asset"]},
        coverage_by_key={"asset": metadata["asset"]["observation_coverage"]})
    pd.testing.assert_frame_equal(first_returns, second_returns)
    if gap_kind == "threshold":
        assert pd.isna(second_returns.at[DAYS[1], "asset"])
        assert pd.notna(second_returns.at[DAYS[2], "asset"])


def test_composite_carry_on_closed_weekend_requires_all_source_calendars_and_cutoff():
    friday, saturday = date(2026, 7, 10), date(2026, 7, 11)
    days = [DAYS[-1], friday]
    native = pd.Series([100., 101.], index=days)
    history = fx(days=days, values=[8., 7.9])
    navs, _, metadata = converted(native, histories=[history], end_date=saturday)
    coverage = metadata["asset"]["observation_coverage"]
    _, carried = align_risk_navs({"asset": navs["asset"]},
        coverage_by_key={"asset": coverage}, calendar=[*days, saturday], end_date=saturday)
    assert carried.at[saturday, "asset"] == 0
    unknown = deepcopy(coverage)
    unknown["component_coverage"]["fx:fx-usd-hkd"]["gap_detection_basis"] = "calendar_day_threshold"
    _, missing = align_risk_navs({"asset": navs["asset"]},
        coverage_by_key={"asset": unknown}, calendar=[*days, saturday], end_date=saturday)
    assert pd.isna(missing.at[saturday, "asset"])
    expired = {**coverage, "end_date": str(friday)}
    _, beyond = align_risk_navs({"asset": navs["asset"]},
        coverage_by_key={"asset": expired}, calendar=[*days, saturday], end_date=saturday)
    assert pd.isna(beyond.at[saturday, "asset"])

