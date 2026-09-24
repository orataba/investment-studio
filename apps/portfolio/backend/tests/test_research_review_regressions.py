from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from portfolio_app.api.contracts import ResearchSettingsUpdateRequest
from portfolio_app.services import research_solver as solver
from portfolio_app.services.risk_model import normalize_portfolio_risk_policy
from tests.research_backtest_fixtures import initial_book, stub_inception_statement


@pytest.mark.parametrize(
    ("capital_mode", "field_name", "extra"),
    [
        ("fixed_gross", "gross_exposure", {}),
        ("target_volatility", "max_gross_exposure", {"target_volatility": 0.1}),
    ],
)
def test_research_settings_reject_nonfinite_exposure(capital_mode, field_name, extra):
    with pytest.raises(ValueError):
        ResearchSettingsUpdateRequest.model_validate(
            {
                "capital_mode": capital_mode,
                field_name: float("inf"),
                **extra,
            }
        )


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"capital_mode": "mystery"}, "capital mode"),
        ({"calculation_frequency": "weekly"}, "calculation frequency"),
        ({"capital_mode": "fixed_gross", "gross_exposure": float("inf")}, "finite and positive"),
        ({"capital_mode": "fixed_gross", "gross_exposure": None}, "requires gross_exposure"),
        ({"capital_mode": "unit_notional", "target_volatility": 0.1}, "must not set"),
    ],
)
def test_research_solve_core_rejects_invalid_configuration(overrides, message):
    inputs = {
        "calculation_frequency": "daily",
        "capital_mode": "unit_notional",
        "gross_exposure": None,
        "target_volatility": None,
        "max_gross_exposure": None,
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        solver._validate_research_solve_configuration(**inputs)


def replay(
    decisions,
    returns,
    *,
    cash_yield=0.0,
    commission=0.0,
    end=date(2026, 6, 3),
    delay=0,
    derivative_events=None,
    cash_borrowing_allowed=False,
    capital_mode="unit_notional",
):
    return solver._replay_backtest_decisions(
        decisions,
        initial_state=initial_book((date.fromisoformat(min(row["decision_date"] for row in decisions)) - timedelta(days=1)).isoformat()),
        returns_by_instrument=returns,
        as_of_date=end,
        cash_yield_annual=cash_yield,
        commission_bps=commission,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=delay,
        derivative_capital_events=derivative_events,
        cash_borrowing_allowed=cash_borrowing_allowed,
        capital_mode=capital_mode,
    )


def decision(day, weights, derivative_weight=0.0):
    return {
        "decision_date": day,
        "derivative_target_weight": derivative_weight,
        "target_weights": [
            {"instrument_id": key, "target_weight": weight, "top_sleeve_label": key}
            for key, weight in weights.items()
        ],
    }


def test_mixed_publication_dates_compound_every_intervening_return():
    result = replay(
        [decision("2026-06-01", {"a": 0.5, "b": 0.5})],
        {
            "a": pd.Series([0.0, 0.1, 0.1], index=[date(2026, 6, d) for d in (1, 2, 3)]),
            "b": pd.Series([0.0, 0.0], index=[date(2026, 6, d) for d in (1, 3)]),
        },
    )
    assert result["points"][-1]["value"] == pytest.approx(0.5 * 1.1 ** 2 + 0.5)
    assert max(abs(r["residual"]) for r in result["contribution_reconciliation_points"]) < 1e-12


def test_derivative_capital_is_not_interest_bearing_cash():
    result = replay(
        [decision("2026-06-01", {"a": 0.5}, 0.4)],
        {"a": pd.Series([0.0, 0.0], index=[date(2026, 6, 1), date(2026, 7, 1)])},
        cash_yield=0.1,
        end=date(2026, 7, 1),
        derivative_events=[
            {
                "effective_date": "2026-06-01",
                "target_value": 0.4,
                "actual_value_base": 40.0,
                "reference_weight": 0.4,
                "source": "actual_derivative_ledger",
            }
        ],
    )
    # The explicit May-31 cash book earns its first day before June-1 EOD
    # execution; subsequently the frozen 0.4 derivative principal earns none.
    initial_cash_growth = 1.1 ** (1 / 365.25)
    assert result["points"][-1]["value"] == pytest.approx(0.9 * initial_cash_growth + 0.1 * initial_cash_growth * 1.1 ** (30 / 365.25))
    assert result["execution_records"][0]["derivative_target_value"] == 0.4
    assert result["execution_records"][0]["cash_target_weight"] == pytest.approx(0.1)
    assert result["execution_records"][0]["derivative_leg_turnover"] == 0.0
    assert result["derivative_capital_events"][0]["capital_change"] == pytest.approx(0.4)
    assert "__derivatives__" in {r["top_sleeve_id"] for r in result["top_sleeve_weight_points"][-1]["sleeves"]}


def test_written_option_liability_is_signed_no_trade_capital():
    result = replay(
        [decision("2026-06-01", {"a": 1.1}, -0.1)],
        {"a": pd.Series([0.0, 0.0], index=[date(2026, 6, 1), date(2026, 6, 3)])},
        derivative_events=[
            {
                "effective_date": "2026-06-01",
                "target_value": -0.1,
                "actual_value_base": -10.0,
                "reference_weight": -0.1,
                "source": "actual_derivative_ledger",
            }
        ],
    )

    execution = result["execution_records"][0]
    assert result["points"][-1]["value"] == pytest.approx(1.0)
    assert execution["derivative_target_weight"] == pytest.approx(-0.1)
    assert execution["cash_target_weight"] == pytest.approx(0.0)
    assert execution["derivative_leg_turnover"] == 0.0


def test_derivative_capital_change_after_decision_invalidates_security_execution():
    result = replay(
        [decision("2026-06-01", {"a": 0.95})],
        {
            "a": pd.Series(
                [0.0, 0.0],
                index=[date(2026, 6, 2), date(2026, 6, 3)],
            )
        },
        derivative_events=[
            {
                "effective_date": "2026-06-02",
                "target_value": 0.15,
                "actual_value_base": 15.0,
                "reference_weight": 0.15,
                "source": "actual_derivative_ledger",
            }
        ],
    )

    assert result["execution_records"] == []
    assert result["points"][-1]["value"] == pytest.approx(1.0)
    assert result["derivative_capital_events"][0]["capital_change"] == pytest.approx(0.15)
    assert "stale capital budget" in result["skipped_executions"][0]["reason"]


def test_derivative_capital_increase_cannot_create_hidden_cash_borrowing():
    with pytest.raises(ValueError, match="implicit cash borrowing"):
        replay(
            [decision("2026-06-01", {"a": 0.95})],
            {
                "a": pd.Series(
                    [0.0, 0.0, 0.0],
                    index=[date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
                )
            },
            derivative_events=[
                {
                    "effective_date": "2026-06-02",
                    "target_value": 0.15,
                    "actual_value_base": 15.0,
                    "reference_weight": 0.15,
                    "source": "actual_derivative_ledger",
                }
            ],
        )


def test_derivative_event_funding_decision_can_override_global_delay():
    old_target = decision("2026-06-01", {"a": 0.95})
    funding_target = decision("2026-06-02", {"a": 0.8}, derivative_weight=0.15)
    funding_target["implementation_delay_days"] = 0

    result = replay(
        [old_target, funding_target],
        {
            "a": pd.Series(
                [0.0, 0.0],
                index=[date(2026, 6, 2), date(2026, 6, 3)],
            )
        },
        delay=1,
        derivative_events=[
            {
                "effective_date": "2026-06-02",
                "target_value": 0.15,
                "actual_value_base": 15.0,
                "reference_weight": 0.15,
                "source": "actual_derivative_ledger",
            }
        ],
    )

    execution = result["execution_records"][0]
    assert execution["decision_date"] == "2026-06-02"
    assert execution["scheduled_execution_date"] == "2026-06-02"
    assert execution["actual_execution_date"] == "2026-06-02"
    assert execution["cash_target_weight"] == pytest.approx(0.05)
    assert execution["derivative_leg_turnover"] == 0.0
    assert "superseded" in result["skipped_executions"][0]["reason"]
    cash_values = [
        sleeve["value"]
        for point in result["top_sleeve_weight_points"]
        for sleeve in point["sleeves"]
        if sleeve["top_sleeve_id"] == "__cash__"
    ]
    assert min(cash_values) >= 0.0


def test_explicit_leverage_mode_can_replay_negative_financing_cash():
    result = replay(
        [decision("2026-06-01", {"a": 1.1})],
        {
            "a": pd.Series(
                [0.0, 0.0],
                index=[date(2026, 6, 1), date(2026, 6, 3)],
            )
        },
        cash_borrowing_allowed=True,
    )

    assert result["execution_records"][0]["cash_target_weight"] == pytest.approx(-0.1)


def test_fully_invested_target_pays_fees_without_hidden_cash_borrowing():
    result = replay(
        [decision("2026-06-01", {"a": 1.0})],
        {"a": pd.Series([0.0, 0.0], index=[date(2026, 6, 1), date(2026, 6, 3)])},
        commission=100.0,
    )
    assert result["points"][-1]["value"] == pytest.approx(1 / 1.01)
    assert all(r["value"] >= -1e-12 for r in result["top_sleeve_weight_points"][-1]["sleeves"])
    assert result["total_cost"] == pytest.approx(1 - 1 / 1.01)


@pytest.mark.parametrize("capital_mode", ["unit_notional", "volatility_cap"])
def test_fixed_derivative_capital_and_fees_are_funded_before_security_sizing(capital_mode):
    result = replay(
        [decision("2026-06-01", {"a": 0.36, "b": 0.24}, 0.4)],
        {key: pd.Series([0.0, 0.0], index=[date(2026, 6, 1), date(2026, 6, 3)]) for key in ("a", "b")},
        commission=100.0,
        capital_mode=capital_mode,
        derivative_events=[{"effective_date": "2026-06-01", "target_value": 0.4}],
    )
    execution = result["execution_records"][0]
    security_capital = 0.6 / 1.01
    assert execution["derivative_target_value"] == 0.4
    assert execution["derivative_leg_turnover"] == 0.0
    assert execution["cash_target_weight"] == pytest.approx(0.0, abs=1e-12)
    assert execution["nav_after_execution"] == pytest.approx(0.4 + security_capital)
    assert execution["total_cost"] == pytest.approx(security_capital * 0.01)
    weights = {row["instrument_id"]: row["target_weight"] for row in execution["target_weights"]}
    assert weights["a"] / weights["b"] == pytest.approx(1.5)
    assert sum(weights.values()) + execution["derivative_target_weight"] == pytest.approx(1.0)
    assert max(abs(row["residual"]) for row in result["contribution_reconciliation_points"]) < 1e-12


def test_execution_sizes_fixed_capital_against_simulation_nav_after_a_drawdown():
    result = replay(
        [decision("2026-06-01", {"a": 0.6}, 0.4), decision("2026-06-03", {"a": 0.6}, 0.4)],
        {"a": pd.Series([0.0, -0.5, 0.0], index=[date(2026, 6, day) for day in (1, 2, 3)])},
        derivative_events=[{"effective_date": "2026-06-01", "target_value": 0.4}],
    )
    execution = result["execution_records"][-1]
    assert execution["nav_after_execution"] == pytest.approx(0.7)
    assert execution["derivative_target_value"] == 0.4
    assert execution["target_weights"][0]["target_weight"] == pytest.approx(0.3 / 0.7)
    assert execution["cash_target_weight"] == pytest.approx(0.0)


def test_execution_funding_cannot_silently_violate_a_hard_sleeve_minimum():
    target = decision("2026-06-01", {"a": 0.6}, 0.4)
    target["target_weights"][0]["top_sleeve_id"] = "a"
    target["top_sleeve_weight_bounds"] = [{"taxonomy_node_id": "a", "min_weight": 0.6}]
    with pytest.raises(ValueError, match="funding.*hard weight bounds.*not executable"):
        replay([target], {"a": pd.Series([0.0], index=[date(2026, 6, 1)])}, commission=100.0,
               derivative_events=[{"effective_date": "2026-06-01", "target_value": 0.4}])


def test_execution_cannot_replace_an_infeasible_cash_reserve_with_zero_securities():
    target = decision("2026-06-01", {"a": 0.1}, 0.4)
    target["cash_reserve_weight"] = 0.5
    with pytest.raises(ValueError, match="insufficient capital.*cash reserve.*not executable"):
        replay([target], {"a": pd.Series([0.0], index=[date(2026, 6, 1)])},
               derivative_events=[{"effective_date": "2026-06-01", "target_value": 0.6}])


def test_derivative_decision_weight_uses_simulated_nav_not_actual_portfolio_nav():
    amount, weight = solver._derivative_backtest_state_on(
        {"events": [{"effective_date": "2026-06-01", "target_value": 0.4, "actual_value_base": 40.0}],
         "nav_points": [{"date": "2026-06-03", "nav": 200.0}]},
        point_date=date(2026, 6, 3), simulation_nav=0.8,
    )
    assert amount == 0.4
    assert weight == 0.5


def test_unfilled_old_target_cannot_overwrite_a_newer_rebalance():
    result = replay(
        [decision("2026-06-01", {"a": 1.0}), decision("2026-06-02", {"b": 1.0})],
        {
            "a": pd.Series([0.0], index=[date(2026, 6, 3)]),
            "b": pd.Series([0.0, 0.0], index=[date(2026, 6, d) for d in (2, 3)]),
        },
    )
    assert [r["decision_date"] for r in result["execution_records"]] == ["2026-06-02"]
    assert "superseded" in result["skipped_executions"][0]["reason"]


def test_next_decision_nav_does_not_include_cost_of_its_superseded_pending_trade():
    result = solver._replay_backtest_decisions(
        [decision("2026-06-01", {"a": 0.6}, 0.4)],
        initial_state=initial_book("2026-05-31"),
        returns_by_instrument={"a": pd.Series([0.0], index=[date(2026, 6, 3)])},
        as_of_date=date(2026, 6, 3), cash_yield_annual=0.0,
        commission_bps=100.0, tax_bps=0.0, slippage_bps=0.0, implementation_delay_days=0,
        derivative_capital_events=[{"effective_date": "2026-06-01", "target_value": 0.4}],
        _superseding_execution_date=date(2026, 6, 3),
    )
    assert not result["execution_records"]
    assert result["total_cost"] == 0.0
    assert "superseded" in result["skipped_executions"][0]["reason"]


def test_delayed_decision_beyond_cutoff_is_pending_not_missing_history():
    result = replay(
        [decision("2026-06-03", {"a": 1.0})],
        {"a": pd.Series([0.0], index=[date(2026, 6, 3)])}, delay=1,
    )
    assert not result["skipped_executions"]
    assert result["pending_executions"][0]["date"] == "2026-06-03"
    assert not result["execution_records"]


@pytest.mark.parametrize("frequency", ["1m", "3m"])
def test_extending_backtest_end_does_not_remove_initial_allocation(frequency):
    short = solver._rebalance_schedule(start_date=date(2026, 8, 3), end_date=date(2026, 8, 31), frequency=frequency)
    extended = solver._rebalance_schedule(start_date=date(2026, 8, 3), end_date=date(2026, 9, 3), frequency=frequency)
    assert short == [date(2026, 8, 3)]
    assert extended == short + [date(2026, 9, 1)]


def allocation_state():
    days = [day.date() for day in pd.bdate_range("2026-01-01", "2026-06-30")]
    phase = np.arange(len(days))
    navs = {
        "a": np.cumprod(1 + 0.002 + 0.012 * np.sin(phase)),
        "b": np.cumprod(1 + 0.001 + 0.004 * np.cos(phase / 3)),
    }
    details = {
        key: {
            "instrument_id": key, "instrument_name": key, "instrument_type": "public_fund",
            "currency": "CNY", "quote_selection_policy": {"total_return": ["total_return_nav"]},
            "market_data": [
                {"metric_family": "nav", "quote_basis": "total_return_nav", "as_of_date": day.isoformat(),
                 "value": value, "currency": "CNY", "price_unit": "per_unit", "price_scale": 1, "status": "complete"}
                for day, value in zip(days, nav)
            ],
        } for key, nav in navs.items()
    }
    return solver.TaxonomyResearchState(
        portfolio_id="review", planning_taxonomy_id="review-taxonomy", taxonomy_name="Review",
        root_allocation_basis="weight", base_currency="CNY", as_of_date=days[-1],
        node_by_id={key: {"node_name": key, "allocation_basis": "weight"} for key in navs},
        children_by_parent={None: list(navs)}, node_path_by_id={key: key for key in navs},
        node_depth_by_id={key: 1 for key in navs}, node_subtree_by_id={key: {key} for key in navs},
        direct_assignments_by_node={key: [{"target_scope": "instrument", "target_entity_id": key}] for key in navs},
        target_sets_by_scope_type={(None, "saa"): [{"target_set_id": "targets", "status": "active"}]},
        target_lines_by_set_id={"targets": {
            ("taxonomy_node", "a"): {"target_value": 0.6},
            ("taxonomy_node", "b"): {"target_value": 0.4},
            ("cash_bucket", "__cash__"): {"target_value": 0.1},
        }},
        account_name_by_id={}, instrument_detail_cache=details, direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(), top_sleeve_weight_bounds={},
    )


def risk_budget_state(*, frozen=frozenset(), top_bounds=None):
    state = allocation_state()
    return replace(
        state,
        root_allocation_basis="risk_budget",
        frozen_taxonomy_node_ids=frozen,
        top_sleeve_weight_bounds=top_bounds or {},
        target_sets_by_scope_type={
            (None, "saa"): [
                {
                    "target_set_id": "targets",
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "targets": {
                ("taxonomy_node", "a"): {"target_value": 0.5},
                ("taxonomy_node", "b"): {"target_value": 0.5},
                ("cash_bucket", "__cash__"): {"target_value": 0.0},
            }
        },
    )


def solve_state(state, *, capital_mode="volatility_cap", target_volatility=0.5, actuals=False):
    return solver._solve_current_scope(
        state, scope_node_id=None, as_of_date=state.as_of_date, lookback_days=30,
        calculation_frequency="daily", capital_mode=capital_mode,
        gross_exposure=None, target_volatility=target_volatility, max_gross_exposure=None,
        missing_return_policy="strict", apply_capital_overlay=True,
        risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": "abs", "parameters": {"min_observations": 15}},
        include_actuals=actuals, resolve_frozen_actuals=actuals,
    )


def test_volatility_cap_does_not_spend_fixed_capital_reserves():
    state = allocation_state()
    state.current_valuation_cache["actual-valuation::2026-06-30"] = {
        "position_value_by_instrument": {"a": 30.0, "b": 20.0},
        "cash_value_by_account": {"cash": 10.0},
        "derivative_total_value": 40.0,
    }
    solved = solve_state(state, actuals=True)
    weights = {row["member_id"]: row["target_weight"] for row in solved.member_target_rows}
    assert weights == pytest.approx({"a": 0.3, "b": 0.2, "__derivatives__": 0.4, "__cash__": 0.1})
    assert solved.solve_event["gross_exposure"] == pytest.approx(0.5)
    derivative = next(row for row in solved.member_target_rows if row["member_id"] == "__derivatives__")
    assert derivative["configured_weight"] is None
    assert derivative["trade_constraint"] == "no_trade"
    assert derivative["risk_model_status"] == "excluded"


def test_volatility_overlay_keeps_frozen_weight_and_reports_infeasibility():
    state = replace(allocation_state(), frozen_taxonomy_node_ids=frozenset({"b"}))
    state.current_valuation_cache["actual-valuation::2026-06-30"] = {
        "position_value_by_instrument": {"a": 30, "b": 20},
        "cash_value_by_account": {"cash": 10}, "derivative_total_value": 40,
    }
    solved = solve_state(state, target_volatility=0.025, actuals=True)
    weights = {row["member_id"]: row["target_weight"] for row in solved.member_target_rows}
    assert weights["b"] == pytest.approx(0.2)
    assert weights["a"] < 0.3
    with pytest.raises(ValueError, match="Frozen sleeve weights make the volatility constraint infeasible"):
        solve_state(state, target_volatility=0.00001, actuals=True)


def test_parent_no_trade_constraint_reaches_every_descendant_instrument():
    base = allocation_state()
    parent_node = {"node_name": "parent", "allocation_basis": "weight"}
    root_lines = dict(base.target_lines_by_set_id["targets"])
    root_lines.pop(("taxonomy_node", "a"))
    root_lines[("taxonomy_node", "parent")] = {"target_value": 0.6}
    state = replace(
        base,
        node_by_id={
            **base.node_by_id,
            "parent": parent_node,
            "a": {**base.node_by_id["a"], "parent_taxonomy_node_id": "parent"},
        },
        children_by_parent={None: ["parent", "b"], "parent": ["a"]},
        node_path_by_id={**base.node_path_by_id, "parent": "parent", "a": "parent / a"},
        node_depth_by_id={**base.node_depth_by_id, "parent": 1, "a": 2},
        node_subtree_by_id={**base.node_subtree_by_id, "parent": {"parent", "a"}},
        target_lines_by_set_id={"targets": root_lines},
        frozen_taxonomy_node_ids=frozenset({"parent"}),
    )
    state.current_valuation_cache["actual-valuation::2026-06-30"] = {
        "position_value_by_instrument": {"a": 30.0, "b": 20.0},
        "cash_value_by_account": {"cash": 10.0},
        "derivative_total_value": 40.0,
    }

    solved = solve_state(state, actuals=True)

    parent = next(row for row in solved.member_target_rows if row["member_id"] == "parent")
    descendant = next(row for row in solved.leaf_target_rows if row["member_id"] == "a")
    assert parent["target_weight"] == pytest.approx(0.3)
    assert parent["trade_constraint"] == "no_trade"
    assert descendant["target_weight"] == pytest.approx(0.3)
    assert descendant["trade_constraint"] == "no_trade"
    assert descendant["risk_model_status"] == "modeled"
    assert all(
        event["no_trade_member_count"] == event["member_count"]
        for event in solved.scope_solve_events
        if event["scope_node_id"] in {"parent", "a"}
    )


@pytest.mark.parametrize("allocation_basis", ["weight", "risk_budget"])
def test_research_targets_depend_on_current_members_and_data_not_legacy_scope_metadata(monkeypatch, allocation_basis):
    base = risk_budget_state()
    configuration = {
        "base_currency": "CNY",
        "taxonomy": {
            "taxonomy_id": base.planning_taxonomy_id,
            "name": base.taxonomy_name,
            "status": "active",
            "root_allocation_basis": allocation_basis,
        },
        "taxonomy_nodes": [
            {"taxonomy_node_id": node_id, "status": "active", **node}
            for node_id, node in base.node_by_id.items()
        ],
        "taxonomy_assignments": [
            {"taxonomy_node_id": node_id, "status": "active", **assignment}
            for node_id, assignments in base.direct_assignments_by_node.items()
            for assignment in assignments
        ],
        "target_sets": [
            {"target_set_type": target_set_type, "comparator_taxonomy_node_id": scope_id, **target_set}
            for (scope_id, target_set_type), target_sets in base.target_sets_by_scope_type.items()
            for target_set in target_sets
        ],
        "target_set_lines": [
            {"target_set_id": set_id, "target_member_type": member_type, "target_member_id": member_id, **line}
            for set_id, lines in base.target_lines_by_set_id.items()
            for (member_type, member_id), line in lines.items()
        ],
    }
    monkeypatch.setattr(solver, "get_portfolio", lambda _portfolio_id: {"base_currency": "CNY"})
    monkeypatch.setattr(solver, "list_accounts", lambda _portfolio_id: [])
    archived_configuration = {
        **deepcopy(configuration),
        "instrument_analytics_scopes": {
            instrument_id: {
                "risk_eligible": False,
                "risk_budget_eligible": False,
                "exclusion_reason": "Legacy exclusion",
            }
            for instrument_id in base.instrument_detail_cache
        },
    }
    weights = []
    for snapshot in (configuration, archived_configuration):
        state = solver._build_taxonomy_state(
            base.portfolio_id,
            planning_taxonomy_id=base.planning_taxonomy_id,
            as_of_date=base.as_of_date,
            target_configuration=snapshot,
            instrument_detail_cache=deepcopy(base.instrument_detail_cache),
            direct_fx_instruments={},
        )
        solved = solver._solve_current_scope(
            state,
            scope_node_id=None,
            as_of_date=state.as_of_date,
            lookback_days=30,
            calculation_frequency="daily",
            capital_mode="unit_notional",
            gross_exposure=None,
            target_volatility=None,
            max_gross_exposure=None,
            missing_return_policy="strict",
            apply_capital_overlay=True,
            risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": "abs", "parameters": {"min_observations": 15}},
            include_actuals=False,
            resolve_frozen_actuals=False,
        )
        weights.append({row["member_id"]: row["target_weight"] for row in solved.leaf_target_rows})

    assert weights[0] == pytest.approx(weights[1])
    assert weights[0]["a"] > 0.0
    assert weights[0]["b"] > 0.0
    assert sum(weights[0].values()) == pytest.approx(1.0)
    if allocation_basis == "weight":
        assert weights[0] == pytest.approx({"a": 0.5, "b": 0.5, "__derivatives__": 0.0, "__cash__": 0.0})


def test_current_policy_uses_market_history_without_target_creation_cutoff(monkeypatch):
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"inception_date": "2026-01-01"})
    stub_inception_statement(monkeypatch, solver)
    state = replace(allocation_state(), frozen_taxonomy_node_ids=frozenset({'a'}))
    graph_before = deepcopy((state.node_by_id, state.direct_assignments_by_node, state.target_lines_by_set_id))
    period_states = []
    observed_prices = []
    original_solve = solver.solve_current_target_weights
    def solve(*args, **kwargs):
        period_state = kwargs['_state']
        assert period_state.as_of_date == kwargs['as_of_date']
        assert not period_state.frozen_taxonomy_node_ids
        assert period_state.current_valuation_cache == {}
        period_state.current_valuation_cache['test_decision_date'] = kwargs['as_of_date']
        period_states.append(period_state)
        observations, _ = solver._build_instrument_nav_series(period_state,
            instrument_id='a', start_date=date(1900, 1, 1), end_date=period_state.as_of_date,
            warn_on_start_clip=False)
        assert observations.index[-1] <= period_state.as_of_date
        observed_prices.append(observations.iloc[-1])
        return original_solve(*args, **kwargs)
    monkeypatch.setattr(solver, 'solve_current_target_weights', solve)
    snapshot = {
        "taxonomy_nodes": [{"taxonomy_node_id": "node", "status": "active"}],
        "taxonomy_assignments": [{"taxonomy_node_id": "node", "target_scope": "instrument", "target_entity_id": key, "status": "active"} for key in ("a", "b")],
    }
    captures = []
    builds = []
    def capture(*args, **kwargs):
        captures.append(True)
        return snapshot
    def build(*args, **kwargs):
        builds.append(kwargs['target_configuration'])
        return state
    monkeypatch.setattr(solver, "capture_current_target_configuration", capture)
    monkeypatch.setattr(solver, "_build_taxonomy_state", build)
    result = solver.build_current_target_backtest(
        "review", planning_taxonomy_id="review-taxonomy", comparator_taxonomy_node_id=None,
        as_of_date=state.as_of_date, lookback_days=90, capital_mode="unit_notional", gross_exposure=None, target_volatility=None, max_gross_exposure=None,
        _direct_fx_instruments={},
    )["backtest"]
    assert result["points"]
    assert result["point_in_time_coverage"]["first_decision_date"] < "2026-06-01"
    assert result["execution_records"][0]["derivative_target_weight"] == pytest.approx(0.0)
    assert result["execution_records"][0]["derivative_no_trade"] is True

    assert captures == [True]
    assert len(builds) == 1
    assert len(period_states) > 2
    assert len({id(item.current_valuation_cache) for item in period_states}) == len(period_states)
    assert len(set(observed_prices)) > 1
    assert state.current_valuation_cache == {}
    assert state.frozen_taxonomy_node_ids == frozenset({'a'})
    assert (state.node_by_id, state.direct_assignments_by_node, state.target_lines_by_set_id) == graph_before
    assert all(configuration is snapshot for configuration in builds)
    assert result['methodology']['target_configuration'] == 'current_snapshot'
    assert "walk_forward" not in result


def test_derivative_backtest_reuses_simulation_capital_with_costs_and_lifecycle_funding(monkeypatch):
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"inception_date": "2026-01-01"})
    stub_inception_statement(monkeypatch, solver, cash=60.0, derivative=40.0)
    state = replace(allocation_state(), frozen_taxonomy_node_ids=frozenset({"a"}))
    state.target_lines_by_set_id["targets"][("cash_bucket", "__cash__")]["target_value"] = 0.0
    configuration = {
        "taxonomy_nodes": [{"taxonomy_node_id": "node", "status": "active"}],
        "taxonomy_assignments": [{"taxonomy_node_id": "node", "target_scope": "instrument", "target_entity_id": key, "status": "active"} for key in ("a", "b")],
    }
    events = [{"effective_date": day, "target_value": amount, "actual_value_base": amount * 100.0}
              for day, amount in [("2026-01-01", 0.4), ("2026-05-15", 0.45)]]
    monkeypatch.setattr(solver, "_build_derivative_backtest_context", lambda *args, **kwargs: {
        "events": events, "nav_points": [{"date": "2026-01-01", "nav": 1_000_000.0}], "warnings": [],
    })
    decisions = []
    original_solve = solver.solve_current_target_weights
    def observe_solve(*args, **kwargs):
        result = original_solve(*args, **kwargs)
        decisions.append((kwargs["as_of_date"], kwargs["_fixed_derivative_weight_override"]))
        assert not kwargs["_state"].frozen_taxonomy_node_ids
        return result
    monkeypatch.setattr(solver, "solve_current_target_weights", observe_solve)
    result = solver.build_current_target_backtest(
        "review", planning_taxonomy_id="review-taxonomy", comparator_taxonomy_node_id=None,
        as_of_date=state.as_of_date, target_configuration=configuration, lookback_days=90,
        capital_mode="unit_notional", gross_exposure=None, target_volatility=None, max_gross_exposure=None,
        cash_yield_annual=0.0, _state=state,
    )["backtest"]
    assert len(result["execution_records"]) >= 3
    assert any(row["actual_execution_date"] == "2026-05-15" for row in result["execution_records"])
    assert any(abs(weight - 0.4) > 1e-3 for day, weight in decisions if day > date(2026, 4, 1))
    for row in result["execution_records"]:
        weights = {item["instrument_id"]: item["target_weight"] for item in row["target_weights"]}
        assert weights["a"] / weights["b"] == pytest.approx(1.5, abs=1e-5)
        assert row["cash_target_weight"] == pytest.approx(0.0, abs=1e-8)
        assert row["derivative_target_value"] == (0.4 if row["actual_execution_date"] < "2026-05-15" else 0.45)
        assert row["derivative_leg_turnover"] == 0.0
    assert result["total_cost"] > 0.0
    assert max(abs(row["residual"]) for row in result["contribution_reconciliation_points"]) < 1e-10


