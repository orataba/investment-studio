from copy import deepcopy
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from portfolio_app.services import research_solver as solver
from tests.research_backtest_fixtures import initial_book, stub_inception_statement
from tests.test_research_review_regressions import risk_budget_state


def run_history(monkeypatch, *, state=None, inception="2026-01-02", securities=None,
                cash=0.0, lookback=90, scope=None, cash_yield=0.0, min_observations=15):
    state = state or risk_budget_state()
    statement = stub_inception_statement(
        monkeypatch, solver, day=inception,
        securities={"a": 60.0, "b": 40.0} if securities is None else securities,
        cash=cash,
    )
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"inception_date": inception, "base_currency": "CNY"})
    configuration = {
        "taxonomy_nodes": [{"taxonomy_node_id": key, "status": "active"} for key in state.node_by_id],
        "taxonomy_assignments": [dict(row, taxonomy_node_id=key, status="active") for key, rows in state.direct_assignments_by_node.items() for row in rows],
    }
    result = solver.build_current_target_backtest(
        "review", planning_taxonomy_id="review-taxonomy", comparator_taxonomy_node_id=scope,
        as_of_date=state.as_of_date, target_configuration=configuration, lookback_days=lookback,
        capital_mode="unit_notional", gross_exposure=None, target_volatility=None, max_gross_exposure=None,
        cash_yield_annual=cash_yield, commission_bps=2.0, tax_bps=10.0, slippage_bps=0.0,
        risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": "abs",
                           "parameters": {"min_observations": min_observations}}, _state=state,
    )["backtest"]
    return result, state, statement


def test_risk_warmup_keeps_actual_initial_holding_returns_and_no_initial_acquisition_cost(monkeypatch):
    result, state, _ = run_history(monkeypatch, securities={"a": 60.0, "b": 20.0}, cash=20.0)
    first = result["execution_records"][0]
    assert first["actual_execution_date"] > "2026-02-01"
    point = next(row for row in result["points"] if row["date"] == "2026-01-05")
    expected = 0.2
    for key, weight in [("a", 0.6), ("b", 0.2)]:
        observations = dict((day, value) for day, value, _ in solver._selected_price_points(state.instrument_detail_cache[key], end_date=state.as_of_date))
        expected += weight * observations[date(2026, 1, 5)] / observations[date(2026, 1, 2)]
    assert point["value"] == pytest.approx(expected)
    assert result["points"][0] == {"date": "2026-01-02", "value": 1.0, "is_start_anchor": True}
    initial_cash = next(row for row in result["top_sleeve_weight_points"][0]["sleeves"] if row["top_sleeve_id"] == "__cash__")
    assert initial_cash["value"] == pytest.approx(0.2)
    assert all(row["execution_cost_contribution"] == 0.0 for row in result["contribution_reconciliation_points"] if row["date"] < first["actual_execution_date"])


def test_no_eligible_rebalance_still_returns_actual_initial_holdings_path(monkeypatch):
    state = replace(risk_budget_state(), as_of_date=date(2026, 1, 20))
    result, _, _ = run_history(monkeypatch, state=state, lookback=90)
    assert result["point_in_time_coverage"]["decision_count"] == 0
    assert result["execution_records"] == []
    assert result["points"][-1]["date"] == "2026-01-20"
    assert result["points"][-1]["value"] != pytest.approx(1.0)
    assert result["total_cost"] == 0.0
    assert result["point_in_time_coverage"]["status"] == "partial"


def test_initial_security_outside_current_targets_is_held_then_sold_at_first_execution(monkeypatch):
    state = risk_budget_state()
    state.instrument_detail_cache["retired"] = {**deepcopy(state.instrument_detail_cache["a"]), "instrument_id": "retired"}
    result, _, _ = run_history(monkeypatch, state=state, securities={"retired": 100.0})
    assert result["initial_state"]["securities"][0]["instrument_id"] == "retired"
    assert result["points"][1]["value"] != pytest.approx(1.0)
    first = result["execution_records"][0]
    assert first["risky_sell_turnover"] == pytest.approx(1.0)
    assert first["tax_cost"] > 0
    assert {row["instrument_id"] for row in first["target_weights"]} == {"a", "b"}


def test_actual_initial_cash_has_daily_yield_even_without_any_eligible_decision(monkeypatch):
    state = replace(risk_budget_state(), as_of_date=date(2026, 2, 3))
    result, _, _ = run_history(monkeypatch, state=state, securities={}, cash=100.0, lookback=730, cash_yield=0.1, min_observations=200)
    assert not result["execution_records"]
    assert [row["date"] for row in result["points"]] == [d.date().isoformat() for d in pd.date_range("2026-01-02", "2026-02-03")]
    assert result["points"][-1]["value"] == pytest.approx(1.1 ** (32 / 365.25))


