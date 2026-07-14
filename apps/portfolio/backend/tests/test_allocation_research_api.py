from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from math import sqrt
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from portfolio_app.calculations.portfolio_daily.release_gate import (
    drain_portfolio_daily_publications,
)
from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import AllocationResearchRunRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_app.services import allocation_policy_replay as policy_replay_service
from portfolio_app.services import allocation_research as allocation_research_service
from portfolio_app.services import allocation_solver as allocation_solver_service
from portfolio_app.services import portfolio_market_data as market_data_service
from portfolio_app.services import allocation_research_workbench as allocation_research_workbench_service
from portfolio_app.services import risk_math as risk_math_service
from portfolio_app.services.allocation_policy_replay import (
    POLICY_REPLAY_METHODOLOGY_WARNINGS,
    POLICY_REPLAY_METRICS_METHOD_VERSION,
    _active_policy_replay_leaf_ids,
    _policy_replay_rebalance_dates,
    _build_policy_replay_metrics,
    _build_policy_replay_sampled_nav_by_instrument,
    _build_sampled_benchmark_points,
    _rebalance_schedule,
    build_current_target_policy_replay,
)
from portfolio_app.services.allocation_research import (
    _build_leaf_target_weight_gaps,
    _selected_target_risk_share,
)
from portfolio_app.services.allocation_solver import (
    CAPITAL_MODE_TARGET_VOLATILITY,
    CAPITAL_MODE_VOLATILITY_CAP,
    SYSTEM_CASH_TARGET_MEMBER_ID,
    SYSTEM_CASH_TARGET_LABEL,
    TARGET_MEMBER_CASH,
    TARGET_MEMBER_INSTRUMENT,
    TARGET_MEMBER_NODE,
    AllocationResearchState,
    RiskBudgetProblem,
    RiskBudgetSolution,
    ScopeMemberRecord,
    _align_member_series,
    _current_scope_actuals,
    _is_better_risk_budget_solution,
    _resolve_active_top_sleeve_bound_vectors,
    _resolve_volatility_overlay_gross_exposure,
    _series_to_nav,
    _solve_current_scope,
    _solve_risk_budget_problem,
    _solve_risk_budget_weights,
)
from portfolio_app.services.instrument_charts import (
    _annualized_volatility,
    build_instrument_trend_metrics_from_points,
)
from portfolio_app.services.performance_reliability import (
    ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM,
    HISTORY_WINDOW_UNAVAILABLE,
    MIN_ANNUALIZED_RETURN_HISTORY_DAYS,
)
from portfolio_app.services.published_holdings import (
    PublishedCashAccountValue,
    PublishedHoldingPosition,
    PublishedHoldingsStatement,
)
from portfolio_app.services.portfolio_market_data import (
    PortfolioMarketDataError,
    _build_instrument_nav_series,
    _lock_portfolio_market_data,
    _lock_portfolio_market_data_in_session,
)
from portfolio_app.services.risk_math import (
    _estimate_covariance,
    _infer_periods_per_year,
    _periodic_nav_series,
    risk_window_start_date,
)
from portfolio_app.services.risk_model import normalize_portfolio_risk_policy, risk_min_observations_for_window


def _published_holdings_statement(
    *,
    as_of_date: date,
    positions: tuple[PublishedHoldingPosition, ...],
    cash_accounts: tuple[PublishedCashAccountValue, ...] = (),
) -> PublishedHoldingsStatement:
    position_values = tuple(
        position.market_value_base_exact for position in positions
    )
    cash_values = tuple(account.value_base_exact for account in cash_accounts)
    return PublishedHoldingsStatement(
        portfolio_id="portfolio-test",
        base_currency="USD",
        valuation_timezone="UTC",
        as_of_date=as_of_date,
        publication_id=UUID("00000000-0000-0000-0000-000000000011"),
        run_id=UUID("00000000-0000-0000-0000-000000000012"),
        manifest_id=UUID("00000000-0000-0000-0000-000000000013"),
        captured_generation=1,
        positions=positions,
        cash_accounts=cash_accounts,
        position_market_value_base_exact=(
            sum(position_values, Decimal("0"))
            if all(value is not None for value in position_values)
            else None
        ),
        settled_cash_base_exact=(
            sum(cash_values, Decimal("0"))
            if all(value is not None for value in cash_values)
            else None
        ),
        pending_settlement_base_exact=Decimal("0"),
        total_nav_base=None,
        nav_coverage_state="complete",
        nav_reason_codes=(),
    )


def _canonical_point(
    point_date: date,
    value: float,
    *,
    quote_basis: str,
    currency: str,
    status: str = "complete",
) -> dict[str, object]:
    metric_family = "nav" if quote_basis.endswith("nav") else ("fx" if quote_basis == "spot" else "price")
    return {
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "as_of_date": point_date.isoformat(),
        "value": str(value),
        "currency": currency,
        "status": status,
        "source_ref": "allocation-research-test",
    }


def _canonical_instrument(
    instrument_id: str,
    *,
    currency: str,
    points: list[dict[str, object]],
    instrument_type: str = "fund",
    total_return_bases: list[str] | None = None,
) -> dict[str, object]:
    if total_return_bases is None:
        total_return_bases = ["total_return_nav"] if instrument_type == "fund" else ["adjusted_close"]
    valuation_bases = (
        ["spot"]
        if instrument_type == "fx"
        else (["official_nav"] if instrument_type == "fund" else ["close"])
    )
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": instrument_type,
        "currency": currency,
        "quote_selection_policy": {
            "total_return": total_return_bases,
            "valuation": valuation_bases,
        },
        "market_data": points,
    }


def _install_canonical_instruments(instruments: list[dict[str, object]]) -> None:
    session_factory = get_session_factory()
    for item in instruments:
        instrument_id = str(item["instrument_id"])
        existing = shared_store.get_instrument(session_factory, instrument_id)
        if existing is None:
            quote_selection_policy = dict(item["quote_selection_policy"])
            valuation_policy = list(quote_selection_policy["valuation"])
            quote_selection_policy.setdefault("trading", valuation_policy)
            quote_selection_policy.setdefault("reference", valuation_policy)
            quote_selection_policy.setdefault(
                "chart",
                list(quote_selection_policy["total_return"]) or valuation_policy,
            )
            created = shared_store.create_instrument(
                session_factory,
                instrument_name=str(item["instrument_name"]),
                instrument_type=str(item["instrument_type"]),
                currency=str(item["currency"]),
                identifiers=[
                    {
                        "identifier_type": "allocation_research_test_id",
                        "identifier_value": instrument_id,
                        "is_primary": True,
                    }
                ],
                quote_selection_policy=quote_selection_policy,
            )
            assert created["instrument_id"] == instrument_id
        points = list(item.get("market_data") or [])
        changed_count = shared_store.upsert_market_data_points(
            session_factory,
            instrument_id=instrument_id,
            rows=points,
        )
        assert changed_count == len(points)


def _publish_portfolio_daily(
    *, requested_as_of: date = date(2026, 4, 15)
) -> None:
    publication_state = drain_portfolio_daily_publications(
        get_engine(),
        settings=get_settings(),
        requested_as_of=requested_as_of,
        timeout_seconds=30.0,
    )
    assert publication_state.complete
    assert publication_state.pending_intent_count == 0
    assert publication_state.active_run_count == 0


def _market_data_only_state(
    *,
    context,
    base_currency: str,
    as_of_date: date,
) -> AllocationResearchState:
    return AllocationResearchState(
        portfolio_id="portfolio-market-data-test",
        planning_taxonomy_id="taxonomy-market-data-test",
        taxonomy_name="Market Data Test",
        root_default_target_dimension="weight",
        base_currency=base_currency,
        as_of_date=as_of_date,
        node_by_id={},
        children_by_parent={},
        node_path_by_id={},
        node_depth_by_id={},
        node_subtree_by_id={},
        direct_assignments_by_node={},
        target_sets_by_scope_type={},
        target_lines_by_set_id={},
        account_name_by_id={},
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
        market_data=context,
    )

def _create_planning_taxonomy(client, *, root_default_target_dimension: str = "weight") -> tuple[str, dict[str, str]]:
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={
            "name": "Allocation Research Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
            "root_default_target_dimension": root_default_target_dimension,
            "purpose": "Allocation Research recursive sleeve test",
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
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
            json=payload,
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
            json={
                "target_scope": assignment[0],
                "target_entity_id": assignment[1],
                "taxonomy_node_id": node_ids[assignment[2]],
            },
        )
        assert assignment_response.status_code == 200

    default_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert default_response.status_code == 200
    _publish_portfolio_daily()
    return taxonomy_id, node_ids


