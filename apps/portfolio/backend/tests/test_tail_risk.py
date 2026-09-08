from copy import deepcopy
from datetime import date, timedelta

import pytest

from portfolio_app.services import tail_risk as service
from .test_valuation_fx_module import _fx_detail


AS_OF = date(2026, 9, 8)


def points(values, *, start=date(2026, 6, 1), step=1):
    return [{"start_date": (start + timedelta(days=index * step)).isoformat(),
             "date": (start + timedelta(days=(index + 1) * step)).isoformat(), "value": value}
            for index, value in enumerate(values)]


def holding(iid="a", value=100, *, returns=None, currency="CNY", **changes):
    return {"position_reference_id": iid, "instrument_core": {"instrument_id": iid,
        "instrument_name": iid, "instrument_type": "stock", "currency": currency},
        "market_value_base": value, "holding_category": "securities",
        "instrument_return_series_all": {"points": returns if returns is not None else points([-0.01] * 40)}, **changes}


def workspace(rows, *, nav=1000, as_of=AS_OF, base="CNY"):
    return {"portfolio_id": "p", "as_of_date": as_of.isoformat(), "base_currency": base,
            "totals": {"nav": nav}, "rows": rows}


def test_empirical_es_retains_fractional_boundary_mass_and_ties():
    # 37.5% of four equally probable observations is 1.5 observations:
    # the worst loss 10 and half the next loss 4, not every loss >= VaR.
    result = service.empirical_tail([1, 4, 4, 10], 0.625)
    assert result["var"] == 4
    assert result["expected_shortfall"] == 8
    assert result["tail_effective_observations"] == 1.5
    assert result["tail_observation_count"] == 2
    assert result["tail_max_observation_weight"] == pytest.approx(2 / 3)


def test_integer_quantile_and_tail_mass_are_not_changed_by_binary_rounding():
    result = service.empirical_tail(list(range(100)), 0.95)
    assert result["var"] == 94
    assert result["expected_shortfall"] == 97
    assert result["tail_effective_observations"] == 5
    assert result["tail_observation_count"] == 5


def test_unresolved_tail_with_less_than_one_observation_is_unavailable():
    result = service.empirical_tail([1] * 90, 0.99)
    assert result["var"] is None and result["expected_shortfall"] is None
    assert result["tail_effective_observations"] == pytest.approx(0.9)


def test_signed_gain_quantile_is_not_clipped_and_nonfinite_is_rejected():
    assert service.empirical_tail([-4, -3, -2, -1], 0.5)["expected_shortfall"] == -1.5
    with pytest.raises(ValueError, match="finite"):
        service.empirical_tail([float("nan")], 0.95)


def test_signed_positions_use_full_nav_and_derivatives_do_not_become_zero_risk():
    rows = [holding(value=600), holding("short", -200),
            holding("fcn", 300, derivative_contract_id="fcn", holding_category="derivatives"),
            holding("option", -10, derivative_contract_id="option", holding_category="derivatives")]
    source = workspace(rows)
    before = deepcopy(source)
    result = service.project_portfolio_tail_risk(source)
    assert source == before
    assert result["var_amount"] == 4
    assert result["expected_shortfall_amount"] == 4
    assert result["var_nav_fraction"] == .004
    assert result["modeled_gross_nav_fraction"] == .8
    assert result["excluded_gross_nav_fraction"] == .31
    assert result["coverage_status"] == "partial"
    assert [row["status"] for row in result["rows"]] == ["modeled", "modeled", "excluded", "excluded"]


def test_exact_common_start_and_end_dates_prevent_cross_interval_alignment():
    a = points([-0.01] * 40)
    b = points([-0.02] * 40)
    b[10]["start_date"] = (date.fromisoformat(b[10]["start_date"]) - timedelta(days=1)).isoformat()
    result = service.project_portfolio_tail_risk(workspace([holding(returns=a), holding("b", returns=b)]))
    assert result["observation_count"] == 39
    assert result["var_amount"] == 3
    assert "non_daily_or_unverified_intervals_removed" in result["limitations"]
    assert "common_period_intersection" in result["limitations"]


