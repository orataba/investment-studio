"""Global allocation invariants, with deterministic covariance and no market calls."""
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from portfolio_app.services import research_solver as solver


CROSS_COVARIANCE = np.asarray([[1.0, 0.0, 0.8], [0.0, 1.0, 0.0], [0.8, 0.0, 1.0]])


@pytest.fixture(autouse=True)
def isolated_portfolio_store():
    """Synthetic taxonomy, holdings and covariance require no database."""
    yield


def tree(*, root_mode="risk_budget", a_mode="risk_budget", frozen=(), bounds=None):
    root_lines = {
        ("taxonomy_node", "a"): {"target_value": 0.5},
        ("taxonomy_node", "b"): {"target_value": 0.5},
        ("cash_bucket", "__cash__"): {"target_value": 0.0},
    }
    return solver.TaxonomyResearchState(
        portfolio_id="global-test", planning_taxonomy_id="tree", taxonomy_name="Tree",
        root_allocation_basis=root_mode, base_currency="CNY", as_of_date=date(2026, 6, 30),
        node_by_id={"a": {"node_name": "A", "allocation_basis": a_mode}, "b": {"node_name": "B", "allocation_basis": "weight"}},
        children_by_parent={None: ["a", "b"]}, node_path_by_id={"a": "A", "b": "B"}, node_depth_by_id={"a": 1, "b": 1},
        node_subtree_by_id={"a": {"a"}, "b": {"b"}},
        direct_assignments_by_node={"a": [{"target_scope": "instrument", "target_entity_id": key} for key in ("x", "y")], "b": [{"target_scope": "instrument", "target_entity_id": "z"}]},
        target_sets_by_scope_type={(None, "saa"): [{"target_set_id": "root"}], ("a", "saa"): [{"target_set_id": "a"}]},
        target_lines_by_set_id={"root": root_lines, "a": {("instrument", key): {"target_value": 0.5} for key in ("x", "y")}},
        account_name_by_id={}, instrument_detail_cache={key: {"instrument_name": key} for key in ("x", "y", "z")}, direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(frozen), top_sleeve_weight_bounds=bounds or {},
    )


def market(monkeypatch, covariance=CROSS_COVARIANCE):
    calls = []
    days = [day.date() for day in pd.bdate_range("2026-05-01", "2026-06-30")]
    navs = {key: pd.Series(np.cumprod(1 + 0.01 * np.sin(np.arange(len(days)) + index)), index=days) for index, key in enumerate(("x", "y", "z"))}
    monkeypatch.setattr(solver, "_build_instrument_nav_series", lambda state, *, instrument_id, **kwargs: (navs[instrument_id], []))
    def estimate(frame, **kwargs):
        calls.append(tuple(frame.columns))
        indices = [("x", "y", "z").index(column.split("::")[1]) for column in frame.columns]
        return pd.DataFrame(np.asarray(covariance)[np.ix_(indices, indices)], index=frame.columns, columns=frame.columns)
    monkeypatch.setattr(solver, "_estimate_covariance", estimate)
    return calls, navs