@pytest.mark.no_database
def test_current_target_solve_fails_closed_for_unassigned_non_cash_holding(monkeypatch):
    state = AllocationResearchState(
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
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    monkeypatch.setattr(
        "portfolio_app.services.allocation_solver.read_current_published_holdings",
        lambda *_args, **_kwargs: _published_holdings_statement(
            as_of_date=date(2026, 1, 2),
            positions=(
                PublishedHoldingPosition(
                    instrument_id="instrument-unassigned",
                    instrument_name="Unassigned",
                    instrument_type="fund",
                    currency="USD",
                    quantity_exact=Decimal("1"),
                    adopted_price_exact=Decimal("100"),
                    market_value_local_exact=Decimal("100"),
                    market_value_base_exact=Decimal("100"),
                    cost_basis_local_exact=Decimal("100"),
                    cost_basis_base_exact=Decimal("100"),
                    portfolio_weight=Decimal("1"),
                    account_ids=("account-1",),
                    valuation_coverage_state="complete",
                    valuation_reason_codes=(),
                ),
            ),
        ),
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


@pytest.mark.no_database
def test_current_scope_actuals_reuses_one_portfolio_valuation_across_scopes(monkeypatch):
    state = AllocationResearchState(
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
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    calls = {"holdings": 0}

    def fake_holdings(_portfolio_id, *, as_of_date):
        calls["holdings"] += 1
        return _published_holdings_statement(
            as_of_date=as_of_date,
            positions=(
                PublishedHoldingPosition(
                    instrument_id="instrument-a",
                    instrument_name="Instrument A",
                    instrument_type="fund",
                    currency="USD",
                    quantity_exact=Decimal("1"),
                    adopted_price_exact=Decimal("100"),
                    market_value_local_exact=Decimal("100"),
                    market_value_base_exact=Decimal("100"),
                    cost_basis_local_exact=Decimal("90"),
                    cost_basis_base_exact=Decimal("90"),
                    portfolio_weight=Decimal("0.666666666666666667"),
                    account_ids=("account-1",),
                    valuation_coverage_state="complete",
                    valuation_reason_codes=(),
                ),
            ),
            cash_accounts=(
                PublishedCashAccountValue(
                    account_id="cash-a",
                    account_name="Cash A",
                    account_type="deposit_account",
                    currency="USD",
                    value_base_exact=Decimal("50"),
                    coverage_state="complete",
                    reason_codes=(),
                ),
            ),
        )

    monkeypatch.setattr(
        "portfolio_app.services.allocation_solver.read_current_published_holdings",
        fake_holdings,
    )

    root_rows, _warnings = _current_scope_actuals(state, scope_node_id=None, as_of_date=date(2026, 1, 2))
    child_rows, _warnings = _current_scope_actuals(state, scope_node_id="node-risk", as_of_date=date(2026, 1, 2))

    assert sum(float(row["current_weight"] or 0.0) for row in root_rows) == pytest.approx(1.0)
    assert child_rows[0]["current_weight"] == pytest.approx(1.0)
    assert calls == {"holdings": 1}

    _current_scope_actuals(state, scope_node_id=None, as_of_date=date(2026, 1, 3))
    assert calls == {"holdings": 2}


def test_allocation_research_source_has_no_legacy_market_or_fx_provider() -> None:
    source = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (
            allocation_research_service,
            allocation_solver_service,
            market_data_service,
            policy_replay_service,
            risk_math_service,
        )
    )

    banned_symbols = {
        "_build_direct_fx_instrument_map",
        "_candidate_quote_bases",
        "_selected_price_points",
        "get_platform_fx_rates",
        "get_registry_instrument_detail",
        "resolve_fx_rate_on",
        "instrument_detail_cache",
        "direct_fx_instruments",
    }
    assert {symbol for symbol in banned_symbols if symbol in source} == set()


def test_zero_risk_budget_member_is_excluded_from_covariance_and_kept_in_results() -> None:
    long_dates = [item.date() for item in pd.bdate_range("2026-01-05", periods=12)]
    return_paths = {
        "asset-a": [0.0, 0.010, -0.004, 0.006, 0.002, -0.003, 0.007, -0.002, 0.004, 0.001, -0.005, 0.006],
        "asset-b": [0.0, -0.003, 0.008, 0.001, -0.004, 0.006, -0.002, 0.005, -0.001, 0.007, 0.002, -0.003],
    }

    def detail(instrument_id: str, dates: list[date], returns: list[float]) -> dict[str, object]:
        value = 1.0
        points: list[dict[str, object]] = []
        for point_date, point_return in zip(dates, returns, strict=True):
            value *= 1.0 + point_return
            points.append(
                _canonical_point(
                    point_date,
                    value,
                    quote_basis="total_return_nav",
                    currency="USD",
                )
            )
        return _canonical_instrument(instrument_id, currency="USD", points=points)

    short_dates = long_dates[-2:]
    instrument_details = [
        detail("asset-a", long_dates, return_paths["asset-a"]),
        detail("asset-b", long_dates, return_paths["asset-b"]),
        detail("asset-short", short_dates, [0.0, 0.02]),
    ]
    _install_canonical_instruments(instrument_details)
    market_data = _lock_portfolio_market_data(
        instrument_ids=["asset-a", "asset-b", "asset-short"],
        base_currency="USD",
        start_date=risk_window_start_date(long_dates[-1], 30),
        end_date=long_dates[-1],
    )
    state = AllocationResearchState(
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
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
        market_data=market_data,
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


def test_zero_weight_member_starting_after_early_rebalance_does_not_block_policy_replay(monkeypatch) -> None:
    active_dates = [item.date() for item in pd.bdate_range("2026-01-02", "2026-07-09")]
    late_dates = [item.date() for item in pd.bdate_range("2026-06-01", "2026-07-09")]

    def detail(instrument_id: str, dates: list[date], daily_return: float) -> dict[str, object]:
        value = 1.0
        points: list[dict[str, object]] = []
        for index, point_date in enumerate(dates):
            value *= 1.0 + (daily_return if index % 2 == 0 else -daily_return / 2.0)
            points.append(
                _canonical_point(
                    point_date,
                    value,
                    quote_basis="total_return_nav",
                    currency="USD",
                )
            )
        return _canonical_instrument(instrument_id, currency="USD", points=points)

    _install_canonical_instruments(
        [
            detail("active", active_dates, 0.002),
            detail("late-zero", late_dates, 0.003),
        ]
    )

    state = AllocationResearchState(
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
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={},
    )
    monkeypatch.setattr(policy_replay_service, "_build_taxonomy_state", lambda *_args, **_kwargs: state)
    original_solve = allocation_research_service._solve_current_target_weights_from_state
    period_solutions: list[dict[str, object]] = []

    def capture_period_solve(*args, **kwargs):
        solution = original_solve(*args, **kwargs)
        period_solutions.append(solution)
        return solution

    monkeypatch.setattr(
        policy_replay_service,
        "_solve_current_target_weights_from_state",
        capture_period_solve,
    )
    current_solution = {
        "leaf_targets": [
            {"member_type": "instrument", "member_id": "active", "target_weight": 1.0},
            {"member_type": "instrument", "member_id": "late-zero", "target_weight": 0.0},
        ],
        "calculation_frequency": {"resolved_frequency": "daily"},
    }

    payload = build_current_target_policy_replay(
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

    assert payload["policy_replay"]["points"]
    assert payload["policy_replay"]["points"][0] == {"date": "2026-03-01", "value": 1.0}
    assert payload["policy_replay"]["points"][-1]["date"] == "2026-07-09"
    assert payload["policy_replay"]["points"][-1]["value"] == pytest.approx(
        1.0479989767001392
    )
    assert payload["market_data_dependencies"]["policy_version"] == (
        "allocation_research_market_data.v1"
    )
    assert payload["market_data_dependencies"]["instrument_ids"] == ["active", "late-zero"]
    assert payload["market_data_dependencies"]["quote_dependencies"]
    assert all(
        item["role"] == "total_return"
        for item in payload["market_data_dependencies"]["quote_dependencies"]
    )
    assert period_solutions
    assert not any(
        "Allocation Research window is clipped" in warning
        for warning in payload["policy_replay"]["warnings"]
    )
    assert any(date.fromisoformat(str(solution["solve_event"]["as_of_date"])) < late_dates[0] for solution in period_solutions)
    for solution in period_solutions:
        row_by_id = {str(row["member_id"]): row for row in solution["leaf_targets"]}
        assert row_by_id["late-zero"]["target_weight"] == pytest.approx(0.0)


def test_positive_top_sleeve_minimum_overrides_zero_configured_weight() -> None:
    dates = [item.date() for item in pd.bdate_range("2026-01-05", periods=8)]

    def detail(instrument_id: str, step: float) -> dict[str, object]:
        return _canonical_instrument(
            instrument_id,
            currency="USD",
            points=[
                _canonical_point(
                    point_date,
                    1.0 + index * step,
                    quote_basis="total_return_nav",
                    currency="USD",
                )
                for index, point_date in enumerate(dates)
            ],
        )

    _install_canonical_instruments(
        [detail("asset-a", 0.01), detail("asset-b", 0.005)]
    )
    market_data = _lock_portfolio_market_data(
        instrument_ids=["asset-a", "asset-b"],
        base_currency="USD",
        start_date=risk_window_start_date(dates[-1], 30),
        end_date=dates[-1],
    )

    state = AllocationResearchState(
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
        frozen_taxonomy_node_ids=frozenset(),
        top_sleeve_weight_bounds={"node-b": {"min_weight": 0.2, "max_weight": None}},
        market_data=market_data,
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


def test_policy_replay_universe_omits_zero_weight_leaf_targets() -> None:
    assert _active_policy_replay_leaf_ids(
        {
            "leaf_targets": [
                {"member_type": "instrument", "member_id": "active-a", "target_weight": 0.6},
                {"member_type": "instrument", "member_id": "excluded", "target_weight": 0.0},
                {"member_type": "instrument", "member_id": "active-b", "target_weight": 0.4},
                {"member_type": "instrument", "member_id": "active-a", "target_weight": 0.6},
                {"member_type": "cash_bucket", "member_id": "cash", "target_weight": 0.2},
            ]
        }
    ) == ["active-a", "active-b"]


def test_allocation_research_policy_replay_methodology_warnings_are_explicit() -> None:
    assert any("not a point-in-time reconstruction" in warning for warning in POLICY_REPLAY_METHODOLOGY_WARNINGS)
    assert any("transaction costs" in warning and "cash residual" in warning for warning in POLICY_REPLAY_METHODOLOGY_WARNINGS)


def test_allocation_research_assumptions_do_not_truncate_volatility_cap_or_policy_replay_caveats() -> None:
    assumptions = allocation_research_workbench_service._build_target_assumptions(
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
    assert any("not a point-in-time reconstruction" in assumption for assumption in assumptions)
    assert any("transaction costs" in assumption for assumption in assumptions)
    assert any("Look-through forward RC" in assumption for assumption in assumptions)


def test_planning_group_snapshot_does_not_count_system_cash_as_unassigned(monkeypatch):
    monkeypatch.setattr(allocation_research_workbench_service, "list_taxonomy_nodes", lambda _portfolio_id: [])
    monkeypatch.setattr(allocation_research_workbench_service, "list_taxonomy_assignments", lambda _portfolio_id: [])

    groups = allocation_research_workbench_service._build_planning_group_snapshot(
        (
            PublishedHoldingPosition(
                instrument_id="instrument-unassigned",
                instrument_name="Unassigned",
                instrument_type="fund",
                currency="USD",
                quantity_exact=Decimal("1"),
                adopted_price_exact=Decimal("100"),
                market_value_local_exact=Decimal("100"),
                market_value_base_exact=Decimal("100"),
                cost_basis_local_exact=Decimal("90"),
                cost_basis_base_exact=Decimal("90"),
                portfolio_weight=Decimal("0.833333333333333333"),
                account_ids=("securities-account",),
                valuation_coverage_state="complete",
                valuation_reason_codes=(),
            ),
        ),
        cash_accounts=(
            PublishedCashAccountValue(
                account_id="cash-account",
                account_name="Cash",
                account_type="deposit_account",
                currency="USD",
                value_base_exact=Decimal("20"),
                coverage_state="complete",
                reason_codes=(),
            ),
        ),
        pending_settlement_base_exact=Decimal("0"),
        portfolio_id="portfolio-unassigned-test",
        planning_taxonomy_id="taxonomy-test",
    )

    unassigned_group = next(item for item in groups if item["group_key"] == "unassigned")
    cash_group = next(item for item in groups if item["group_key"] == SYSTEM_CASH_TARGET_MEMBER_ID)
    assert unassigned_group["position_count"] == 1
    assert unassigned_group["end_value_base"] == 100.0
    assert cash_group["group_label"] == SYSTEM_CASH_TARGET_LABEL
    assert cash_group["end_value_base"] == 20.0


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
                    "target_member_type": TARGET_MEMBER_CASH,
                    "target_member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
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
                    "target_member_type": TARGET_MEMBER_CASH,
                    "target_member_id": SYSTEM_CASH_TARGET_MEMBER_ID,
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
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
            json=payload,
        )
        assert target_set_response.status_code == 200, target_set_response.json()
    _publish_portfolio_daily()


def _create_completed_allocation_research_run(
    client,
    *,
    as_of_mode: str = "dynamic",
    as_of_date: str = "2026-04-15",
) -> dict[str, object]:
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_mode": as_of_mode,
            "as_of_date": as_of_date,
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200, settings_response.json()
    run_response = client.post(
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    return run_response.json()


def test_allocation_research_workbench_returns_target_solve_defaults(client):
    response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "portfolio-ops"
    assert payload["settings"]["planning_taxonomy_id"] is None
    assert payload["settings"]["target_dimension"] == "scope_default"
    assert payload["settings"]["capital_mode"] == "unit_notional"
    assert payload["settings"]["calculation_frequency"] == "auto"
    assert payload["settings"]["missing_return_policy"] == "strict"
    assert payload["settings"]["policy_replay_rebalance_frequency"] == "1m"
    assert payload["settings"]["policy_replay_benchmark_instrument_id"] is None
    assert payload["calculation_frequency"]["resolved_frequency"] == "daily"
    assert payload["risk_policy"]["model_name"] == "Production Risk Model"
    assert payload["risk_policy"]["model_role"] == "production"
    assert payload["risk_policy"]["covariance_model_id"] == "ewma_vol_shrinkage_corr_covariance"
    assert payload["risk_policy"]["lookback_days"] == 90
    assert payload["risk_policy"]["calculation_frequency"] == "auto"
    assert payload["risk_policy"]["resolved_calculation_frequency"] == "daily"
    assert payload["risk_policy"]["missing_return_policy"] == "strict"
    assert payload["risk_policy"]["contribution_mode"] == "signed"
    assert payload["risk_policy"]["parameters"]["min_observations"] == 45
    assert payload["risk_policy"]["parameters"]["corr_min_observations"] == 45
    assert payload["risk_policy"]["parameters_by_frequency"]["daily"]["min_observations"] == 45
    assert payload["risk_policy"]["parameters_by_frequency"]["weekly"]["min_observations"] == 9
    assert payload["risk_policy"]["parameters_by_frequency"]["monthly"]["min_observations"] == 3
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


def test_production_risk_window_min_observations_scale_with_calendar_window() -> None:
    assert risk_min_observations_for_window("daily", 30) == 15
    assert risk_min_observations_for_window("daily", 90) == 45
    assert risk_min_observations_for_window("daily", 180) == 90
    assert risk_min_observations_for_window("weekly", 90) == 9
    assert risk_min_observations_for_window("monthly", 90) == 3


def test_production_risk_policy_rejects_unsupported_windows() -> None:
    with pytest.raises(ValueError, match="Risk window must be one of 1M, 3M, 6M, 12M, 24M"):
        normalize_portfolio_risk_policy({"lookback_days": 7})


def test_database_rejects_unsupported_historical_allocation_research_window(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            AllocationResearchRunRecordModel(
                allocation_research_run_id="unsupported-window-run",
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


def test_allocation_research_window_dates_use_calendar_months() -> None:
    assert risk_window_start_date(date(2026, 5, 29), 30) == date(2026, 4, 29)
    assert risk_window_start_date(date(2026, 5, 29), 90) == date(2026, 2, 28)
    assert risk_window_start_date(date(2026, 5, 29), 180) == date(2025, 11, 29)


def test_allocation_research_policy_replay_rebalance_schedule_rolls_from_first_valid_month() -> None:
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


def test_allocation_research_policy_replay_metrics_empty_data_is_versioned_and_fails_closed() -> None:
    metrics = _build_policy_replay_metrics([], {})

    assert metrics["method_version"] == (
        "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
    )
    assert metrics["method_version"] == POLICY_REPLAY_METRICS_METHOD_VERSION
    assert metrics["history_reliability"] == {
        "start_date": None,
        "end_date": None,
        "elapsed_days": None,
        "calendar_span_days": None,
        "minimum_history_days": MIN_ANNUALIZED_RETURN_HISTORY_DAYS,
        "annualized_return_eligible": False,
        "annualized_return_reason_codes": [HISTORY_WINDOW_UNAVAILABLE],
        "sample_label": (
            "Observed period unavailable · 0 snapshots · "
            "0 return observations · 0 risk observations"
        ),
        "annualization_message": (
            "Observed performance history is unavailable. Annualized TWR, "
            "IRR / MWRR, and Calmar Ratio are withheld."
        ),
    }
    for field_name in (
        "period_return",
        "ytd_return",
        "annualized_return",
        "annualized_volatility",
        "sharpe_ratio",
        "max_drawdown",
        "current_drawdown",
        "calmar_ratio",
    ):
        assert metrics[field_name] is None


def test_allocation_research_policy_replay_short_history_retains_non_geometric_metrics() -> None:
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

    metrics = _build_policy_replay_metrics(points, returns)
    periods_per_year = _infer_periods_per_year(
        [date.fromisoformat(item) for item in returns]
    )
    return_values = np.asarray(list(returns.values()), dtype="float64")
    expected_volatility = float(
        np.std(return_values, ddof=1) * sqrt(periods_per_year)
    )
    expected_arithmetic_sharpe = float(
        (np.mean(return_values) * periods_per_year) / expected_volatility
    )

    assert metrics["method_version"] == POLICY_REPLAY_METRICS_METHOD_VERSION
    history_reliability = metrics["history_reliability"]
    assert history_reliability["elapsed_days"] == 12
    assert history_reliability["annualized_return_eligible"] is False
    assert history_reliability["annualized_return_reason_codes"] == [
        ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM
    ]
    assert metrics["period_return"] == pytest.approx(0.12)
    assert metrics["ytd_return"] == pytest.approx(0.12)
    assert metrics["annualized_return"] is None
    assert metrics["annualized_volatility"] == pytest.approx(expected_volatility)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_arithmetic_sharpe)
    assert metrics["max_drawdown"] == pytest.approx(-0.2)
    assert metrics["max_drawdown_days"] == 3
    assert metrics["max_drawdown_recovery_days"] == 7
    assert metrics["current_drawdown"] == pytest.approx(0.0)
    assert metrics["calmar_ratio"] is None


def test_allocation_research_policy_replay_metrics_publish_geometric_annualization_at_365_days() -> None:
    start_date = date(2025, 1, 1)
    drawdown_date = date(2025, 7, 2)
    end_date = date(2026, 1, 1)
    points = [
        {"date": start_date.isoformat(), "value": 1.0},
        {"date": drawdown_date.isoformat(), "value": 0.9},
        {"date": end_date.isoformat(), "value": 1.1},
    ]
    returns = {
        drawdown_date.isoformat(): -0.1,
        end_date.isoformat(): 1.1 / 0.9 - 1.0,
    }

    metrics = _build_policy_replay_metrics(points, returns)
    periods_per_year = _infer_periods_per_year([drawdown_date, end_date])
    expected_annualized_return = 1.1 ** (periods_per_year / 2.0) - 1.0
    expected_volatility = float(
        np.std(np.asarray(list(returns.values())), ddof=1)
        * sqrt(periods_per_year)
    )
    expected_arithmetic_sharpe = float(
        (np.mean(list(returns.values())) * periods_per_year)
        / expected_volatility
    )

    assert metrics["method_version"] == POLICY_REPLAY_METRICS_METHOD_VERSION
    history_reliability = metrics["history_reliability"]
    assert history_reliability["elapsed_days"] == 365
    assert history_reliability["annualized_return_eligible"] is True
    assert history_reliability["annualized_return_reason_codes"] == []
    assert metrics["period_return"] == pytest.approx(0.1)
    assert metrics["annualized_return"] == pytest.approx(
        expected_annualized_return
    )
    assert metrics["annualized_volatility"] == pytest.approx(expected_volatility)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_arithmetic_sharpe)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["calmar_ratio"] == pytest.approx(
        expected_annualized_return / 0.1
    )


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


def test_allocation_research_settings_updates_production_risk_policy(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "calculation_frequency": "weekly",
            "missing_return_policy": "complete_case_drop",
            "covariance_model_id": "sample_covariance",
            "contribution_mode": "abs",
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    risk_policy_response = client.get("/api/portfolios/portfolio-ops/risk-policy")
    assert risk_policy_response.status_code == 200
    risk_policy = risk_policy_response.json()
    assert risk_policy["covariance_model_id"] == "sample_covariance"
    assert risk_policy["lookback_days"] == 180
    assert risk_policy["calculation_frequency"] == "weekly"
    assert risk_policy["resolved_calculation_frequency"] == "weekly"
    assert risk_policy["missing_return_policy"] == "complete_case_drop"
    assert risk_policy["contribution_mode"] == "abs"
    assert risk_policy["parameters"]["min_observations"] == 18
    assert risk_policy["parameters"]["corr_min_observations"] == 18
    assert risk_policy["parameters_by_frequency"]["daily"]["min_observations"] == 90
    assert risk_policy["parameters_by_frequency"]["weekly"]["min_observations"] == 18
    assert risk_policy["parameters_by_frequency"]["monthly"]["min_observations"] == 5

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    workbench_policy = workbench_response.json()["risk_policy"]
    assert workbench_policy["covariance_model_id"] == "sample_covariance"
    assert workbench_policy["lookback_days"] == 180
    assert workbench_policy["calculation_frequency"] == "weekly"
    assert workbench_policy["missing_return_policy"] == "complete_case_drop"
    assert workbench_policy["contribution_mode"] == "abs"


def test_allocation_research_settings_updates_policy_replay_controls(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
        json={
            "planning_taxonomy_id": None,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "policy_replay_rebalance_frequency": "1w",
            "policy_replay_benchmark_instrument_id": "fund-us-agg",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["policy_replay_rebalance_frequency"] == "1w"
    assert settings_payload["policy_replay_benchmark_instrument_id"] == "fund-us-agg"

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    workbench_settings = workbench_response.json()["settings"]
    assert workbench_settings["policy_replay_rebalance_frequency"] == "1w"
    assert workbench_settings["policy_replay_benchmark_instrument_id"] == "fund-us-agg"


def test_allocation_research_settings_accepts_volatility_cap_mode(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    assert workbench_response.json()["settings"]["capital_mode"] == "volatility_cap"


def test_allocation_research_target_volatility_defaults_max_gross_to_unit_leverage(client):
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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


def test_allocation_research_workbench_reads_canonical_run_top_holdings(client):
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            AllocationResearchRunRecordModel(
                allocation_research_run_id="canonical-run",
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
                    ]
                },
                artifacts_json=[],
                request_payload_json={},
                error_message=None,
            )
        )
        session.commit()

    response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["detail"] is None
    top_holding = payload["selected_run"]["detail"]["top_holdings"][0]
    assert top_holding["instrument_id"] == "equity-us-abbv"
    assert top_holding["instrument_name"] == "AbbVie Inc"
    assert top_holding["instrument_type"] == "equity"
    assert payload["selected_run"]["detail"]["top_holdings"][0]["instrument_id"] == "equity-us-abbv"
    assert payload["selected_run"]["reliability_state"] == "stale"
    assert any(
        "predates planning-state fingerprinting" in reason
        for reason in payload["selected_run"]["reliability_reasons"]
    )

    run_response = client.get("/api/portfolios/portfolio-ops/allocation-research/runs/canonical-run")
    assert run_response.status_code == 200
    assert run_response.json()["detail"]["top_holdings"][0]["instrument_id"] == "equity-us-abbv"


def test_allocation_research_dynamic_as_of_tracks_latest_portfolio_date(client):
    response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["as_of_mode"] == "dynamic"
    assert payload["settings"]["pinned_as_of_date"] is None
    assert payload["settings"]["as_of_date"] == payload["as_of_date"]
    assert payload["current_context"]["as_of_date"] == payload["as_of_date"]


def test_allocation_research_pinned_as_of_requires_explicit_mode(client):
    settings = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench").json()["settings"]
    response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
            "policy_replay_rebalance_frequency": settings["policy_replay_rebalance_frequency"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_mode"] == "pinned"
    assert payload["as_of_date"] == "2026-04-01"
    assert payload["pinned_as_of_date"] == "2026-04-01"

    refreshed = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench").json()
    assert refreshed["current_context"]["as_of_date"] == "2026-04-01"


def test_allocation_research_series_prefers_total_return_nav_for_funds() -> None:
    start_date = date(2026, 4, 14)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "fund-test",
                currency="USD",
                points=[
                    _canonical_point(start_date, 1.0, quote_basis="official_nav", currency="USD"),
                    _canonical_point(start_date, 1.12, quote_basis="total_return_nav", currency="USD"),
                    _canonical_point(end_date, 1.01, quote_basis="official_nav", currency="USD"),
                    _canonical_point(end_date, 1.135, quote_basis="total_return_nav", currency="USD"),
                ],
            )
        ]
    )
    context = _lock_portfolio_market_data(
        instrument_ids=["fund-test"],
        base_currency="USD",
        start_date=start_date,
        end_date=end_date,
    )
    state = _market_data_only_state(
        context=context,
        base_currency="USD",
        as_of_date=end_date,
    )

    series, warnings = _build_instrument_nav_series(
        state,
        instrument_id="fund-test",
        start_date=start_date,
        end_date=end_date,
    )

    assert context.total_return_book.windows["fund-test"].quote_basis == "total_return_nav"
    assert series.to_dict() == {start_date: 1.12, end_date: 1.135}
    assert warnings == []


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        ("partial", "partial_series"),
        ("rejected", "rejected_observation"),
        ("withdrawn", "withdrawn_observation"),
    ],
)
def test_allocation_research_series_fails_closed_for_later_noncomplete_observation(
    status: str,
    expected_reason: str,
) -> None:
    start_date = date(2026, 4, 14)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "fund-status-test",
                currency="USD",
                points=[
                    _canonical_point(start_date, 1.12, quote_basis="total_return_nav", currency="USD"),
                    _canonical_point(
                        end_date,
                        1.135,
                        quote_basis="total_return_nav",
                        currency="USD",
                        status=("complete" if status == "withdrawn" else status),
                    ),
                ],
            )
        ]
    )
    if status == "withdrawn":
        replaced = shared_store.replace_nav_history(
            get_session_factory(),
            instrument_id="fund-status-test",
            rows=[
                {
                    "as_of_date": start_date.isoformat(),
                    "nav_with_dividend": "1.12",
                    "currency": "USD",
                }
            ],
            source_ref="allocation-research-test",
            point_status="complete",
            refresh_status="success",
            updated_by="pytest",
            message="Withdraw the omitted endpoint through replacement semantics.",
            replace_all=True,
        )
        assert replaced is not None
    context = _lock_portfolio_market_data(
        instrument_ids=["fund-status-test"],
        base_currency="USD",
        start_date=start_date,
        end_date=end_date,
    )
    state = _market_data_only_state(
        context=context,
        base_currency="USD",
        as_of_date=end_date,
    )

    with pytest.raises(PortfolioMarketDataError) as error:
        _build_instrument_nav_series(
            state,
            instrument_id="fund-status-test",
            start_date=start_date,
            end_date=end_date,
        )

    assert expected_reason in error.value.reason_codes
    assert error.value.dependency["role"] == "total_return"
    non_complete = error.value.dependency["non_complete_observations"][-1]
    assert non_complete["status"] == status
    assert non_complete["revision_id"] in error.value.dependency[
        "window_calculation_dependency"
    ]["excluded_revision_ids"]