def test_low_frequency_sources_and_weekly_intervals_are_never_daily_zero_filled():
    weekly = holding("weekly", 600, returns=points([-.1] * 10, step=7))
    result = service.project_portfolio_tail_risk(workspace([weekly, holding("daily", 400)]),
        instrument_details={"weekly": {"source_settings": {"expected_frequency": "weekly"}}})
    assert result["observation_count"] == 40
    assert result["rows"][0]["reason"] == "non_daily_source"
    assert result["excluded_gross_nav_fraction"] == .6
    assert result["var_nav_fraction"] == .004
    no_metadata = service.project_portfolio_tail_risk(workspace([weekly]))
    assert no_metadata["observation_count"] == 0 and no_metadata["var_amount"] is None


def test_event_driven_delivery_schedule_accepts_only_genuine_one_day_observations():
    result = service.project_portfolio_tail_risk(workspace([holding()]),
        instrument_details={"a": {"source_settings": {"expected_frequency": "event_driven"}}})
    assert result["observation_count"] == 40


@pytest.mark.parametrize("invalid", [None, {"start_date": "2026-06-01", "date": "2026-06-02", "value": -2}])
def test_invalid_return_data_does_not_create_an_estimate(invalid):
    sample = points([-.01] * 40)
    sample.append(invalid)
    result = service.project_portfolio_tail_risk(workspace([holding(returns=sample)]))
    assert result["status"] == "unavailable"
    assert result["rows"][0]["reason"] == "invalid_return_period"


def test_duplicate_end_dates_cannot_double_weight_a_scenario():
    sample = points([-.01] * 40)
    sample.append({**sample[10], "start_date": sample[9]["start_date"]})
    result = service.project_portfolio_tail_risk(workspace([holding(returns=sample)]))
    assert result["status"] == "unavailable"
    assert result["rows"][0]["reason"] == "invalid_return_period"


def test_verified_calendar_retains_weekend_session_but_rejects_missing_session(monkeypatch):
    sessions = [date(2026, 6, 5), date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)]
    monkeypatch.setattr(service, "market_calendar_sessions", lambda *_: tuple(sessions))
    sample = [{"start_date": sessions[0], "date": sessions[1], "value": -.02},
              {"start_date": sessions[1], "date": sessions[3], "value": -.08}]
    result = service.project_portfolio_tail_risk(workspace([holding(returns=sample)]), confidence=.5,
        instrument_details={"a": {"source_settings": {"expected_frequency": "daily", "market_calendar": "TEST"}}})
    assert result["observation_count"] == 1
    assert result["rows"][0]["rejected_period_count"] == 1


def fx_inputs(*, missing_day=None):
    start = date(2026, 6, 1)
    observations = [((start + timedelta(days=i)).isoformat(), 7 * (1.01 ** i))
                    for i in range(41) if i != missing_day]
    detail = _fx_detail("fx-usd-cny", "CNY", observations)
    payload = {"rates": [{"source_kind": "direct", "base_currency": "USD", "quote_currency": "CNY", "instrument_id": "fx-usd-cny"}]}
    return {"fx-usd-cny": detail}, payload


def test_synchronized_security_and_fx_shocks_include_cross_term():
    details, fx = fx_inputs()
    result = service.project_portfolio_tail_risk(workspace([holding(value=1000, currency="USD", returns=points([-.02] * 40))]),
        instrument_details=details, fx_payload=fx)
    assert result["observation_count"] == 40
    assert result["var_amount"] == pytest.approx(10.2)
    assert result["var_nav_fraction"] == pytest.approx(.0102)
    assert result["rows"][0]["fx_instrument_ids"] == ["fx-usd-cny"]


def test_missing_fx_boundary_removes_real_periods_and_never_carries_last_rate():
    details, fx = fx_inputs(missing_day=20)
    result = service.project_portfolio_tail_risk(workspace([holding(value=1000, currency="USD")]),
        instrument_details=details, fx_payload=fx)
    assert result["observation_count"] == 38
    # Local loss -1% and FX gain 1% leave a -0.01% base-currency return.
    assert result["var_amount"] == pytest.approx(.1)


