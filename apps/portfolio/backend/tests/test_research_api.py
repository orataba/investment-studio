from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from math import sqrt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError

from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import research as research_service
from portfolio_app.services import research_solver as research_solver_service
from portfolio_app.services.instrument_charts import (
    _annualized_volatility,
    _candidate_chart_bases,
    build_instrument_trend_metrics_from_detail,
)
from portfolio_app.services.risk_model import normalize_portfolio_risk_policy, risk_min_observations_for_window

from portfolio_app.services.research_solver import (
    CAPITAL_MODE_TARGET_VOLATILITY,
    CAPITAL_MODE_VOLATILITY_CAP,
    RESEARCH_BACKTEST_METHODOLOGY_WARNINGS,
    RiskBudgetProblem,
    RiskBudgetSolution,
    SYSTEM_CASH_TARGET_MEMBER_ID,
    SYSTEM_CASH_TARGET_LABEL,
    SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
    TARGET_MEMBER_CASH,
    TARGET_MEMBER_DERIVATIVE,
    TARGET_MEMBER_INSTRUMENT,
    TARGET_MEMBER_NODE,
    ScopeMemberRecord,
    TaxonomyResearchState,
    _historical_backtest_instrument_ids,
    _align_member_series,
    _backtest_return_map_from_points,
    _backtest_rebalance_dates,
    _build_backtest_metrics,
    _build_backtest_sampled_nav_by_instrument,
    _build_leaf_target_weight_gaps,
    _build_sampled_benchmark_points,
    _build_taxonomy_state,
    _estimate_covariance,
    _infer_periods_per_year,
    _is_better_risk_budget_solution,
    _periodic_nav_series,
    _rebalance_schedule,
    _resolve_active_top_sleeve_bound_vectors,
    _selected_price_points,
    _selected_target_risk_share,
    _current_scope_actuals,
    _series_to_nav,
    _resolve_volatility_overlay_gross_exposure,
    _replay_backtest_decisions,
    _solve_current_scope,
    _solve_risk_budget_problem,
    _solve_risk_budget_weights,
    _top_sleeve_for_member,
    _build_walk_forward_validation,
    build_current_target_backtest,
    research_window_start_date,
)

EFFECTIVE_FROM = "2026-01-01"

def _create_planning_taxonomy(client, *, root_default_target_dimension: str = "weight") -> tuple[str, dict[str, str]]:
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
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
    ]:
        node_response = client.post(
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
            json={"effective_from": EFFECTIVE_FROM, **payload},
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
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
            json={"effective_from": EFFECTIVE_FROM, **payload},
        )
        assert node_response.status_code == 200
        node_ids[payload["node_name"]] = node_response.json()["taxonomy_node_id"]

    for assignment in [
        ("instrument", "equity-us-abbv", "Defensive Equity"),
        ("instrument", "fund-hk-2800", "Hong Kong Beta"),
        ("instrument", "fund-us-agg", "Rates"),
    ]:
        assignment_response = client.post(
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
            json={"effective_from": EFFECTIVE_FROM,
                "target_scope": assignment[0],
                "target_entity_id": assignment[1],
                "taxonomy_node_id": node_ids[assignment[2]],
            },
        )
        assert assignment_response.status_code == 200

    default_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM,"taxonomy_id": taxonomy_id},
    )
    assert default_response.status_code == 200
    return taxonomy_id, node_ids


def test_risk_target_resolution_excludes_cash_but_preserves_capital_context() -> None:
    state = TaxonomyResearchState(
        portfolio_id="portfolio-cash-contract",
        planning_taxonomy_id="taxonomy-cash-contract",
        taxonomy_name="Planning",
        root_default_target_dimension="risk_budget",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={
            (None, "taa"): [
                {
                    "target_set_id": "target-cash-contract",
                    "target_set_type": "taa",
                    "name": "Combined Target",
                    "weight_enabled": True,
                    "risk_budget_enabled": True,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "target-cash-contract": {
                (TARGET_MEMBER_INSTRUMENT, "asset-a"): {
                    "target_weight": 0.8,
                    "target_risk_share": 1.0,
                },
                (TARGET_MEMBER_CASH, SYSTEM_CASH_TARGET_MEMBER_ID): {
                    "target_weight": 0.2,
                    "target_risk_share": None,
                },
            }
        },
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    members = [
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="asset-a",
            label="Asset A",
        ),
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_CASH,
            member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
            label=SYSTEM_CASH_TARGET_LABEL,
        ),
    ]

    rows, warnings = research_solver_service._resolve_dimension_target_rows(
        state,
        scope_node_id=None,
        scope_members=members,
        as_of_date=date(2026, 1, 2),
        selected_dimension="risk_budget",
    )

    assert warnings == []
    risky_row = next(row for row in rows if row["member_type"] == TARGET_MEMBER_INSTRUMENT)
    cash_row = next(row for row in rows if row["member_type"] == TARGET_MEMBER_CASH)
    assert risky_row["selected_value"] == pytest.approx(1.0)
    assert risky_row["target_risk_share"] == pytest.approx(1.0)
    assert cash_row["target_weight"] == pytest.approx(0.2)
    assert cash_row["selected_value"] is None
    assert cash_row["target_risk_share"] is None


def test_cash_is_rendered_as_cash_instead_of_unassigned() -> None:
    state = TaxonomyResearchState(
        portfolio_id="portfolio-cash-label",
        planning_taxonomy_id="taxonomy-cash-label",
        taxonomy_name="Planning",
        root_default_target_dimension="risk_budget",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )

    assert _top_sleeve_for_member(
        state,
        member_type=TARGET_MEMBER_CASH,
        member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
    ) == (SYSTEM_CASH_TARGET_MEMBER_ID, SYSTEM_CASH_TARGET_LABEL, SYSTEM_CASH_TARGET_LABEL)


def test_risk_target_resolution_is_unavailable_for_cash_only_scope() -> None:
    state = TaxonomyResearchState(
        portfolio_id="portfolio-cash-only",
        planning_taxonomy_id="taxonomy-cash-only",
        taxonomy_name="Planning",
        root_default_target_dimension="risk_budget",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )

    with pytest.raises(ValueError, match="risk budget is unavailable: no risky members"):
        research_solver_service._resolve_dimension_target_rows(
            state,
            scope_node_id=None,
            scope_members=[
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_CASH,
                    member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
                    label=SYSTEM_CASH_TARGET_LABEL,
                )
            ],
            as_of_date=date(2026, 1, 2),
            selected_dimension="risk_budget",
        )


def test_single_risky_member_with_cash_does_not_bypass_incomplete_enabled_target_set() -> None:
    state = TaxonomyResearchState(
        portfolio_id="portfolio-incomplete-single-risky",
        planning_taxonomy_id="taxonomy-incomplete-single-risky",
        taxonomy_name="Planning",
        root_default_target_dimension="risk_budget",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={
            (None, "taa"): [
                {
                    "target_set_id": "target-incomplete-single-risky",
                    "target_set_type": "taa",
                    "name": "Incomplete Combined Target",
                    "weight_enabled": True,
                    "risk_budget_enabled": True,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "target-incomplete-single-risky": {
                (TARGET_MEMBER_CASH, SYSTEM_CASH_TARGET_MEMBER_ID): {
                    "target_weight": 0.2,
                    "target_risk_share": None,
                }
            }
        },
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )

    with pytest.raises(ValueError, match="target set is incomplete.*Asset A"):
        research_solver_service._resolve_dimension_target_rows(
            state,
            scope_node_id=None,
            scope_members=[
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_INSTRUMENT,
                    member_id="asset-a",
                    label="Asset A",
                ),
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_CASH,
                    member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
                    label=SYSTEM_CASH_TARGET_LABEL,
                ),
            ],
            as_of_date=date(2026, 1, 2),
            selected_dimension="risk_budget",
        )