def test_risk_window_uses_pre_weekend_close_and_does_not_fill_stale_tail():
    members = [solver.ScopeMemberRecord("instrument", key, key) for key in ("a", "b")]
    series = {
        ("instrument", "a"): pd.Series([100, 110, 121], index=[date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2)]),
        ("instrument", "b"): pd.Series([100, 105], index=[date(2026, 5, 29), date(2026, 6, 1)]),
    }
    aligned, _, _ = solver._align_member_series(
        members, series, start_date=date(2026, 5, 31), end_date=date(2026, 6, 2),
    )
    assert aligned[0].returns.loc[date(2026, 6, 1)] == pytest.approx(0.1)
    assert aligned[1].returns.loc[date(2026, 6, 1)] == pytest.approx(0.05)
    assert pd.isna(aligned[1].returns.loc[date(2026, 6, 2)])


def test_frozen_risk_sleeve_is_part_of_the_full_risk_budget_equation():
    state = risk_budget_state(frozen=frozenset({"a"}))
    state.current_valuation_cache["actual-valuation::2026-06-30"] = {
        "position_value_by_instrument": {"a": 80.0, "b": 20.0},
        "cash_value_by_account": {},
        "derivative_total_value": 0.0,
    }

    solved = solver._solve_current_scope(
        state,
        scope_node_id=None,
        as_of_date=state.as_of_date,
        lookback_days=30,
        calculation_frequency="daily",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        apply_capital_overlay=True,
        risk_model_config={
            "covariance_model_id": "sample_covariance",
            "contribution_mode": "abs",
            "parameters": {"min_observations": 15},
        },
        include_actuals=True,
        resolve_frozen_actuals=True,
    )

    weights = {row["member_id"]: row["target_weight"] for row in solved.member_target_rows}
    assert weights["a"] == pytest.approx(0.8)
    assert weights["b"] == pytest.approx(0.2)
    assert solved.solve_event["max_risk_share_gap"] > 0.4
    assert solved.solve_event["target_status"] == "constrained_optimum"
    assert solved.solve_event["execution_ready"] is True
    frozen = next(row for row in solved.member_target_rows if row["member_id"] == "a")
    assert frozen["trade_constraint"] == "no_trade"
    assert frozen["risk_model_status"] == "modeled"