def test_instrument_trend_uses_explicit_locked_total_return_basis() -> None:
    metrics = build_instrument_trend_metrics_from_points(
        [
            {"date": date(2026, 1, 1), "value": 100.0},
            {"date": date(2026, 1, 10), "value": 110.0},
        ],
        as_of_date=date(2026, 1, 10),
        selected_basis="adjusted_close",
    )

    assert metrics["instrument_trend_basis"] == "adjusted_close"
    assert metrics["instrument_return_mtd"] is None
    assert metrics["instrument_return_ytd"] is None


def test_instrument_trend_mtd_and_ytd_require_prior_period_anchor() -> None:
    metrics = build_instrument_trend_metrics_from_points(
        [
            {"date": date(2025, 12, 31), "value": 100.0},
            {"date": date(2026, 1, 1), "value": 101.0},
            {"date": date(2026, 1, 10), "value": 110.0},
        ],
        as_of_date=date(2026, 1, 10),
        selected_basis="adjusted_close",
    )

    assert metrics["instrument_return_mtd"] == pytest.approx(0.1)
    assert metrics["instrument_return_ytd"] == pytest.approx(0.1)


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


def test_instrument_trend_volatility_can_use_weekly_risk_basis() -> None:
    points = [
        {"date": date(2026, 1, 1), "value": 100.0},
        {"date": date(2026, 1, 2), "value": 101.0},
        {"date": date(2026, 1, 5), "value": 104.0},
        {"date": date(2026, 1, 9), "value": 106.0},
        {"date": date(2026, 1, 12), "value": 102.0},
        {"date": date(2026, 1, 16), "value": 103.0},
    ]
    returns = [106.0 / 101.0 - 1.0, 103.0 / 106.0 - 1.0]
    mean_return = sum(returns) / len(returns)
    sample_stddev = sqrt(sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1))
    periods_per_year = 2 / 14 * 365.25

    assert _annualized_volatility(
        points,
        calculation_frequency="weekly",
        final_date=date(2026, 1, 16),
    ) == pytest.approx(sample_stddev * sqrt(periods_per_year))