def test_current_target_solve_fails_closed_for_unassigned_non_cash_holding(monkeypatch):
    state = TaxonomyResearchState(
        portfolio_id="portfolio-unassigned-test",
        planning_taxonomy_id="taxonomy-test",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={
            "node-risk": {
                "node_name": "Risk",
                "parent_taxonomy_node_id": None,
                "default_target_dimension": "weight",
            }
        },
        children_by_parent={None: ["node-risk"]},
        node_path_by_id={"node-risk": "Portfolio / Risk"},
        node_depth_by_id={"node-risk": 1},
        node_subtree_by_id={"node-risk": {"node-risk"}},
        direct_assignments_by_node={},
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    monkeypatch.setattr(
        "portfolio_app.services.research_solver.get_portfolio",
        lambda _portfolio_id: {"portfolio_id": "portfolio-unassigned-test", "base_currency": "USD"},
    )
    monkeypatch.setattr("portfolio_app.services.research_solver.list_accounts", lambda _portfolio_id: [])
    monkeypatch.setattr("portfolio_app.services.research_solver.list_transactions", lambda _portfolio_id: [])
    monkeypatch.setattr(
        "portfolio_app.services.research_solver.build_holdings_report",
        lambda *_args, **_kwargs: {
            "positions": [
                {
                    "instrument_id": "instrument-unassigned",
                    "market_value_base": 100.0,
                }
            ]
        },
    )
    monkeypatch.setattr(
        "portfolio_app.services.research_solver.build_account_workspace",
        lambda *_args, **_kwargs: {"accounts": []},
    )

    with pytest.raises(
        ValueError,
        match="complete planning-taxonomy coverage.*instrument-unassigned",
    ):
        _current_scope_actuals(
            state,
            scope_node_id=None,
            as_of_date=date(2026, 1, 2),
        )


def test_current_scope_actuals_keeps_derivatives_and_all_account_liquidity_as_zero_volatility_weight(monkeypatch):
    state = TaxonomyResearchState(
        portfolio_id="portfolio-derivative-boundary",
        planning_taxonomy_id="taxonomy-test",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={
            "node-risk": {
                "node_name": "Risk",
                "parent_taxonomy_node_id": None,
                "default_target_dimension": "weight",
            }
        },
        children_by_parent={None: ["node-risk"]},
        node_path_by_id={"node-risk": "Portfolio / Risk"},
        node_depth_by_id={"node-risk": 1},
        node_subtree_by_id={"node-risk": {"node-risk"}},
        direct_assignments_by_node={
            "node-risk": [
                {
                    "target_scope": "instrument",
                    "target_entity_id": "ordinary-fund",
                }
            ]
        },
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    monkeypatch.setattr(
        research_solver_service,
        "get_portfolio",
        lambda _portfolio_id: {
            "portfolio_id": "portfolio-derivative-boundary",
            "base_currency": "USD",
        },
    )
    monkeypatch.setattr(research_solver_service, "list_accounts", lambda _portfolio_id: [])
    monkeypatch.setattr(research_solver_service, "list_transactions", lambda _portfolio_id: [])
    monkeypatch.setattr(
        research_solver_service,
        "build_holdings_report",
        lambda *_args, **_kwargs: {
            "positions": [
                {
                    "instrument_id": "ordinary-fund",
                    "instrument_ref": {
                        "instrument_id": "ordinary-fund",
                        "instrument_type": "public_fund",
                    },
                    "market_value_base": 100.0,
                },
                {
                    "instrument_id": None,
                    "derivative_contract_id": "fcn-tracking",
                    "derivative_contract": {
                        "derivative_contract_id": "fcn-tracking",
                        "portfolio_id": "portfolio-derivative-boundary",
                        "account_id": "broker",
                        "contract_name": "FCN tracking",
                        "contract_type": "fcn",
                        "currency": "USD",
                        "terms": {
                            "notional": "50",
                            "annual_coupon_rate_pct": None,
                            "issue_date": "2026-01-01",
                            "final_observation_date": None,
                            "maturity_date": "2026-12-31",
                            "issuer": "Test Issuer",
                            "counterparty": "Test Broker",
                            "underlyings": [
                                {
                                    "instrument_id": "ordinary-fund",
                                    "initial_reference_price": None,
                                    "strike_level_pct": None,
                                    "knock_in_level_pct": None,
                                    "knock_out_level_pct": None,
                                    "deliverable": True,
                                }
                            ],
                        },
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    "market_value_base": 50.0,
                },
            ]
        },
    )
    monkeypatch.setattr(
        research_solver_service,
        "build_account_workspace",
        lambda *_args, **_kwargs: {
            "accounts": [
                {
                    "account": {
                        "account_id": "broker",
                        "account_type": "securities_account",
                    },
                    "derived_cash_balance_base": 20.0,
                    "pending_settlement_base": 10.0,
                },
                {
                    "account": {
                        "account_id": "deposit",
                        "account_type": "deposit_account",
                    },
                    "derived_cash_balance_base": 20.0,
                    "pending_settlement_base": 0.0,
                },
            ]
        },
    )

    rows, warnings = _current_scope_actuals(
        state,
        scope_node_id=None,
        as_of_date=date(2026, 1, 2),
    )

    risk_row = next(row for row in rows if row["member_id"] == "node-risk")
    derivative_row = next(
        row for row in rows if row["member_id"] == SYSTEM_DERIVATIVE_TARGET_MEMBER_ID
    )
    cash_row = next(
        row for row in rows if row["member_id"] == SYSTEM_CASH_TARGET_MEMBER_ID
    )
    assert risk_row["current_value_base"] == pytest.approx(100.0)
    assert risk_row["current_weight"] == pytest.approx(0.5)
    assert derivative_row["current_value_base"] == pytest.approx(50.0)
    assert derivative_row["current_weight"] == pytest.approx(0.25)
    assert cash_row["current_value_base"] == pytest.approx(50.0)
    assert cash_row["current_weight"] == pytest.approx(0.25)
    assert warnings == [
        "Derivative holdings retain carrying-value weights but are excluded from the risk solve and Risk Budget: fcn-tracking."
    ]


def test_taxonomy_state_uses_registry_instruments_only(monkeypatch) -> None:
    seeded_details = {
        "ordinary-fund": {
            "instrument_id": "ordinary-fund",
            "instrument_type": "public_fund",
        },
    }
    monkeypatch.setattr(
        research_solver_service,
        "get_portfolio",
        lambda _portfolio_id: {"portfolio_id": "portfolio", "base_currency": "USD"},
    )
    monkeypatch.setattr(
        research_solver_service,
        "taxonomy_configuration_as_of",
        lambda *_args, **_kwargs: {
            "taxonomy": {
                "taxonomy_id": "taxonomy",
                "name": "Planning",
                "root_default_target_dimension": "weight",
                "primary_assignment_scope": "instrument",
                "planning_enabled": True,
                "status": "active",
            },
            "taxonomy_nodes": [
                {
                    "taxonomy_node_id": "ordinary-node",
                    "node_name": "Ordinary",
                    "parent_taxonomy_node_id": None,
                    "status": "active",
                },
            ],
            "taxonomy_assignments": [
                {
                    "taxonomy_node_id": "ordinary-node",
                    "target_scope": "instrument",
                    "target_entity_id": "ordinary-fund",
                    "status": "active",
                },
            ],
            "target_sets": [],
            "target_set_lines": [],
            "configuration_version": 1,
            "effective_from": "2026-01-01",
        },
    )
    monkeypatch.setattr(research_solver_service, "list_accounts", lambda _portfolio_id: [])

    state = _build_taxonomy_state(
        "portfolio",
        planning_taxonomy_id="taxonomy",
        as_of_date=date(2026, 1, 2),
        instrument_detail_cache=seeded_details,
        direct_fx_instruments={},
    )
    members, _source = research_solver_service._scope_members(
        state,
        scope_node_id=None,
    )

    assert state.direct_assignments_by_node["ordinary-node"]
    assert [member.member_id for member in members] == [
        "ordinary-node",
        SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
        SYSTEM_CASH_TARGET_MEMBER_ID,
    ]


def test_current_scope_actuals_reuses_one_portfolio_valuation_across_scopes(monkeypatch):
    state = TaxonomyResearchState(
        portfolio_id="portfolio-cache-test",
        planning_taxonomy_id="taxonomy-test",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
        node_by_id={
            "node-risk": {
                "node_name": "Risk",
                "parent_taxonomy_node_id": None,
                "default_target_dimension": "weight",
            }
        },
        children_by_parent={None: ["node-risk"]},
        node_path_by_id={"node-risk": "Portfolio / Risk"},
        node_depth_by_id={"node-risk": 1},
        node_subtree_by_id={"node-risk": {"node-risk"}},
        direct_assignments_by_node={
            "node-risk": [
                {
                    "target_scope": "instrument",
                    "target_entity_id": "instrument-a",
                }
            ]
        },
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    calls = {"portfolio": 0, "accounts": 0, "transactions": 0, "holdings": 0, "workspace": 0}

    def fake_portfolio(_portfolio_id):
        calls["portfolio"] += 1
        return {"portfolio_id": "portfolio-cache-test", "base_currency": "USD"}

    def fake_accounts(_portfolio_id):
        calls["accounts"] += 1
        return [{"account_id": "cash-a", "account_type": "deposit_account"}]

    def fake_transactions(_portfolio_id):
        calls["transactions"] += 1
        return []

    def fake_holdings(*_args, **_kwargs):
        calls["holdings"] += 1
        return {"positions": [{"instrument_id": "instrument-a", "market_value_base": 100.0}]}

    def fake_workspace(*_args, **_kwargs):
        calls["workspace"] += 1
        return {
            "accounts": [
                {
                    "account": {"account_id": "cash-a", "account_type": "deposit_account"},
                    "derived_cash_balance_base": 50.0,
                    "pending_settlement_base": 0.0,
                }
            ]
        }

    monkeypatch.setattr("portfolio_app.services.research_solver.get_portfolio", fake_portfolio)
    monkeypatch.setattr("portfolio_app.services.research_solver.list_accounts", fake_accounts)
    monkeypatch.setattr("portfolio_app.services.research_solver.list_transactions", fake_transactions)
    monkeypatch.setattr("portfolio_app.services.research_solver.build_holdings_report", fake_holdings)
    monkeypatch.setattr("portfolio_app.services.research_solver.build_account_workspace", fake_workspace)

    root_rows, _warnings = _current_scope_actuals(state, scope_node_id=None, as_of_date=date(2026, 1, 2))
    child_rows, _warnings = _current_scope_actuals(state, scope_node_id="node-risk", as_of_date=date(2026, 1, 2))

    assert sum(float(row["current_weight"] or 0.0) for row in root_rows) == pytest.approx(1.0)
    assert child_rows[0]["current_weight"] == pytest.approx(1.0)
    assert calls == {"portfolio": 1, "accounts": 1, "transactions": 1, "holdings": 1, "workspace": 1}

    _current_scope_actuals(state, scope_node_id=None, as_of_date=date(2026, 1, 3))
    assert calls == {"portfolio": 2, "accounts": 2, "transactions": 2, "holdings": 2, "workspace": 2}


def test_taxonomy_state_reuses_seeded_market_data_caches(monkeypatch) -> None:
    seeded_details = {"instrument-a": {"instrument_id": "instrument-a", "market_data": []}}
    seeded_fx = {("HKD", "USD"): "fx-hkd-usd"}

    monkeypatch.setattr(
        "portfolio_app.services.research_solver.get_portfolio",
        lambda _portfolio_id: {"portfolio_id": "portfolio-cache-test", "base_currency": "USD"},
    )
    monkeypatch.setattr(
        "portfolio_app.services.research_solver.taxonomy_configuration_as_of",
        lambda _portfolio_id, _taxonomy_id, _as_of_date: {
            "taxonomy": {
                "taxonomy_id": "taxonomy-cache-test",
                "name": "Planning",
                "root_default_target_dimension": "risk_budget",
                "primary_assignment_scope": "instrument",
                "planning_enabled": True,
                "status": "active",
            },
            "taxonomy_nodes": [],
            "taxonomy_assignments": [],
            "target_sets": [],
            "target_set_lines": [],
            "configuration_version": 1,
            "effective_from": "2026-01-01",
        },
    )
    monkeypatch.setattr("portfolio_app.services.research_solver.list_accounts", lambda _portfolio_id: [])
    monkeypatch.setattr(
        "portfolio_app.services.research_solver.get_platform_fx_rates",
        lambda: pytest.fail("seeded FX map should avoid a platform reload"),
    )

    state = _build_taxonomy_state(
        "portfolio-cache-test",
        planning_taxonomy_id="taxonomy-cache-test",
        as_of_date=date(2026, 1, 2),
        instrument_detail_cache=seeded_details,
        direct_fx_instruments=seeded_fx,
    )

    assert state.instrument_detail_cache is seeded_details
    assert state.direct_fx_instruments is seeded_fx


def test_zero_risk_budget_member_is_excluded_from_covariance_and_kept_in_results() -> None:
    long_dates = [item.date() for item in pd.bdate_range("2026-01-05", periods=12)]
    return_paths = {
        "asset-a": [0.0, 0.010, -0.004, 0.006, 0.002, -0.003, 0.007, -0.002, 0.004, 0.001, -0.005, 0.006],
        "asset-b": [0.0, -0.003, 0.008, 0.001, -0.004, 0.006, -0.002, 0.005, -0.001, 0.007, 0.002, -0.003],
    }

    def detail(instrument_id: str, dates: list[date], returns: list[float]) -> dict[str, object]:
        value = 1.0
        points = []
        for point_date, point_return in zip(dates, returns, strict=True):
            value *= 1.0 + point_return
            points.append(
                {
                    "metric_family": "nav",
                    "as_of_date": point_date.isoformat(),
                    "value": value,
                    "quote_basis": "total_return_nav",
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": 1.0,
                    "status": "complete",
                }
            )
        return {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "public_fund",
            "currency": "USD",
            "quote_selection_policy": {"total_return": ["total_return_nav"]},
            "market_data": points,
        }

    short_dates = long_dates[-2:]
    state = TaxonomyResearchState(
        portfolio_id="portfolio-zero-budget",
        planning_taxonomy_id="taxonomy-zero-budget",
        taxonomy_name="Planning",
        root_default_target_dimension="risk_budget",
        base_currency="USD",
        as_of_date=long_dates[-1],
        node_by_id={
            "scope": {
                "node_name": "Risk Sleeve",
                "parent_taxonomy_node_id": None,
                "default_target_dimension": "risk_budget",
            }
        },
        children_by_parent={None: ["scope"]},
        node_path_by_id={"scope": "Top Level / Risk Sleeve"},
        node_depth_by_id={"scope": 1},
        node_subtree_by_id={"scope": {"scope"}},
        direct_assignments_by_node={
            "scope": [
                {"target_scope": "instrument", "target_entity_id": "asset-a"},
                {"target_scope": "instrument", "target_entity_id": "asset-b"},
                {"target_scope": "instrument", "target_entity_id": "asset-short"},
            ]
        },
        target_sets_by_scope_type={
            ("scope", "taa"): [
                {
                    "target_set_id": "target-zero-budget",
                    "target_set_type": "taa",
                    "name": "Zero budget exclusion",
                    "weight_enabled": False,
                    "risk_budget_enabled": True,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "target-zero-budget": {
                ("instrument", "asset-a"): {"target_risk_share": 0.5},
                ("instrument", "asset-b"): {"target_risk_share": 0.5},
                ("instrument", "asset-short"): {"target_risk_share": 0.0},
            }
        },
        account_name_by_id={},
        instrument_detail_cache={
            "asset-a": detail("asset-a", long_dates, return_paths["asset-a"]),
            "asset-b": detail("asset-b", long_dates, return_paths["asset-b"]),
            "asset-short": detail("asset-short", short_dates, [0.0, 0.02]),
        },
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )

    result = _solve_current_scope(
        state,
        scope_node_id="scope",
        as_of_date=long_dates[-1],
        lookback_days=30,
        calculation_frequency="daily",
        target_dimension="risk_budget",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        apply_capital_overlay=False,
        risk_model_config={
            "covariance_model_id": "sample_covariance",
            "contribution_mode": "signed",
            "parameters": {"min_observations": 5},
        },
        include_actuals=False,
    )

    row_by_id = {str(row["member_id"]): row for row in result.member_target_rows}
    assert row_by_id["asset-short"]["target_weight"] == pytest.approx(0.0)
    assert row_by_id["asset-a"]["target_weight"] + row_by_id["asset-b"]["target_weight"] == pytest.approx(1.0)
    assert result.solve_event["covariance_observations"] == 11
    assert min(result.return_series.dropna().index) < short_dates[0]
    assert any("excludes 0% risk budget members" in warning for warning in result.warnings)


def test_zero_weight_member_starting_after_early_rebalance_does_not_block_backtest(monkeypatch) -> None:
    active_dates = [item.date() for item in pd.bdate_range("2026-01-02", "2026-07-09")]
    late_dates = [item.date() for item in pd.bdate_range("2026-06-01", "2026-07-09")]

    def detail(instrument_id: str, dates: list[date], daily_return: float) -> dict[str, object]:
        value = 1.0
        points = []
        for index, point_date in enumerate(dates):
            value *= 1.0 + (daily_return if index % 2 == 0 else -daily_return / 2.0)
            points.append(
                {
                    "metric_family": "nav",
                    "as_of_date": point_date.isoformat(),
                    "value": value,
                    "quote_basis": "total_return_nav",
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": 1.0,
                    "status": "complete",
                }
            )
        return {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "public_fund",
            "currency": "USD",
            "quote_selection_policy": {"total_return": ["total_return_nav"]},
            "market_data": points,
        }

    state = TaxonomyResearchState(
        portfolio_id="portfolio-zero-weight",
        planning_taxonomy_id="taxonomy-zero-weight",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="USD",
        as_of_date=date(2026, 7, 9),
        node_by_id={
            "scope": {
                "node_name": "Low Correlation",
                "parent_taxonomy_node_id": None,
                "default_target_dimension": "weight",
            }
        },
        children_by_parent={None: ["scope"]},
        node_path_by_id={"scope": "Top Level / Low Correlation"},
        node_depth_by_id={"scope": 1},
        node_subtree_by_id={"scope": {"scope"}},
        direct_assignments_by_node={
            "scope": [
                {"target_scope": "instrument", "target_entity_id": "active"},
                {"target_scope": "instrument", "target_entity_id": "late-zero"},
            ]
        },
        target_sets_by_scope_type={
            ("scope", "taa"): [
                {
                    "target_set_id": "target-zero-weight",
                    "target_set_type": "taa",
                    "name": "Zero weight exclusion",
                    "weight_enabled": True,
                    "risk_budget_enabled": False,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "target-zero-weight": {
                ("instrument", "active"): {"target_weight": 1.0},
                ("instrument", "late-zero"): {"target_weight": 0.0},
            }
        },
        account_name_by_id={},
        instrument_detail_cache={
            "active": detail("active", active_dates, 0.002),
            "late-zero": detail("late-zero", late_dates, 0.003),
        },
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    monkeypatch.setattr(research_solver_service, "_build_taxonomy_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(
        research_solver_service,
        "taxonomy_configuration_revisions_through",
        lambda *_args, **_kwargs: [
            {
                "effective_from": "2026-01-01",
                "taxonomy_assignments": [
                    {
                        "status": "active",
                        "target_scope": "instrument",
                        "target_entity_id": "active",
                    },
                    {
                        "status": "active",
                        "target_scope": "instrument",
                        "target_entity_id": "late-zero",
                    },
                ],
            }
        ],
    )
    original_solve = research_solver_service.solve_current_target_weights
    period_solutions: list[dict[str, object]] = []
    frozen_actual_flags: list[bool] = []

    def capture_period_solve(*args, **kwargs):
        frozen_actual_flags.append(bool(kwargs.get("_resolve_frozen_actuals")))
        solution = original_solve(*args, **kwargs)
        period_solutions.append(solution)
        return solution

    monkeypatch.setattr(research_solver_service, "solve_current_target_weights", capture_period_solve)
    current_solution = {
        "leaf_targets": [
            {"member_type": "instrument", "member_id": "active", "target_weight": 1.0},
            {"member_type": "instrument", "member_id": "late-zero", "target_weight": 0.0},
        ],
        "calculation_frequency": {"resolved_frequency": "daily"},
    }

    payload = build_current_target_backtest(
        "portfolio-zero-weight",
        planning_taxonomy_id="taxonomy-zero-weight",
        comparator_taxonomy_node_id="scope",
        as_of_date=date(2026, 7, 9),
        lookback_days=30,
        calculation_frequency="daily",
        target_dimension="scope_default",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        rebalance_frequency="1m",
        current_solution=current_solution,
    )

    assert payload["backtest"]["points"]
    assert period_solutions
    assert frozen_actual_flags and all(frozen_actual_flags)
    assert not any(
        "research window is clipped" in warning
        for warning in payload["backtest"]["warnings"]
    )
    assert any(date.fromisoformat(str(solution["solve_event"]["as_of_date"])) < late_dates[0] for solution in period_solutions)
    for solution in period_solutions:
        row_by_id = {str(row["member_id"]): row for row in solution["leaf_targets"]}
        assert row_by_id["late-zero"]["target_weight"] == pytest.approx(0.0)


def test_positive_top_sleeve_minimum_overrides_zero_configured_weight(monkeypatch) -> None:
    dates = [item.date() for item in pd.bdate_range("2026-01-05", periods=8)]

    def detail(instrument_id: str, step: float) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "public_fund",
            "currency": "USD",
            "quote_selection_policy": {"total_return": ["total_return_nav"]},
            "market_data": [
                {
                    "metric_family": "nav",
                    "as_of_date": point_date.isoformat(),
                    "value": 1.0 + index * step,
                    "quote_basis": "total_return_nav",
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": 1.0,
                    "status": "complete",
                }
                for index, point_date in enumerate(dates)
            ],
        }

    state = TaxonomyResearchState(
        portfolio_id="portfolio-bound-zero",
        planning_taxonomy_id="taxonomy-bound-zero",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="USD",
        as_of_date=dates[-1],
        node_by_id={
            "node-a": {"node_name": "A", "parent_taxonomy_node_id": None, "default_target_dimension": "weight"},
            "node-b": {"node_name": "B", "parent_taxonomy_node_id": None, "default_target_dimension": "weight"},
        },
        children_by_parent={None: ["node-a", "node-b"]},
        node_path_by_id={"node-a": "Top Level / A", "node-b": "Top Level / B"},
        node_depth_by_id={"node-a": 1, "node-b": 1},
        node_subtree_by_id={"node-a": {"node-a"}, "node-b": {"node-b"}},
        direct_assignments_by_node={
            "node-a": [{"target_scope": "instrument", "target_entity_id": "asset-a"}],
            "node-b": [{"target_scope": "instrument", "target_entity_id": "asset-b"}],
        },
        target_sets_by_scope_type={
            (None, "taa"): [
                {
                    "target_set_id": "root-zero-with-min",
                    "target_set_type": "taa",
                    "name": "Root weights",
                    "weight_enabled": True,
                    "risk_budget_enabled": False,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "root-zero-with-min": {
                ("taxonomy_node", "node-a"): {"target_weight": 1.0},
                ("taxonomy_node", "node-b"): {"target_weight": 0.0},
            }
        },
        account_name_by_id={},
        instrument_detail_cache={"asset-a": detail("asset-a", 0.01), "asset-b": detail("asset-b", 0.005)},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={"node-b": {"min_weight": 0.2, "max_weight": None}},
    )

    result = _solve_current_scope(
        state,
        scope_node_id=None,
        as_of_date=dates[-1],
        lookback_days=30,
        calculation_frequency="daily",
        target_dimension="weight",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        apply_capital_overlay=False,
        include_actuals=False,
    )

    row_by_id = {str(row["member_id"]): row for row in result.member_target_rows}
    assert row_by_id["node-b"]["configured_weight"] == pytest.approx(0.0)
    assert row_by_id["node-b"]["target_weight"] == pytest.approx(0.2)
    assert row_by_id["node-a"]["target_weight"] == pytest.approx(0.8)

    frozen_state = replace(state, frozen_taxonomy_node_ids=frozenset({"node-b"}))

    def frozen_actuals(_state, *, scope_node_id, as_of_date):
        del _state, as_of_date
        if scope_node_id == "node-b":
            return ([{
                "member_type": TARGET_MEMBER_INSTRUMENT,
                "member_id": "asset-b",
                "label": "asset-b",
                "current_weight": 1.0,
                "current_value_base": 35.0,
            }], [])
        return ([
            {
                "member_type": TARGET_MEMBER_NODE,
                "member_id": "node-a",
                "label": "A",
                "current_weight": 0.65,
                "current_value_base": 65.0,
            },
            {
                "member_type": TARGET_MEMBER_NODE,
                "member_id": "node-b",
                "label": "B",
                "current_weight": 0.35,
                "current_value_base": 35.0,
            },
            {
                "member_type": TARGET_MEMBER_CASH,
                "member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
                "label": SYSTEM_CASH_TARGET_LABEL,
                "current_weight": 0.0,
                "current_value_base": 0.0,
            },
        ], [])

    monkeypatch.setattr(research_solver_service, "_current_scope_actuals", frozen_actuals)
    frozen_result = _solve_current_scope(
        frozen_state,
        scope_node_id=None,
        as_of_date=dates[-1],
        lookback_days=30,
        calculation_frequency="daily",
        target_dimension="weight",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        missing_return_policy="strict",
        apply_capital_overlay=False,
        include_actuals=False,
        resolve_frozen_actuals=True,
    )
    frozen_row_by_id = {str(row["member_id"]): row for row in frozen_result.member_target_rows}
    assert frozen_row_by_id["node-b"]["target_weight"] == pytest.approx(0.35)
    assert frozen_row_by_id["node-a"]["target_weight"] == pytest.approx(0.65)

    with pytest.raises(ValueError, match=r"B final weight .* is below its minimum"):
        _solve_current_scope(
            state,
            scope_node_id=None,
            as_of_date=dates[-1],
            lookback_days=30,
            calculation_frequency="daily",
            target_dimension="weight",
            capital_mode=CAPITAL_MODE_VOLATILITY_CAP,
            gross_exposure=None,
            target_volatility=0.000001,
            max_gross_exposure=None,
            missing_return_policy="strict",
            apply_capital_overlay=True,
            risk_model_config={
                "covariance_model_id": "sample_covariance",
                "parameters": {"min_observations": 2},
            },
            include_actuals=False,
        )

    all_fixed_capital_state = replace(
        state,
        target_sets_by_scope_type={
            (None, "taa"): [
                {
                    "target_set_id": "root-all-fixed-capital",
                    "target_set_type": "taa",
                    "name": "Root all fixed capital",
                    "weight_enabled": True,
                    "risk_budget_enabled": False,
                    "status": "active",
                }
            ]
        },
        target_lines_by_set_id={
            "root-all-fixed-capital": {
                (TARGET_MEMBER_NODE, "node-a"): {"target_weight": 0.0},
                (TARGET_MEMBER_NODE, "node-b"): {"target_weight": 0.0},
                (TARGET_MEMBER_DERIVATIVE, SYSTEM_DERIVATIVE_TARGET_MEMBER_ID): {"target_weight": 0.0},
                (TARGET_MEMBER_CASH, SYSTEM_CASH_TARGET_MEMBER_ID): {"target_weight": 1.0},
            }
        },
        top_sleeve_weight_bounds={},
    )
    with pytest.raises(ValueError, match="capital overlay is unavailable: no positive risky target weight"):
        _solve_current_scope(
            all_fixed_capital_state,
            scope_node_id=None,
            as_of_date=dates[-1],
            lookback_days=30,
            calculation_frequency="daily",
            target_dimension="weight",
            capital_mode=CAPITAL_MODE_VOLATILITY_CAP,
            gross_exposure=None,
            target_volatility=0.08,
            max_gross_exposure=None,
            missing_return_policy="strict",
            apply_capital_overlay=True,
            risk_model_config={
                "covariance_model_id": "sample_covariance",
                "parameters": {"min_observations": 2},
            },
            include_actuals=False,
        )


def test_backtest_universe_comes_from_point_in_time_assignment_revisions() -> None:
    assert _historical_backtest_instrument_ids(
        [
            {
                "taxonomy_assignments": [
                    {
                        "status": "active",
                        "target_scope": "instrument",
                        "target_entity_id": "former-member",
                    },
                    {
                        "status": "inactive",
                        "target_scope": "instrument",
                        "target_entity_id": "inactive-member",
                    },
                ]
            },
            {
                "taxonomy_assignments": [
                    {
                        "status": "active",
                        "target_scope": "instrument",
                        "target_entity_id": "current-member",
                    }
                ]
            },
        ],
        instrument_detail_cache={
            "former-member": {"instrument_type": "public_fund"},
            "current-member": {"instrument_type": "equity"},
        },
    ) == ["current-member", "former-member"]


def test_research_backtest_methodology_warnings_are_explicit() -> None:
    assert any("effective on its decision date" in warning for warning in RESEARCH_BACKTEST_METHODOLOGY_WARNINGS)
    assert any("commission" in warning and "implementation delay" in warning for warning in RESEARCH_BACKTEST_METHODOLOGY_WARNINGS)


def test_backtest_replay_deducts_buy_and_sell_friction_and_reconciles_contributions() -> None:
    return_dates = [date(2026, 1, 2), date(2026, 1, 3)]
    decisions = [
        {
            "decision_date": "2026-01-01",
            "taxonomy_configuration_version": 1,
            "taxonomy_configuration_effective_from": "2026-01-01",
            "target_weights": [
                {
                    "instrument_id": "asset-a",
                    "target_weight": 1.0,
                    "top_sleeve_id": "risk-assets",
                    "top_sleeve_label": "Risk Assets",
                }
            ],
        },
        {
            "decision_date": "2026-01-02",
            "taxonomy_configuration_version": 1,
            "taxonomy_configuration_effective_from": "2026-01-01",
            "target_weights": [],
        },
    ]

    replay = _replay_backtest_decisions(
        decisions,
        returns_by_instrument={
            "asset-a": pd.Series([0.0, 0.0], index=return_dates, dtype="float64")
        },
        as_of_date=date(2026, 1, 3),
        cash_yield_annual=0.0,
        commission_bps=2.0,
        tax_bps=10.0,
        slippage_bps=5.0,
        implementation_delay_days=1,
    )

    executions = replay["execution_records"]
    assert len(executions) == 2
    assert executions[0]["risky_buy_turnover"] == pytest.approx(1.0)
    assert executions[0]["commission_cost"] == pytest.approx(0.0002)
    assert executions[0]["tax_cost"] == pytest.approx(0.0)
    assert executions[0]["slippage_cost"] == pytest.approx(0.0005)
    assert executions[1]["risky_sell_turnover"] > 1.0
    assert executions[1]["tax_cost"] > 0.0
    assert replay["total_cost"] == pytest.approx(
        sum(item["total_cost"] for item in executions)
    )
    assert replay["points"][-1]["value"] == pytest.approx(
        1.0 - replay["total_cost"]
    )
    assert all(
        abs(item["residual"]) < 1e-12
        for item in replay["contribution_reconciliation_points"]
    )


def test_backtest_replay_compounds_cash_by_actual_calendar_days() -> None:
    replay = _replay_backtest_decisions(
        [
            {
                "decision_date": "2026-01-01",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [],
            }
        ],
        returns_by_instrument={},
        as_of_date=date(2026, 1, 3),
        cash_yield_annual=0.10,
        commission_bps=0.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=1,
    )

    assert replay["points"][-1]["value"] == pytest.approx(
        (1.0 + 0.10) ** (2.0 / 365.25)
    )
    assert replay["total_cost"] == pytest.approx(0.0)


def test_backtest_eod_execution_does_not_consume_return_ending_on_execution_date() -> None:
    replay = _replay_backtest_decisions(
        [
            {
                "decision_date": "2026-01-01",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [
                    {
                        "instrument_id": "asset-a",
                        "target_weight": 1.0,
                        "top_sleeve_id": "risk-assets",
                        "top_sleeve_label": "Risk Assets",
                    }
                ],
            }
        ],
        returns_by_instrument={
            "asset-a": pd.Series(
                [0.10, 0.05],
                index=[date(2026, 1, 2), date(2026, 1, 3)],
                dtype="float64",
            )
        },
        as_of_date=date(2026, 1, 2),
        cash_yield_annual=0.0,
        commission_bps=0.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=1,
    )

    # The Jan-2 return is Jan-1 EOD -> Jan-2 EOD.  A Jan-2 EOD execution
    # starts after that observation, so the as-of NAV remains at 1.0.
    assert replay["execution_records"][0]["actual_execution_date"] == "2026-01-02"
    assert replay["points"][-1]["value"] == pytest.approx(1.0)

    next_day = _replay_backtest_decisions(
        [
            {
                "decision_date": "2026-01-01",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [
                    {
                        "instrument_id": "asset-a",
                        "target_weight": 1.0,
                        "top_sleeve_id": "risk-assets",
                        "top_sleeve_label": "Risk Assets",
                    }
                ],
            }
        ],
        returns_by_instrument={
            "asset-a": pd.Series(
                [0.10, 0.05],
                index=[date(2026, 1, 2), date(2026, 1, 3)],
                dtype="float64",
            )
        },
        as_of_date=date(2026, 1, 3),
        cash_yield_annual=0.0,
        commission_bps=0.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=1,
    )
    assert next_day["points"][-1]["value"] == pytest.approx(1.05)


def test_backtest_skips_execution_when_pre_trade_holdings_lack_complete_eod_return() -> None:
    replay = _replay_backtest_decisions(
        [
            {
                "decision_date": "2026-01-01",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [
                    {
                        "instrument_id": "asset-a",
                        "target_weight": 1.0,
                        "top_sleeve_id": "risk-assets",
                        "top_sleeve_label": "Risk Assets",
                    }
                ],
            },
            {
                "decision_date": "2026-01-02",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [
                    {
                        "instrument_id": "asset-b",
                        "target_weight": 1.0,
                        "top_sleeve_id": "risk-assets",
                        "top_sleeve_label": "Risk Assets",
                    }
                ],
            },
        ],
        returns_by_instrument={
            "asset-a": pd.Series(
                [0.0, 0.10],
                index=[date(2026, 1, 2), date(2026, 1, 4)],
                dtype="float64",
            ),
            "asset-b": pd.Series(
                [0.0, 0.50],
                index=[date(2026, 1, 3), date(2026, 1, 4)],
                dtype="float64",
            ),
        },
        as_of_date=date(2026, 1, 4),
        cash_yield_annual=0.0,
        commission_bps=0.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=1,
    )

    assert len(replay["execution_records"]) == 1
    assert replay["execution_records"][0]["actual_execution_date"] == "2026-01-02"
    assert replay["skipped_executions"][0]["date"] == "2026-01-02"
    assert "asset-a" in replay["skipped_executions"][0]["reason"]
    assert replay["points"][-1]["value"] == pytest.approx(1.10)
    assert replay["warnings"] == [replay["skipped_executions"][0]["reason"]]


def test_backtest_structures_skipped_execution_when_target_observations_never_align() -> None:
    replay = _replay_backtest_decisions(
        [
            {
                "decision_date": "2026-01-01",
                "taxonomy_configuration_version": 1,
                "taxonomy_configuration_effective_from": "2026-01-01",
                "target_weights": [
                    {"instrument_id": "asset-a", "target_weight": 0.5},
                    {"instrument_id": "asset-b", "target_weight": 0.5},
                ],
            }
        ],
        returns_by_instrument={
            "asset-a": pd.Series(
                [0.01], index=[date(2026, 1, 2)], dtype="float64"
            ),
            "asset-b": pd.Series(
                [0.02], index=[date(2026, 1, 3)], dtype="float64"
            ),
        },
        as_of_date=date(2026, 1, 3),
        cash_yield_annual=0.0,
        commission_bps=0.0,
        tax_bps=0.0,
        slippage_bps=0.0,
        implementation_delay_days=1,
    )

    assert replay["execution_records"] == []
    assert replay["points"] == []
    assert replay["skipped_executions"][0]["date"] == "2026-01-01"
    assert "lack a common post-delay observation" in replay["warnings"][0]


def test_walk_forward_only_publishes_points_after_each_test_window_starts() -> None:
    point_dates = [item.date() for item in pd.date_range("2024-01-31", "2026-01-31", freq="ME")]
    points = [
        {"date": point_date.isoformat(), "value": 1.0 + index * 0.01}
        for index, point_date in enumerate(point_dates)
    ]
    validation = _build_walk_forward_validation(
        points,
        [
            {
                "decision_date": point_date.isoformat(),
                "taxonomy_configuration_version": 1,
            }
            for point_date in point_dates
        ],
        training_months=12,
        test_months=3,
    )

    assert validation["available"] is True
    assert validation["windows"]
    for window in validation["windows"]:
        test_start = date.fromisoformat(window["test_start_date"])
        published_return_dates = [
            date.fromisoformat(item)
            for item in _backtest_return_map_from_points(window["points"])
        ]
        assert all(item > test_start for item in published_return_dates)
    assert validation["oos_metrics"]["period_return"] is not None


def test_research_assumptions_do_not_truncate_volatility_cap_or_backtest_caveats() -> None:
    assumptions = research_service._build_target_assumptions(
        settings_payload={
            "target_dimension": "scope_default",
            "calculation_frequency": "daily",
            "missing_return_policy": "strict",
            "capital_mode": "volatility_cap",
        },
        target_rows=[],
        solve_event={
            "target_dimension": "risk_budget",
            "calculation_frequency": "daily",
            "missing_return_policy": "strict",
            "covariance_model": "sample_covariance",
            "risk_contribution_mode": "signed",
        },
    )

    assert len(assumptions) > 5
    assert any("only scales risky exposure down" in assumption for assumption in assumptions)
    assert any("effective on its decision date" in assumption for assumption in assumptions)
    assert any("commission" in assumption for assumption in assumptions)
    assert any("Look-through forward RC" in assumption for assumption in assumptions)


def test_planning_group_snapshot_does_not_count_system_cash_as_unassigned(monkeypatch):
    monkeypatch.setattr(research_service, "list_taxonomy_nodes", lambda _portfolio_id: [])
    monkeypatch.setattr(research_service, "list_taxonomy_assignments", lambda _portfolio_id: [])

    groups = research_service._build_planning_group_snapshot(
        [
            {
                "instrument_id": "instrument-unassigned",
                "market_value_base": 100.0,
                "cost_basis_base": 90.0,
            }
        ],
        account_rows=[
            {
                "account": {
                    "account_id": "cash-account",
                    "account_type": "deposit_account",
                },
                "derived_cash_balance_base": 20.0,
                "pending_settlement_base": 0.0,
            }
        ],
        portfolio_id="portfolio-unassigned-test",
        planning_taxonomy_id="taxonomy-test",
        as_of_date=date(2026, 1, 2),
    )

    unassigned_group = next(item for item in groups if item["group_key"] == "unassigned")
    cash_group = next(item for item in groups if item["group_key"] == SYSTEM_CASH_TARGET_MEMBER_ID)
    assert unassigned_group["position_count"] == 1
    assert unassigned_group["end_value_base"] == 100.0
    assert cash_group["group_label"] == SYSTEM_CASH_TARGET_LABEL
    assert cash_group["end_value_base"] == 20.0


def _create_target_sets(client, taxonomy_id: str, node_ids: dict[str, str]) -> None:
    for payload in sorted(
        [
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
                    "target_member_type": TARGET_MEMBER_CASH,
                    "target_member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
                    "target_weight": 0.15,
                    "target_risk_share": None,
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
                    "target_member_type": TARGET_MEMBER_CASH,
                    "target_member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
                    "target_weight": 0.15,
                    "target_risk_share": None,
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
        ],
        key=lambda item: item["effective_from"],
    ):
        target_set_response = client.post(
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
            json={"effective_from": EFFECTIVE_FROM, **payload},
        )
        assert target_set_response.status_code == 200, target_set_response.json()


def test_research_workbench_returns_target_solve_defaults(client):
    response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "portfolio-ops"
    assert payload["settings"]["planning_taxonomy_id"] is None
    assert payload["settings"]["target_dimension"] == "scope_default"
    assert payload["settings"]["capital_mode"] == "unit_notional"
    assert payload["settings"]["calculation_frequency"] == "daily"
    assert payload["settings"]["missing_return_policy"] == "strict"
    assert payload["settings"]["backtest_rebalance_frequency"] == "1m"
    assert payload["settings"]["backtest_benchmark_instrument_id"] is None
    assert payload["calculation_frequency"]["resolved_frequency"] == "daily"
    assert payload["risk_policy"]["model_name"] == "Production Risk Model"
    assert payload["risk_policy"]["model_role"] == "production"
    assert payload["risk_policy"]["covariance_model_id"] == "ewma_vol_shrinkage_corr_covariance"
    assert payload["risk_policy"]["lookback_days"] == 90
    assert payload["risk_policy"]["calculation_frequency"] == "daily"
    assert payload["risk_policy"]["resolved_calculation_frequency"] == "daily"
    assert payload["risk_policy"]["missing_return_policy"] == "strict"
    assert payload["risk_policy"]["contribution_mode"] == "signed"
    assert payload["risk_policy"]["parameters"]["min_observations"] == 45
    assert payload["risk_policy"]["parameters"]["corr_min_observations"] == 45
    assert payload["risk_policy"]["parameters_by_frequency"]["daily"]["min_observations"] == 45
    assert "weekly" not in payload["risk_policy"]["parameters_by_frequency"]
    assert "monthly" not in payload["risk_policy"]["parameters_by_frequency"]
    assert payload["settings"]["gross_exposure"] is None
    assert payload["settings"]["target_volatility"] is None
    assert payload["settings"]["max_gross_exposure"] is None
    assert payload["settings"]["top_sleeve_weight_bounds"] == []
    assert "run_template" not in payload["settings"]
    assert "target_set_mode" not in payload["settings"]
    assert "rebalance_frequency" not in payload["settings"]
    assert "start_date" not in payload["settings"]
    assert payload["planning_taxonomy_options"] == []
    assert payload["planning_scope_options"] == []
    assert payload["runs"] == []
    assert payload["selected_run"] is None
    assert payload["current_context"]["holdings_count"] == 3
    assert payload["current_context"]["planning_group_count"] == 0
    assert payload["current_context"]["nav"] == payload["current_context"]["summary"]["end_nav"]


def test_research_current_context_uses_canonical_portfolio_performance(client, monkeypatch):
    monkeypatch.setattr(
        research_service,
        "get_cached_materialized_performance_report",
        lambda _portfolio_id, *, start_date, end_date: {
            "base_currency": "USD",
            "summary": {
                "cumulative_twr": 0.025,
                "annualized_volatility": 0.12,
                "current_drawdown": -0.01,
                "max_drawdown": -0.04,
                "start_nav": 200_000.0,
                "end_nav": 216_470.0,
            },
            "daily_series": [
                {
                    "as_of_date": date(2026, 4, 13),
                    "ending_nav": 210_000.0,
                    "return_observation_eligible": True,
                },
                {
                    "as_of_date": date(2026, 4, 14),
                    "ending_nav": 212_000.0,
                    "return_observation_eligible": False,
                },
                {
                    "as_of_date": date(2026, 4, 15),
                    "ending_nav": 216_470.0,
                    "return_observation_eligible": True,
                },
            ],
        },
    )

    response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert response.status_code == 200
    context = response.json()["current_context"]

    assert context["chart_label"] == "Portfolio NAV"
    assert "Canonical portfolio NAV from Performance" in context["chart_note"]
    assert context["chart_currency"] == "USD"
    assert context["chart_points"] == [
        {"date": "2026-04-13", "value": 210_000.0},
        {"date": "2026-04-15", "value": 216_470.0},
    ]
    assert context["nav"] == pytest.approx(216_470.0)
    assert context["summary"] == {
        "period_return": pytest.approx(0.025),
        "annualized_volatility": pytest.approx(0.12),
        "current_drawdown": pytest.approx(-0.01),
        "max_drawdown": pytest.approx(-0.04),
        "start_nav": pytest.approx(200_000.0),
        "end_nav": pytest.approx(216_470.0),
    }


def test_production_risk_window_min_observations_scale_with_calendar_window() -> None:
    assert risk_min_observations_for_window("daily", 30) == 15
    assert risk_min_observations_for_window("daily", 90) == 45
    assert risk_min_observations_for_window("daily", 180) == 90


def test_production_risk_policy_rejects_unsupported_windows() -> None:
    with pytest.raises(ValueError, match="Risk window must be one of 1M, 3M, 6M, 12M, 24M"):
        normalize_portfolio_risk_policy({"lookback_days": 7})


def test_database_rejects_unsupported_historical_research_window(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            ResearchRunRecordModel(
                research_run_id="unsupported-window-run",
                portfolio_id="portfolio-ops",
                job_type="target_weight_solve",
                status="completed",
                requested_at="2026-04-15T10:00:00Z",
                started_at="2026-04-15T10:00:00Z",
                finished_at="2026-04-15T10:01:00Z",
                as_of_date=date(2026, 4, 15),
                planning_taxonomy_id=None,
                lookback_days=8,
                requested_by="test",
                headline="Unsupported legacy run",
                detail_json={},
                artifacts_json=[],
                request_payload_json={"lookback_days": 8},
                error_message=None,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_research_window_dates_use_calendar_months() -> None:
    assert research_window_start_date(date(2026, 5, 29), 30) == date(2026, 4, 29)
    assert research_window_start_date(date(2026, 5, 29), 90) == date(2026, 2, 28)
    assert research_window_start_date(date(2026, 5, 29), 180) == date(2025, 11, 29)


def test_research_backtest_rebalance_schedule_rolls_from_first_valid_month() -> None:
    assert _rebalance_schedule(
        start_date=date(2026, 5, 5),
        end_date=date(2026, 5, 26),
        frequency="1w",
    ) == [date(2026, 5, 5), date(2026, 5, 12), date(2026, 5, 19), date(2026, 5, 26)]
    assert _rebalance_schedule(
        start_date=date(2026, 4, 1),
        end_date=date(2026, 10, 15),
        frequency="3m",
    ) == [date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)]
    assert _rebalance_schedule(
        start_date=date(2026, 5, 2),
        end_date=date(2026, 12, 15),
        frequency="3m",
    ) == [date(2026, 6, 1), date(2026, 9, 1), date(2026, 12, 1)]


def test_research_backtest_metrics_include_ytd_drawdown_duration_and_calmar() -> None:
    points = [
        {"date": "2025-12-31", "value": 1.0},
        {"date": "2026-01-02", "value": 1.1},
        {"date": "2026-01-05", "value": 0.88},
        {"date": "2026-01-12", "value": 1.12},
    ]
    returns = {
        "2026-01-02": 0.1,
        "2026-01-05": -0.2,
        "2026-01-12": 1.12 / 0.88 - 1.0,
    }

    metrics = _build_backtest_metrics(points, returns)

    assert metrics["period_return"] == pytest.approx(0.12)
    assert metrics["ytd_return"] == pytest.approx(0.12)
    assert metrics["max_drawdown"] == pytest.approx(-0.2)
    assert metrics["max_drawdown_days"] == 3
    assert metrics["max_drawdown_recovery_days"] == 7
    assert metrics["current_drawdown"] == pytest.approx(0.0)
    assert metrics["annualization_eligible"] is False
    assert metrics["annualized_return"] is None
    assert metrics["calmar_ratio"] is None


def test_research_backtest_metrics_require_ytd_anchor_and_use_arithmetic_mean_sharpe() -> None:
    anchored_points = [
        {"date": "2024-12-31", "value": 1.0},
        {"date": "2025-01-02", "value": 1.1},
        {"date": "2025-12-31", "value": 0.99},
        {"date": "2026-01-02", "value": 1.2},
    ]
    returns = {
        "2025-01-02": 0.1,
        "2025-12-31": -0.1,
        "2026-01-02": 1.2 / 0.99 - 1.0,
    }

    metrics = _build_backtest_metrics(anchored_points, returns)
    periods_per_year = _infer_periods_per_year(
        [date(2025, 1, 2), date(2025, 12, 31), date(2026, 1, 2)]
    )
    values = np.asarray(list(returns.values()), dtype="float64")
    expected_volatility = float(np.std(values, ddof=1) * sqrt(periods_per_year))
    expected_sharpe = float(np.mean(values) * periods_per_year / expected_volatility)
    expected_annualization_years = 1.0 + (2.0 / 365.0)

    assert metrics["annualization_eligible"] is True
    assert metrics["annualization_years"] == pytest.approx(
        expected_annualization_years
    )
    assert metrics["annualized_return"] == pytest.approx(
        1.2 ** (1.0 / expected_annualization_years) - 1.0
    )
    assert metrics["ytd_return"] == pytest.approx(1.2 / 0.99 - 1.0)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_sharpe)
    assert metrics["calmar_ratio"] is not None

    unanchored_metrics = _build_backtest_metrics(
        [
            {"date": "2026-01-02", "value": 1.0},
            {"date": "2026-01-05", "value": 1.02},
        ],
        {"2026-01-05": 0.02},
    )
    assert unanchored_metrics["ytd_return"] is None

    stale_anchor_metrics = _build_backtest_metrics(
        [
            {"date": "2024-12-31", "value": 1.0},
            {"date": "2026-07-01", "value": 1.2},
        ],
        {"2026-07-01": 0.2},
    )
    assert stale_anchor_metrics["ytd_return"] is None


def test_top_sleeve_bounds_reject_fixed_gross_above_max_capacity() -> None:
    member_by_key = {
        "taxonomy_node::cta": ScopeMemberRecord(
            member_type=TARGET_MEMBER_NODE,
            member_id="cta",
            label="CTA",
        ),
        "taxonomy_node::macro": ScopeMemberRecord(
            member_type=TARGET_MEMBER_NODE,
            member_id="macro",
            label="Macro",
        ),
    }

    with pytest.raises(ValueError, match="maximum weights allow only 70.00%"):
        _resolve_active_top_sleeve_bound_vectors(
            scope_label="Top Level",
            active_keys=list(member_by_key),
            active_budget=0.8,
            bounds_by_key={
                "taxonomy_node::cta": {"min_weight": None, "max_weight": 0.35},
                "taxonomy_node::macro": {"min_weight": None, "max_weight": 0.35},
            },
            member_by_key=member_by_key,
            allow_upper_shortfall=False,
        )


def test_research_settings_only_accepts_daily_production_risk_frequency(client):
    settings_payload = {
        "planning_taxonomy_id": None,
        "comparator_taxonomy_node_id": None,
        "as_of_date": "2026-04-15",
        "lookback_days": 180,
        "missing_return_policy": "complete_case_drop",
        "covariance_model_id": "sample_covariance",
        "contribution_mode": "abs",
        "target_dimension": "scope_default",
        "capital_mode": "unit_notional",
    }
    for unsupported_frequency in ("auto", "weekly", "monthly"):
        unsupported_response = client.put(
            "/api/portfolios/portfolio-ops/research/settings",
            json={
                **settings_payload,
                "calculation_frequency": unsupported_frequency,
            },
        )
        assert unsupported_response.status_code == 422

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            **settings_payload,
            "calculation_frequency": "daily",
        },
    )
    assert settings_response.status_code == 200

    risk_policy_response = client.get("/api/portfolios/portfolio-ops/risk-policy")
    assert risk_policy_response.status_code == 200
    risk_policy = risk_policy_response.json()
    assert risk_policy["covariance_model_id"] == "sample_covariance"
    assert risk_policy["lookback_days"] == 180
    assert risk_policy["calculation_frequency"] == "daily"
    assert risk_policy["resolved_calculation_frequency"] == "daily"
    assert risk_policy["missing_return_policy"] == "complete_case_drop"
    assert risk_policy["contribution_mode"] == "abs"
    assert risk_policy["parameters"]["min_observations"] == 90
    assert risk_policy["parameters"]["corr_min_observations"] == 90
    assert risk_policy["parameters_by_frequency"]["daily"]["min_observations"] == 90
    assert "weekly" not in risk_policy["parameters_by_frequency"]
    assert "monthly" not in risk_policy["parameters_by_frequency"]

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    workbench_policy = workbench_response.json()["risk_policy"]
    assert workbench_policy["covariance_model_id"] == "sample_covariance"
    assert workbench_policy["lookback_days"] == 180
    assert workbench_policy["calculation_frequency"] == "daily"
    assert workbench_policy["missing_return_policy"] == "complete_case_drop"
    assert workbench_policy["contribution_mode"] == "abs"


def test_research_settings_updates_backtest_controls(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "backtest_rebalance_frequency": "1w",
            "backtest_benchmark_instrument_id": "fund-us-agg",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["backtest_rebalance_frequency"] == "1w"
    assert settings_payload["backtest_benchmark_instrument_id"] == "fund-us-agg"

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    workbench_settings = workbench_response.json()["settings"]
    assert workbench_settings["backtest_rebalance_frequency"] == "1w"
    assert workbench_settings["backtest_benchmark_instrument_id"] == "fund-us-agg"


def test_research_settings_omitted_backtest_controls_preserve_existing_configuration(client):
    initial = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "backtest_rebalance_frequency": "3m",
            "backtest_benchmark_instrument_id": "fund-us-agg",
            "backtest_cash_yield_annual": 0.035,
            "backtest_commission_bps": 4,
            "backtest_tax_bps": 18,
            "backtest_slippage_bps": 9,
            "backtest_implementation_delay_days": 4,
            "backtest_robustness_scenarios": [
                {
                    "scenario_id": "custom-stress",
                    "label": "Custom stress",
                    "cash_yield_annual": -0.01,
                    "commission_bps": 8,
                    "tax_bps": 24,
                    "slippage_bps": 16,
                    "implementation_delay_days": 6,
                }
            ],
            "backtest_walk_forward_training_months": 18,
            "backtest_walk_forward_test_months": 3,
        },
    )
    assert initial.status_code == 200, initial.text

    partial = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Only notes changed",
        },
    )
    assert partial.status_code == 200, partial.text
    payload = partial.json()
    assert payload["notes"] == "Only notes changed"
    assert payload["backtest_rebalance_frequency"] == "3m"
    assert payload["backtest_benchmark_instrument_id"] == "fund-us-agg"
    assert payload["backtest_cash_yield_annual"] == pytest.approx(0.035)
    assert payload["backtest_commission_bps"] == pytest.approx(4)
    assert payload["backtest_tax_bps"] == pytest.approx(18)
    assert payload["backtest_slippage_bps"] == pytest.approx(9)
    assert payload["backtest_implementation_delay_days"] == 4
    assert payload["backtest_robustness_scenarios"][0]["scenario_id"] == "custom-stress"
    assert payload["backtest_walk_forward_training_months"] == 18
    assert payload["backtest_walk_forward_test_months"] == 3


def test_research_settings_accepts_volatility_cap_mode(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "volatility_cap",
            "target_volatility": 0.08,
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["capital_mode"] == "volatility_cap"
    assert settings_payload["target_volatility"] == pytest.approx(0.08)
    assert settings_payload["gross_exposure"] is None
    assert settings_payload["max_gross_exposure"] is None

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    assert workbench_response.json()["settings"]["capital_mode"] == "volatility_cap"


def test_research_target_volatility_defaults_max_gross_to_unit_leverage(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "target_volatility",
            "target_volatility": 0.08,
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["capital_mode"] == "target_volatility"
    assert settings_payload["max_gross_exposure"] == pytest.approx(1.0)


def test_volatility_overlay_gross_exposure_separates_target_and_cap_modes() -> None:
    assert _resolve_volatility_overlay_gross_exposure(
        capital_mode=CAPITAL_MODE_TARGET_VOLATILITY,
        estimated_volatility=0.05,
        target_volatility=0.10,
        max_gross_exposure=None,
    ) == pytest.approx(1.0)
    assert _resolve_volatility_overlay_gross_exposure(
        capital_mode=CAPITAL_MODE_TARGET_VOLATILITY,
        estimated_volatility=0.05,
        target_volatility=0.10,
        max_gross_exposure=2.0,
    ) == pytest.approx(2.0)
    assert _resolve_volatility_overlay_gross_exposure(
        capital_mode=CAPITAL_MODE_VOLATILITY_CAP,
        estimated_volatility=0.05,
        target_volatility=0.10,
        max_gross_exposure=None,
    ) == pytest.approx(1.0)
    assert _resolve_volatility_overlay_gross_exposure(
        capital_mode=CAPITAL_MODE_VOLATILITY_CAP,
        estimated_volatility=0.20,
        target_volatility=0.10,
        max_gross_exposure=None,
    ) == pytest.approx(0.5)


def test_research_workbench_reads_canonical_run_top_holdings(client):
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            ResearchRunRecordModel(
                research_run_id="canonical-run",
                portfolio_id="portfolio-ops",
                job_type="target_weight_solve",
                status="completed",
                requested_at="2026-04-15T10:00:00Z",
                started_at="2026-04-15T10:00:00Z",
                finished_at="2026-04-15T10:01:00Z",
                as_of_date=date(2026, 4, 15),
                planning_taxonomy_id=None,
                lookback_days=90,
                requested_by=None,
                headline="Canonical run",
                detail_json={
                    "top_holdings": [
                        {
                            "instrument_id": "equity-us-abbv",
                            "instrument_name": "AbbVie Inc",
                            "instrument_type": "equity",
                            "allocation": 0.25,
                            "market_value_base": 100.0,
                            "cost_basis_base": 90.0,
                            "base_currency": "USD",
                            "price": 206.47,
                        }
                    ],
                    "backtest": {
                        "points": [
                            {"date": "2026-04-01", "value": 100.0},
                            {"date": "2026-04-15", "value": 101.8},
                        ],
                        "metrics": {
                            "annualization_eligible": True,
                            "annualized_return": 9.99,
                        },
                    },
                },
                artifacts_json=[],
                request_payload_json={},
                error_message=None,
            )
        )
        session.commit()

    response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["detail"] is None
    assert payload["detail_level"] == "compact"
    assert payload["selected_run"]["detail"] is None
    assert payload["selected_run"]["artifacts"] == []
    selected_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": "canonical-run"},
    )
    assert selected_response.status_code == 200
    selected_payload = selected_response.json()
    assert selected_payload["detail_level"] == "selected_run"
    top_holding = selected_payload["selected_run"]["detail"]["top_holdings"][0]
    assert top_holding["instrument_id"] == "equity-us-abbv"
    assert top_holding["instrument_name"] == "AbbVie Inc"
    assert top_holding["instrument_type"] == "equity"
    selected_metrics = selected_payload["selected_run"]["detail"]["backtest"]["metrics"]
    assert selected_metrics["period_return"] == pytest.approx(0.018)
    assert selected_metrics["annualization_eligible"] is False
    assert selected_metrics["annualized_return"] is None
    assert selected_payload["selected_run"]["reliability_state"] == "stale"
    assert any(
        "predates planning-state fingerprinting" in reason
        for reason in selected_payload["selected_run"]["reliability_reasons"]
    )

    run_response = client.get("/api/portfolios/portfolio-ops/research/runs/canonical-run")
    assert run_response.status_code == 200
    assert run_response.json()["detail"]["top_holdings"][0]["instrument_id"] == "equity-us-abbv"
    assert run_response.json()["detail"]["backtest"]["metrics"]["annualized_return"] is None


def test_research_dynamic_as_of_tracks_latest_portfolio_date(client):
    response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["as_of_mode"] == "dynamic"
    assert payload["settings"]["pinned_as_of_date"] is None
    assert payload["settings"]["as_of_date"] == payload["as_of_date"]
    assert payload["current_context"]["as_of_date"] == payload["as_of_date"]


def test_research_pinned_as_of_requires_explicit_mode(client):
    settings = client.get("/api/portfolios/portfolio-ops/research/workbench").json()["settings"]
    response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": settings["planning_taxonomy_id"],
            "comparator_taxonomy_node_id": settings["comparator_taxonomy_node_id"],
            "as_of_mode": "pinned",
            "as_of_date": "2026-04-01",
            "lookback_days": settings["lookback_days"],
            "calculation_frequency": settings["calculation_frequency"],
            "missing_return_policy": settings["missing_return_policy"],
            "target_dimension": settings["target_dimension"],
            "capital_mode": settings["capital_mode"],
            "backtest_rebalance_frequency": settings["backtest_rebalance_frequency"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_mode"] == "pinned"
    assert payload["as_of_date"] == "2026-04-01"
    assert payload["pinned_as_of_date"] == "2026-04-01"

    refreshed = client.get("/api/portfolios/portfolio-ops/research/workbench").json()
    assert refreshed["current_context"]["as_of_date"] == "2026-04-01"


def test_research_series_prefers_total_return_nav_for_funds() -> None:
    detail = {
        "instrument_id": "fund-test",
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "1.0000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-14",
                "value": "1.1200",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-15",
                "value": "1.0100",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "1.1350",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 1.12),
        ("2026-04-15", 1.135),
    ]


def test_fund_analytics_remain_unavailable_without_total_return_nav() -> None:
    detail = {
        "instrument_id": "fund-unit-nav-only",
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for as_of_date, value in (
                ("2026-04-14", "1.0000"),
                ("2026-04-15", "1.0100"),
            )
        ],
    }

    assert _selected_price_points(detail, end_date=date(2026, 4, 15)) == []
    metrics = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 4, 15),
    )
    assert metrics["instrument_trend_basis"] is None
    assert metrics["instrument_trend_reason"] == "total_return_series_unavailable"


def test_research_series_uses_only_complete_market_data() -> None:
    detail = {
        "instrument_id": "fund-status-test",
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-14",
                "value": "1.1200",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "1.1350",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "partial",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [("2026-04-14", 1.12)]


def test_fund_instrument_chart_basis_is_exact_total_return_nav() -> None:
    detail = {
        "instrument_type": "public_fund",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav"],
            "total_return": ["total_return_nav"],
        },
    }

    assert _candidate_chart_bases(detail) == ["total_return_nav"]


def test_instrument_trend_requires_an_explicit_quote_policy() -> None:
    detail = {
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "USD",
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "100",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-01-01",
                "value": "100",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-01-10",
                "value": "110",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
        ],
    }

    metrics = build_instrument_trend_metrics_from_detail(detail, as_of_date=date(2026, 1, 10))

    assert metrics["instrument_trend_basis"] is None
    assert metrics["instrument_return_ytd"] is None
    assert metrics["instrument_trend_reason"] == "quote_policy_unavailable"


def test_instrument_trend_volatility_uses_quote_observation_density() -> None:
    points = [
        {"date": date(2026, 1, 1), "value": 100.0},
        {"date": date(2026, 1, 8), "value": 102.0},
        {"date": date(2026, 1, 15), "value": 99.0},
    ]
    returns = [0.02, 99.0 / 102.0 - 1.0]
    mean_return = sum(returns) / len(returns)
    sample_stddev = sqrt(sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1))
    periods_per_year = 2 / 14 * 365.25

    assert _annualized_volatility(points) == pytest.approx(sample_stddev * sqrt(periods_per_year))


