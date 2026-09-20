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
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_metadata", lambda ids: loaded.extend(ids) or {})
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", lambda _ids: pytest.fail("base-currency securities reuse workspace returns"))
    source = workspace([holding()])
    result = service.read_portfolio_tail_risk("p", as_of_date=AS_OF, workspace=source)
    assert result["as_of_date"] == AS_OF.isoformat()
    assert loaded == ["a"]
    with pytest.raises(ValueError, match="another portfolio"):
        service.read_portfolio_tail_risk("other", workspace=source)
    with pytest.raises(ValueError, match="date differs"):
        service.read_portfolio_tail_risk("p", as_of_date=AS_OF - timedelta(days=1), workspace=source)


@pytest.mark.parametrize("missing_fx_day", [None, 20])
@pytest.mark.parametrize("lowercase_currencies", [False, True])
def test_reader_reuses_security_returns_and_loads_only_full_fx_history(monkeypatch, missing_fx_day, lowercase_currencies):
    from portfolio_app.services import instrument_registry

    fx_details, fx_payload = fx_inputs(missing_day=missing_fx_day)
    fx_payload["rates"].append({"source_kind": "direct", "base_currency": "USD",
                               "quote_currency": "EUR", "instrument_id": "fx-usd-eur"})
    metadata = {"a": {"exchange_code": "TEST", "source_settings": {"expected_frequency": "daily"}},
                "weekly": {"source_settings": {"expected_frequency": "weekly"}}}
    complete_details = {**fx_details, **deepcopy(metadata)}
    complete_details["a"]["market_data"] = [{"unused_security_history": True}]
    source = workspace([holding(value=900, currency="USD"), holding("weekly", value=100)])
    if lowercase_currencies:
        source["base_currency"] = source["base_currency"].lower()
        for row in source["rows"]:
            row["instrument_core"]["currency"] = row["instrument_core"]["currency"].lower()
    verified_calendar_days(monkeypatch)
    metadata_reads, history_reads = [], []

    def read_metadata(ids):
        metadata_reads.append(set(ids))
        return deepcopy(metadata)

    def read_history(ids):
        history_reads.append(set(ids))
        return deepcopy(fx_details)

    monkeypatch.setattr(instrument_registry, "get_registry_instrument_metadata", read_metadata)
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", read_history)
    monkeypatch.setattr(instrument_registry, "get_shared_fx_rates", lambda: fx_payload)
    expected = service.project_portfolio_tail_risk(source, instrument_details=complete_details, fx_payload=fx_payload)
    actual = service.read_portfolio_tail_risk("p", as_of_date=AS_OF, workspace=source)
    assert actual == expected
    assert metadata_reads == [{"a", "weekly"}]
    assert history_reads == [{"fx-usd-cny"}]
    assert actual["observation_count"] == (40 if missing_fx_day is None else 38)
    assert actual["rows"][1]["reason"] == "non_daily_source"


def test_rows_share_one_fx_history_resolution_without_filling_missing_boundaries(monkeypatch):
    details, fx = fx_inputs(missing_day=20)
    original = service.resolve_quote_series
    resolved = []

    def resolve(detail, **kwargs):
        resolved.append(detail["instrument_id"])
        return original(detail, **kwargs)

    monkeypatch.setattr(service, "resolve_quote_series", resolve)
    result = service.project_portfolio_tail_risk(workspace([
        holding("stock-a", 400, currency="USD"), holding("stock-b", 400, currency="USD"),
        holding("cash", 200, currency="USD", holding_category="cash_and_settlement"),
    ]), instrument_details=details, fx_payload=fx)
    assert resolved == ["fx-usd-cny"]
    assert result["observation_count"] == 38
    assert all(row["observation_count"] == 38 for row in result["rows"])
    result["rows"][0]["fx_instrument_ids"].clear()
    assert result["rows"][1]["fx_instrument_ids"] == ["fx-usd-cny"]


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


def verified_calendar_days(monkeypatch):
    monkeypatch.setattr(service, "market_calendar_sessions", lambda _calendar, start, end:
                        tuple(start + timedelta(days=i) for i in range((end - start).days + 1)))
    return {"source_settings": {"expected_frequency": "daily", "market_calendar": "TEST"}}