def test_holdings_instrument_volatility_requires_window_start_coverage() -> None:
    metrics = build_instrument_trend_metrics_from_points(
        [
            {
                "date": date(2026, 1, 1) + timedelta(days=offset),
                "value": 100.0 + offset / 10,
            }
            for offset in range(0, 106, 7)
        ],
        as_of_date=date(2026, 4, 16),
        selected_basis="adjusted_close",
        calculation_frequency="weekly",
    )

    assert metrics["instrument_volatility_6m"] is None


def test_holdings_instrument_volatility_allows_complete_weekly_window() -> None:
    metrics = build_instrument_trend_metrics_from_points(
        [
            {
                "date": date(2025, 10, 16) + timedelta(days=offset),
                "value": 100.0 + offset / 10,
            }
            for offset in range(0, 183, 7)
        ],
        as_of_date=date(2026, 4, 16),
        selected_basis="adjusted_close",
        calculation_frequency="weekly",
    )

    assert metrics["instrument_volatility_6m"] is not None


def test_allocation_research_series_prefers_adjusted_close_for_equities() -> None:
    start_date = date(2026, 4, 14)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "equity-test",
                currency="USD",
                instrument_type="equity",
                points=[
                    _canonical_point(start_date, 100.0, quote_basis="close", currency="USD"),
                    _canonical_point(start_date, 108.0, quote_basis="adjusted_close", currency="USD"),
                    _canonical_point(end_date, 102.0, quote_basis="close", currency="USD"),
                    _canonical_point(end_date, 110.5, quote_basis="adjusted_close", currency="USD"),
                ],
            )
        ]
    )
    context = _lock_portfolio_market_data(
        instrument_ids=["equity-test"],
        base_currency="USD",
        start_date=start_date,
        end_date=end_date,
    )
    state = _market_data_only_state(
        context=context,
        base_currency="USD",
        as_of_date=end_date,
    )

    series, _warnings = _build_instrument_nav_series(
        state,
        instrument_id="equity-test",
        start_date=start_date,
        end_date=end_date,
    )

    assert context.total_return_book.windows["equity-test"].quote_basis == "adjusted_close"
    assert series.to_dict() == {start_date: 108.0, end_date: 110.5}