def solve(state, **overrides):
    inputs = dict(scope_node_id=None, as_of_date=state.as_of_date, lookback_days=30, calculation_frequency="daily", capital_mode="unit_notional", gross_exposure=None, target_volatility=None, max_gross_exposure=None, missing_return_policy="strict", apply_capital_overlay=True, include_actuals=False, risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": "signed", "parameters": {"min_observations": 2}})
    inputs.update(overrides)
    return solver._solve_current_scope(state, **inputs)


def risky_weights(result):
    return np.asarray([next(row["target_weight"] for row in result.leaf_target_rows if row["member_id"] == key) for key in ("x", "y", "z")])


def set_actuals(state, *, x=0.3, y=0.3, z=0.4, derivative=0.0, cash=0.0):
    state.current_valuation_cache["actual-valuation::2026-06-30"] = {"position_value_by_instrument": {"x": x, "y": y, "z": z}, "cash_value_by_account": {"cash": cash}, "derivative_total_value": derivative}


def test_global_euler_budget_includes_cross_sleeve_covariance_and_is_estimated_once(monkeypatch):
    calls, _ = market(monkeypatch)
    result = solve(tree())
    weights = risky_weights(result)
    shares = weights * (CROSS_COVARIANCE @ weights) / (weights @ CROSS_COVARIANCE @ weights)
    assert weights == pytest.approx([0.224744871, 0.355051026, 0.420204103], abs=2e-7)
    assert shares == pytest.approx([0.25, 0.25, 0.5], abs=1e-7)
    assert calls == [("instrument::x", "instrument::y", "instrument::z")]
    assert result.solve_event["execution_ready"] is True
    assert all(event["solver_version"] == solver.RESEARCH_TARGET_SOLVER_VERSION for event in result.scope_solve_events)
    assert all(event["global_leaf_count"] == 3 for event in result.scope_solve_events)
    assert list(result.forward_risk_contribution_by_key.values())[:3] == pytest.approx(shares)
    # Recomputing the old local solution exposes, but does not fix, its 68/32 miss.
    old_weights = np.asarray([0.292893219, 0.292893219, 0.414213562])
    old_q = old_weights * (CROSS_COVARIANCE @ old_weights)
    assert old_q[0] / old_q[:2].sum() == pytest.approx(0.680651, abs=1e-6)


@pytest.mark.parametrize("root_mode,a_mode", [("weight", "risk_budget"), ("risk_budget", "weight")])
def test_mixed_capital_and_risk_targets_are_solved_simultaneously(monkeypatch, root_mode, a_mode):
    market(monkeypatch)
    result = solve(tree(root_mode=root_mode, a_mode=a_mode))
    weights = risky_weights(result)
    q = weights * (CROSS_COVARIANCE @ weights)
    if root_mode == "weight":
        assert weights[:2].sum() == pytest.approx(0.5, abs=1e-6)
        assert q[0] / q[:2].sum() == pytest.approx(0.5, abs=1e-6)
        assert all(row.get("global_target_risk_share") is None for row in result.leaf_target_rows if row["member_type"] == "instrument")
    else:
        assert q[:2].sum() / q.sum() == pytest.approx(0.5, abs=1e-6)
        assert weights[0] / weights[:2].sum() == pytest.approx(0.5, abs=1e-6)
    assert result.solve_event["execution_ready"] is True


def test_fixed_weight_basket_ratios_remain_exact_when_risk_budget_is_unreachable(monkeypatch):
    covariance = np.diag([100.0, 1.0, 1.0])
    market(monkeypatch, covariance)
    result = solve(tree(a_mode="weight", bounds={"a": {"min_weight": 0.1, "max_weight": 0.1}}))
    weights = risky_weights(result)
    assert weights == pytest.approx([0.05, 0.05, 0.9], abs=1e-9)
    q = weights * (covariance @ weights)
    assert q[:2].sum() / q.sum() == pytest.approx(0.2525 / 1.0625)
    assert result.solve_event["target_status"] == "constrained_solution"
    assert result.solve_event["execution_ready"] is True


@pytest.mark.parametrize("correlated", [False, True])
def test_fixed_weight_sleeve_matches_synthetic_basket_in_global_risk_solve(monkeypatch, correlated):
    state = tree()
    keys = ("x", "y", "z", "u", "v")
    state.direct_assignments_by_node["b"] = [{"target_scope": "instrument", "target_entity_id": key} for key in keys[2:]]
    state.instrument_detail_cache.update({key: {"instrument_name": key} for key in keys})
    state.target_sets_by_scope_type[("b", "saa")] = [{"target_set_id": "b"}]
    state.target_lines_by_set_id["b"] = {("instrument", key): {"target_value": value} for key, value in zip(keys[2:], (0.5, 0.1, 0.4))}
    loadings = np.asarray([[1, 0, .5], [0, 2, .2], [.7, .2, 1], [.1, .7, .2], [.4, .1, .8]])
    covariance = loadings @ loadings.T + np.diag([.2, .1, .4, .3, .2]) if correlated else np.eye(5)
    days = [day.date() for day in pd.bdate_range("2026-05-01", "2026-06-30")]
    monkeypatch.setattr(solver, "_build_instrument_nav_series", lambda state, *, instrument_id, **kwargs: (pd.Series(np.cumprod(1 + .01 * np.sin(np.arange(len(days)) + keys.index(instrument_id))), index=days), []))
    monkeypatch.setattr(solver, "_estimate_covariance", lambda frame, **kwargs: pd.DataFrame(covariance, index=frame.columns, columns=frame.columns))
    result = solve(state)
    weights = np.asarray([next(row["target_weight"] for row in result.leaf_target_rows if row["member_id"] == key) for key in keys])
    basket_weight = weights[2:].sum()
    assert weights[2:] / basket_weight == pytest.approx([.5, .1, .4], abs=1e-9)
    # Collapse B into its fixed basket. Cross-covariances with both A leaves
    # remain in the transformed covariance, so this independently checks the
    # user's three-risk-unit formulation against the five-leaf result.
    transform = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, .5], [0, 0, .1], [0, 0, .4]])
    composite_covariance = transform.T @ covariance @ transform
    composite_weights = np.asarray([weights[0], weights[1], basket_weight])
    composite_q = composite_weights * (composite_covariance @ composite_weights)
    assert composite_q / composite_q.sum() == pytest.approx([.25, .25, .5], abs=1e-6)
    leaf_q = weights * (covariance @ weights)
    assert leaf_q[2:].sum() == pytest.approx(composite_q[2])
    assert leaf_q[2:] / leaf_q[2:].sum() != pytest.approx([.5, .1, .4], abs=1e-3)
    assert result.solve_event["execution_ready"] is True