def test_short_history_is_not_reported_as_a_full_requested_year_window(monkeypatch):
    detail = verified_calendar_days(monkeypatch)
    source = workspace([holding(value=1000, returns=points([-.01] * 40, start=AS_OF - timedelta(days=40)))])
    for lookback in [365, 1095, 1825]:
        result = service.project_portfolio_tail_risk(source, lookback_days=lookback, instrument_details={"a": detail})
        assert result["status"] == "available" and result["coverage_status"] == "complete"
        assert result["var_amount"] == 10
        assert result["history_coverage_status"] == "partial"
        assert result["observation_count"] == 40
        assert result["expected_common_observation_count"] == lookback
        assert result["uncovered_leading_observation_count"] == lookback - 40
        assert result["missing_internal_observation_count"] == 0
        assert result["uncovered_trailing_observation_count"] == 0
        assert result["actual_history_days"] == 40
        assert result["history_span_fraction"] == pytest.approx(40 / lookback)
        assert result["sources"][0]["history_coverage_status"] == "partial"
        assert "requested_history_partially_covered" in result["limitations"]
    complete = service.project_portfolio_tail_risk(source, lookback_days=40, instrument_details={"a": detail})
    assert complete["history_coverage_status"] == "complete"
    assert complete["uncovered_observation_count"] == 0


def test_history_coverage_separates_leading_internal_and_trailing_absence(monkeypatch):
    detail = verified_calendar_days(monkeypatch)
    sample = points([-.01] * 40, start=AS_OF - timedelta(days=50))
    del sample[20]
    result = service.project_portfolio_tail_risk(workspace([holding(returns=sample)]),
                                               lookback_days=60, instrument_details={"a": detail})
    assert result["observation_count"] == 39
    assert result["expected_common_observation_count"] == 60
    assert result["uncovered_observation_count"] == 21
    assert result["uncovered_leading_observation_count"] == 10
    assert result["missing_internal_observation_count"] == 1
    assert result["uncovered_trailing_observation_count"] == 10


def test_coverage_uses_common_market_sessions_without_inventing_holiday_gaps(monkeypatch):
    calendars = {
        "A": [date(2026, 9, day) for day in [1, 2, 3, 4, 7, 8]],
        "B": [date(2026, 9, day) for day in [1, 2, 4, 7, 8]],
    }
    monkeypatch.setattr(service, "market_calendar_sessions", lambda name, *_: tuple(calendars[name]))
    rows, details = [], {}
    for iid, sessions in calendars.items():
        sample = [{"start_date": left, "date": right, "value": -.01} for left, right in zip(sessions, sessions[1:])]
        rows.append(holding(iid, returns=sample))
        details[iid] = {"source_settings": {"expected_frequency": "daily", "market_calendar": iid}}
    result = service.project_portfolio_tail_risk(workspace(rows), confidence=.5, lookback_days=7, instrument_details=details)
    assert result["observation_count"] == result["expected_common_observation_count"] == 3
    assert result["history_coverage_status"] == "complete"
    assert result["uncovered_observation_count"] == 0


def test_security_fx_alignment_losses_are_disclosed_even_for_one_security(monkeypatch):
    detail = verified_calendar_days(monkeypatch)
    details, fx = fx_inputs(missing_day=20)
    details["a"] = detail
    details["fx-usd-cny"]["source_settings"] = detail["source_settings"]
    result = service.project_portfolio_tail_risk(workspace([holding(value=1000, currency="USD")], as_of=date(2026, 7, 11)),
                                               lookback_days=40, instrument_details=details, fx_payload=fx)
    assert result["observation_count"] == 38
    assert result["var_amount"] == pytest.approx(.1)
    assert result["history_coverage_status"] == "partial"
    assert result["missing_internal_observation_count"] == 2
    assert result["rows"][0]["unmatched_fx_period_count"] == 2
    assert result["rows"][0]["fx_rejected_period_count"] == 1
    assert result["rows"][0]["first_scenario_start_date"] == "2026-06-01"
    assert result["rows"][0]["last_scenario_end_date"] == "2026-07-11"
    assert "unmatched_security_fx_periods_removed" in result["limitations"]
    assert "unverified_fx_intervals_removed" in result["limitations"]