def test_historical_simulation_uses_converged_global_solution_under_binding_constraints(monkeypatch):
    monkeypatch.setattr(solver, "get_portfolio", lambda _: {"inception_date": "2026-01-01"})
    stub_inception_statement(monkeypatch, solver)
    state = risk_budget_state(top_bounds={"a": {"min_weight": None, "max_weight": 0.1}})
    monkeypatch.setattr(solver, "_build_taxonomy_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(solver, "capture_current_target_configuration", lambda *args, **kwargs: {
        "taxonomy_nodes": [{"taxonomy_node_id": "node", "status": "active"}],
        "taxonomy_assignments": [{"taxonomy_node_id": "node", "target_scope": "instrument", "target_entity_id": key, "status": "active"} for key in ("a", "b")],
    })

    result = solver.build_current_target_backtest(
        "review",
        planning_taxonomy_id="review-taxonomy",
        comparator_taxonomy_node_id=None,
        as_of_date=state.as_of_date,
        lookback_days=30,
        calculation_frequency="daily",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        rebalance_frequency="1m",
        top_sleeve_weight_bounds=[{"taxonomy_node_id": "a", "max_weight": 0.1}],
        risk_model_config={
            "covariance_model_id": "sample_covariance",
            "contribution_mode": "abs",
            "parameters": {"min_observations": 15},
        },
        _direct_fx_instruments={},
    )["backtest"]

    assert result["point_in_time_coverage"]["status"] in {"complete", "partial"}
    assert result["execution_records"]
    first_weights = {item["instrument_id"]: item["target_weight"] for item in result["execution_records"][0]["target_weights"]}
    assert first_weights["a"] == pytest.approx(0.1, abs=1e-6)
    assert any("not a proof of a global optimum" in warning for warning in result["warnings"])


