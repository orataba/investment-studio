"""Frozen result hierarchy, capital accounting and published gross exposure."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from portfolio_app.api.research_solution_contracts import ResearchSolutionTreeRecord
from portfolio_app.services.research_solution_tree import build_research_solution_tree, read_solution_exposures
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
                         "current_value_base": current, "target_value_base": target, "solved_weight": target / 1000,
                         "target_risk_share": rc, "forward_risk_contribution": rc,
                         "trade_constraint": "no_trade" if kind == "derivative_bucket" else "adjustable",
                         "risk_model_status": "modeled" if kind == "instrument" else "excluded"})
        groups.append({"top_sleeve_id": sleeve, "top_sleeve_label": sleeve, "rows": rows})
    request = {"as_of_date": "2026-06-30", "target_configuration_snapshot": config}
    detail = {"scope": {"taxonomy_node_id": None}, "risk_attribution_scope": "portfolio", "solved_result_groups": groups}
    valuation = {"portfolio_nav": 1000, "exposure_snapshot_complete": True, "exposures": {
        "instrument:x": {"amount_base": 300}, "instrument:y": {"amount_base": 200}, "instrument:z": {"amount_base": 400},
        "derivative_bucket:__derivatives__": {"amount_base": 200},
    }}
    return detail, request, valuation


def test_frozen_full_hierarchy_has_no_double_counting_and_keeps_capital_distinct_from_exposure():
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
    assert rows["root"]["current_exposure_weight"] == pytest.approx(1.1)
    assert rows["root"]["solved_risk_share"] == pytest.approx(1)
    assert rows["node:a0"]["solved_risk_share"] == 0
    # A Weight split is not a risk-budget vector, including a 100/0 allocation.
    assert rows["node:a1"]["target_risk_share"] is None
    assert rows["node:a"]["target_risk_share"] == .6
    assert rows["derivative_bucket:__derivatives__"]["rebalance_value_base"] == 0
    assert rows["derivative_bucket:__derivatives__"]["target_value_base"] == 50
    assert rows["cash_bucket:__cash__"]["current_exposure_weight"] is None


def test_unknown_exposure_propagates_instead_of_summing_only_known_children():
    detail, request, valuation = saved_result()
    valuation["exposures"]["instrument:y"]["amount_base"] = None
    result = build_research_solution_tree(detail, request, valuation=valuation)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert rows["root"]["current_exposure_base"] is None
    assert rows["node:a"]["current_exposure_base"] is None
    assert rows["node:b"]["current_exposure_base"] == 400
    assert rows["root"]["current_value_base"] == 1000


def test_old_snapshot_preserves_recorded_targets_and_scope_weights_without_inventing_nav_or_exposure():
    detail, request, _ = saved_result()
    request["target_configuration_snapshot"]["snapshot_schema_version"] = 2
    detail["solved_result_groups"][0]["target_risk_share"] = .7
    result = build_research_solution_tree(detail, request)
    rows = {row["row_id"]: row for row in result["rows"]}
    assert result["portfolio_nav"] is None
    assert result["capital_weight_basis"] == "saved_scope"
    assert rows["node:a"]["target_risk_share"] == .7
    assert rows["node:a1"]["target_risk_share"] is None
    assert rows["instrument:x"]["target_weight"] == .12
    assert rows["root"]["current_exposure_base"] is None
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
    detail["scope"]["taxonomy_node_id"] = "a1"
    detail["risk_attribution_scope"] = "selected_research_scope"
    detail["solved_result_groups"] = detail["solved_result_groups"][:1]
    for row, rc in zip(detail["solved_result_groups"][0]["rows"], [.25, .75], strict=True):
        row["forward_risk_contribution"] = rc
    result = build_research_solution_tree(detail, request, valuation=valuation)
    root = result["rows"][0]
    assert root["current_value_base"] == 300
    assert root["target_weight"] == .4
    assert root["solved_risk_share"] == 1
    assert all(row["label"] not in {"B", "z", "__cash__"} for row in result["rows"])


def test_published_exposure_keeps_offsetting_accounts_and_fcn_principal(monkeypatch):
    from portfolio_app.db import session as db_session
    holdings = [
        SimpleNamespace(account_id=account, position_reference_id="x", holding_kind="position", instrument_id="x", derivative_contract_id=None,
                        quantity=quantity, market_value_base=value, holding_json={"instrument_core": {"instrument_id": "x", "instrument_type": "equity"}})
        for account, quantity, value in [("long", 2, 200), ("short", -1, -100)]
    ]
    holdings.append(SimpleNamespace(account_id="broker", position_reference_id="fcn", holding_kind="derivative_contract", instrument_id=None,
        derivative_contract_id="fcn", quantity=2, market_value_base=80, holding_json={"derivative_contract": {"contract_type": "fcn", "currency": "USD", "terms": {"notional": 100}}}))
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args): return SimpleNamespace(daily_snapshot_status="current")
        def scalar(self, *args): return SimpleNamespace(valuation_coverage_state="complete")
        def scalars(self, *args): return holdings
    monkeypatch.setattr(db_session, "get_session_factory", lambda: Session)
    result = read_solution_exposures("synthetic", as_of_date=date(2026, 6, 30), base_currency="USD", nav=1000)
    assert result["exposures"]["instrument:x"]["amount_base"] == 300
    assert result["exposures"]["derivative_bucket:__derivatives__"]["amount_base"] == 200
    holdings.append(SimpleNamespace(account_id="broker", position_reference_id="option", holding_kind="option_obligation", instrument_id=None,
        derivative_contract_id="option", quantity=1, market_value_base=5, holding_json={"derivative_contract": {"contract_type": "option"}}))
    result = read_solution_exposures("synthetic", as_of_date=date(2026, 6, 30), base_currency="USD", nav=1000)
    assert result["exposures"]["derivative_bucket:__derivatives__"]["amount_base"] is None


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