def test_allocation_research_never_falls_back_to_valuation_when_total_return_role_is_empty() -> None:
    start_date = date(2026, 4, 14)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "equity-no-total-return",
                currency="USD",
                instrument_type="equity",
                total_return_bases=[],
                points=[
                    _canonical_point(start_date, 100.0, quote_basis="close", currency="USD"),
                    _canonical_point(end_date, 102.0, quote_basis="close", currency="USD"),
                ],
            )
        ]
    )
    context = _lock_portfolio_market_data(
        instrument_ids=["equity-no-total-return"],
        base_currency="USD",
        start_date=start_date,
        end_date=end_date,
    )
    state = _market_data_only_state(
        context=context,
        base_currency="USD",
        as_of_date=end_date,
    )

    with pytest.raises(PortfolioMarketDataError) as error:
        _build_instrument_nav_series(
            state,
            instrument_id="equity-no-total-return",
            start_date=start_date,
            end_date=end_date,
        )

    assert "missing_quote_policy" in error.value.reason_codes
    assert error.value.dependency["role"] == "total_return"


def test_allocation_research_fails_closed_when_total_return_endpoint_is_late() -> None:
    start_date = date(2026, 4, 1)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "equity-late-total-return",
                currency="USD",
                instrument_type="equity",
                points=[
                    _canonical_point(
                        start_date,
                        100.0,
                        quote_basis="adjusted_close",
                        currency="USD",
                    )
                ],
            )
        ]
    )
    context = _lock_portfolio_market_data(
        instrument_ids=["equity-late-total-return"],
        base_currency="USD",
        start_date=start_date,
        end_date=end_date,
    )
    state = _market_data_only_state(
        context=context,
        base_currency="USD",
        as_of_date=end_date,
    )

    with pytest.raises(PortfolioMarketDataError) as error:
        _build_instrument_nav_series(
            state,
            instrument_id="equity-late-total-return",
            start_date=start_date,
            end_date=end_date,
        )

    assert {"late_observation", "freshness_limit_exceeded"}.issubset(
        error.value.reason_codes
    )
    assert error.value.dependency["endpoint_calculation_dependency"] is not None