def test_overlay_preserves_explicit_derivative_target_and_routes_financing_to_cash():
    derivative_key = f"{solver.TARGET_MEMBER_DERIVATIVE}::{solver.SYSTEM_DERIVATIVE_TARGET_MEMBER_ID}"
    cash_key = f"{solver.TARGET_MEMBER_CASH}::{solver.SYSTEM_CASH_TARGET_MEMBER_ID}"
    allocated = solver._allocate_fixed_capital_weights(
        member_index=[derivative_key, cash_key],
        preferred_weights=pd.Series({derivative_key: 0.3, cash_key: 0.1}),
        total_weight=-0.5,
    )

    assert allocated[derivative_key] == pytest.approx(0.3)
    assert allocated[cash_key] == pytest.approx(-0.8)
    assert allocated.sum() == pytest.approx(-0.5)


def test_covariance_honors_configured_trailing_staleness_parameter():
    returns = pd.DataFrame(
        {"a": [0.01, -0.01, 0.02], "b": [-0.005, 0.004, 0.003]},
        index=[date(2026, 1, day) for day in (6, 7, 8)],
    )
    with pytest.raises(ValueError, match="maximum allowed for daily is 0 days"):
        solver._estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 2, "max_period_staleness_days": 0},
            missing_return_policy="strict",
            calculation_frequency="daily",
            as_of_date=date(2026, 1, 10),
        )


