"""Dated Registry FX must reach every risk reader without changing native facts."""
from copy import deepcopy
from datetime import date

import pandas as pd
import pytest

from portfolio_app.services import holdings_workspace, research_solver
from portfolio_app.services.risk_fx_sources import (
    base_currency_risk_profile, base_currency_risk_profiles, risk_fx_histories_from_details, risk_fx_instrument_ids,
)
from portfolio_app.services.portfolio_risk_context import project_portfolio_risk
from .test_portfolio_risk_context import catalog
from .test_risk_model import AS_OF_DATE, _holding, _risk_policy, _workspace


def fx_detail():
    dates = [day.date() for day in pd.bdate_range("2026-06-23", AS_OF_DATE)]
    return {"instrument_id": "fx-usd-hkd", "instrument_type": "fx", "currency": "HKD",
            "source_settings": {"market_calendar": "24/5", "expected_frequency": "daily"},
            "market_data": [{"as_of_date": day.isoformat(), "value": 7.8 + .01 * (i % 5),
                             "quote_basis": "spot", "metric_family": "fx", "currency": "HKD",
                             "price_unit": "rate", "price_scale": 1, "status": "complete"}
                            for i, day in enumerate(dates)]}


def test_fx_loader_keeps_observed_direction_cutoff_and_source_contract():
    detail = fx_detail()
    cutoff = date(2026, 7, 22)
    histories = risk_fx_histories_from_details({"fx-usd-hkd": detail}, end_date=cutoff)
    history = histories["fx-usd-hkd"]
    assert (history.base_currency, history.quote_currency) == ("USD", "HKD")
    assert max(history.levels.index) == cutoff
    assert history.observation_coverage["gap_detection_basis"] == "market_calendar:24/5"
    assert risk_fx_instrument_ids({"HKD"}, "USD") == ["fx-usd-hkd"]
    assert set(risk_fx_instrument_ids({"HKD"}, "CNY")) == {"fx-usd-hkd", "fx-usd-cny"}
    assert risk_fx_instrument_ids({"USD"}, "USD") == []
    invalid = deepcopy(detail)
    invalid["market_data"][5]["currency"] = "EUR"
    assert risk_fx_histories_from_details({"fx-usd-hkd": invalid}, end_date=AS_OF_DATE) == {}


def test_holdings_read_loads_fx_once_and_cash_rc_is_not_lost_in_context(monkeypatch):
    details = {"fx-usd-hkd": fx_detail()}
    loads = []
    def load(ids):
        loads.append(ids)
        return {iid: details[iid] for iid in ids}
    monkeypatch.setattr(holdings_workspace, "get_registry_instrument_details", load)
    stock = _holding("alpha", .8, currency="HKD")
    stock.update(holding_category="securities", holding_kind="position", position_reference_id="alpha")
    cash = _holding("cash:hkd", .2, currency="HKD", points=[])
    cash["instrument_core"]["instrument_type"] = "cash"
    cash.update(holding_category="cash_and_settlement", holding_kind="settled_cash", risk_eligible=False)
    native = deepcopy(stock["instrument_return_series_all"])
    value = _workspace([stock, cash], base_currency="USD", portfolio_id="p",
                       as_of_date=AS_OF_DATE.isoformat(), totals={"nav": 1_000_000})
    result = holdings_workspace._calculate_holdings_forward_risk(
        value, as_of_date=AS_OF_DATE, calculation_frequency="daily",
        risk_policy=_risk_policy(), instrument_details={},
    )
    assert loads == [["fx-usd-hkd"]]
    assert result["forward_risk"]["status"] == "ok"
    assert stock["instrument_return_series_all"] == native
    assert cash["risk_eligible"] is False
    assert cash["forward_risk_status"] == "ok"
    first = stock["risk_return_series"]["points"][0]
    assert first["value"] == pytest.approx((1 + native["points"][0]["value"]) * 7.8 / 7.81 - 1)
    assert cash["risk_return_series"]["points"][0]["value"] == pytest.approx(7.8 / 7.81 - 1)
    projected = project_portfolio_risk(result, catalog())
    groups = projected["portfolio_metrics"]["groups"]["rows"]
    cash_group = next(row for row in groups if row["group_id"] == "cash_bucket:__cash__")
    assert cash_group["risk_share"] == pytest.approx(cash["forward_risk_share"])
    assert sum(row["risk_share"] or 0 for row in groups) == pytest.approx(1)


