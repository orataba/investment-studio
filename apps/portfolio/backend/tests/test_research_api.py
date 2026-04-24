from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services.research_backtest import _selected_price_points

def _create_planning_taxonomy(client, *, root_default_target_dimension: str = "weight") -> tuple[str, dict[str, str]]:
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Research Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
            "root_default_target_dimension": root_default_target_dimension,
            "purpose": "Research recursive sleeve test",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_ids: dict[str, str] = {}
    for payload in [
        {"node_name": "Risk Assets", "node_code": "RISK"},
        {"node_name": "Rates", "node_code": "RATES"},
        {"node_name": "Cash Reserve", "node_code": "CASH"},
    ]:
        node_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
            json=payload,
        )
        assert node_response.status_code == 200
        node_ids[payload["node_name"]] = node_response.json()["taxonomy_node_id"]

    for payload in [
        {
            "node_name": "Defensive Equity",
            "node_code": "DEF",
            "parent_taxonomy_node_id": node_ids["Risk Assets"],
            "default_target_dimension": "weight",
        },
        {
            "node_name": "Hong Kong Beta",
            "node_code": "HKB",
            "parent_taxonomy_node_id": node_ids["Risk Assets"],
            "default_target_dimension": "risk_budget",
        },
    ]:
        node_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
            json=payload,
        )
        assert node_response.status_code == 200
        node_ids[payload["node_name"]] = node_response.json()["taxonomy_node_id"]

    for assignment in [
        ("instrument", "equity-us-abbv", "Defensive Equity"),
        ("instrument", "fund-hk-2800", "Hong Kong Beta"),
        ("instrument", "fund-us-agg", "Rates"),
        ("cash_bucket", "cash-usd-main", "Cash Reserve"),
    ]:
        assignment_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
            json={
                "target_scope": assignment[0],
                "target_entity_id": assignment[1],
                "taxonomy_node_id": node_ids[assignment[2]],
            },
        )
        assert assignment_response.status_code == 200

    default_response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert default_response.status_code == 200
    return taxonomy_id, node_ids


def _create_target_sets(client, taxonomy_id: str, node_ids: dict[str, str]) -> None:
    for payload in [
        {
            "target_set_type": "saa",
            "name": "Root SAA",
            "effective_from": "2026-03-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.55,
                    "target_risk_share": 0.6,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.3,
                    "target_risk_share": 0.4,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Cash Reserve"],
                    "target_weight": 0.15,
                    "target_risk_share": 0.0,
                },
            ],
        },
        {
            "target_set_type": "taa",
            "name": "Root TAA",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.6,
                    "target_risk_share": 0.62,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.25,
                    "target_risk_share": 0.38,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Cash Reserve"],
                    "target_weight": 0.15,
                    "target_risk_share": 0.0,
                },
            ],
        },
        {
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "target_set_type": "saa",
            "name": "Risk Assets SAA",
            "effective_from": "2026-03-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Defensive Equity"],
                    "target_weight": 0.58,
                    "target_risk_share": 0.52,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Hong Kong Beta"],
                    "target_weight": 0.42,
                    "target_risk_share": 0.48,
                },
            ],
        },
        {
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "target_set_type": "taa",
            "name": "Risk Assets TAA",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Defensive Equity"],
                    "target_weight": 0.5,
                    "target_risk_share": 0.45,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Hong Kong Beta"],
                    "target_weight": 0.5,
                    "target_risk_share": 0.55,
                },
            ],
        },
    ]:
        target_set_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
            json=payload,
        )
        assert target_set_response.status_code == 200