def test_holdings_daily_volatility_requires_window_start_coverage() -> None:
    detail = {
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "USD",
        "quote_selection_policy": {"total_return": ["adjusted_close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "value": str(100.0 + offset / 10),
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for offset in range(0, 106, 7)
        ],
    }

    metrics = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 4, 16),
        calculation_frequency="daily",
    )

    assert metrics["instrument_volatility_6m"] is None


def test_holdings_risk_window_stays_anchored_to_requested_as_of_calendar_month() -> None:
    first_date = date(2026, 6, 20)
    last_quote_date = date(2026, 7, 24)
    detail = {
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": {"total_return": ["total_return_nav"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": (first_date + timedelta(days=offset)).isoformat(),
                "value": str(100.0 + offset * 0.1 + (0.25 if offset % 2 else 0.0)),
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for offset in range((last_quote_date - first_date).days + 1)
        ],
    }

    metrics = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 7, 28),
        calculation_frequency="daily",
    )

    assert metrics["instrument_trend_as_of_date"] == "2026-07-24"
    assert metrics["instrument_return_series_1m"]["first_return_start_date"] == "2026-06-28"
    assert metrics["instrument_return_series_1m"]["points"][0]["start_date"] == "2026-06-28"
    assert metrics["instrument_volatility_1m"] is not None


