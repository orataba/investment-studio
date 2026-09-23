"""Frozen result hierarchy, globally derived targets and signed capital weights."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from portfolio_app.api.research_solution_contracts import ResearchSolutionTreeRecord
from portfolio_app.services.research_solution_tree import build_research_solution_tree
from portfolio_app.services import research_solver as solver
from tests.test_global_research_solver import market, tree


@pytest.fixture(autouse=True)
def isolated_portfolio_store():
    """All inputs below are synthetic; no business database or market provider."""
    yield


def saved_result():
    config = {
        "snapshot_schema_version": 3, "captured_at": "2026-06-30T12:00:00Z", "base_currency": "USD",
        "taxonomy": {"taxonomy_id": "t", "name": "Saved taxonomy", "root_allocation_basis": "risk_budget"},
        "taxonomy_nodes": [
            {"taxonomy_node_id": key, "node_name": label, "parent_taxonomy_node_id": parent, "allocation_basis": basis}
            for key, label, parent, basis in [("a", "A", None, "weight"), ("a1", "Nested", "a", "risk_budget"), ("a0", "Empty", "a", "weight"), ("b", "B", None, "weight")]
        ],
        "taxonomy_assignments": [{"target_scope": "instrument", "target_entity_id": key, "taxonomy_node_id": node} for key, node in [("x", "a1"), ("y", "a1"), ("z", "b")]],
        "target_sets": [], "target_set_lines": [],
    }
    for scope, values in [(None, [("taxonomy_node", "a", .6), ("taxonomy_node", "b", .4)]), ("a", [("taxonomy_node", "a1", 1), ("taxonomy_node", "a0", 0)]), ("a1", [("instrument", "x", .25), ("instrument", "y", .75)])]:
        set_id = scope or "root"
        config["target_sets"].append({"target_set_id": set_id, "comparator_taxonomy_node_id": scope, "target_set_type": "saa"})
        config["target_set_lines"].extend({"target_set_id": set_id, "target_member_type": kind, "target_member_id": key, "target_value": value} for kind, key, value in values)
    groups = []
    for sleeve, values in [("a", [("x", 100, 120, .15), ("y", 200, 280, .45)]), ("b", [("z", 400, 450, .4)]), ("__cash__", [("__cash__", 250, 100, None)]), ("__derivatives__", [("__derivatives__", 50, 49, None)])]:
        rows = []
        for key, current, target, rc in values:
            kind = "cash_bucket" if key == "__cash__" else "derivative_bucket" if key == "__derivatives__" else "instrument"
            rows.append({"member_type": kind, "member_id": key, "label": key, "top_sleeve_id": sleeve,
                         "current_value_base": current, "current_weight": current / 1000, "target_value_base": target, "solved_weight": target / 1000,
                         "target_risk_share": rc, "forward_risk_contribution": rc,
                         "trade_constraint": "no_trade" if kind == "derivative_bucket" else "adjustable",
                         "risk_model_status": "modeled" if kind == "instrument" else "excluded"})
        groups.append({"top_sleeve_id": sleeve, "top_sleeve_label": sleeve, "rows": rows})
    request = {"as_of_date": "2026-06-30", "target_configuration_snapshot": config}
    detail = {"scope": {"taxonomy_node_id": None}, "risk_attribution_scope": "portfolio", "solved_result_groups": groups}
    valuation = {"portfolio_nav": 1000}
    return detail, request, valuation


def test_frozen_full_hierarchy_has_no_double_counting_and_uses_signed_carrying_amount_for_weights():
    detail, request, valuation = saved_result()
    # A duplicate catalogue leaf must not duplicate its cash or risk subtotal.
    detail["solved_result_groups"].append(deepcopy(detail["solved_result_groups"][0]))
    result = build_research_solution_tree(detail, request, valuation=valuation)
    ResearchSolutionTreeRecord.model_validate(result)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert rows["instrument:x"]["path"] == ["Saved taxonomy", "A", "Nested", "x"]
    assert rows["node:a"]["current_value_base"] == 300
    assert rows["root"]["current_value_base"] == 1000
    assert rows["root"]["target_value_base"] == 1000
    assert rows["root"]["target_weight"] == pytest.approx(1)
    assert rows["root"]["current_weight"] == pytest.approx(1)
    assert rows["instrument:x"]["current_weight"] == .1
    assert rows["root"]["solved_risk_share"] == pytest.approx(1)
    assert rows["node:a0"]["solved_risk_share"] == 0
    # A Weight split is not a risk-budget vector, including a 100/0 allocation.
    assert rows["node:a1"]["target_risk_share"] is None
    assert rows["instrument:x"]["target_risk_share"] is None
    assert rows["instrument:y"]["target_risk_share"] is None
    assert rows["node:a"]["target_risk_share"] == .6
    assert rows["derivative_bucket:__derivatives__"]["rebalance_value_base"] == 0
    assert rows["derivative_bucket:__derivatives__"]["target_value_base"] == 50
    assert rows["cash_bucket:__cash__"]["current_weight"] == .25
    assert rows["derivative_bucket:__derivatives__"]["current_weight"] == .05
    assert "current_exposure_weight" not in rows["root"]


def test_risk_targets_multiply_from_portfolio_root_instead_of_using_saved_local_leaf_targets():
    detail, request, valuation = saved_result()
    request["target_configuration_snapshot"]["taxonomy_nodes"][0]["allocation_basis"] = "risk_budget"
    detail["solved_result_groups"][0]["rows"][0]["target_risk_share"] = 1
    result = build_research_solution_tree(detail, request, valuation=valuation)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert rows["node:a"]["target_risk_share"] == .6
    assert rows["node:a1"]["target_risk_share"] == .6
    assert rows["instrument:x"]["target_risk_share"] == pytest.approx(.6 * .25)
    assert rows["instrument:y"]["target_risk_share"] == pytest.approx(.6 * .75)


def test_old_snapshot_does_not_reinterpret_local_targets_with_the_current_resolver():
    detail, request, _ = saved_result()
    request["target_configuration_snapshot"]["snapshot_schema_version"] = 2
    detail["solved_result_groups"][0]["target_risk_share"] = .7
    result = build_research_solution_tree(detail, request)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert result["portfolio_nav"] == 1000
    assert result["capital_weight_basis"] == "portfolio_nav"
    assert rows["node:a"]["target_risk_share"] is None
    assert rows["node:a1"]["target_risk_share"] is None
    assert rows["instrument:x"]["target_weight"] == .12
    assert rows["instrument:x"]["target_risk_share"] is None
    assert rows["root"]["current_value_base"] == 1000


def test_saved_solution_uses_statement_nav_even_when_performance_context_ends_at_an_older_nav():
    from portfolio_app.services.research import _build_current_target_detail
    solution, request, valuation = saved_result()
    solution["solution_valuation"] = valuation
    detail = _build_current_target_detail({"nav": 900, "as_of_date": "2026-06-30"},
        planning_taxonomy_name="Saved taxonomy", settings_payload=request, solution=solution)
    assert detail["solution_tree"]["portfolio_nav"] == 1000
    assert detail["solution_tree"]["rows"][0]["target_weight"] == 1


def test_archive_without_snapshot_only_uses_recorded_groups():
    detail, _, _ = saved_result()
    result = build_research_solution_tree(detail, {"as_of_date": "2026-06-30"})
    assert result["hierarchy_status"] == "recorded_groups_only"
    assert result["base_currency"] is None
    assert next(row for row in result["rows"] if row["row_id"] == "node:a")["current_value_base"] == 300


def test_selected_scope_uses_total_portfolio_nav_for_capital_but_scope_risk_for_rc():
    detail, request, valuation = saved_result()
    request["target_configuration_snapshot"]["taxonomy_nodes"][0]["allocation_basis"] = "risk_budget"
    detail["scope"]["taxonomy_node_id"] = "a1"
    detail["risk_attribution_scope"] = "selected_research_scope"
    detail["solved_result_groups"] = detail["solved_result_groups"][:1]
    for row, rc in zip(detail["solved_result_groups"][0]["rows"], [.25, .75], strict=True):
        row["forward_risk_contribution"] = rc
    result = build_research_solution_tree(detail, request, valuation=valuation)
    root = result["rows"][0]
    assert root["current_value_base"] == 300
    assert root["target_weight"] == .4
    assert root["target_risk_share"] == .6
    assert root["solved_risk_share"] == 1
    assert next(row for row in result["rows"] if row["member_id"] == "x")["target_risk_share"] == .15
    assert all(row["label"] not in {"B", "z", "__cash__"} for row in result["rows"])


@pytest.mark.parametrize("member_type", ["instrument", "taxonomy_node"])
def test_zero_weight_members_and_empty_scopes_do_not_erase_saved_global_sleeve_rc(member_type):
    detail, request, valuation = saved_result()
    zero = {**detail["solved_result_groups"][0]["rows"][0], "member_type": member_type, "member_id": "unused", "label": "Unused",
            "current_value_base": 0, "current_weight": 0, "target_value_base": 0, "solved_weight": 0, "forward_risk_contribution": None}
    detail["solved_result_groups"][0]["rows"].append(zero)
    result = build_research_solution_tree(detail, request, valuation=valuation)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert rows[f"{member_type}:unused"]["solved_risk_share"] == 0
    assert rows["node:a"]["solved_risk_share"] == pytest.approx(.6)
    assert rows["root"]["solved_risk_share"] == pytest.approx(1)
    zero["solved_weight"] = .01
    result = build_research_solution_tree(detail, request, valuation=valuation)
    assert result["rows"][0]["solved_risk_share"] is None


def test_zero_weight_without_a_saved_risk_estimate_is_not_fabricated_as_zero_risk():
    detail, request, valuation = saved_result()
    for group in detail["solved_result_groups"]:
        for row in group["rows"]:
            row["forward_risk_contribution"] = None
            row["solved_weight"] = 0
    result = build_research_solution_tree(detail, request, valuation=valuation)
    assert all(row["solved_risk_share"] is None for row in result["rows"])


@pytest.mark.parametrize("path_source", ["member_path", "scope_event"])
def test_old_direct_risk_members_can_be_proven_but_missing_intermediate_budgets_cannot(path_source):
    detail, _, _ = saved_result()
    detail["member_targets"] = [
        {"member_type": "taxonomy_node", "member_id": key, "member_path": f"Portfolio / {key}", "selected_target_dimension": "risk_budget", "configured_risk_share": value}
        for key, value in [("a", .6), ("b", .4)]
    ]
    detail["leaf_targets"] = [
        {"member_type": "instrument", "member_id": "x", "scope_path": "Portfolio / a / Nested", "selected_target_dimension": "risk_budget", "configured_risk_share": 1},
        {"member_type": "instrument", "member_id": "z", "scope_path": "Portfolio / b", "selected_target_dimension": "risk_budget", "configured_risk_share": 1},
    ]
    if path_source == "scope_event":
        detail["scope_solve_events"] = [
            {"scope_node_id": row["member_id"], "scope_path": row.pop("member_path")}
            for row in detail["member_targets"]
        ]
    result = build_research_solution_tree(detail, {"as_of_date": "2026-06-30"})
    rows = {row["row_id"]: row for row in result["rows"]}
    assert rows["node:a"]["target_risk_share"] == .6
    assert rows["instrument:z"]["target_risk_share"] == .4
    assert rows["instrument:x"]["target_risk_share"] is None


@pytest.mark.parametrize("problem", ["partial_scope", "missing_value", "missing_weight", "unreconciled_weights"])
def test_legacy_nav_recovery_requires_proven_complete_portfolio_capital(problem):
    detail, request, _ = saved_result()
    if problem == "partial_scope":
        detail["scope"]["taxonomy_node_id"] = "a"
    else:
        row = detail["solved_result_groups"][0]["rows"][0]
        row[{"missing_value": "current_value_base", "missing_weight": "current_weight", "unreconciled_weights": "current_weight"}[problem]] = .9 if problem == "unreconciled_weights" else None
    result = build_research_solution_tree(detail, request)
    assert result["portfolio_nav"] is None
    assert all(row["current_weight"] is None for row in result["rows"])
    assert result["capital_weight_basis"] == "saved_scope"


def test_cached_tree_projection_upgrades_without_mutating_the_saved_run():
    from portfolio_app.services.research import _serialize_run_row
    detail, request, _ = saved_result()
    detail["solution_tree"] = {"schema_version": 1, "portfolio_nav": 1000, "rows": [{"solved_risk_share": None}]}
    saved = deepcopy(detail)
    record = SimpleNamespace(planning_taxonomy_id="t", detail_json=detail, request_payload_json=request, artifacts_json=[],
        research_run_id="saved", portfolio_id="p", job_type="portfolio_research", status="completed", requested_at=None,
        started_at=None, finished_at=None, as_of_date=date(2026, 6, 30), lookback_days=90, requested_by=None, headline=None, error_message=None)
    result = _serialize_run_row(record, {"t": "Saved taxonomy"})
    assert result["detail"]["solution_tree"]["schema_version"] == 2
    assert result["detail"]["solution_tree"]["rows"][0]["current_weight"] == 1
    assert result["detail"]["solution_tree"]["rows"][0]["solved_risk_share"] == 1
    assert detail == saved


def test_current_solve_includes_written_option_liability_in_nav_and_keeps_it_frozen(monkeypatch):
    state = tree(root_mode="weight", a_mode="weight")
    market(monkeypatch)
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"portfolio_id": state.portfolio_id})
    monkeypatch.setattr(solver, "list_accounts", lambda _: [])
    monkeypatch.setattr(solver, "list_transactions", lambda _: [])
    monkeypatch.setattr(solver, "list_portfolio_instrument_universe", lambda _: [])
    monkeypatch.setattr(solver, "build_holdings_report", lambda *args, **kwargs: {
        "positions": [*[{"instrument_id": key, "market_value_base": value} for key, value in [("x", 200), ("y", 300), ("z", 300)]],
                      {"holding_kind": "option_obligation", "derivative_contract_id": "written-option", "derivative_contract": {"contract_type": "option"}, "market_value_base": -50}],
        "derivative_liability_base": 50, "open_option_obligation_count": 1, "total_nav_base": 1000})
    monkeypatch.setattr(solver, "build_account_workspace", lambda *args, **kwargs: {"accounts": [{
        "account": {"account_id": "broker"}, "derived_cash_balance_base": 240, "pending_settlement_base": 10}]})
    result = solver.solve_current_target_weights(state.portfolio_id, planning_taxonomy_id=state.planning_taxonomy_id,
        comparator_taxonomy_node_id=None, as_of_date=state.as_of_date, lookback_days=30, capital_mode="unit_notional",
        gross_exposure=None, target_volatility=None, max_gross_exposure=None, _state=state,
        risk_model_config={"covariance_model_id": "sample_covariance", "parameters": {"min_observations": 2}})
    assert result["solution_valuation"]["portfolio_nav"] == 1000
    actual = {row["member_id"]: row for row in result["actual_rows"]}
    assert actual["__derivatives__"]["current_value_base"] == -50
    assert actual["__derivatives__"]["current_weight"] == -.05
    assert actual["__cash__"]["current_value_base"] == 250
    rows = {row["member_id"]: row for group in result["solved_result_groups"] for row in group["rows"]}
    assert sum(row["target_value_base"] for row in rows.values()) == pytest.approx(1000)
    assert rows["__derivatives__"]["target_value_base"] == -50
    assert rows["__derivatives__"]["trade_constraint"] == "no_trade"
    assert sum(rows[key]["target_value_base"] for key in ("x", "y", "z")) == pytest.approx(1050)