def test_research_workbench_returns_backtest_defaults(client):
    response = client.get("/api/portfolios/yungu/research/workbench")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "yungu"
    assert payload["settings"]["planning_taxonomy_id"] is None
    assert payload["settings"]["run_template"] == "taxonomy_backtest"
    assert payload["settings"]["target_set_mode"] == "taa_over_saa"
    assert payload["settings"]["target_dimension"] == "scope_default"
    assert payload["settings"]["capital_mode"] == "unit_notional"
    assert payload["settings"]["gross_exposure"] is None
    assert payload["settings"]["target_volatility"] is None
    assert payload["settings"]["max_gross_exposure"] is None
    assert payload["settings"]["rebalance_frequency"] == "monthly"
    assert payload["settings"]["start_date"] is not None
    assert payload["planning_taxonomy_options"] == []
    assert payload["planning_scope_options"] == []
    assert payload["runs"] == []
    assert payload["selected_run"] is None
    assert payload["current_context"]["holdings_count"] == 3
    assert payload["current_context"]["planning_group_count"] == 0


def test_research_series_prefers_total_return_nav_for_funds() -> None:
    detail = {
        "asset_id": "fund-test",
        "asset_type": "fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav", "official_nav"],
            "total_return": ["total_return_nav", "official_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "1.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-14",
                "value": "1.1200",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-15",
                "value": "1.0100",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "1.1350",
                "currency": "USD",
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 1.12),
        ("2026-04-15", 1.135),
    ]


def test_research_series_prefers_adjusted_close_for_equities() -> None:
    detail = {
        "asset_id": "equity-test",
        "asset_type": "equity",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "chart": ["adjusted_close", "close"],
            "total_return": ["adjusted_close", "close"],
        },
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-14",
                "value": "100.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-14",
                "value": "108.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-15",
                "value": "102.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-15",
                "value": "110.5000",
                "currency": "USD",
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 108.0),
        ("2026-04-15", 110.5),
    ]


def test_research_run_creates_recursive_backtest_outputs(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 7,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "rebalance_frequency": "weekly",
            "notes": "Research regression test",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["planning_taxonomy_id"] == taxonomy_id
    assert settings_payload["comparator_taxonomy_node_id"] == node_ids["Risk Assets"]
    assert settings_payload["target_dimension"] == "scope_default"

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert len(workbench_payload["planning_taxonomy_options"]) == 1
    assert any(item["label"] == "Top Level" for item in workbench_payload["planning_scope_options"])
    assert any(item["label"] == "Risk Assets" for item in workbench_payload["planning_scope_options"])
    assert any(item["group_label"] == "Cash Reserve" for item in workbench_payload["current_context"]["planning_groups"])

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()

    run_payload = run_response.json()
    assert run_payload["status"] == "completed"
    assert run_payload["planning_taxonomy_id"] == taxonomy_id
    assert run_payload["run_template"] == "taxonomy_backtest"
    assert run_payload["artifact_count"] == 10
    assert run_payload["detail"]["selected_scope"]["taxonomy_node_id"] == node_ids["Risk Assets"]
    assert run_payload["detail"]["selected_scope"]["label"] == "Risk Assets"
    assert len(run_payload["detail"]["backtest_metrics"]) >= 8
    assert len(run_payload["detail"]["backtest_curve"]) >= 2
    assert len(run_payload["detail"]["weight_schedule"]) >= 2
    assert run_payload["detail"]["weight_schedule"][0]["nav"] is not None
    assert len(run_payload["detail"]["member_summaries"]) == 2
    assert len(run_payload["detail"]["construction_assumptions"]) >= 1
    assert len(run_payload["detail"]["construction_rows"]) == 2
    assert any(item["label"] == "Defensive Equity" for item in run_payload["detail"]["member_summaries"])
    assert any(item["label"] == "Hong Kong Beta" for item in run_payload["detail"]["member_summaries"])
    assert any(item["source_label"] in {"TAA", "SAA", "Fallback Weight", "Fallback Risk Budget"} for item in run_payload["detail"]["construction_rows"])
    assert len(run_payload["detail"]["rebalance_events"]) >= 1
    assert len(run_payload["detail"]["rebalance_suggestions"]) >= 1
    signal_labels = {item["label"] for item in run_payload["detail"]["signals"]}
    assert "Scope Default" in signal_labels
    assert "Solver" in signal_labels

    report_artifact = next(item for item in run_payload["artifacts"] if item["artifact_id"] == "report")
    artifact_response = client.get(
        "/api/portfolios/yungu/research/artifacts/content",
        params={"path": report_artifact["path"]},
    )
    assert artifact_response.status_code == 200
    artifact_payload = artifact_response.json()
    assert artifact_payload["preview_kind"] == "text"
    assert "# Research Run" in artifact_payload["content"]
    assert "Backtest Summary" in artifact_payload["content"]

    selected_workbench_response = client.get(
        "/api/portfolios/yungu/research/workbench",
        params={"selected_run_id": run_payload["research_run_id"]},
    )
    assert selected_workbench_response.status_code == 200
    selected_workbench_payload = selected_workbench_response.json()
    assert selected_workbench_payload["selected_run"]["research_run_id"] == run_payload["research_run_id"]


def test_research_backtest_actuals_keep_cash_until_security_settlement(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 7,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "rebalance_frequency": "weekly",
            "notes": "Delayed settlement actuals regression",
        },
    )
    assert settings_response.status_code == 200

    baseline_workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert baseline_workbench_response.status_code == 200
    baseline_cash_value = next(
        item["end_value_base"]
        for item in baseline_workbench_response.json()["current_context"]["planning_groups"]
        if item["group_label"] == "Cash Reserve"
    )

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "asset_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 206.47,
            "gross_amount": 206.47,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_payload = run_response.json()
    cash_row = next(
        item
        for item in run_payload["detail"]["construction_rows"]
        if item["label"] == "Cash Reserve"
    )
    assert cash_row["current_value_base"] == pytest.approx(baseline_cash_value)