def test_selected_scope_uses_actual_initial_scoped_capital_not_portfolio_cash(monkeypatch):
    result, _, _ = run_history(monkeypatch, scope="a", securities={"a": 30.0, "b": 50.0}, cash=20.0)
    initial = result["initial_state"]
    assert initial["portfolio_nav_base"] == 100.0
    assert initial["scope_nav_base"] == 30.0
    assert initial["cash_value_base"] == 0.0
    assert initial["derivative_value_base"] == 0.0
    assert [row["instrument_id"] for row in initial["securities"]] == ["a"]
    assert initial["securities"][0]["initial_weight"] == 1.0


def test_empty_initial_scope_is_unavailable_instead_of_using_portfolio_cash(monkeypatch):
    result, _, _ = run_history(monkeypatch, scope="a", securities={}, cash=100.0)
    assert result["initial_state"]["status"] == "unavailable"
    assert "no positive actual inception holdings" in result["initial_state"]["unavailable_reason"]
    assert result["points"] == []
    assert result["execution_records"] == []


def test_missing_initial_nav_has_no_cash_or_later_book_fallback(monkeypatch):
    state = risk_budget_state()
    statement = stub_inception_statement(monkeypatch, solver)
    statement["total_nav_base"] = None
    with pytest.raises(ValueError, match="complete security, cash"):
        solver._initial_backtest_state(state, portfolio={}, inception_date=date(2026, 1, 2), scope_node_id=None)


def test_signed_initial_option_liability_is_counted_once(monkeypatch):
    state = risk_budget_state()
    statement = stub_inception_statement(monkeypatch, solver, day="2026-01-02", securities={"a": 80.0}, cash=30.0, derivative=-10.0)
    statement["positions"][-1]["derivative_contract"] = {"contract_type": "option"}
    statement["derivative_liability_base"] = 10.0
    initial = solver._initial_backtest_state(state, portfolio={}, inception_date=date(2026, 1, 2), scope_node_id=None)
    assert initial["portfolio_nav_base"] == 100.0
    assert initial["derivative_value_base"] == -10.0
    assert initial["cash_value_base"] == 30.0