def test_holdings_risk_window_withholds_uniformly_stale_tail_data() -> None:
    first_date = date(2026, 6, 20)
    last_quote_date = date(2026, 7, 24)
    detail = {
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": {"total_return": ["total_return_nav"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": (first_date + timedelta(days=offset)).isoformat(),
                "value": str(100.0 + offset * 0.1 + (0.25 if offset % 2 else 0.0)),
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for offset in range((last_quote_date - first_date).days + 1)
        ],
    }

    metrics = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 7, 30),
        calculation_frequency="daily",
    )

    assert metrics["instrument_trend_as_of_date"] == "2026-07-24"
    assert metrics["instrument_volatility_1m"] is None
    assert metrics["instrument_return_series_1m"] == {
        "first_return_start_date": None,
        "points": [],
    }


def test_research_series_prefers_adjusted_close_for_equities() -> None:
    detail = {
        "instrument_id": "equity-test",
        "instrument_type": "equity",
        "exchange_code": "XNYS",
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
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-14",
                "value": "108.0000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-15",
                "value": "102.0000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-15",
                "value": "110.5000",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 108.0),
        ("2026-04-15", 110.5),
    ]


def test_research_daily_alignment_carries_last_valid_marks_between_source_updates() -> None:
    members = [
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-a",
            label="Instrument A",
        ),
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-b",
            label="Instrument B",
        ),
    ]
    nav_series_by_member = {
        (TARGET_MEMBER_INSTRUMENT, "instrument-a"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 5): 110.0,
            },
            dtype="float64",
        ),
        (TARGET_MEMBER_INSTRUMENT, "instrument-b"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 2): 102.0,
                date(2026, 1, 5): 101.0,
            },
            dtype="float64",
        ),
    }

    aligned_members, calendar, _warnings = _align_member_series(
        members,
        nav_series_by_member,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        calculation_frequency="daily",
    )

    returns_by_member = {item.member.member_id: item.returns for item in aligned_members}
    assert calendar == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)]
    assert returns_by_member["instrument-a"].loc[date(2026, 1, 2)] == pytest.approx(0.0)
    assert returns_by_member["instrument-a"].loc[date(2026, 1, 5)] == pytest.approx(0.1)
    assert returns_by_member["instrument-b"].loc[date(2026, 1, 2)] == pytest.approx(0.02)
    assert _infer_periods_per_year([date(2026, 1, 2), date(2026, 1, 5)]) == pytest.approx(2 / 6 * 365.25)