def test_research_scope_default_respects_taxonomy_root_default_dimension(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 7,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "rebalance_frequency": "weekly",
        },
    )
    assert settings_response.status_code == 200

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    top_level_scope = next(
        item for item in workbench_response.json()["planning_scope_options"] if item["taxonomy_node_id"] is None
    )
    assert top_level_scope["default_target_dimension"] == "risk_budget"


def test_deleting_selected_research_taxonomy_clears_settings(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 90,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "rebalance_frequency": "monthly",
        },
    )
    assert settings_response.status_code == 200

    delete_response = client.delete(f"/api/portfolios/yungu/taxonomies/{taxonomy_id}")
    assert delete_response.status_code == 200

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert workbench_payload["default_planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["comparator_taxonomy_node_id"] is None


def test_research_target_volatility_scales_risk_assets_into_cash(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 7,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "weight",
            "capital_mode": "target_volatility",
            "target_volatility": 0.01,
            "max_gross_exposure": 1.0,
            "rebalance_frequency": "weekly",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["capital_mode"] == "target_volatility"
    assert settings_payload["target_volatility"] == pytest.approx(0.01)
    assert settings_payload["max_gross_exposure"] == pytest.approx(1.0)

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_payload = run_response.json()
    signal_map = {item["label"]: item["value"] for item in run_payload["detail"]["signals"]}
    assert signal_map["Capital Mode"] == "Target Volatility"
    assert signal_map["Target Volatility"] == "1.00%"

    cash_row = next(
        item
        for item in run_payload["detail"]["construction_rows"]
        if item["label"] == "Cash Reserve"
    )
    assert cash_row["implementation_weight"] is not None
    assert cash_row["implementation_weight"] > (cash_row["target_weight"] or 0.0)


def test_research_run_rejects_incomplete_full_lookback_for_risk_budget(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "start_date": "2026-03-20",
            "lookback_days": 180,
            "benchmark_mode": "none",
            "run_template": "taxonomy_backtest",
            "target_set_mode": "taa_over_saa",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "rebalance_frequency": "monthly",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400
    assert "full 180-day lookback" in run_response.json()["detail"]