def test_research_and_catalogue_keep_fx_only_day_and_native_gap(monkeypatch):
    days = [date(2026, 7, day) for day in (20, 21, 22)]
    detail = {"instrument_id": "hk-stock", "instrument_type": "equity", "currency": "HKD",
              "quote_selection_policy": {"total_return": ["adjusted_close"]},
              "source_settings": {"expected_frequency": "event_driven"},
              "market_data": [{"as_of_date": day.isoformat(), "value": value,
                               "currency": "HKD", "quote_basis": "adjusted_close", "metric_family": "price",
                               "price_unit": "per_unit", "price_scale": 1, "status": "complete"}
                              for day, value in [(days[0], 100), (days[2], 110)]]}
    fx = fx_detail()
    fx["market_data"] = [{**fx["market_data"][0], "as_of_date": day.isoformat(), "value": rate}
                         for day, rate in zip(days, [8, 8.1, 8.2])]
    sources = {"hk-stock": detail, "fx-usd-hkd": fx}
    state = research_solver.ResearchMarketState("USD", days[-1], sources, {("USD", "HKD"): "fx-usd-hkd"})
    navs, _ = research_solver._build_instrument_nav_series(
        state, instrument_id="hk-stock", start_date=days[0], end_date=days[-1],
        retain_missing_valuations=True,
    )
    assert list(navs.index) == days
    assert navs.tolist() == pytest.approx([100 / 8, 100 / 8.1, 110 / 8.2])
    profile = base_currency_risk_profile(detail, base_currency="USD", end_date=days[-1],
        fx_histories=risk_fx_histories_from_details(sources, end_date=days[-1]))
    assert profile["currency"] == "USD"
    assert profile["source_instrument_ids"] == ["fx-usd-hkd"]
    assert [point["value"] for point in profile["points"]] == pytest.approx([8 / 8.1 - 1, 1.1 * 8.1 / 8.2 - 1])
    detail["source_settings"] = {"expected_frequency": "daily", "market_calendar": "24/5"}
    profile = base_currency_risk_profile(detail, base_currency="USD", end_date=days[-1],
        fx_histories=risk_fx_histories_from_details(sources, end_date=days[-1]))
    assert [point["value"] for point in profile["points"]] == [None, None]


def test_catalogue_uses_common_periods_on_us_holiday_and_isolates_missing_fx():
    days = [date(2026, 7, day) for day in (2, 3, 6)]
    def stock(currency, calendar, observations):
        return {"instrument_type": "equity", "currency": currency,
                "quote_selection_policy": {"total_return": ["adjusted_close"]},
                "source_settings": {"expected_frequency": "daily", "market_calendar": calendar},
                "market_data": [{"as_of_date": day.isoformat(), "value": value,
                                 "currency": currency, "quote_basis": "adjusted_close", "metric_family": "price",
                                 "price_unit": "per_unit", "price_scale": 1, "status": "complete"}
                                for day, value in observations]}
    details = {
        "us": stock("USD", "XNYS", [(days[0], 100), (days[2], 110)]),
        "hk": stock("HKD", "XHKG", list(zip(days, [100, 105, 110]))),
        "eur": stock("EUR", "24/5", list(zip(days, [100, 101, 102]))),
    }
    fx = fx_detail()
    fx["market_data"] = [{**fx["market_data"][0], "as_of_date": day.isoformat(), "value": rate}
                         for day, rate in zip(days, [8, 8.1, 8.2])]
    profiles = base_currency_risk_profiles(details, base_currency="USD", end_date=days[-1],
        fx_histories=risk_fx_histories_from_details({"fx-usd-hkd": fx}, end_date=days[-1]))
    for key in ("us", "hk"):
        assert [(p["start_date"], p["date"]) for p in profiles[key]["points"]] == [
            (days[0].isoformat(), days[1].isoformat()), (days[1].isoformat(), days[2].isoformat()),
        ]
        assert profiles[key]["observation_coverage"]["gap_dates"] == []
    assert [p["value"] for p in profiles["us"]["points"]] == pytest.approx([0, .1])
    assert [p["value"] for p in profiles["hk"]["points"]] == pytest.approx([1.05 * 8 / 8.1 - 1, 110 / 105 * 8.1 / 8.2 - 1])
    assert profiles["eur"]["points"] == []
    assert "EUR to USD" in profiles["eur"]["unavailable_reason"]


def test_exchange_calendar_gap_is_preserved_for_catalogue_and_stored_native_profile():
    detail = {"instrument_id": "us", "instrument_type": "equity", "currency": "USD",
              "exchange_code": "XNYS", "quote_selection_policy": {"total_return": ["adjusted_close"]},
              "market_data": [{"as_of_date": day, "value": value, "currency": "USD",
                               "quote_basis": "adjusted_close", "metric_family": "price",
                               "price_unit": "per_unit", "price_scale": 1, "status": "complete"}
                              for day, value in [("2026-01-02", 100), ("2026-01-06", 110)]]}
    profile = base_currency_risk_profile(detail, base_currency="USD", end_date=date(2026, 1, 6), fx_histories={})
    assert profile["observation_coverage"]["gap_dates"] == ["2026-01-05"]
    assert [p["value"] for p in profile["points"]] == [None, None]
    row = _holding("us", 1, currency="USD")
    row["instrument_return_series_all"] = {
        "first_return_start_date": "2026-01-02",
        "points": [{"start_date": "2026-01-02", "date": "2026-01-06", "value": .1}],
        "observation_coverage": {"gap_dates": [], "gap_detection_basis": "calendar_day_threshold"},
    }
    saved = _workspace([row], base_currency="USD", as_of_date="2026-01-06")
    original = deepcopy(saved)
    response = holdings_workspace._materialized_holdings_workspace_response(
        saved, risk_basis_profile={"resolved_frequency": "daily"}, instrument_details={"us": detail},
    )
    assert saved == original
    series = response["rows"][0]["instrument_return_series_all"]
    assert series["points"] == original["rows"][0]["instrument_return_series_all"]["points"]
    assert series["observation_coverage"]["gap_dates"] == ["2026-01-05"]