@pytest.mark.parametrize(("left_calendar", "right_calendar", "explicit_calendar", "expected_status", "expected_count"), [
    ("24/5", "24/5", None, "complete", 3),
    ("24/7", "24/7", None, "complete", 5),
    ("24/5", "24/7", None, "unverified", 2),
    ("24/7", "24/5", None, "unverified", 2),
    ("24/5", "24/7", "24/5", "complete", 3),
])
def test_fx_pivot_uses_both_resolved_calendars_not_just_equal_source_settings(
    left_calendar, right_calendar, explicit_calendar, expected_status, expected_count,
):
    start, end = date(2026, 9, 4), date(2026, 9, 9)
    details = {}
    for iid, calendar, currency, initial, direction in [
        ("fx-usd-eur", left_calendar, "EUR", .9, -1), ("fx-usd-cny", right_calendar, "CNY", 7.0, 1),
    ]:
        sessions = service.market_calendar_sessions(explicit_calendar or calendar, start, end)
        detail = _fx_detail(iid, currency, [(day.isoformat(), initial * (1.01 ** (direction * i))) for i, day in enumerate(sessions)])
        detail["exchange_code"] = calendar
        detail["source_settings"] = {"expected_frequency": "daily"}
        # Provider settings can differ without changing a proven daily horizon;
        # conversely identical settings cannot hide different fallback calendars.
        if left_calendar == right_calendar:
            detail["source_settings"]["provider_marker"] = iid
        if explicit_calendar:
            detail["source_settings"]["market_calendar"] = explicit_calendar
        details[iid] = detail
    fx = {"rates": [
        {"source_kind": "direct", "base_currency": "USD", "quote_currency": "EUR", "instrument_id": "fx-usd-eur"},
        {"source_kind": "direct", "base_currency": "USD", "quote_currency": "CNY", "instrument_id": "fx-usd-cny"},
    ]}
    result = service.project_portfolio_tail_risk(
        workspace([holding(value=1000, currency="EUR", holding_category="cash_and_settlement")], as_of=end),
        confidence=.5, lookback_days=5, instrument_details=details, fx_payload=fx,
    )
    assert result["status"] == "available"
    assert result["history_coverage_status"] == expected_status
    assert result["observation_count"] == expected_count
    assert result["var_amount"] == pytest.approx(-20.1)
    assert result["rows"][0]["fx_instrument_ids"] == ["fx-usd-cny", "fx-usd-eur"]
    if expected_status == "unverified":
        # Friday–Monday is one 24/5 session but three 24/7 sessions. It cannot
        # enter the daily sample; Tuesday/Wednesday boundaries remain usable.
        assert result["first_scenario_start_date"] == "2026-09-07"
        assert result["expected_common_observation_count"] is None
        assert result["rows"][0]["fx_rejected_period_count"] == 1
        assert "requested_history_calendar_unverified" in result["limitations"]
        assert "unverified_fx_intervals_removed" in result["limitations"]
    else:
        assert result["first_scenario_start_date"] == "2026-09-04"
        assert result["expected_common_observation_count"] == expected_count
        assert result["rows"][0]["fx_rejected_period_count"] == 0


def test_unknown_calendar_does_not_claim_a_known_expected_history_count():
    result = service.project_portfolio_tail_risk(workspace([holding()]))
    assert result["status"] == "available"
    assert result["history_coverage_status"] == "unverified"
    assert result["expected_common_observation_count"] is None
    assert result["uncovered_observation_count"] is None
    assert "requested_history_calendar_unverified" in result["limitations"]


def test_unmodeled_derivative_keeps_its_contract_name_in_coverage():
    result = service.project_portfolio_tail_risk(workspace([holding(), {
        "derivative_contract_id": "fcn-1", "derivative_contract": {"contract_name": "My FCN"},
        "market_value_base": 200, "holding_category": "derivatives",
    }]))
    assert result["rows"][1]["name"] == "My FCN"
    assert result["rows"][1]["reason"] == "derivative_fair_value_unmodeled"