def test_foreign_cash_uses_fx_while_base_cash_is_not_a_stochastic_security():
    details, fx = fx_inputs()
    rows = [holding("usd-cash", 500, currency="USD", holding_category="cash_and_settlement"),
            holding("cny-cash", 500, holding_category="cash_and_settlement")]
    result = service.project_portfolio_tail_risk(workspace(rows), instrument_details=details, fx_payload=fx)
    assert result["observation_count"] == 40
    assert result["expected_shortfall_amount"] == pytest.approx(-5)
    assert [row["status"] for row in result["rows"]] == ["modeled", "base_currency_cash"]


def test_asof_and_window_boundaries_reject_future_shocks_and_crossing_intervals():
    sample = points([-.01] * 40, start=date(2026, 8, 1))
    result = service.project_portfolio_tail_risk(workspace([holding(value=1000, returns=sample)]),
                                                lookback_days=30, confidence=.95)
    assert result["observation_count"] == 30
    assert result["first_scenario_start_date"] == "2026-08-09"
    assert result["last_scenario_end_date"] == "2026-09-08"
    assert result["var_amount"] == 10


@pytest.mark.parametrize("rows,nav", [([], 1000), ([holding()], 0), ([holding()], None)])
def test_empty_or_invalid_nav_never_reports_safe_zero(rows, nav):
    result = service.project_portfolio_tail_risk(workspace(rows, nav=nav))
    assert result["status"] == "unavailable"
    assert result["var_amount"] is None and result["expected_shortfall_nav_fraction"] is None


def test_unknown_excluded_value_is_not_reported_as_zero_unmodeled_exposure():
    result = service.project_portfolio_tail_risk(workspace([holding(), holding("unknown", None)]))
    assert result["status"] == "available" and result["coverage_status"] == "partial"
    assert result["excluded_gross_exposure"] is None
    assert result["excluded_gross_nav_fraction"] is None


def test_reader_reuses_workspace_and_does_not_read_future_or_other_portfolio(monkeypatch):
    from portfolio_app.services import instrument_registry
    loaded = []
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", lambda ids: loaded.extend(ids) or {})
    source = workspace([holding()])
    result = service.read_portfolio_tail_risk("p", as_of_date=AS_OF, workspace=source)
    assert result["as_of_date"] == AS_OF.isoformat()
    assert loaded == ["a"]
    with pytest.raises(ValueError, match="another portfolio"):
        service.read_portfolio_tail_risk("other", workspace=source)
    with pytest.raises(ValueError, match="date differs"):
        service.read_portfolio_tail_risk("p", as_of_date=AS_OF - timedelta(days=1), workspace=source)


def test_single_source_discloses_requested_window_actual_scenarios_and_unavailable_tail():
    source = workspace([holding(returns=points([-.01] * 90))])
    source["portfolio_id"] = "p/one"
    result = service.project_portfolio_tail_risk(source, confidence=.99)
    assert len(result["sources"]) == 1
    citation = result["sources"][0]
    assert citation["portfolio_id"] == "p/one"
    assert citation["detail_path"] == "/portfolios/p%2Fone/risk"
    assert citation["start_date"] == result["window_start_date"]
    assert citation["end_date"] == result["as_of_date"]
    assert citation["scenario_start_date"] == result["first_scenario_start_date"]
    assert citation["scenario_end_date"] == result["last_scenario_end_date"]
    assert citation["observation_count"] == 90
    assert citation["tail_effective_observations"] == pytest.approx(.9)
    assert citation["confidence"] == .99
    assert citation["result_status"] == "unavailable"


def test_route_preserves_financial_read_and_validates_query_parameters():
    from portfolio_app.api.financial_read import FinancialReadRoute
    from portfolio_app.api.routes.tail_risk import router
    assert len(router.routes) == 1
    assert isinstance(router.routes[0], FinancialReadRoute)
    assert router.routes[0].path == "/{portfolio_id}/tail-risk"