def test_recursive_child_nav_preserves_first_valid_return_without_filling_internal_gaps() -> None:
    child_returns = pd.Series(
        {
            date(2026, 1, 1): np.nan,
            date(2026, 1, 2): 0.10,
            date(2026, 1, 3): np.nan,
            date(2026, 1, 4): 0.20,
        },
        dtype="float64",
    )
    child_nav = _series_to_nav(child_returns, as_of_date=date(2026, 1, 4))

    assert child_nav.loc[date(2026, 1, 1)] == pytest.approx(1.0)
    assert child_nav.loc[date(2026, 1, 2)] == pytest.approx(1.1)
    assert pd.isna(child_nav.loc[date(2026, 1, 3)])
    assert pd.isna(child_nav.loc[date(2026, 1, 4)])

    aligned_members, _calendar, _warnings = _align_member_series(
        [ScopeMemberRecord(member_type=TARGET_MEMBER_NODE, member_id="child", label="Child")],
        {(TARGET_MEMBER_NODE, "child"): child_nav},
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
        calculation_frequency="daily",
    )

    parent_returns = aligned_members[0].returns
    assert parent_returns.loc[date(2026, 1, 2)] == pytest.approx(0.1)
    assert pd.isna(parent_returns.loc[date(2026, 1, 3)])
    assert pd.isna(parent_returns.loc[date(2026, 1, 4)])