def test_allocation_research_canonical_cross_fx_keeps_two_leg_dependencies() -> None:
    start_date = date(2026, 4, 14)
    end_date = date(2026, 4, 15)
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "fund-hkd-cross",
                currency="HKD",
                points=[
                    _canonical_point(start_date, 100.0, quote_basis="total_return_nav", currency="HKD"),
                    _canonical_point(end_date, 101.0, quote_basis="total_return_nav", currency="HKD"),
                ],
            ),
            _canonical_instrument(
                "fx-usd-hkd",
                currency="HKD",
                instrument_type="fx",
                total_return_bases=[],
                points=[
                    _canonical_point(start_date, 7.8, quote_basis="spot", currency="HKD"),
                    _canonical_point(end_date, 7.82, quote_basis="spot", currency="HKD"),
                ],
            ),
            _canonical_instrument(
                "fx-usd-cny",
                currency="CNY",
                instrument_type="fx",
                total_return_bases=[],
                points=[
                    _canonical_point(start_date, 7.2, quote_basis="spot", currency="CNY"),
                    _canonical_point(end_date, 7.29, quote_basis="spot", currency="CNY"),
                ],
            ),
        ]
    )
    statements: list[str] = []
    session_factory = get_session_factory()
    with session_factory() as session:
        bind = session.get_bind()

        def record_query(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(str(statement))

        event.listen(bind, "before_cursor_execute", record_query)
        try:
            context = _lock_portfolio_market_data_in_session(
                session,
                instrument_ids=["fund-hkd-cross"],
                base_currency="CNY",
                start_date=start_date,
                end_date=end_date,
            )
        finally:
            event.remove(bind, "before_cursor_execute", record_query)
    state = _market_data_only_state(
        context=context,
        base_currency="CNY",
        as_of_date=end_date,
    )

    series, _warnings = _build_instrument_nav_series(
        state,
        instrument_id="fund-hkd-cross",
        start_date=start_date,
        end_date=end_date,
    )
    manifest = allocation_research_service._portfolio_market_data_manifest_from_state(state)

    assert series.loc[start_date] == pytest.approx(100.0 * 7.2 / 7.8)
    assert series.loc[end_date] == pytest.approx(101.0 * 7.29 / 7.82)
    assert manifest is not None
    assert len(manifest["quote_dependencies"]) == 1
    assert len(manifest["fx_dependencies"]) == 2
    assert all(item["path_kind"] == "cross" for item in manifest["fx_dependencies"])
    assert all(len(item["legs"]) == 2 for item in manifest["fx_dependencies"])
    assert len(statements) == 9


def test_portfolio_market_data_lock_has_bounded_queries_and_nav_is_sql_free() -> None:
    start_date = date(2026, 4, 1)
    end_date = date(2026, 4, 15)
    points = [
        _canonical_point(
            point_date.date(),
            100.0 + index,
            quote_basis="adjusted_close",
            currency="USD",
        )
        for index, point_date in enumerate(pd.bdate_range(start_date, end_date))
    ]
    _install_canonical_instruments(
        [
            _canonical_instrument(
                "equity-query-bound",
                currency="USD",
                instrument_type="equity",
                points=points,
            )
        ]
    )
    statements: list[str] = []
    session_factory = get_session_factory()
    with session_factory() as session:
        bind = session.get_bind()

        def record_query(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(str(statement))

        event.listen(bind, "before_cursor_execute", record_query)
        try:
            context = _lock_portfolio_market_data_in_session(
                session,
                instrument_ids=["equity-query-bound"],
                base_currency="USD",
                start_date=start_date,
                end_date=end_date,
            )
            lock_query_count = len(statements)
            state = _market_data_only_state(
                context=context,
                base_currency="USD",
                as_of_date=end_date,
            )
            for _index in range(25):
                series, _warnings = _build_instrument_nav_series(
                    state,
                    instrument_id="equity-query-bound",
                    start_date=start_date,
                    end_date=end_date,
                )
                assert len(series) == len(points)
            assert len(statements) == lock_query_count
        finally:
            event.remove(bind, "before_cursor_execute", record_query)

    assert lock_query_count == 4


def test_allocation_research_daily_alignment_does_not_span_missing_dates() -> None:
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
    assert pd.isna(returns_by_member["instrument-a"].loc[date(2026, 1, 2)])
    assert pd.isna(returns_by_member["instrument-a"].loc[date(2026, 1, 5)])
    assert returns_by_member["instrument-b"].loc[date(2026, 1, 2)] == pytest.approx(0.02)
    assert _infer_periods_per_year([date(2026, 1, 2), date(2026, 1, 5)]) == pytest.approx(2 / 6 * 365.25)


def test_allocation_research_weekly_alignment_uses_period_end_observations() -> None:
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
        calculation_frequency="weekly",
    )

    returns_by_member = {item.member.member_id: item.returns for item in aligned_members}
    assert calendar == [date(2026, 1, 2), date(2026, 1, 5)]
    assert returns_by_member["instrument-a"].loc[date(2026, 1, 5)] == pytest.approx(0.1)
    assert returns_by_member["instrument-b"].loc[date(2026, 1, 5)] == pytest.approx(101.0 / 102.0 - 1.0)


def test_allocation_research_weekly_staleness_applies_to_selected_period_observation_only() -> None:
    series = pd.Series(
        {
            date(2026, 1, 5): 100.0,
            date(2026, 1, 9): 101.0,
            date(2026, 1, 12): 102.0,
        },
        dtype="float64",
    )

    periodic = _periodic_nav_series(
        series,
        calculation_frequency="weekly",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 9),
    )

    assert periodic.loc[date(2026, 1, 9)] == pytest.approx(101.0)
    with pytest.raises(ValueError, match="stale observation"):
        _periodic_nav_series(
            pd.Series({date(2026, 1, 12): 102.0}, dtype="float64"),
            calculation_frequency="weekly",
            start_date=date(2026, 1, 12),
            end_date=date(2026, 1, 16),
        )


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


def test_allocation_research_covariance_annualizes_complete_aligned_dates() -> None:
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


def test_allocation_research_covariance_rejects_partial_missing_rows() -> None:
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


def test_allocation_research_covariance_complete_case_drop_uses_only_complete_rows() -> None:
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


def test_allocation_research_covariance_complete_case_drop_rejects_excessive_missing_rows() -> None:
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


def test_allocation_research_covariance_complete_case_drop_rejects_stale_latest_complete_row() -> None:
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


def test_allocation_research_risk_budget_solver_matches_tight_tolerance() -> None:
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


def test_allocation_research_risk_budget_solve_allows_empty_current_reference_weights() -> None:
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