def test_backtest_treats_excess_early_complete_case_loss_as_a_warmup_gap():
    error = ValueError(
        "Risk budget solve complete-case drop would remove 2 of 4 return rows "
        "(50.00%), exceeding the 10.00% limit."
    )
    assert solver._is_rebalance_data_gap_error(error) is True


def test_complete_case_coverage_reports_leading_boundary_separately_from_later_gaps():
    returns = pd.DataFrame(
        {
            "a": [None, None, 0.01, 0.02, 0.01, 0.03, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02],
            "b": [0.01, 0.01, 0.02, 0.01, None, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01],
        },
        index=[date(2026, 1, day) for day in range(1, 13)],
        dtype="float64",
    )

    coverage = solver._apply_missing_return_policy(
        returns,
        min_observations=5,
        label="QA",
        missing_return_policy="complete_case_drop",
        calculation_frequency="daily",
        as_of_date=date(2026, 1, 12),
    )

    assert coverage.leading_incomplete_row_count == 2
    assert coverage.post_warmup_missing_row_count == 1
    assert coverage.post_warmup_missing_row_fraction == pytest.approx(0.1)
    assert coverage.missing_row_count == 3
    assert coverage.rows_after == 9


def test_global_covariance_failure_reports_the_selected_research_scope(monkeypatch):
    state = replace(
        risk_budget_state(),
        children_by_parent={None: ["parent"], "parent": ["a", "b"]},
        target_sets_by_scope_type={
            (None, "saa"): [{"target_set_id": "root", "status": "active"}],
            ("parent", "saa"): [{"target_set_id": "child", "status": "active"}],
        },
        target_lines_by_set_id={
            "root": {("taxonomy_node", "parent"): {"target_value": 1.0}},
            "child": {
                ("taxonomy_node", "a"): {"target_value": 0.5},
                ("taxonomy_node", "b"): {"target_value": 0.5},
            },
        },
    )
    state.node_by_id["parent"] = {"node_name": "Parent", "allocation_basis": "risk_budget"}
    state.node_path_by_id.update({"parent": "Top Level / Parent", "a": "Top Level / Parent / a", "b": "Top Level / Parent / b"})
    state.node_depth_by_id.update({"parent": 1, "a": 2, "b": 2})
    state.node_subtree_by_id["parent"] = {"parent", "a", "b"}
    monkeypatch.setattr(solver, "_solver_return_window", lambda **kwargs: pd.DataFrame())

    with pytest.raises(ValueError, match="Top Level global leaf risk model unavailable"):
        solver._solve_current_scope(
            state,
            scope_node_id=None,
            as_of_date=state.as_of_date,
            lookback_days=30,
            calculation_frequency="daily",
            capital_mode="unit_notional",
            gross_exposure=None,
            target_volatility=None,
            max_gross_exposure=None,
            missing_return_policy="strict",
            apply_capital_overlay=True,
            risk_model_config={"covariance_model_id": "sample_covariance", "contribution_mode": "signed"},
            include_actuals=False,
        )


