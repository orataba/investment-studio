from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.services import research, research_solver as solver
from tests.test_research_review_regressions import risk_budget_state


def _actual_point(day, twr, *, eligible=True, continuous=True, nav=100.0):
    return {
        "as_of_date": date.fromisoformat(day),
        "cumulative_twr": twr,
        "return_observation_eligible": eligible,
        "return_chain_continuous": continuous,
        "ending_nav": nav,
    }


def test_actual_index_preserves_funded_first_day_and_ignores_external_flows():
    report = {
        "summary": {"start_date": date(2026, 4, 13), "include_start_date_return": True},
        "daily_series": [
            _actual_point("2026-04-13", 0.05, nav=105.0),
            _actual_point("2026-04-14", 0.05, nav=205.0),
            _actual_point("2026-04-15", 0.071, nav=209.1),
        ],
    }

    points = research._research_performance_index_points(report)

    assert points[0] == {"date": "2026-04-12", "value": 1.0, "is_start_anchor": True}
    assert [point["value"] for point in points] == pytest.approx([1.0, 1.05, 1.05, 1.071])
    assert points[-1]["value"] / points[0]["value"] - 1.0 == pytest.approx(0.071)
    assert not any(point["is_start_anchor"] for point in points[1:])


def test_actual_index_preserves_imported_eod_anchor_without_inventing_first_day_return():
    report = {
        "summary": {"start_date": date(2026, 4, 13), "include_start_date_return": False},
        "daily_series": [
            _actual_point("2026-04-13", 0.0, eligible=False),
            _actual_point("2026-04-14", 0.1),
        ],
    }

    assert research._research_performance_index_points(report) == [
        {"date": "2026-04-13", "value": 1.0, "is_start_anchor": True},
        {"date": "2026-04-14", "value": 1.1, "is_start_anchor": False},
    ]


@pytest.mark.parametrize("missing_return", [None, float("nan"), float("inf")])
def test_actual_index_stops_at_gap_even_if_a_later_row_has_a_value(missing_return):
    report = {
        "summary": {"start_date": date(2026, 4, 13), "include_start_date_return": False},
        "daily_series": [
            _actual_point("2026-04-13", 0.0, eligible=False),
            _actual_point("2026-04-14", 0.1),
            _actual_point("2026-04-15", missing_return),
            _actual_point("2026-04-16", 0.3),
        ],
    }

    assert [point["date"] for point in research._research_performance_index_points(report)] == [
        "2026-04-13", "2026-04-14",
    ]


def _historical_simulation(monkeypatch, *, inception, history_start=None, delay=1):
    state = risk_budget_state()
    if history_start is not None:
        details = deepcopy(state.instrument_detail_cache)
        for detail in details.values():
            detail["market_data"] = [point for point in detail["market_data"] if point["as_of_date"] >= history_start]
        state = replace(state, instrument_detail_cache=details)
    configuration = {
        "taxonomy_nodes": [{"taxonomy_node_id": key, "status": "active"} for key in ("a", "b")],
        "taxonomy_assignments": [
            {"taxonomy_node_id": key, "target_scope": "instrument", "target_entity_id": key, "status": "active"}
            for key in ("a", "b")
        ],
    }
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"inception_date": inception})
    monkeypatch.setattr(solver, "list_transactions", lambda _: [])
    return solver.build_current_target_backtest(
        "review",
        planning_taxonomy_id="review-taxonomy",
        comparator_taxonomy_node_id=None,
        as_of_date=state.as_of_date,
        target_configuration=configuration,
        lookback_days=90,
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        cash_yield_annual=0.0,
        commission_bps=2.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=delay,
        risk_model_config={
            "covariance_model_id": "sample_covariance", "contribution_mode": "abs",
            "parameters": {"min_observations": 15},
        },
        _state=state,
    )["backtest"]


def test_backtest_starts_at_inception_and_uses_pre_inception_market_warmup(monkeypatch):
    backtest = _historical_simulation(monkeypatch, inception="2026-05-15")

    assert backtest["requested_start_date"] == "2026-05-15"
    assert backtest["common_history_start_date"] == "2026-01-01"
    assert backtest["point_in_time_coverage"]["first_decision_date"] == "2026-05-15"
    assert backtest["start_date"] == "2026-05-15"
    assert backtest["points"][0]["is_start_anchor"] is True
    assert backtest["execution_records"]
    assert all(row["decision_date"] >= "2026-05-15" for row in backtest["execution_records"])
    assert all(row["actual_execution_date"] >= "2026-05-15" for row in backtest["execution_records"])
    assert backtest["methodology"]["point_in_time_taxonomy"] is False
    assert backtest["methodology"]["target_configuration"] == "current_snapshot"


def test_insufficient_history_postpones_simulation_without_fabricating_inception_returns(monkeypatch):
    backtest = _historical_simulation(monkeypatch, inception="2026-01-02", history_start="2026-03-02")

    assert backtest["requested_start_date"] == "2026-01-02"
    assert backtest["point_in_time_coverage"]["status"] == "partial"
    assert backtest["point_in_time_coverage"]["first_decision_date"] > "2026-03-02"
    assert backtest["start_date"] > "2026-03-02"
    assert backtest["point_in_time_coverage"]["skipped_rebalances"][0]["date"] == "2026-01-02"
    assert not any(point["date"] < backtest["start_date"] for point in backtest["points"])


def test_zero_delay_keeps_marked_cost_anchor_without_pre_inception_trading(monkeypatch):
    backtest = _historical_simulation(monkeypatch, inception="2026-05-15", delay=0)

    assert backtest["points"][0] == {"date": "2026-05-14", "value": 1.0, "is_start_anchor": True}
    assert backtest["execution_records"][0]["actual_execution_date"] == "2026-05-15"
    assert backtest["execution_records"][0]["total_cost"] > 0.0
    assert backtest["points"][1]["value"] < 1.0
    assert all(point["date"] >= "2026-05-15" for point in backtest["points"][1:])