def test_allocation_research_risk_budget_default_model_uses_calendar_window_observation_floor() -> None:
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
                "target_weight": 0.0,
            }
        ],
        base_currency="CNY",
    )

    assert gaps == [
        {
            "member_type": "instrument",
            "member_id": "short-history-fund",
            "label": "Short History Fund",
            "current_weight": pytest.approx(0.08),
            "target_weight": pytest.approx(0.0),
            "gap": pytest.approx(-0.08),
            "current_value_base": None,
            "base_currency": "CNY",
            "action": "Review",
            "execution_status": "manual_review_required",
            "execution_note": (
                "Current holdings with a 0% solved target require an explicit PM decision; "
                "Allocation Research does not infer an executable liquidation from target eligibility or limited history."
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


def test_allocation_research_run_creates_current_target_weight_outputs(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 30,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Allocation Research regression test",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["planning_taxonomy_id"] == taxonomy_id
    assert settings_payload["comparator_taxonomy_node_id"] == node_ids["Risk Assets"]
    assert settings_payload["target_dimension"] == "scope_default"

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert len(workbench_payload["planning_taxonomy_options"]) == 1
    assert any(item["label"] == "Top Level" for item in workbench_payload["planning_scope_options"])
    assert any(item["label"] == "Risk Assets" for item in workbench_payload["planning_scope_options"])

    run_response = client.post(
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()

    run_payload = run_response.json()
    assert run_payload["status"] == "completed"
    assert run_payload["planning_taxonomy_id"] == taxonomy_id
    assert "run_template" not in run_payload
    assert run_payload["artifact_count"] == 11
    assert run_payload["detail"]["selected_scope"]["taxonomy_node_id"] == node_ids["Risk Assets"]
    assert run_payload["detail"]["selected_scope"]["label"] == "Risk Assets"
    assert "policy_replay_metrics" not in run_payload["detail"]
    assert "policy_replay_curve" not in run_payload["detail"]
    assert "weight_schedule" not in run_payload["detail"]
    assert len(run_payload["detail"]["member_targets"]) == 2
    assert len(run_payload["detail"]["leaf_targets"]) == 2
    assert run_payload["detail"]["policy_replay"]["rebalance_frequency"] == "1m"
    assert run_payload["detail"]["policy_replay"]["lookback_days"] == 30
    assert any(
        "not a point-in-time reconstruction" in warning
        for warning in run_payload["detail"]["policy_replay"]["warnings"]
    )
    assert any(
        "transaction costs" in warning
        for warning in run_payload["detail"]["policy_replay"]["warnings"]
    )
    assert run_payload["detail"]["policy_replay_benchmark"] is None
    assert run_payload["detail"]["policy_replay_relative_metrics"] is None
    comparison_response = client.get(
        f"/api/portfolios/portfolio-ops/allocation-research/runs/"
        f"{run_payload['allocation_research_run_id']}"
        "/policy-replay/benchmark-comparison",
        params={"benchmark_instrument_id": "fund-hk-2800"},
    )
    assert comparison_response.status_code == 200, comparison_response.json()
    comparison_payload = comparison_response.json()
    assert comparison_payload["policy_replay_benchmark"]["instrument_id"] == "fund-hk-2800"
    assert comparison_payload["policy_replay_benchmark"]["points"]
    assert comparison_payload["policy_replay_benchmark"]["metrics"]["period_return"] is not None
    assert comparison_payload["policy_replay_relative_metrics"] is not None
    portfolio_period_return = run_payload["detail"]["policy_replay"]["metrics"]["period_return"]
    benchmark_period_return = comparison_payload["policy_replay_benchmark"]["metrics"]["period_return"]
    assert comparison_payload["policy_replay_relative_metrics"]["excess_return"] == pytest.approx(
        portfolio_period_return - benchmark_period_return
    )
    assert len(run_payload["detail"]["solved_result_groups"]) == 1
    solved_group = run_payload["detail"]["solved_result_groups"][0]
    assert solved_group["top_sleeve_label"] == "Risk Assets"
    solved_rows_by_member = {item["member_id"]: item for item in solved_group["rows"]}
    assert set(solved_rows_by_member) == {"equity-us-abbv", "fund-hk-2800"}
    assert solved_rows_by_member["equity-us-abbv"]["target_risk_share"] is None
    assert solved_rows_by_member["fund-hk-2800"]["target_risk_share"] == pytest.approx(1.0)
    member_targets_by_label = {item["label"]: item for item in run_payload["detail"]["member_targets"]}
    assert member_targets_by_label["Defensive Equity"]["configured_risk_share"] == pytest.approx(0.45)
    assert member_targets_by_label["Hong Kong Beta"]["configured_risk_share"] == pytest.approx(0.55)
    leaf_targets_by_member = {item["member_id"]: item for item in run_payload["detail"]["leaf_targets"]}
    assert leaf_targets_by_member["equity-us-abbv"]["configured_risk_share"] is None
    assert leaf_targets_by_member["fund-hk-2800"]["configured_risk_share"] == pytest.approx(1.0)
    assert len(run_payload["detail"]["target_assumptions"]) >= 1
    assert any(
        "not a point-in-time reconstruction" in assumption
        for assumption in run_payload["detail"]["target_assumptions"]
    )
    assert len(run_payload["detail"]["target_rows"]) == 2
    assert any(item["label"] == "Defensive Equity" for item in run_payload["detail"]["member_targets"])
    assert any(item["label"] == "Hong Kong Beta" for item in run_payload["detail"]["member_targets"])
    assert all(item["source_label"] in {"TAA", "SAA", "Single Member"} for item in run_payload["detail"]["target_rows"])
    assert run_payload["detail"]["solve_event"]["as_of_date"] == "2026-04-15"
    assert len(run_payload["detail"]["target_weight_gaps"]) >= 1
    signal_labels = {item["label"] for item in run_payload["detail"]["signals"]}
    assert "Scope Default" in signal_labels
    assert "Solver" in signal_labels
    assert "Missing Returns" in signal_labels
    assert "Estimated Volatility" in signal_labels
    assert all(item["artifact_id"] != "reference_tape" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "target_weights" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "leaf_targets" for item in run_payload["artifacts"])
    assert all(item["artifact_id"] != "policy_replay_curve" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "solve_event" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "scope_solve_events" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "target_weight_gaps" for item in run_payload["artifacts"])
    assert all(item["label"] != "Daily NAV CSV" for item in run_payload["artifacts"])

    report_artifact = next(item for item in run_payload["artifacts"] if item["artifact_id"] == "report")
    artifact_response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/artifacts/content",
        params={"path": report_artifact["path"]},
    )
    assert artifact_response.status_code == 200
    artifact_payload = artifact_response.json()
    assert artifact_payload["preview_kind"] == "text"
    assert "# Allocation Research Run" in artifact_payload["content"]
    assert "Current Target Weights" in artifact_payload["content"]
    assert "## Assumptions and Limitations" in artifact_payload["content"]
    assert "not a point-in-time reconstruction" in artifact_payload["content"]
    assert "transaction costs" in artifact_payload["content"]

    selected_workbench_response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": run_payload["allocation_research_run_id"]},
    )
    assert selected_workbench_response.status_code == 200
    selected_workbench_payload = selected_workbench_response.json()
    assert selected_workbench_payload["selected_run"]["allocation_research_run_id"] == run_payload["allocation_research_run_id"]
    assert selected_workbench_payload["selected_run"]["reliability_state"] == "current"
    assert selected_workbench_payload["selected_run"]["is_current"] is True
    assert selected_workbench_payload["selected_run"]["reliability_reasons"] == []
    session_factory = get_session_factory()
    with session_factory() as session:
        stored_run = session.get(AllocationResearchRunRecordModel, run_payload["allocation_research_run_id"])
        assert stored_run is not None
        assert stored_run.request_payload_json["planning_state_fingerprint_version"] == 1
        assert stored_run.request_payload_json["planning_state_fingerprint"].startswith("sha256:")
        holdings_input = stored_run.request_payload_json["published_holdings_input"]
        assert holdings_input["schema_version"] == 1
        assert holdings_input["content_fingerprint"].startswith("sha256:")
        assert set(holdings_input["publication_lineage"]) == {
            "publication_id",
            "run_id",
            "manifest_id",
            "captured_generation",
        }
        market_input = stored_run.request_payload_json["market_data_input"]
        assert market_input["schema_version"] == 1
        assert market_input["fingerprint"].startswith("sha256:")
        manifests_by_consumer = {
            item["consumer"]: item
            for item in market_input["dependency_manifests"]
        }
        assert set(manifests_by_consumer) == {
            "current_target_solve",
            "policy_replay",
        }
        assert all(
            item["fingerprint"].startswith("sha256:")
            for item in manifests_by_consumer.values()
        )
        assert manifests_by_consumer["current_target_solve"]["dependencies"][
            "quote_dependencies"
        ]
        assert manifests_by_consumer["policy_replay"]["dependencies"][
            "fx_dependencies"
        ]


def test_allocation_research_run_becomes_stale_after_target_line_change(client) -> None:
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)
    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_id = run_response.json()["allocation_research_run_id"]

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
        json={
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
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
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


def test_allocation_research_workbench_returns_404_for_missing_selected_run(client) -> None:
    response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": "missing-allocation-research-run"},
    )

    assert response.status_code == 404, response.json()
    assert "missing-allocation-research-run" in response.json()["detail"]


def test_dynamic_allocation_research_run_stales_after_same_day_transaction_republish(
    client,
) -> None:
    run_payload = _create_completed_allocation_research_run(client)
    run_id = str(run_payload["allocation_research_run_id"])

    transaction_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "actor": {
                "actor_type": "user",
                "actor_id": "pm:allocation-research-reliability-test",
                "display_name": "Allocation Research Reliability Test",
                "actor_source": "client_asserted",
            },
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": "1",
            "price": "206.47",
            "gross_amount": "206.47",
            "fees": "0",
            "taxes": "0",
            "currency": "USD",
        },
    )
    assert transaction_response.status_code == 200, transaction_response.json()
    _publish_portfolio_daily()

    response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": run_id},
    )

    assert response.status_code == 200, response.json()
    selected_run = response.json()["selected_run"]
    assert selected_run["reliability_state"] == "stale"
    assert (
        "Published holdings at the Allocation Research analysis date changed after this run was created."
        in selected_run["reliability_reasons"]
    )
    assert (
        "The authoritative published-holdings publication lineage changed after this run was created."
        in selected_run["reliability_reasons"]
    )


def test_allocation_research_run_stales_after_quote_and_fx_dependency_revisions(
    client,
) -> None:
    run_payload = _create_completed_allocation_research_run(client)
    run_id = str(run_payload["allocation_research_run_id"])
    session_factory = get_session_factory()

    quote_revision_count = shared_store.upsert_market_data_points(
        session_factory,
        instrument_id="equity-us-abbv",
        rows=[
            _canonical_point(
                date(2026, 4, 15),
                207.01,
                quote_basis="adjusted_close",
                currency="USD",
            )
        ],
    )
    fx_revision_count = shared_store.upsert_market_data_points(
        session_factory,
        instrument_id="fx-usd-hkd",
        rows=[
            _canonical_point(
                date(2026, 4, 2),
                7.83,
                quote_basis="spot",
                currency="HKD",
            )
        ],
    )
    assert quote_revision_count == 1
    assert fx_revision_count == 1
    _publish_portfolio_daily()

    response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": run_id},
    )

    assert response.status_code == 200, response.json()
    selected_run = response.json()["selected_run"]
    assert selected_run["reliability_state"] == "stale"
    assert (
        "Canonical quote/NAV inputs used by this run were revised."
        in selected_run["reliability_reasons"]
    )
    assert (
        "Canonical FX inputs used by this run were revised."
        in selected_run["reliability_reasons"]
    )


def test_pinned_allocation_research_run_remains_current_after_later_date_publication(
    client,
) -> None:
    run_payload = _create_completed_allocation_research_run(
        client,
        as_of_mode="pinned",
        as_of_date="2026-04-15",
    )
    run_id = str(run_payload["allocation_research_run_id"])

    transaction_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "actor": {
                "actor_type": "user",
                "actor_id": "pm:allocation-research-pinned-test",
                "display_name": "Allocation Research Pinned Test",
                "actor_source": "client_asserted",
            },
            "transaction_type": "buy",
            "trade_date": "2026-04-16",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": "1",
            "price": "206.47",
            "gross_amount": "206.47",
            "fees": "0",
            "taxes": "0",
            "currency": "USD",
        },
    )
    assert transaction_response.status_code == 200, transaction_response.json()
    _publish_portfolio_daily(requested_as_of=date(2026, 4, 16))

    response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": run_id},
    )

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["as_of_date"] == "2026-04-16"
    assert payload["current_context"]["as_of_date"] == "2026-04-15"
    assert payload["selected_run"]["reliability_state"] == "current"
    assert payload["selected_run"]["reliability_reasons"] == []