def test_research_covariance_annualizes_complete_aligned_dates() -> None:
    returns = pd.DataFrame(
        {
            "asset_a": [0.01, 0.02, 0.03],
            "asset_b": [0.05, 0.07, 0.04],
        },
        index=[date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)],
        dtype="float64",
    )

    covariance = _estimate_covariance(
        returns,
        model_id="sample_covariance",
        lookback_days=30,
        parameters={"min_observations": 2},
    )

    annualization = _infer_periods_per_year([date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)])
    expected_covariance = returns.cov(ddof=1) * annualization

    assert covariance.loc["asset_a", "asset_a"] == pytest.approx(expected_covariance.loc["asset_a", "asset_a"])
    assert covariance.loc["asset_a", "asset_b"] == pytest.approx(expected_covariance.loc["asset_a", "asset_b"])
    assert covariance.loc["asset_b", "asset_a"] == pytest.approx(expected_covariance.loc["asset_b", "asset_a"])


def test_research_covariance_rejects_partial_missing_rows() -> None:
    returns = pd.DataFrame(
        {
            "instrument_a": [0.01, 0.02, None, None],
            "instrument_b": [None, None, -0.01, 0.03],
        },
        index=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)],
        dtype="float64",
    )

    with pytest.raises(ValueError, match="complete aligned return observations"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 2},
        )


def test_research_covariance_complete_case_drop_uses_only_complete_rows() -> None:
    dates = [date(2026, 1, day) for day in range(1, 12)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 12)],
            "instrument_b": [0.002 * day for day in range(1, 12)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[date(2026, 1, 5), "instrument_b"] = np.nan

    covariance = _estimate_covariance(
        returns,
        model_id="sample_covariance",
        lookback_days=30,
        parameters={"min_observations": 5},
        missing_return_policy="complete_case_drop",
        calculation_frequency="daily",
        as_of_date=date(2026, 1, 11),
    )

    complete = returns.dropna(how="any")
    expected_covariance = complete.cov(ddof=1) * _infer_periods_per_year(list(complete.index))
    assert covariance.loc["instrument_a", "instrument_a"] == pytest.approx(
        expected_covariance.loc["instrument_a", "instrument_a"]
    )
    assert covariance.loc["instrument_a", "instrument_b"] == pytest.approx(
        expected_covariance.loc["instrument_a", "instrument_b"]
    )


def test_research_covariance_complete_case_drop_rejects_excessive_missing_rows() -> None:
    dates = [date(2026, 1, day) for day in range(1, 11)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 11)],
            "instrument_b": [0.002 * day for day in range(1, 11)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[[date(2026, 1, 4), date(2026, 1, 8)], "instrument_b"] = np.nan

    with pytest.raises(ValueError, match="exceeding"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 5},
            missing_return_policy="complete_case_drop",
            calculation_frequency="daily",
            as_of_date=date(2026, 1, 10),
        )


def test_research_covariance_complete_case_drop_rejects_stale_latest_complete_row() -> None:
    dates = [date(2026, 1, day) for day in range(1, 21)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 21)],
            "instrument_b": [0.002 * day for day in range(1, 21)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[date(2026, 1, 20), "instrument_b"] = np.nan

    with pytest.raises(ValueError, match="latest complete return observation"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 5},
            missing_return_policy="complete_case_drop",
            calculation_frequency="daily",
            as_of_date=date(2026, 1, 31),
        )


def test_research_risk_budget_solver_matches_tight_tolerance() -> None:
    problem = RiskBudgetProblem(
        bucket_ids=["asset_a", "asset_b", "asset_c"],
        covariance=np.array(
            [
                [0.04, 0.01, 0.002],
                [0.01, 0.01, 0.001],
                [0.002, 0.001, 0.0225],
            ],
            dtype="float64",
        ),
        target_risk_shares=np.array([0.4, 0.35, 0.25], dtype="float64"),
        lower_bounds=np.zeros(3, dtype="float64"),
        upper_bounds=np.ones(3, dtype="float64"),
        reference_weights=np.array([0.33, 0.34, 0.33], dtype="float64"),
    )

    solution = _solve_risk_budget_problem(problem)

    assert solution.max_abs_share_gap <= 1e-4
    assert solution.weights.sum() == pytest.approx(1.0)


def test_research_risk_budget_solve_allows_empty_current_reference_weights() -> None:
    returns = pd.DataFrame(
        {
            "asset_a": [0.01, -0.01, 0.0, 0.0, 0.012, -0.012],
            "asset_b": [0.0, 0.0, 0.02, -0.02, -0.004, 0.004],
        },
        index=[date(2026, 1, day) for day in range(1, 7)],
        dtype="float64",
    )

    solution = _solve_risk_budget_weights(
        target_shares=np.asarray([0.5, 0.5], dtype="float64"),
        return_window=returns,
        reference_weights=np.asarray([0.0, 0.0], dtype="float64"),
        as_of_date=date(2026, 1, 6),
        lookback_days=30,
        calculation_frequency="daily",
        missing_return_policy="strict",
        risk_model_config={
            "covariance_model_id": "sample_covariance",
            "contribution_mode": "signed",
            "parameters": {"min_observations": 2, "max_period_staleness_days": 0},
        },
    )

    assert solution.weights.sum() == pytest.approx(1.0)
    assert all(weight > 0 for weight in solution.weights)
    assert solution.max_abs_share_gap <= 1e-4


def test_research_risk_budget_default_model_uses_calendar_window_observation_floor() -> None:
    dates = [item.date() for item in pd.bdate_range(end="2026-05-29", periods=48)]
    returns = pd.DataFrame(
        {
            "asset_a": [0.004 if index % 2 == 0 else -0.002 for index in range(len(dates))],
            "asset_b": [-0.001 if index % 3 == 0 else 0.003 for index in range(len(dates))],
        },
        index=dates,
        dtype="float64",
    )

    solution = _solve_risk_budget_weights(
        target_shares=np.asarray([0.5, 0.5], dtype="float64"),
        return_window=returns,
        reference_weights=None,
        as_of_date=date(2026, 5, 29),
        lookback_days=90,
        calculation_frequency="daily",
        missing_return_policy="strict",
        risk_model_config=None,
    )

    assert solution.covariance_observations == 48
    assert solution.weights.sum() == pytest.approx(1.0)
    assert solution.max_abs_share_gap <= 1e-4


def test_risk_budget_solution_selection_prioritizes_normalized_max_gap() -> None:
    problem = RiskBudgetProblem(
        bucket_ids=["large", "small_a", "small_b"],
        covariance=np.eye(3, dtype="float64"),
        target_risk_shares=np.array([0.8, 0.1, 0.1], dtype="float64"),
        lower_bounds=np.zeros(3, dtype="float64"),
        upper_bounds=np.ones(3, dtype="float64"),
        reference_weights=np.array([0.8, 0.1, 0.1], dtype="float64"),
    )
    smaller_absolute_gap_but_less_fair = RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=np.array([0.8, 0.05, 0.15], dtype="float64"),
        achieved_risk_shares=np.array([0.8, 0.05, 0.15], dtype="float64"),
        objective_value=0.0,
        max_abs_share_gap=0.05,
        iterations=1,
        message="candidate",
        solver_kind="test",
        contribution_mode="signed",
    )
    larger_absolute_gap_but_more_fair = RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=np.array([0.74, 0.13, 0.13], dtype="float64"),
        achieved_risk_shares=np.array([0.74, 0.13, 0.13], dtype="float64"),
        objective_value=0.0,
        max_abs_share_gap=0.06,
        iterations=1,
        message="candidate",
        solver_kind="test",
        contribution_mode="signed",
    )

    assert _is_better_risk_budget_solution(
        problem,
        larger_absolute_gap_but_more_fair,
        smaller_absolute_gap_but_less_fair,
    )
    assert not _is_better_risk_budget_solution(
        problem,
        smaller_absolute_gap_but_less_fair,
        larger_absolute_gap_but_more_fair,
    )


def test_selected_target_risk_share_only_applies_to_active_risk_budget_dimension() -> None:
    assert _selected_target_risk_share(
        {
            "selected_target_dimension": "weight",
            "configured_risk_share": 0.5,
        }
    ) is None
    assert _selected_target_risk_share(
        {
            "selected_target_dimension": "risk_budget",
            "configured_risk_share": 0.5,
        }
    ) == pytest.approx(0.5)


