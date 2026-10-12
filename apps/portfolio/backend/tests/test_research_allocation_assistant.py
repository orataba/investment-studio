"""The assistant invokes the saved production mandate, never a backtest or trade."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from portfolio_app.services import research


@pytest.fixture
def allocation(monkeypatch):
    settings = SimpleNamespace(planning_taxonomy_id="allocation", comparator_taxonomy_node_id="root",
        as_of_mode="latest", as_of_date=None, lookback_days=90, missing_return_policy="strict",
        capital_mode="unit_notional", gross_exposure=1.0, target_volatility=None,
        max_gross_exposure=None, frozen_taxonomy_node_ids_json=["cash"], top_sleeve_weight_bounds_json=[])
    state = {"settings": settings, "fingerprint": "unchanged", "calls": []}
    @contextmanager
    def session():
        yield SimpleNamespace(get=lambda *args: state["settings"])
    monkeypatch.setattr(research, "get_session_factory", lambda: session)
    monkeypatch.setattr(research, "get_portfolio", lambda pid: {"portfolio_id": pid})
    monkeypatch.setattr(research, "ensure_portfolio_daily_snapshots", lambda pid: None)
    monkeypatch.setattr(research, "_validate_planning_taxonomy", lambda *args: "allocation")
    monkeypatch.setattr(research, "_validate_research_scope", lambda *args, **kwargs: "root")
    monkeypatch.setattr(research, "capture_current_target_configuration", lambda *args, **kwargs: {"saved": True})
    monkeypatch.setattr(research, "_planning_state_fingerprint", lambda *args, **kwargs: state["fingerprint"])
    monkeypatch.setattr(research, "get_portfolio_risk_policy", lambda pid: {"lookback_days": 120, "missing_return_policy": "strict"})
    def solve(pid, **kwargs):
        state["calls"].append((pid, kwargs))
        if state.get("change_settings"):
            settings.target_volatility = 0.15
        if state.get("change_inputs"):
            state["fingerprint"] = "new-transactions-or-quotes"
        return {"weights": [{"instrument_id": "xlk", "weight": 0.4}]}
    monkeypatch.setattr(research, "solve_current_target_weights", solve)
    def no_backtest(*args, **kwargs):
        raise AssertionError("Assistant calculation must not run a historical backtest")
    monkeypatch.setattr(research, "build_current_target_backtest", no_backtest)
    return state


def test_assistant_allocation_uses_saved_constraints_and_production_risk_policy(allocation):
    result = research.calculate_allocation("portfolio", as_of_date=date(2026, 10, 12))
    assert result["solution"]["weights"][0]["weight"] == 0.4
    pid, inputs = allocation["calls"][0]
    assert pid == "portfolio"
    assert inputs["frozen_taxonomy_node_ids"] == ["cash"]
    assert inputs["lookback_days"] == 120
    assert inputs["target_configuration"] == {"saved": True}
    assert result["as_of_date"] == "2026-10-12"


@pytest.mark.parametrize("change", ["change_settings", "change_inputs"])
def test_concurrent_settings_or_financial_change_invalidates_result(allocation, change):
    allocation[change] = True
    with pytest.raises(ValueError, match="计算期间"):
        research.calculate_allocation("portfolio", as_of_date=date(2026, 10, 12))


def test_no_saved_mandate_does_not_invent_one(allocation):
    allocation["settings"] = None
    with pytest.raises(ValueError, match="保存目标"):
        research.calculate_allocation("portfolio", as_of_date=date(2026, 10, 12))
    assert not allocation["calls"]