def test_sample_covariance_matches_independent_n_minus_one_formula():
    dates = [day.date() for day in pd.bdate_range("2026-01-05", periods=8)]
    returns = pd.DataFrame(
        {
            "equity": [0.01, -0.02, 0.015, 0.005, -0.01, 0.012, 0.003, -0.004],
            "bonds": [0.002, 0.004, -0.001, 0.003, 0.001, -0.002, 0.002, 0.001],
            "gold": [-0.005, 0.008, 0.004, -0.003, 0.006, 0.002, -0.001, 0.007],
        },
        index=dates,
    )
    annualization = solver._infer_periods_per_year(dates)
    expected = np.cov(returns.to_numpy(), rowvar=False, ddof=1) * annualization

    actual = solver.estimate_covariance(
        returns,
        model_id="sample_covariance",
        lookback_days=30,
        parameters={"min_observations": 2, "max_period_staleness_days": 5},
        as_of_date=dates[-1],
    )

    assert actual.to_numpy() == pytest.approx(expected, rel=1e-11, abs=1e-12)


def test_ewma_covariance_matches_independent_weighted_formula():
    dates = [day.date() for day in pd.bdate_range("2026-01-05", periods=7)]
    values = np.asarray(
        [
            [0.010, 0.002],
            [-0.020, 0.004],
            [0.015, -0.001],
            [0.005, 0.003],
            [-0.010, 0.001],
            [0.012, -0.002],
            [0.003, 0.002],
        ]
    )
    decay = 0.91
    raw_weights = decay ** np.arange(len(values) - 1, -1, -1)
    weights = raw_weights / raw_weights.sum()
    mean = np.average(values, axis=0, weights=weights)
    centered = values - mean
    expected = (centered * weights[:, None]).T @ centered
    expected *= solver._infer_periods_per_year(dates)

    actual = solver.estimate_covariance(
        pd.DataFrame(values, index=dates, columns=["equity", "bonds"]),
        model_id="ewma_covariance",
        lookback_days=30,
        parameters={"min_observations": 2, "decay": decay, "max_period_staleness_days": 5},
        as_of_date=dates[-1],
    )

    assert actual.to_numpy() == pytest.approx(expected, rel=1e-11, abs=1e-12)


