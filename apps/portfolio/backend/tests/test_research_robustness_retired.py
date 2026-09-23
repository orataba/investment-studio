from copy import deepcopy
from datetime import date

from portfolio_app.db.models import ResearchRunRecordModel, ResearchSettingsRecordModel
from portfolio_app.db.session import get_session_factory


PORTFOLIO_ID = "investment-studio"
LEGACY_SCENARIOS = [{
    "scenario_id": "saved-friction", "label": "Saved friction",
    "cash_yield_annual": 0.0, "commission_bps": 4.0, "tax_bps": 20.0,
    "slippage_bps": 10.0, "implementation_delay_days": 2,
}]


def test_current_settings_ignore_retired_scenarios_and_preserve_base_execution_costs(client):
    initial = client.get(f"/api/portfolios/{PORTFOLIO_ID}/research/workbench")
    assert initial.status_code == 200
    assert "backtest_robustness_scenarios" not in initial.json()["settings"]
    with get_session_factory()() as session:
        record = session.get(ResearchSettingsRecordModel, PORTFOLIO_ID)
        assert record.backtest_robustness_scenarios_json is None
        record.backtest_robustness_scenarios_json = deepcopy(LEGACY_SCENARIOS)
        session.commit()

    saved = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "as_of_mode": "dynamic", "lookback_days": 30,
        "backtest_cash_yield_annual": 0.03, "backtest_commission_bps": 3,
        "backtest_tax_bps": 12, "backtest_slippage_bps": 7,
        "backtest_implementation_delay_days": 2,
    })
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert "backtest_robustness_scenarios" not in payload
    assert payload["backtest_cash_yield_annual"] == 0.03
    assert payload["backtest_commission_bps"] == 3
    assert payload["backtest_tax_bps"] == 12
    assert payload["backtest_slippage_bps"] == 7
    assert payload["backtest_implementation_delay_days"] == 2
    with get_session_factory()() as session:
        assert session.get(ResearchSettingsRecordModel, PORTFOLIO_ID).backtest_robustness_scenarios_json == LEGACY_SCENARIOS


def test_saved_robustness_results_remain_readable_without_rewriting_archive(client):
    request = {"backtest_robustness_scenarios": deepcopy(LEGACY_SCENARIOS)}
    saved_result = {**LEGACY_SCENARIOS[0], "total_cost": 0.012, "warnings": []}
    detail = {"backtest": {"robustness_results": [saved_result]}}
    with get_session_factory()() as session:
        session.add(ResearchRunRecordModel(
            research_run_id="saved-scenario-run", portfolio_id=PORTFOLIO_ID,
            job_type="target_weight_solve", status="completed",
            requested_at="2026-04-15T10:00:00Z", as_of_date=date(2026, 4, 15),
            lookback_days=30, detail_json=deepcopy(detail),
            request_payload_json=deepcopy(request), artifacts_json=[],
        ))
        session.commit()

    response = client.get(f"/api/portfolios/{PORTFOLIO_ID}/research/runs/saved-scenario-run")
    assert response.status_code == 200, response.text
    archived = response.json()["detail"]["backtest"]["robustness_results"]
    assert len(archived) == 1
    assert all(archived[0][key] == value for key, value in saved_result.items())
    with get_session_factory()() as session:
        stored = session.get(ResearchRunRecordModel, "saved-scenario-run")
        assert stored.detail_json == detail
        assert stored.request_payload_json == request