def test_allocation_research_workbench_marks_historical_run_stale_and_non_current(client) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            AllocationResearchRunRecordModel(
                allocation_research_run_id="historical-stale-run",
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
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": "historical-stale-run"},
    )

    assert response.status_code == 200, response.json()
    selected_run = response.json()["selected_run"]
    assert selected_run["allocation_research_run_id"] == "historical-stale-run"
    assert selected_run["reliability_state"] == "stale"
    assert selected_run["is_current"] is False
    assert any("does not match the current Allocation Research date 2026-04-15" in reason for reason in selected_run["reliability_reasons"])
    assert any("predates planning-state fingerprinting" in reason for reason in selected_run["reliability_reasons"])
    assert any("published-holdings content fingerprint" in reason for reason in selected_run["reliability_reasons"])
    assert any("canonical market-data dependency manifest" in reason for reason in selected_run["reliability_reasons"])


def test_policy_replay_benchmark_returns_use_sampling_interval_returns() -> None:
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


def test_policy_replay_weekly_sampling_uses_periodic_returns_for_daily_series() -> None:
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

    sampled = _build_policy_replay_sampled_nav_by_instrument(
        {"daily": daily_nav, "weekly": weekly_nav},
        calculation_frequency="weekly",
        end_date=date(2026, 4, 10),
    )
    returns_by_instrument = {instrument_id: sampled_nav.pct_change().dropna() for instrument_id, sampled_nav in sampled.items()}
    rebal_dates = _policy_replay_rebalance_dates(
        start_date=date(2026, 3, 30),
        end_date=date(2026, 4, 10),
        frequency="1w",
        calculation_frequency="weekly",
        returns_by_instrument=returns_by_instrument,
    )

    assert returns_by_instrument["daily"].loc[date(2026, 4, 3)] == pytest.approx(110.0 / 100.0 - 1.0)
    assert returns_by_instrument["daily"].loc[date(2026, 4, 10)] == pytest.approx(121.0 / 110.0 - 1.0)
    assert rebal_dates == [date(2026, 4, 3), date(2026, 4, 10)]


def test_allocation_research_run_replaces_previous_run(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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

    first_response = client.post("/api/portfolios/portfolio-ops/allocation-research/runs", json={"requested_by": "pytest"})
    assert first_response.status_code == 200, first_response.json()
    first_run_id = first_response.json()["allocation_research_run_id"]

    second_response = client.post("/api/portfolios/portfolio-ops/allocation-research/runs", json={"requested_by": "pytest"})
    assert second_response.status_code == 200, second_response.json()
    second_run_id = second_response.json()["allocation_research_run_id"]
    assert second_run_id != first_run_id

    session_factory = get_session_factory()
    with session_factory() as session:
        remaining_runs = session.query(AllocationResearchRunRecordModel).filter_by(portfolio_id="portfolio-ops").all()
    assert [item.allocation_research_run_id for item in remaining_runs] == [second_run_id]

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert [item["allocation_research_run_id"] for item in workbench_payload["runs"]] == [second_run_id]
    assert workbench_payload["selected_run"]["allocation_research_run_id"] == second_run_id


def test_failed_allocation_research_run_keeps_previous_completed_run(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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

    first_response = client.post("/api/portfolios/portfolio-ops/allocation-research/runs", json={"requested_by": "pytest"})
    assert first_response.status_code == 200, first_response.json()
    first_run_id = first_response.json()["allocation_research_run_id"]

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": TARGET_MEMBER_INSTRUMENT,
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_ids["Defensive Equity"],
        },
    )
    assert assignment_response.status_code == 200, assignment_response.json()
    _publish_portfolio_daily()

    failed_response = client.post("/api/portfolios/portfolio-ops/allocation-research/runs", json={"requested_by": "pytest"})
    assert failed_response.status_code == 400, failed_response.json()
    assert "Defensive Equity" in failed_response.json()["detail"]
    assert "weight target set" in failed_response.json()["detail"]

    session_factory = get_session_factory()
    with session_factory() as session:
        remaining_runs = session.query(AllocationResearchRunRecordModel).filter_by(portfolio_id="portfolio-ops").all()
    run_by_id = {item.allocation_research_run_id: item for item in remaining_runs}
    assert run_by_id[first_run_id].status == "completed"
    assert any(item.status == "failed" for item in remaining_runs)

    workbench_response = client.get(
        "/api/portfolios/portfolio-ops/allocation-research/workbench",
        params={"selected_run_id": first_run_id},
    )
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert first_run_id in {item["allocation_research_run_id"] for item in workbench_payload["runs"]}
    assert workbench_payload["selected_run"]["allocation_research_run_id"] == first_run_id


def test_allocation_research_target_solve_actuals_include_pending_security_settlement(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
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
            "actor": {
                "actor_type": "user",
                "actor_id": "pm:allocation-research-api-test",
                "display_name": "Allocation Research API Test Manager",
                "actor_source": "client_asserted",
            },
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": "1.000000000000",
            "price": "206.470000000000",
            "gross_amount": "206.47000000",
            "fees": "0.00000000",
            "taxes": "0.00000000",
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200
    _publish_portfolio_daily()

    run_response = client.post(
        "/api/portfolios/portfolio-ops/allocation-research/runs",
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


def test_allocation_research_run_rejects_incomplete_scope_targets_after_new_watch_member(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={
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
        json={
            "target_scope": TARGET_MEMBER_INSTRUMENT,
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_ids["Defensive Equity"],
        },
    )
    assert assignment_response.status_code == 200, assignment_response.json()
    _publish_portfolio_daily()

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    error_detail = run_response.json()["detail"]
    assert "Defensive Equity weight target set is incomplete" in error_detail
    assert "Watchlist Fund" in error_detail


def test_allocation_research_scope_default_respects_taxonomy_root_default_dimension(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    top_level_scope = next(
        item for item in workbench_response.json()["planning_scope_options"] if item["taxonomy_node_id"] is None
    )
    assert top_level_scope["default_target_dimension"] == "risk_budget"


def test_allocation_research_scope_default_requires_configured_dimension_target_set(client):
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
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "has no active complete" in run_response.json()["detail"]
    assert "target set" in run_response.json()["detail"]


def test_deleting_selected_allocation_research_taxonomy_clears_settings(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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

    delete_response = client.delete(f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}")
    assert delete_response.status_code == 200
    _publish_portfolio_daily()

    workbench_response = client.get("/api/portfolios/portfolio-ops/allocation-research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert workbench_payload["default_planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["comparator_taxonomy_node_id"] is None


def test_allocation_research_settings_preserve_frozen_nodes_when_field_is_omitted(client):
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
    initial_response = client.put("/api/portfolios/portfolio-ops/allocation-research/settings", json=payload)
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
    omitted_response = client.put("/api/portfolios/portfolio-ops/allocation-research/settings", json=omitted_payload)
    assert omitted_response.status_code == 200
    assert omitted_response.json()["frozen_taxonomy_node_ids"] == [node_ids["Risk Assets"]]
    assert omitted_response.json()["top_sleeve_weight_bounds"] == [
        {"taxonomy_node_id": node_ids["Risk Assets"], "min_weight": 0.2, "max_weight": 0.55},
    ]

    clear_payload = dict(omitted_payload)
    clear_payload["frozen_taxonomy_node_ids"] = []
    clear_payload["top_sleeve_weight_bounds"] = []
    clear_response = client.put("/api/portfolios/portfolio-ops/allocation-research/settings", json=clear_payload)
    assert clear_response.status_code == 200
    assert clear_response.json()["frozen_taxonomy_node_ids"] == []
    assert clear_response.json()["top_sleeve_weight_bounds"] == []


def test_allocation_research_settings_rejects_non_top_sleeve_weight_bounds(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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


def test_allocation_research_risk_budget_solver_accepts_binding_weight_bounds():
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


def test_allocation_research_risk_budget_solver_returns_binding_signed_negative_constrained_solution():
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


def test_allocation_research_target_volatility_rejects_unaligned_risk_history(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned allocation research dates" in run_response.json()["detail"]


def test_allocation_research_target_volatility_rejects_missing_child_sleeve_history(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client, root_default_target_dimension="weight")
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned allocation research dates" in run_response.json()["detail"]


def test_allocation_research_run_rejects_insufficient_history_for_unaligned_sparse_window(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/portfolio-ops/allocation-research/settings",
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
        "/api/portfolios/portfolio-ops/allocation-research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned allocation research dates" in run_response.json()["detail"]