def test_composite_covariance_preserves_ewma_vol_and_shrinks_correlation():
    dates = [day.date() for day in pd.bdate_range("2026-01-05", periods=9)]
    returns = pd.DataFrame(
        {
            "equity": [0.01, -0.02, 0.015, 0.005, -0.01, 0.012, 0.003, -0.004, 0.006],
            "bonds": [0.002, 0.004, -0.001, 0.003, 0.001, -0.002, 0.002, 0.001, -0.001],
            "gold": [-0.005, 0.008, 0.004, -0.003, 0.006, 0.002, -0.001, 0.007, 0.003],
        },
        index=dates,
    )
    decay = 0.93
    shrinkage = 0.2
    parameters = {
        "min_observations": 2,
        "corr_min_observations": 2,
        "vol_decay": decay,
        "corr_shrinkage": shrinkage,
        "max_period_staleness_days": 5,
    }
    actual = solver.estimate_covariance(
        returns,
        model_id="ewma_vol_shrinkage_corr_covariance",
        lookback_days=30,
        parameters=parameters,
        as_of_date=dates[-1],
    )
    ewma = solver.estimate_covariance(
        returns,
        model_id="ewma_covariance",
        lookback_days=30,
        parameters={"min_observations": 2, "decay": decay, "max_period_staleness_days": 5},
        as_of_date=dates[-1],
    )
    expected_corr = (1 - shrinkage) * returns.corr().to_numpy() + shrinkage * np.eye(3)
    expected_vol = np.sqrt(np.diag(ewma.to_numpy()))
    expected = np.diag(expected_vol) @ expected_corr @ np.diag(expected_vol)

    assert actual.to_numpy() == pytest.approx(expected, rel=1e-10, abs=1e-11)
    assert np.linalg.eigvalsh(actual.to_numpy()).min() >= -1e-12


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"covariance_model_id": "mystery_covariance"}, "Unsupported production covariance model"),
        ({"contribution_mode": "automatic"}, "Unsupported production risk-contribution mode"),
    ],
)
def test_production_risk_policy_rejects_unknown_methods(policy, message):
    with pytest.raises(ValueError, match=message):
        normalize_portfolio_risk_policy(policy)