def test_current_holding_with_zero_solved_target_requires_manual_review() -> None:
    gaps = _build_leaf_target_weight_gaps(
        leaf_target_rows=[
            {
                "member_type": "instrument",
                "member_id": "short-history-fund",
                "label": "Short History Fund",
                "current_weight": 0.08,
                "current_value_base": 80.0,
                "target_weight": 0.0,
            }
        ],
        base_currency="CNY",
        scope_value_base=1_000.0,
    )

    assert gaps == [
        {
            "member_type": "instrument",
            "member_id": "short-history-fund",
            "label": "Short History Fund",
            "current_weight": pytest.approx(0.08),
            "target_weight": pytest.approx(0.0),
            "gap": pytest.approx(-0.08),
            "current_value_base": pytest.approx(80.0),
            "target_value_base": pytest.approx(0.0),
            "base_currency": "CNY",
            "action": "Review",
            "execution_status": "manual_review_required",
            "execution_note": (
                "Current holdings with a 0% solved target require an explicit PM decision; "
                "Research does not infer an executable liquidation from target eligibility or limited history."
            ),
        }
    ]


def test_cash_with_zero_solved_target_is_not_mislabeled_as_a_liquidation() -> None:
    gaps = _build_leaf_target_weight_gaps(
        leaf_target_rows=[
            {
                "member_type": "cash_bucket",
                "member_id": "__cash__",
                "label": "Cash",
                "current_weight": 0.006,
                "target_weight": 0.0,
            }
        ],
        base_currency="CNY",
    )

    assert gaps[0]["action"] == "Hold"
    assert gaps[0]["execution_status"] == "ready"
    assert gaps[0]["execution_note"] is None


def test_research_run_creates_current_target_weight_outputs(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Research regression test",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["planning_taxonomy_id"] == taxonomy_id
    assert settings_payload["comparator_taxonomy_node_id"] == node_ids["Risk Assets"]
    assert settings_payload["target_dimension"] == "scope_default"

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert len(workbench_payload["planning_taxonomy_options"]) == 1
    assert any(item["label"] == "Top Level" for item in workbench_payload["planning_scope_options"])
    assert any(item["label"] == "Risk Assets" for item in workbench_payload["planning_scope_options"])

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()

    run_payload = run_response.json()
    assert run_payload["status"] == "completed"
    assert run_payload["planning_taxonomy_id"] == taxonomy_id
    assert "run_template" not in run_payload
    assert run_payload["artifact_count"] == 12
    assert run_payload["detail"]["selected_scope"]["taxonomy_node_id"] == node_ids["Risk Assets"]
    assert run_payload["detail"]["selected_scope"]["label"] == "Risk Assets"
    assert "backtest_metrics" not in run_payload["detail"]
    assert "backtest_curve" not in run_payload["detail"]
    assert "weight_schedule" not in run_payload["detail"]
    assert len(run_payload["detail"]["member_targets"]) == 2
    assert len(run_payload["detail"]["leaf_targets"]) == 2
    assert run_payload["detail"]["backtest"]["rebalance_frequency"] == "1m"
    assert run_payload["detail"]["backtest"]["lookback_days"] == 30
    assert any(
        "effective on its decision date" in warning
        for warning in run_payload["detail"]["backtest"]["warnings"]
    )
    assert any(
        "commission" in warning and "implementation delay" in warning
        for warning in run_payload["detail"]["backtest"]["warnings"]
    )
    backtest = run_payload["detail"]["backtest"]
    assert backtest["methodology"]["point_in_time_universe"] is True
    assert backtest["methodology"]["point_in_time_taxonomy"] is True
    assert backtest["point_in_time_coverage"]["decision_count"] >= 1
    assert backtest["point_in_time_coverage"]["configuration_versions_used"]
    assert backtest["execution_records"]
    assert backtest["total_cost"] > 0
    assert len(backtest["robustness_results"]) == 2
    assert backtest["walk_forward"]["available"] is False
    assert all(
        abs(item["residual"]) < 1e-10
        for item in backtest["contribution_reconciliation_points"]
    )
    assert run_payload["detail"]["backtest_benchmark"] is None
    assert run_payload["detail"]["backtest_relative_metrics"] is None
    comparison_response = client.get(
        f"/api/portfolios/portfolio-ops/research/runs/{run_payload['research_run_id']}/benchmark-comparison",
        params={"benchmark_instrument_id": "fund-hk-2800"},
    )
    assert comparison_response.status_code == 200, comparison_response.json()
    comparison_payload = comparison_response.json()
    assert comparison_payload["backtest_benchmark"]["instrument_id"] == "fund-hk-2800"
    assert comparison_payload["backtest_benchmark"]["points"]
    assert comparison_payload["backtest_benchmark"]["metrics"]["period_return"] is not None
    assert comparison_payload["backtest_relative_metrics"] is not None
    portfolio_period_return = run_payload["detail"]["backtest"]["metrics"]["period_return"]
    benchmark_period_return = comparison_payload["backtest_benchmark"]["metrics"]["period_return"]
    assert comparison_payload["backtest_relative_metrics"]["excess_return"] == pytest.approx(
        portfolio_period_return - benchmark_period_return
    )
    assert len(run_payload["detail"]["solved_result_groups"]) == 1
    solved_group = run_payload["detail"]["solved_result_groups"][0]
    assert solved_group["top_sleeve_label"] == "Risk Assets"
    assert solved_group["current_weight"] is not None
    assert solved_group["current_value_base"] is not None
    assert solved_group["target_value_base"] is not None
    solved_rows_by_member = {item["member_id"]: item for item in solved_group["rows"]}
    assert set(solved_rows_by_member) == {"equity-us-abbv", "fund-hk-2800"}
    assert solved_rows_by_member["equity-us-abbv"]["target_risk_share"] is None
    assert solved_rows_by_member["fund-hk-2800"]["target_risk_share"] == pytest.approx(1.0)
    assert all(item["current_weight"] is not None for item in solved_rows_by_member.values())
    assert all(item["current_value_base"] is not None for item in solved_rows_by_member.values())
    assert all(item["target_value_base"] is not None for item in solved_rows_by_member.values())
    member_targets_by_label = {item["label"]: item for item in run_payload["detail"]["member_targets"]}
    assert member_targets_by_label["Defensive Equity"]["configured_risk_share"] == pytest.approx(0.45)
    assert member_targets_by_label["Hong Kong Beta"]["configured_risk_share"] == pytest.approx(0.55)
    leaf_targets_by_member = {item["member_id"]: item for item in run_payload["detail"]["leaf_targets"]}
    assert leaf_targets_by_member["equity-us-abbv"]["configured_risk_share"] is None
    assert leaf_targets_by_member["fund-hk-2800"]["configured_risk_share"] == pytest.approx(1.0)
    assert len(run_payload["detail"]["target_assumptions"]) >= 1
    assert any(
        "effective on its decision date" in assumption
        for assumption in run_payload["detail"]["target_assumptions"]
    )
    assert len(run_payload["detail"]["target_rows"]) == 2
    assert any(item["label"] == "Defensive Equity" for item in run_payload["detail"]["member_targets"])
    assert any(item["label"] == "Hong Kong Beta" for item in run_payload["detail"]["member_targets"])
    assert all(item["source_label"] in {"TAA", "SAA", "Single Member"} for item in run_payload["detail"]["target_rows"])
    assert run_payload["detail"]["solve_event"]["as_of_date"] == "2026-04-15"
    assert len(run_payload["detail"]["target_weight_gaps"]) >= 1
    assert all(item["current_value_base"] is not None for item in run_payload["detail"]["target_weight_gaps"])
    assert all(item["target_value_base"] is not None for item in run_payload["detail"]["target_weight_gaps"])
    signal_labels = {item["label"] for item in run_payload["detail"]["signals"]}
    assert "Scope Default" in signal_labels
    assert "Solver" in signal_labels
    assert "Missing Returns" in signal_labels
    assert "Estimated Volatility" in signal_labels
    portfolio_nav_tape_artifact = next(
        item for item in run_payload["artifacts"] if item["artifact_id"] == "portfolio_nav_tape"
    )
    assert portfolio_nav_tape_artifact["label"] == "Portfolio NAV Tape CSV"
    assert portfolio_nav_tape_artifact["path"].endswith("/portfolio_nav_tape.csv")
    assert any(item["artifact_id"] == "target_weights" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "leaf_targets" for item in run_payload["artifacts"])
    assert all(item["artifact_id"] != "backtest_curve" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "solve_event" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "scope_solve_events" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "target_weight_gaps" for item in run_payload["artifacts"])
    assert all(item["label"] != "Daily NAV CSV" for item in run_payload["artifacts"])

    report_artifact = next(item for item in run_payload["artifacts"] if item["artifact_id"] == "report")
    artifact_response = client.get(
        "/api/portfolios/portfolio-ops/research/artifacts/content",
        params={"path": report_artifact["path"]},
    )
    assert artifact_response.status_code == 200
    artifact_payload = artifact_response.json()
    assert artifact_payload["preview_kind"] == "text"
    assert "# Research Run" in artifact_payload["content"]
    assert "Current Target Weights" in artifact_payload["content"]
    assert "## Assumptions and Limitations" in artifact_payload["content"]
    assert "effective on its decision date" in artifact_payload["content"]
    assert "commission" in artifact_payload["content"]

    selected_workbench_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": run_payload["research_run_id"]},
    )
    assert selected_workbench_response.status_code == 200
    selected_workbench_payload = selected_workbench_response.json()
    assert selected_workbench_payload["selected_run"]["research_run_id"] == run_payload["research_run_id"]
    assert selected_workbench_payload["selected_run"]["reliability_state"] == "current"
    assert selected_workbench_payload["selected_run"]["is_current"] is True
    assert selected_workbench_payload["selected_run"]["reliability_reasons"] == []
    session_factory = get_session_factory()
    with session_factory() as session:
        stored_run = session.get(ResearchRunRecordModel, run_payload["research_run_id"])
        assert stored_run is not None
        assert stored_run.request_payload_json["planning_state_fingerprint_version"] == 2
        assert stored_run.request_payload_json["planning_state_fingerprint"].startswith("sha256:")


def test_research_run_becomes_stale_after_target_line_change(client) -> None:
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200, settings_response.json()
    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_id = run_response.json()["research_run_id"]

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200, catalog_response.json()
    catalog = catalog_response.json()
    selected_taa = next(
        item
        for item in catalog["target_sets"]
        if item["taxonomy_id"] == taxonomy_id
        and item["comparator_taxonomy_node_id"] == node_ids["Risk Assets"]
        and item["target_set_type"] == "taa"
    )
    selected_taa_lines = [
        item
        for item in catalog["target_set_lines"]
        if item["target_set_id"] == selected_taa["target_set_id"]
    ]
    changed_weight_by_member_id = {
        node_ids["Defensive Equity"]: 0.51,
        node_ids["Hong Kong Beta"]: 0.49,
    }
    update_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets/{selected_taa['target_set_id']}",
        json={"effective_from": "2026-04-15",
            "lines": [
                {
                    "target_member_type": item["target_member_type"],
                    "target_member_id": item["target_member_id"],
                    "target_weight": changed_weight_by_member_id[item["target_member_id"]],
                    "target_risk_share": item["target_risk_share"],
                    "notes": item["notes"],
                }
                for item in selected_taa_lines
            ]
        },
    )
    assert update_response.status_code == 200, update_response.json()

    workbench_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": run_id},
    )
    assert workbench_response.status_code == 200, workbench_response.json()
    selected_run = workbench_response.json()["selected_run"]
    assert selected_run["reliability_state"] == "stale"
    assert selected_run["is_current"] is False
    assert selected_run["reliability_reasons"] == [
        "Planning taxonomy structure, active assignments, or active SAA/TAA target configuration changed "
        "after this run was created."
    ]


def test_research_workbench_marks_historical_run_stale_and_non_current(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            ResearchRunRecordModel(
                research_run_id="historical-stale-run",
                portfolio_id="portfolio-ops",
                job_type="target_weight_solve",
                status="completed",
                requested_at="2026-04-16T10:00:00Z",
                started_at="2026-04-16T10:00:00Z",
                finished_at="2026-04-16T10:01:00Z",
                as_of_date=date(2026, 4, 1),
                planning_taxonomy_id=None,
                lookback_days=90,
                requested_by="test",
                headline="Historical run",
                detail_json={},
                artifacts_json=[],
                request_payload_json={},
                error_message=None,
            )
        )
        session.commit()

    response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": "historical-stale-run"},
    )

    assert response.status_code == 200, response.json()
    selected_run = response.json()["selected_run"]
    assert selected_run["research_run_id"] == "historical-stale-run"
    assert selected_run["reliability_state"] == "stale"
    assert selected_run["is_current"] is False
    assert any("does not match the latest portfolio date 2026-04-15" in reason for reason in selected_run["reliability_reasons"])
    assert any("Portfolio transactions exist through 2026-04-15" in reason for reason in selected_run["reliability_reasons"])
    assert any("predates planning-state fingerprinting" in reason for reason in selected_run["reliability_reasons"])


def test_backtest_benchmark_returns_use_sampling_interval_returns() -> None:
    benchmark_nav = pd.Series(
        {
            date(2026, 3, 30): 4491.95,
            date(2026, 4, 3): 4440.7889,
            date(2026, 4, 10): 4636.5655,
        },
        dtype="float64",
    )
    portfolio_points = [
        {"date": "2026-03-30", "value": 1.0},
        {"date": "2026-04-03", "value": 1.01},
        {"date": "2026-04-10", "value": 1.02},
    ]

    benchmark_points, benchmark_returns = _build_sampled_benchmark_points(benchmark_nav, portfolio_points)

    assert benchmark_returns["2026-04-03"] == pytest.approx(4440.7889 / 4491.95 - 1.0)
    assert benchmark_returns["2026-04-10"] == pytest.approx(4636.5655 / 4440.7889 - 1.0)
    assert benchmark_points[-1]["value"] == pytest.approx(4636.5655 / 4491.95)