def test_selected_subtree_has_its_own_explicit_global_scope(monkeypatch):
    calls, _ = market(monkeypatch)
    result = solve(tree(), scope_node_id="a", apply_capital_overlay=False)
    assert result.solve_event["risk_attribution_scope"] == "selected_research_scope"
    assert calls == [("instrument::x", "instrument::y")]
    assert [row["target_weight"] for row in result.leaf_target_rows] == pytest.approx([0.5, 0.5])


def test_frozen_parent_fixes_every_leaf_and_keeps_it_in_global_risk_equations(monkeypatch):
    market(monkeypatch)
    state = tree(frozen=("a",))
    set_actuals(state, x=0.4, y=0.4, z=0.2)
    result = solve(state, include_actuals=True)
    assert risky_weights(result) == pytest.approx([0.4, 0.4, 0.2])
    assert result.solve_event["max_risk_share_gap"] > 0.1
    assert result.solve_event["target_status"] == "constrained_optimum"
    assert all(row["trade_constraint"] == "no_trade" for row in result.leaf_target_rows if row["member_id"] in {"x", "y"})
    shares = np.asarray([0.4, 0.4, 0.2]) * (CROSS_COVARIANCE @ np.asarray([0.4, 0.4, 0.2]))
    shares /= shares.sum()
    assert [row["current_risk_share"] for row in result.leaf_target_rows[:3]] == pytest.approx(shares)


def test_binding_box_reports_converged_target_miss_without_claiming_global_optimum(monkeypatch):
    market(monkeypatch, np.diag([1.0, 1.0, 100.0]))
    result = solve(tree(bounds={"a": {"max_weight": 0.1}}))
    assert risky_weights(result)[:2].sum() <= 0.1 + 1e-8
    assert result.solve_event["target_status"] == "constrained_solution"
    assert result.solve_event["execution_ready"] is True
    assert result.solve_event["max_risk_share_gap"] > 0.4