def test_adjusted_history_cannot_borrow_raw_close_for_inception_anchor(monkeypatch):
    state = replace(risk_budget_state(), as_of_date=date(2026, 1, 7))
    state.instrument_detail_cache["a"] = {
        "instrument_id": "a", "instrument_type": "equity", "currency": "CNY",
        "quote_selection_policy": {"total_return": ["adjusted_close", "close"]},
        "market_data": [
            {"metric_family": "price", "quote_basis": basis, "as_of_date": day, "value": value,
             "currency": "CNY", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for basis, day, value in [("close", "2026-01-02", "100"), ("adjusted_close", "2026-01-05", "50"), ("adjusted_close", "2026-01-06", "51"), ("adjusted_close", "2026-01-07", "52")]
        ],
    }
    result, _, _ = run_history(monkeypatch, state=state, securities={"a": 100.0})
    assert result["points"] == [{"date": "2026-01-02", "value": 1.0, "is_start_anchor": True}]
    assert "inception-anchored return history" in result["point_in_time_coverage"]["unavailable_reason"]


def test_missing_expected_exchange_session_stops_reliable_prefix(monkeypatch):
    state = replace(risk_budget_state(), as_of_date=date(2026, 1, 8))
    detail = state.instrument_detail_cache["a"]
    detail["exchange_code"] = "XNYS"
    detail["market_data"] = [row for row in detail["market_data"] if row["as_of_date"] != "2026-01-05"]
    result, _, _ = run_history(monkeypatch, state=state, securities={"a": 100.0})
    assert len(result["points"]) == 1
    assert "2026-01-05 holding valuation is unavailable" in result["point_in_time_coverage"]["unavailable_reason"]


@pytest.mark.parametrize("quote_day, inception, available", [("2026-01-02", "2026-01-04", True), ("2026-01-02", "2026-01-05", False)])
def test_initial_book_carries_quote_only_across_confirmed_market_closure(monkeypatch, quote_day, inception, available):
    state = risk_budget_state()
    state.instrument_detail_cache["a"]["exchange_code"] = "XNYS"
    statement = stub_inception_statement(monkeypatch, solver, day=quote_day, securities={"a": 100.0}, cash=0.0)
    if available:
        initial = solver._initial_backtest_state(state, portfolio={}, inception_date=date.fromisoformat(inception), scope_node_id=None)
        assert initial["status"] == "available"
    else:
        with pytest.raises(ValueError, match="stale quote"):
            solver._initial_backtest_state(state, portfolio={}, inception_date=date.fromisoformat(inception), scope_node_id=None)


def test_explicit_invalid_held_return_cannot_be_skipped_and_restarted():
    result = solver._replay_backtest_decisions(
        [], initial_state=initial_book("2026-01-02", securities={"a": 100.0}, cash=0.0),
        returns_by_instrument={"a": pd.Series([0.1, float("nan"), 0.2], index=[date(2026, 1, d) for d in (3, 4, 5)])},
        as_of_date=date(2026, 1, 5), cash_yield_annual=0.0, commission_bps=0.0,
        tax_bps=0.0, slippage_bps=0.0, implementation_delay_days=1,
    )
    assert [row["date"] for row in result["points"]] == ["2026-01-02", "2026-01-03"]
    assert result["points"][-1]["value"] == pytest.approx(1.1)
    assert "later observations do not restart" in result["valuation_unavailable_reason"]


def test_invalid_target_observation_cannot_be_an_execution_price():
    result = solver._replay_backtest_decisions(
        [{"decision_date": "2026-01-02", "target_weights": [{"instrument_id": "a", "target_weight": 1.0}]}],
        initial_state=initial_book("2026-01-02"),
        returns_by_instrument={"a": pd.Series([float("nan"), 0.1], index=[date(2026, 1, 3), date(2026, 1, 4)])},
        as_of_date=date(2026, 1, 4), cash_yield_annual=0.0, commission_bps=0.0,
        tax_bps=0.0, slippage_bps=0.0, implementation_delay_days=1,
    )
    assert result["execution_records"][0]["actual_execution_date"] == "2026-01-04"
    assert result["points"][-1]["value"] == 1.0


def test_inception_anchor_uses_inception_fx_even_when_asset_market_was_closed(monkeypatch):
    state = replace(risk_budget_state(), as_of_date=date(2026, 1, 5))
    detail = state.instrument_detail_cache["a"]
    detail["exchange_code"] = "XNYS"
    detail["currency"] = "USD"
    detail["market_data"] = [
        {**detail["market_data"][0], "as_of_date": day, "value": 100.0, "currency": "USD"}
        for day in ("2026-01-02", "2026-01-05")
    ]
    rates = {date(2026, 1, 2): 7.0, date(2026, 1, 4): 7.5, date(2026, 1, 5): 8.0}
    def convert(state, *, point_date, value, point_currency, require_fresh_fx=False):
        return value * rates[point_date] if point_currency == "USD" else value
    monkeypatch.setattr(solver, "_convert_price_to_base", convert)
    result, _, _ = run_history(monkeypatch, state=state, inception="2026-01-04", securities={"a": 100.0})
    assert result["points"][-1]["value"] == pytest.approx(8.0 / 7.5)


def test_derivative_lifecycle_without_shared_quote_does_not_publish_stale_security_nav():
    result = solver._replay_backtest_decisions(
        [], initial_state=initial_book("2026-01-02", securities={"a": 50.0}, cash=50.0),
        returns_by_instrument={"a": pd.Series([0.1, 0.1], index=[date(2026, 1, 3), date(2026, 1, 5)])},
        derivative_capital_events=[{"effective_date": "2026-01-04", "target_value": 0.1}],
        as_of_date=date(2026, 1, 5), cash_yield_annual=0.0, commission_bps=0.0,
        tax_bps=0.0, slippage_bps=0.0, implementation_delay_days=1,
    )
    assert [row["date"] for row in result["points"]] == ["2026-01-02", "2026-01-03", "2026-01-05"]
    assert result["points"][-1]["value"] == pytest.approx(1.105)


@pytest.mark.parametrize("missing_fx", [False, True])
def test_held_security_fx_gap_stops_prefix_without_restarting(monkeypatch, missing_fx):
    state = replace(risk_budget_state(), as_of_date=date(2026, 1, 8))
    detail = state.instrument_detail_cache["a"]
    detail["currency"] = "USD"
    for point in detail["market_data"]:
        point["currency"] = "USD"
    def resolve_fx(*, as_of_date, **kwargs):
        if as_of_date == date(2026, 1, 6):
            return None if missing_fx else {"rate": 7.0, "stale": True}
        return {"rate": 7.0, "stale": False}
    monkeypatch.setattr(solver.valuation_fx, "resolve_fx_rate_on", resolve_fx)
    result, _, _ = run_history(monkeypatch, state=state, securities={"a": 100.0})
    assert result["points"][-1]["date"] == "2026-01-05"
    assert "2026-01-06 holding valuation is unavailable" in result["point_in_time_coverage"]["unavailable_reason"]
    assert result["point_in_time_coverage"]["status"] == "partial"