def test_research_benchmark_distinguishes_index_close_return_semantics() -> None:
    benchmark_detail = {
        "instrument_id": "index-benchmark",
        "instrument_name": "Index Benchmark",
        "instrument_type": "index",
        "currency": "CNY",
        "identifiers": [],
        "source_settings": {"return_semantics": "price_return"},
        "quote_selection_policy": {
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["close"],
            "chart": ["close"],
            "reference": ["close"],
        },
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": point_date,
                "value": value,
                "currency": "CNY",
                "price_unit": "per_unit",
                "price_scale": "1",
                "status": "complete",
            }
            for point_date, value in (
                ("2026-03-30", "100"),
                ("2026-04-03", "101"),
                ("2026-04-10", "102"),
            )
        ],
    }
    state = TaxonomyResearchState(
        portfolio_id="portfolio-benchmark-semantics",
        planning_taxonomy_id="taxonomy-benchmark-semantics",
        taxonomy_name="Planning",
        root_default_target_dimension="weight",
        base_currency="CNY",
        as_of_date=date(2026, 4, 10),
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        instrument_detail_cache={"index-benchmark": benchmark_detail},
        direct_fx_instruments={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    portfolio_points = [
        {"date": "2026-03-30", "value": 1.0},
        {"date": "2026-04-03", "value": 1.01},
        {"date": "2026-04-10", "value": 1.02},
    ]

    price_result = research_solver_service._build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id="index-benchmark",
        portfolio_points=portfolio_points,
    )

    assert price_result["backtest_benchmark"]["points"]
    assert price_result["backtest_relative_metrics"] is not None
    assert price_result["backtest_benchmark"]["warnings"] == [
        "index-benchmark uses a price-return series. Portfolio returns include income, "
        "so excess return and relative statistics include that basis difference."
    ]

    benchmark_detail["source_settings"] = {"return_semantics": "unknown"}
    unknown_result = research_solver_service._build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id="index-benchmark",
        portfolio_points=portfolio_points,
    )

    assert unknown_result["backtest_benchmark"]["points"] == []
    assert unknown_result["backtest_relative_metrics"] is None

    benchmark_detail["source_settings"] = {"return_semantics": "total_return"}
    total_return_result = research_solver_service._build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id="index-benchmark",
        portfolio_points=portfolio_points,
    )

    assert total_return_result["backtest_benchmark"]["points"]
    assert total_return_result["backtest_relative_metrics"] is not None
    assert total_return_result["backtest_benchmark"]["warnings"] == []


def test_weekly_rebalance_uses_daily_calculation_returns() -> None:
    daily_nav = pd.Series(
        {
            date(2026, 3, 27): 100.0,
            date(2026, 3, 30): 102.0,
            date(2026, 4, 2): 103.0,
            date(2026, 4, 3): 110.0,
            date(2026, 4, 10): 121.0,
        },
        dtype="float64",
    )
    weekly_nav = pd.Series(
        {
            date(2026, 3, 27): 200.0,
            date(2026, 4, 3): 220.0,
            date(2026, 4, 10): 242.0,
        },
        dtype="float64",
    )

    sampled = _build_backtest_sampled_nav_by_instrument(
        {"daily": daily_nav, "weekly": weekly_nav},
        end_date=date(2026, 4, 10),
    )
    returns_by_instrument = {instrument_id: sampled_nav.pct_change().dropna() for instrument_id, sampled_nav in sampled.items()}
    rebal_dates = _backtest_rebalance_dates(
        start_date=date(2026, 3, 30),
        end_date=date(2026, 4, 10),
        frequency="1w",
    )

    assert returns_by_instrument["daily"].loc[date(2026, 4, 3)] == pytest.approx(110.0 / 103.0 - 1.0)
    assert returns_by_instrument["daily"].loc[date(2026, 4, 10)] == pytest.approx(121.0 / 110.0 - 1.0)
    assert rebal_dates == [date(2026, 3, 30), date(2026, 4, 6)]


def test_research_run_replaces_previous_run(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    first_response = client.post("/api/portfolios/portfolio-ops/research/runs", json={"requested_by": "pytest"})
    assert first_response.status_code == 200, first_response.json()
    first_run_id = first_response.json()["research_run_id"]

    second_response = client.post("/api/portfolios/portfolio-ops/research/runs", json={"requested_by": "pytest"})
    assert second_response.status_code == 200, second_response.json()
    second_run_id = second_response.json()["research_run_id"]
    assert second_run_id != first_run_id

    session_factory = get_session_factory()
    with session_factory() as session:
        remaining_runs = session.query(ResearchRunRecordModel).filter_by(portfolio_id="portfolio-ops").all()
    assert [item.research_run_id for item in remaining_runs] == [second_run_id]

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert [item["research_run_id"] for item in workbench_payload["runs"]] == [second_run_id]
    assert workbench_payload["selected_run"]["research_run_id"] == second_run_id


def test_failed_research_run_keeps_previous_completed_run(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    first_response = client.post("/api/portfolios/portfolio-ops/research/runs", json={"requested_by": "pytest"})
    assert first_response.status_code == 200, first_response.json()
    first_run_id = first_response.json()["research_run_id"]

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": "2026-04-15",
            "target_scope": TARGET_MEMBER_INSTRUMENT,
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_ids["Defensive Equity"],
        },
    )
    assert assignment_response.status_code == 200, assignment_response.json()

    failed_response = client.post("/api/portfolios/portfolio-ops/research/runs", json={"requested_by": "pytest"})
    assert failed_response.status_code == 400, failed_response.json()
    assert "Defensive Equity" in failed_response.json()["detail"]
    assert "weight target set" in failed_response.json()["detail"]

    session_factory = get_session_factory()
    with session_factory() as session:
        remaining_runs = session.query(ResearchRunRecordModel).filter_by(portfolio_id="portfolio-ops").all()
    run_by_id = {item.research_run_id: item for item in remaining_runs}
    assert run_by_id[first_run_id].status == "completed"
    assert any(item.status == "failed" for item in remaining_runs)

    workbench_response = client.get(
        "/api/portfolios/portfolio-ops/research/workbench",
        params={"selected_run_id": first_run_id},
    )
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert first_run_id in {item["research_run_id"] for item in workbench_payload["runs"]}
    assert workbench_payload["selected_run"]["research_run_id"] == first_run_id


def test_research_target_solve_actuals_include_pending_security_settlement(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "missing_return_policy": "complete_case_drop",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Delayed settlement actuals regression",
        },
    )
    assert settings_response.status_code == 200

    baseline_run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert baseline_run_response.status_code == 200, baseline_run_response.json()
    baseline_cash_value = next(
        item["current_value_base"]
        for item in baseline_run_response.json()["detail"]["target_rows"]
        if item["label"] == SYSTEM_CASH_TARGET_LABEL
    )

    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
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
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_payload = run_response.json()
    cash_row = next(
        item
        for item in run_payload["detail"]["target_rows"]
        if item["label"] == SYSTEM_CASH_TARGET_LABEL
    )
    assert cash_row["current_value_base"] == pytest.approx(baseline_cash_value - 206.47)


def test_research_run_rejects_incomplete_scope_targets_after_new_watch_member(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": "2026-04-15",
            "comparator_taxonomy_node_id": node_ids["Defensive Equity"],
            "target_set_type": "taa",
            "name": "Defensive Equity Direct Weights",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "target_member_type": TARGET_MEMBER_INSTRUMENT,
                    "target_member_id": "equity-us-abbv",
                    "target_weight": 1.0,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200, target_set_response.json()

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": "2026-04-15",
            "target_scope": TARGET_MEMBER_INSTRUMENT,
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_ids["Defensive Equity"],
        },
    )
    assert assignment_response.status_code == 200, assignment_response.json()

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Defensive Equity"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    error_detail = run_response.json()["detail"]
    assert "Defensive Equity weight target set is incomplete" in error_detail
    assert "Watchlist Fund" in error_detail


def test_research_scope_default_respects_taxonomy_root_default_dimension(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "missing_return_policy": "complete_case_drop",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    top_level_scope = next(
        item for item in workbench_response.json()["planning_scope_options"] if item["taxonomy_node_id"] is None
    )
    assert top_level_scope["default_target_dimension"] == "risk_budget"


def test_research_scope_default_requires_configured_dimension_target_set(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={
            "target_set_type": "taa",
            "name": "Root Weight Only",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.7,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.2,
                },
                {
                    "target_member_type": TARGET_MEMBER_CASH,
                    "target_member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
                    "target_weight": 0.1,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "has no active complete" in run_response.json()["detail"]
    assert "target set" in run_response.json()["detail"]


def test_deleting_selected_research_taxonomy_clears_settings(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    delete_response = client.delete(f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}", params={"effective_from": EFFECTIVE_FROM})
    assert delete_response.status_code == 200

    workbench_response = client.get("/api/portfolios/portfolio-ops/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert workbench_payload["default_planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["comparator_taxonomy_node_id"] is None


def test_research_settings_preserve_frozen_nodes_when_field_is_omitted(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    payload = {
        "planning_taxonomy_id": taxonomy_id,
        "comparator_taxonomy_node_id": None,
        "as_of_date": "2026-04-15",
        "lookback_days": 90,
        "missing_return_policy": "complete_case_drop",
        "target_dimension": "scope_default",
        "capital_mode": "unit_notional",
        "frozen_taxonomy_node_ids": [node_ids["Risk Assets"]],
        "top_sleeve_weight_bounds": [
            {"taxonomy_node_id": node_ids["Risk Assets"], "min_weight": 0.2, "max_weight": 0.55},
        ],
    }
    initial_response = client.put("/api/portfolios/portfolio-ops/research/settings", json=payload)
    assert initial_response.status_code == 200
    assert initial_response.json()["frozen_taxonomy_node_ids"] == [node_ids["Risk Assets"]]
    assert initial_response.json()["top_sleeve_weight_bounds"] == [
        {"taxonomy_node_id": node_ids["Risk Assets"], "min_weight": 0.2, "max_weight": 0.55},
    ]
    assert initial_response.json()["missing_return_policy"] == "complete_case_drop"

    omitted_payload = dict(payload)
    omitted_payload.pop("frozen_taxonomy_node_ids")
    omitted_payload.pop("top_sleeve_weight_bounds")
    omitted_payload["notes"] = "Preserve frozen sleeves"
    omitted_response = client.put("/api/portfolios/portfolio-ops/research/settings", json=omitted_payload)
    assert omitted_response.status_code == 200
    assert omitted_response.json()["frozen_taxonomy_node_ids"] == [node_ids["Risk Assets"]]
    assert omitted_response.json()["top_sleeve_weight_bounds"] == [
        {"taxonomy_node_id": node_ids["Risk Assets"], "min_weight": 0.2, "max_weight": 0.55},
    ]

    clear_payload = dict(omitted_payload)
    clear_payload["frozen_taxonomy_node_ids"] = []
    clear_payload["top_sleeve_weight_bounds"] = []
    clear_response = client.put("/api/portfolios/portfolio-ops/research/settings", json=clear_payload)
    assert clear_response.status_code == 200
    assert clear_response.json()["frozen_taxonomy_node_ids"] == []
    assert clear_response.json()["top_sleeve_weight_bounds"] == []


def test_research_settings_rejects_non_top_sleeve_weight_bounds(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "missing_return_policy": "complete_case_drop",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "top_sleeve_weight_bounds": [
                {"taxonomy_node_id": node_ids["Defensive Equity"], "max_weight": 0.3},
            ],
        },
    )
    assert response.status_code == 400
    assert "top-level nodes" in response.json()["detail"]


def test_research_risk_budget_solver_accepts_binding_weight_bounds():
    dates = pd.date_range(end="2026-05-29", periods=60, freq="B")
    factor = np.sin(np.linspace(0.0, 8.0 * np.pi, len(dates)))
    returns = pd.DataFrame(
        {
            "low_vol": 0.0003 + 0.002 * factor,
            "high_vol": 0.0005 + 0.018 * factor + 0.001 * np.cos(np.linspace(0.0, 4.0 * np.pi, len(dates))),
        },
        index=[item.date() for item in dates],
    )

    bounded = _solve_risk_budget_weights(
        target_shares=np.asarray([0.5, 0.5], dtype="float64"),
        return_window=returns,
        reference_weights=None,
        as_of_date=date(2026, 5, 29),
        lookback_days=90,
        calculation_frequency="daily",
        missing_return_policy="strict",
        risk_model_config=None,
        lower_bounds=np.asarray([0.0, 0.0], dtype="float64"),
        upper_bounds=np.asarray([0.3, 1.0], dtype="float64"),
    )

    assert bounded.weights[0] == pytest.approx(0.3, abs=1e-6)
    assert bounded.weights.sum() == pytest.approx(1.0)
    assert bounded.solver_detail in {"slsqp_minimax", "slsqp_minimax_balanced"}
    assert bounded.max_abs_share_gap is not None
    assert bounded.max_abs_share_gap > 1e-4
    assert bounded.target_status == "constrained_target_miss"
    assert bounded.execution_ready is False


def test_research_risk_budget_solver_returns_binding_signed_negative_constrained_solution():
    problem = RiskBudgetProblem(
        bucket_ids=["diversifier", "risk_asset"],
        covariance=np.array(
            [
                [0.01, -0.09],
                [-0.09, 1.0],
            ],
            dtype="float64",
        ),
        target_risk_shares=np.array([0.5, 0.5], dtype="float64"),
        lower_bounds=np.array([0.3, 0.0], dtype="float64"),
        upper_bounds=np.array([0.6, 1.0], dtype="float64"),
        reference_weights=np.array([0.5, 0.5], dtype="float64"),
        contribution_mode="signed",
    )

    solution = _solve_risk_budget_problem(problem, enforce_tolerance=False)

    assert solution.weights.sum() == pytest.approx(1.0)
    assert solution.solver_kind in {"slsqp_minimax", "slsqp_minimax_balanced"}
    assert solution.max_abs_share_gap > 1e-4
    assert float(solution.achieved_risk_shares.min()) < 0.0
    assert solution.target_status == "constrained_target_miss"
    assert solution.execution_ready is False


def test_research_target_volatility_rejects_unaligned_risk_history(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "weight",
            "capital_mode": "target_volatility",
            "target_volatility": 0.0001,
            "max_gross_exposure": 1.0,
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["capital_mode"] == "target_volatility"
    assert settings_payload["target_volatility"] == pytest.approx(0.0001)
    assert settings_payload["max_gross_exposure"] == pytest.approx(1.0)

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]


def test_research_target_volatility_rejects_missing_child_sleeve_history(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client, root_default_target_dimension="weight")
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "weight",
            "capital_mode": "target_volatility",
            "target_volatility": 1.0,
            "max_gross_exposure": 1.0,
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]


def test_research_run_rejects_insufficient_history_for_unaligned_sparse_window(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/portfolio-ops/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]