def test_zero_target_has_zero_weight_and_does_not_require_its_history(monkeypatch):
    calls, navs = market(monkeypatch)
    navs["y"] = pd.Series({date(2026, 6, 30): 100.0})
    state = tree()
    state.target_lines_by_set_id["a"][("instrument", "x")]["target_value"] = 1.0
    state.target_lines_by_set_id["a"][("instrument", "y")]["target_value"] = 0.0
    result = solve(state)
    assert risky_weights(result)[1] == 0.0
    assert calls == [("instrument::x", "instrument::z")]
    assert result.solve_event["execution_ready"] is True


@pytest.mark.parametrize("mode", ["signed", "abs"])
def test_signed_hedges_and_explicit_leaf_absolute_aggregation(monkeypatch, mode):
    covariance = np.asarray([[0.04, 0.0, -0.08], [0.0, 0.04, 0.0], [-0.08, 0.0, 1.0]])
    market(monkeypatch, covariance)
    state = tree(root_mode="weight", a_mode="weight", frozen=("a", "b"))
    set_actuals(state)
    result = solve(state, include_actuals=True, risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": mode, "parameters": {"min_observations": 2}})
    weights = risky_weights(result)
    q = weights * (covariance @ weights)
    assert q[0] < 0.0 < q[1]
    expected = (np.abs(q) if mode == "abs" else q)
    expected /= expected.sum()
    assert [result.forward_risk_contribution_by_key[f"instrument::{key}"] for key in ("x", "y", "z")] == pytest.approx(expected)
    assert result.solve_event["execution_ready"] is True


def test_negative_parent_contribution_cannot_satisfy_positive_conditional_budget(monkeypatch):
    covariance = np.asarray([[0.04, 0.0, -0.08], [0.0, 0.04, -0.08], [-0.08, -0.08, 1.0]])
    market(monkeypatch, covariance)
    state = tree(frozen=("a", "b"))
    set_actuals(state)
    result = solve(state, include_actuals=True)
    assert result.solve_event["execution_ready"] is False
    assert any("zero or negative parent" in warning for warning in result.warnings)
    assert result.forward_risk_contribution_by_key["instrument::x"] < 0.0


def test_fixed_gross_and_signed_derivative_capital_are_preserved(monkeypatch):
    market(monkeypatch)
    state = tree()
    result = solve(state, capital_mode="fixed_gross", gross_exposure=1.4, fixed_derivative_weight_override=-0.2)
    rows = {row["member_id"]: row for row in result.leaf_target_rows}
    assert risky_weights(result).sum() == pytest.approx(1.4)
    assert rows["__derivatives__"]["target_weight"] == pytest.approx(-0.2)
    assert rows["__cash__"]["target_weight"] == pytest.approx(-0.2)
    assert sum(row["target_weight"] for row in result.leaf_target_rows) == pytest.approx(1.0)
    assert result.solve_event["execution_ready"] is True


def test_fixed_gross_above_root_box_capacity_is_rejected(monkeypatch):
    market(monkeypatch)
    with pytest.raises(ValueError, match="maximum weights.*fixed gross"):
        solve(tree(bounds={"a": {"max_weight": 0.2}, "b": {"max_weight": 0.3}}), capital_mode="fixed_gross", gross_exposure=1.0)


@pytest.mark.parametrize("capital_mode", ["volatility_cap", "target_volatility"])
def test_volatility_overlay_uses_same_covariance_and_keeps_global_budgets(monkeypatch, capital_mode):
    calls, _ = market(monkeypatch)
    result = solve(tree(), capital_mode=capital_mode, target_volatility=0.2, max_gross_exposure=1.0)
    weights = risky_weights(result)
    assert weights.sum() > 0.0
    assert np.sqrt(weights @ CROSS_COVARIANCE @ weights) == pytest.approx(0.2, abs=1e-7)
    assert weights * (CROSS_COVARIANCE @ weights) / (weights @ CROSS_COVARIANCE @ weights) == pytest.approx([0.25, 0.25, 0.5], abs=1e-6)
    assert len(calls) == 1
    assert result.solve_event["execution_ready"] is True


def test_target_volatility_above_possible_maximum_is_rejected(monkeypatch):
    market(monkeypatch)
    with pytest.raises(ValueError, match="Target volatility cannot be reached"):
        solve(tree(), capital_mode="target_volatility", target_volatility=2.0, max_gross_exposure=1.0)


def test_result_group_risk_uses_solved_covariance_without_reestimation(monkeypatch):
    calls, _ = market(monkeypatch)
    state = tree()
    result = solve(state)
    groups, _ = solver._build_solved_result_groups(state, leaf_rows=result.leaf_target_rows, member_rows=result.member_target_rows, scope_value_base=100.0, forward_rc_by_key=result.forward_risk_contribution_by_key)
    assert len(calls) == 1
    group_a = next(group for group in groups if group["top_sleeve_id"] == "a")
    assert group_a["forward_risk_contribution"] == pytest.approx(0.5)
    assert [row["target_risk_share"] for row in group_a["rows"]] == pytest.approx([0.25, 0.25])


def test_shared_global_window_keeps_missing_trailing_returns(monkeypatch):
    _, navs = market(monkeypatch)
    navs["x"] = navs["x"].drop(date(2026, 6, 30))
    with pytest.raises(ValueError, match="global leaf risk model unavailable"):
        solve(tree())


def test_non_psd_covariance_is_rejected_by_current_model_boundary():
    with pytest.raises(ValueError, match="positive-semidefinite"):
        solver._finalize_covariance(pd.DataFrame([[0.01, 0.02], [0.02, 0.01]], index=["x", "y"], columns=["x", "y"]))


def test_positive_risk_budget_cannot_be_satisfied_by_zero_parent_capital(monkeypatch):
    market(monkeypatch)
    state = tree(bounds={"a": {"max_weight": 0.0}})
    result = solve(state, capital_mode="volatility_cap", target_volatility=0.2)
    assert result.solve_event["execution_ready"] is False
    assert result.solve_event["target_status"] == "constrained_target_miss"
    assert any("zero or negative parent" in warning for warning in result.warnings)


def test_zero_fixed_weight_conflicting_with_hard_minimum_is_rejected(monkeypatch):
    market(monkeypatch)
    state = tree(root_mode="weight", a_mode="weight", frozen=("a",), bounds={"b": {"min_weight": 0.2}})
    state.target_lines_by_set_id["root"][("taxonomy_node", "a")]["target_value"] = 1.0
    state.target_lines_by_set_id["root"][("taxonomy_node", "b")]["target_value"] = 0.0
    set_actuals(state, x=0.4, y=0.4, z=0.2)
    with pytest.raises(ValueError, match="fixed weight ratios.*infeasible"):
        solve(state, include_actuals=True)


def test_exactly_offsetting_signed_group_risk_is_zero_not_missing(monkeypatch):
    market(monkeypatch)
    state = tree(root_mode="weight", a_mode="weight")
    result = solve(state)
    groups, _ = solver._build_solved_result_groups(state, leaf_rows=result.leaf_target_rows, member_rows=result.member_target_rows, scope_value_base=100.0, forward_rc_by_key={"instrument::x": -0.1, "instrument::y": 0.1, "instrument::z": 1.0})
    assert next(group for group in groups if group["top_sleeve_id"] == "a")["forward_risk_contribution"] == 0.0


@pytest.mark.parametrize("capital_mode,overrides", [("fixed_gross", {"gross_exposure": 1.0}), ("target_volatility", {"target_volatility": 0.7, "max_gross_exposure": 1.0})])
def test_no_borrowing_modes_reserve_derivative_carrying_capital(monkeypatch, capital_mode, overrides):
    market(monkeypatch)
    with pytest.raises(ValueError, match="cash borrowing|Target volatility cannot be reached"):
        solve(tree(), capital_mode=capital_mode, fixed_derivative_weight_override=0.5, **overrides)


def test_explicit_leverage_allows_negative_cash_while_preserving_derivative_capital(monkeypatch):
    market(monkeypatch)
    result = solve(tree(), capital_mode="fixed_gross", gross_exposure=1.2, fixed_derivative_weight_override=0.5)
    rows = {row["member_id"]: row for row in result.leaf_target_rows}
    assert rows["__derivatives__"]["target_weight"] == pytest.approx(0.5)
    assert rows["__cash__"]["target_weight"] == pytest.approx(-0.7)
    assert result.solve_event["execution_ready"] is True


def test_subtree_scope_keeps_ancestor_no_trade_constraint(monkeypatch):
    market(monkeypatch)
    state = tree(frozen=("parent",))
    state.node_subtree_by_id["parent"] = {"parent", "a"}
    set_actuals(state, x=0.6, y=0.2, z=0.2)
    result = solve(state, scope_node_id="a", apply_capital_overlay=False, include_actuals=True)
    assert [row["target_weight"] for row in result.leaf_target_rows] == pytest.approx([0.75, 0.25])
    assert all(row["trade_constraint"] == "no_trade" for row in result.leaf_target_rows)


def test_subtree_report_does_not_compare_its_normalized_weight_to_portfolio_bounds(monkeypatch):
    market(monkeypatch)
    state = tree(bounds={"a": {"max_weight": 0.3}})
    result = solve(state, scope_node_id="a", apply_capital_overlay=False)
    groups, _ = solver._build_solved_result_groups(state, leaf_rows=result.leaf_target_rows, member_rows=result.member_target_rows, scope_value_base=100.0, forward_rc_by_key=result.forward_risk_contribution_by_key, scope_node_id="a")
    assert groups[0]["solved_weight"] == pytest.approx(1.0)
    assert groups[0]["max_weight"] is None
    assert groups[0]["bound_status"] is None


def test_unconverged_target_miss_remains_unusable_for_simulation(monkeypatch):
    market(monkeypatch, np.diag([1.0, 1.0, 100.0]))
    original = solver.minimize
    def non_success(*args, **kwargs):
        result = original(*args, **kwargs)
        result.success = False
        result.message = "Numerical iteration stopped without convergence."
        return result
    monkeypatch.setattr(solver, "minimize", non_success)
    result = solve(tree(bounds={"a": {"max_weight": 0.1}}))
    assert risky_weights(result)[:2].sum() <= 0.1 + 1e-8
    assert result.solve_event["target_status"] == "constrained_target_miss"
    assert result.solve_event["execution_ready"] is False


def test_independently_verified_exact_target_survives_non_success_termination(monkeypatch):
    market(monkeypatch)
    original = solver.minimize
    def non_success(*args, **kwargs):
        result = original(*args, **kwargs)
        result.success = False
        return result
    monkeypatch.setattr(solver, "minimize", non_success)
    result = solve(tree())
    assert result.solve_event["target_status"] == "satisfied"
    assert result.solve_event["execution_ready"] is True
    assert result.solve_event["max_risk_share_gap"] < 1e-6


def test_unknown_contribution_mode_cannot_silently_fall_back_to_signed(monkeypatch):
    market(monkeypatch)
    with pytest.raises(ValueError, match="Unsupported risk contribution mode"):
        solve(tree(), risk_model_config={"contribution_mode": "unknown"})


def test_root_box_equal_to_funding_does_not_block_mixed_sleeve_solve(monkeypatch):
    market(monkeypatch)
    state = tree(root_mode="weight", a_mode="weight", bounds={"a": {"max_weight": 1.0}})
    state.children_by_parent[None] = ["a"]
    state.target_lines_by_set_id["root"].pop(("taxonomy_node", "b"))
    state.target_lines_by_set_id["root"][("taxonomy_node", "a")]["target_value"] = 1.0
    state.target_lines_by_set_id["a"][("instrument", "x")]["target_value"] = 0.8
    state.target_lines_by_set_id["a"][("instrument", "y")]["target_value"] = 0.2
    result = solve(state)
    weights = {row["member_id"]: row["target_weight"] for row in result.leaf_target_rows}
    assert weights["x"] == pytest.approx(0.8, abs=1e-6)
    assert weights["y"] == pytest.approx(0.2, abs=1e-6)
    assert result.solve_event["execution_ready"] is True


def test_hard_zero_cannot_erase_positive_risk_target_and_hide_missing_covariance(monkeypatch):
    market(monkeypatch)
    state = tree(a_mode="weight", bounds={"a": {"max_weight": 0.0}})
    def missing_covariance(*args, **kwargs):
        raise ValueError("Covariance estimation has insufficient complete return observations.")
    monkeypatch.setattr(solver, "_estimate_covariance", missing_covariance)
    with pytest.raises(ValueError, match="global leaf risk model unavailable"):
        solve(state)


def test_all_cash_weight_target_does_not_invent_a_global_risk_target(monkeypatch):
    market(monkeypatch)
    state = tree(root_mode="weight", a_mode="weight")
    state.children_by_parent[None] = ["a"]
    state.direct_assignments_by_node["a"] = [{"target_scope": "instrument", "target_entity_id": "x"}]
    state.target_lines_by_set_id["root"].pop(("taxonomy_node", "b"))
    state.target_lines_by_set_id["root"][("taxonomy_node", "a")]["target_value"] = 0.0
    state.target_lines_by_set_id["root"][("cash_bucket", "__cash__")]["target_value"] = 1.0
    result = solve(state)
    sleeve = next(row for row in result.member_target_rows if row["member_id"] == "a")
    leaf = next(row for row in result.leaf_target_rows if row["member_id"] == "x")
    assert sleeve["target_weight"] == 0.0
    assert sleeve["global_target_risk_share"] is None
    assert leaf["global_target_risk_share"] is None
    groups, _ = solver._build_solved_result_groups(state, leaf_rows=result.leaf_target_rows, member_rows=result.member_target_rows, scope_value_base=100.0, forward_rc_by_key=result.forward_risk_contribution_by_key)
    group = next(row for row in groups if row["top_sleeve_id"] == "a")
    assert group["target_risk_share"] is None
    assert group["rows"][0]["target_risk_share"] is None


@pytest.mark.parametrize("capital_mode", ["unit_notional", "volatility_cap"])
def test_fixed_capital_cannot_silently_reduce_an_infeasible_cash_reserve(monkeypatch, capital_mode):
    market(monkeypatch)
    state = tree(root_mode="weight", a_mode="weight")
    for key in ("a", "b"):
        state.target_lines_by_set_id["root"][("taxonomy_node", key)]["target_value"] = 0.0
    state.target_lines_by_set_id["root"][("cash_bucket", "__cash__")]["target_value"] = 1.0
    with pytest.raises(ValueError, match="cash reserve exceed total NAV"):
        solve(state, capital_mode=capital_mode, target_volatility=0.2 if capital_mode == "volatility_cap" else None,
              fixed_derivative_weight_override=0.2)


def test_explicit_zero_risk_budget_stays_zero_through_excluded_weight_descendant(monkeypatch):
    market(monkeypatch)
    state = tree(root_mode="risk_budget", a_mode="weight")
    state.direct_assignments_by_node["a"] = [{"target_scope": "instrument", "target_entity_id": "x"}]
    state.target_lines_by_set_id["root"][("taxonomy_node", "a")]["target_value"] = 0.0
    state.target_lines_by_set_id["root"][("taxonomy_node", "b")]["target_value"] = 1.0
    result = solve(state)
    sleeve = next(row for row in result.member_target_rows if row["member_id"] == "a")
    leaf = next(row for row in result.leaf_target_rows if row["member_id"] == "x")
    assert sleeve["global_target_risk_share"] == 0.0
    assert leaf["global_target_risk_share"] == 0.0
