from __future__ import annotations

from datetime import date

from sqlalchemy import select

from portfolio_app.db.models import AnalyticsScopePolicyRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.api.routes import workspace as workspace_route
from portfolio_app.services import daily_snapshots
from portfolio_app.services.analytics_scope import (
    ROOT_POLICY_NODE_ID,
    UNASSIGNED_POLICY_NODE_ID,
    analytics_policy_version,
    resolve_instrument_analytics_scopes,
    taxonomy_configuration_as_of,
)


PORTFOLIO_ID = "portfolio-ops"
JAN_1 = "2026-01-01"


def _create_planning_tree(
    client,
    *,
    name: str,
    select_from: str | None = JAN_1,
) -> dict[str, str]:
    taxonomy_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies",
        json={
            "effective_from": JAN_1,
            "name": name,
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    assert taxonomy_response.status_code == 200, taxonomy_response.text
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    parent_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": JAN_1, "node_name": "Market Assets"},
    )
    assert parent_response.status_code == 200, parent_response.text
    parent_id = parent_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}/nodes",
        json={
            "effective_from": JAN_1,
            "node_name": "Listed Equity",
            "parent_taxonomy_node_id": parent_id,
        },
    )
    assert child_response.status_code == 200, child_response.text
    child_id = child_response.json()["taxonomy_node_id"]

    sibling_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}/nodes",
        json={
            "effective_from": JAN_1,
            "node_name": "Listed Funds",
            "parent_taxonomy_node_id": parent_id,
        },
    )
    assert sibling_response.status_code == 200, sibling_response.text
    sibling_id = sibling_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}/assignments",
        json={
            "effective_from": JAN_1,
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": child_id,
        },
    )
    assert assignment_response.status_code == 200, assignment_response.text

    if select_from is not None:
        selection_response = client.put(
            f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/default-planning",
            json={"effective_from": select_from, "taxonomy_id": taxonomy_id},
        )
        assert selection_response.status_code == 200, selection_response.text

    return {
        "taxonomy_id": taxonomy_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "sibling_id": sibling_id,
        "assignment_id": assignment_response.json()["assignment_id"],
    }


def _replace_policy(
    client,
    tree: dict[str, str],
    *,
    effective_from: str,
    risk_eligible: bool,
    risk_budget_eligible: bool,
    performance_scope: str,
    valuation_basis: str,
    exclusion_reason: str | None,
):
    response = client.put(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/"
        f"{tree['taxonomy_id']}/analytics-scope-policies/{tree['parent_id']}",
        json={
            "effective_from": effective_from,
            "risk_eligible": risk_eligible,
            "risk_budget_eligible": risk_budget_eligible,
            "performance_scope": performance_scope,
            "valuation_basis": valuation_basis,
            "exclusion_reason": exclusion_reason,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_analytics_mutations_require_explicit_effective_from(client) -> None:
    taxonomy_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies",
        json={
            "name": "Missing Effective Date",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    assert taxonomy_response.status_code == 422

    tree = _create_planning_tree(client, name="Effective Date Contract")
    policy_response = client.put(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/"
        f"{tree['taxonomy_id']}/analytics-scope-policies/{tree['parent_id']}",
        json={
            "risk_eligible": False,
            "risk_budget_eligible": False,
            "performance_scope": "operational_only",
            "valuation_basis": "carrying",
            "exclusion_reason": "Explicit policy date is required.",
        },
    )
    assert policy_response.status_code == 422


def test_scope_defaults_fail_closed_and_inherit_nearest_ancestor(client) -> None:
    tree = _create_planning_tree(client, name="Scope Inheritance")

    initial = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 1, 15),
        instrument_ids=["equity-us-abbv", "fund-us-watch"],
    )
    assigned = initial["equity-us-abbv"]
    assert assigned["scope_status"] == "resolved"
    assert assigned["taxonomy_node_id"] == tree["child_id"]
    assert assigned["resolved_policy_node_id"] == ROOT_POLICY_NODE_ID
    assert assigned["inherited_from_node_id"] == ROOT_POLICY_NODE_ID
    assert assigned["risk_eligible"] is True
    assert assigned["risk_budget_eligible"] is True
    assert assigned["performance_scope"] == "ordinary"

    unassigned = initial["fund-us-watch"]
    assert unassigned["scope_status"] == "resolved"
    assert unassigned["taxonomy_node_id"] is None
    assert unassigned["resolved_policy_node_id"] == UNASSIGNED_POLICY_NODE_ID
    assert unassigned["risk_eligible"] is False
    assert unassigned["risk_budget_eligible"] is False
    assert unassigned["performance_scope"] == "unallocated"

    parent_policy = _replace_policy(
        client,
        tree,
        effective_from="2026-02-01",
        risk_eligible=False,
        risk_budget_eligible=False,
        performance_scope="operational_only",
        valuation_basis="carrying",
        exclusion_reason="Operational carrying exposure.",
    )
    inherited = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 2, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    assert inherited["analytics_scope_policy_id"] == parent_policy[
        "analytics_scope_policy_id"
    ]
    assert inherited["resolved_policy_node_id"] == tree["parent_id"]
    assert inherited["inherited_from_node_id"] == tree["parent_id"]
    assert inherited["risk_eligible"] is False


def test_policy_replacement_preserves_audit_and_non_overlapping_replay(client) -> None:
    tree = _create_planning_tree(client, name="Policy History")
    february_policy = _replace_policy(
        client,
        tree,
        effective_from="2026-02-01",
        risk_eligible=False,
        risk_budget_eligible=False,
        performance_scope="operational_only",
        valuation_basis="carrying",
        exclusion_reason="February exclusion.",
    )
    superseded_march_policy = _replace_policy(
        client,
        tree,
        effective_from="2026-03-01",
        risk_eligible=True,
        risk_budget_eligible=True,
        performance_scope="ordinary",
        valuation_basis="market",
        exclusion_reason=None,
    )
    final_march_policy = _replace_policy(
        client,
        tree,
        effective_from="2026-03-01",
        risk_eligible=False,
        risk_budget_eligible=False,
        performance_scope="derivative_lifecycle",
        valuation_basis="event",
        exclusion_reason="Same-day corrected classification.",
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(AnalyticsScopePolicyRecordModel)
            .where(
                AnalyticsScopePolicyRecordModel.portfolio_id == PORTFOLIO_ID,
                AnalyticsScopePolicyRecordModel.taxonomy_id == tree["taxonomy_id"],
                AnalyticsScopePolicyRecordModel.taxonomy_node_id == tree["parent_id"],
            )
            .order_by(AnalyticsScopePolicyRecordModel.policy_version)
        ).all()

    assert len(records) == 3
    assert records[0].analytics_scope_policy_id == february_policy[
        "analytics_scope_policy_id"
    ]
    assert records[0].effective_to == date(2026, 2, 28)
    assert records[0].superseded_by_policy_id is None
    assert records[1].analytics_scope_policy_id == superseded_march_policy[
        "analytics_scope_policy_id"
    ]
    assert records[1].superseded_by_policy_id == final_march_policy[
        "analytics_scope_policy_id"
    ]
    assert records[2].superseded_by_policy_id is None

    active_records = [record for record in records if record.superseded_by_policy_id is None]
    assert active_records[0].effective_to is not None
    assert active_records[0].effective_to < active_records[1].effective_from

    february = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 2, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    march = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 3, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    assert february["analytics_scope_policy_id"] == february_policy[
        "analytics_scope_policy_id"
    ]
    assert march["analytics_scope_policy_id"] == final_march_policy[
        "analytics_scope_policy_id"
    ]


def test_selection_assignment_and_target_configuration_replay_as_of(client) -> None:
    first = _create_planning_tree(client, name="Point In Time Primary")
    target_response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/"
        f"{first['taxonomy_id']}/target-sets",
        json={
            "effective_from": JAN_1,
            "comparator_taxonomy_node_id": first["child_id"],
            "target_set_type": "saa",
            "name": "January Target",
            "weight_enabled": True,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_weight": 1.0,
                }
            ],
        },
    )
    assert target_response.status_code == 200, target_response.text
    target_set_id = target_response.json()["target_set_id"]

    target_update = client.patch(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/"
        f"{first['taxonomy_id']}/target-sets/{target_set_id}",
        json={"effective_from": "2026-02-01", "name": "February Target"},
    )
    assert target_update.status_code == 200, target_update.text
    assignment_update = client.patch(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/"
        f"{first['taxonomy_id']}/assignments/{first['assignment_id']}",
        json={
            "effective_from": "2026-02-01",
            "taxonomy_node_id": first["sibling_id"],
        },
    )
    assert assignment_update.status_code == 200, assignment_update.text

    january_configuration = taxonomy_configuration_as_of(
        PORTFOLIO_ID,
        first["taxonomy_id"],
        date(2026, 1, 15),
    )
    february_configuration = taxonomy_configuration_as_of(
        PORTFOLIO_ID,
        first["taxonomy_id"],
        date(2026, 2, 15),
    )
    assert january_configuration is not None
    assert february_configuration is not None
    assert january_configuration["taxonomy_assignments"][0]["taxonomy_node_id"] == first[
        "child_id"
    ]
    assert february_configuration["taxonomy_assignments"][0]["taxonomy_node_id"] == first[
        "sibling_id"
    ]
    assert january_configuration["target_sets"][0]["name"] == "January Target"
    assert february_configuration["target_sets"][0]["name"] == "February Target"
    assert january_configuration["configuration_version"] < february_configuration[
        "configuration_version"
    ]

    second = _create_planning_tree(
        client,
        name="Point In Time Secondary",
        select_from=None,
    )
    selection_response = client.put(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/default-planning",
        json={"effective_from": "2026-03-01", "taxonomy_id": second["taxonomy_id"]},
    )
    assert selection_response.status_code == 200, selection_response.text

    january_scope = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 1, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    march_scope = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        as_of_date=date(2026, 3, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    assert january_scope["taxonomy_id"] == first["taxonomy_id"]
    assert january_scope["taxonomy_node_id"] == first["child_id"]
    assert march_scope["taxonomy_id"] == second["taxonomy_id"]
    assert march_scope["taxonomy_node_id"] == second["child_id"]
    assert january_scope["taxonomy_selection_version"] < march_scope[
        "taxonomy_selection_version"
    ]


def test_inactive_and_deleted_taxonomy_revisions_fail_closed(client) -> None:
    tree = _create_planning_tree(client, name="Inactive Scope")
    archived_response = client.patch(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}",
        json={"effective_from": "2026-03-01", "status": "archived"},
    )
    assert archived_response.status_code == 200, archived_response.text

    historical = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        taxonomy_id=tree["taxonomy_id"],
        as_of_date=date(2026, 2, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    inactive = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        taxonomy_id=tree["taxonomy_id"],
        as_of_date=date(2026, 3, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    assert historical["scope_status"] == "resolved"
    assert inactive["scope_status"] == "missing"
    assert "inactive or deleted" in inactive["exclusion_reason"]

    delete_response = client.delete(
        f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}",
        params={"effective_from": "2026-04-01"},
    )
    assert delete_response.status_code == 200, delete_response.text
    deleted = resolve_instrument_analytics_scopes(
        PORTFOLIO_ID,
        taxonomy_id=tree["taxonomy_id"],
        as_of_date=date(2026, 4, 15),
        instrument_ids=["equity-us-abbv"],
    )["equity-us-abbv"]
    assert deleted["scope_status"] == "missing"
    assert "inactive or deleted" in deleted["exclusion_reason"]


def test_materialized_scope_parity_source_generation_and_copy_rejection(client) -> None:
    tree = _create_planning_tree(client, name="Materialized Scope")
    _replace_policy(
        client,
        tree,
        effective_from=JAN_1,
        risk_eligible=True,
        risk_budget_eligible=True,
        performance_scope="ordinary",
        valuation_basis="market",
        exclusion_reason=None,
    )

    dynamic_response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": PORTFOLIO_ID, "as_of_date": "2026-04-15"},
    )
    assert dynamic_response.status_code == 200, dynamic_response.text

    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID)
    refresh = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        PORTFOLIO_ID
    )
    assert refresh is not None
    assert refresh["source_generation_status"] == "stable"
    generation = refresh["source_generation_before"]
    assert generation["analytics_policy_version"] == analytics_policy_version(
        PORTFOLIO_ID
    )

    materialized_response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": PORTFOLIO_ID, "as_of_date": "2026-04-15"},
    )
    assert materialized_response.status_code == 200, materialized_response.text

    def scopes_by_line(payload: dict[str, object]) -> dict[str, tuple[object, ...]]:
        return {
            str(row["line_id"]): (
                row.get("scope_status"),
                row.get("taxonomy_id"),
                row.get("taxonomy_node_id"),
                row.get("resolved_policy_node_id"),
                row.get("scope_policy_version"),
                row.get("configuration_version"),
                row.get("taxonomy_selection_version"),
                row.get("risk_eligible"),
                row.get("risk_budget_eligible"),
                row.get("performance_scope"),
            )
            for row in payload["rows"]
        }

    assert scopes_by_line(materialized_response.json()) == scopes_by_line(
        dynamic_response.json()
    )

    copy_response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/copy")
    assert copy_response.status_code == 409
    assert "effective-dated analytics history" in copy_response.json()["detail"]


def test_workspace_scope_requires_complete_market_valuation_even_when_policy_is_eligible(monkeypatch) -> None:
    policy = {
        "scope_status": "resolved",
        "taxonomy_id": "planning-taxonomy",
        "taxonomy_node_id": "market-assets",
        "resolved_policy_node_id": ROOT_POLICY_NODE_ID,
        "inherited_from_node_id": ROOT_POLICY_NODE_ID,
        "analytics_scope_policy_id": "scope-policy-root",
        "scope_policy_version": 1,
        "configuration_version": 1,
        "taxonomy_selection_version": 1,
        "risk_eligible": True,
        "risk_budget_eligible": True,
        "performance_scope": "ordinary",
        "valuation_basis_policy": "market",
        "exclusion_reason": None,
    }
    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        lambda *_args, **_kwargs: {
            instrument_id: dict(policy)
            for instrument_id in ("equity", "fcn", "option", "stale")
        },
    )
    response = workspace_route._enrich_holdings_analytics_scope(
        {
            "rows": [
                {
                    "line_id": "equity-line",
                    "holding_kind": "position",
                    "instrument_core": {
                        "instrument_id": "equity",
                        "instrument_name": "Equity",
                        "instrument_type": "equity",
                    },
                    "last_price": 10.0,
                    "valuation_basis": "market_quote",
                    "fair_value_coverage_status": "complete",
                    "market_value_base": 100.0,
                },
                {
                    "line_id": "fcn-line",
                    "holding_kind": "position",
                    "instrument_core": {
                        "instrument_id": "fcn",
                        "instrument_name": "Carried FCN",
                        "instrument_type": "fcn",
                    },
                    "last_price": None,
                    "valuation_basis": "carried_cost",
                    "fair_value_coverage_status": "unavailable",
                    "carrying_value_base": 50.0,
                },
                {
                    "line_id": "option-obligation-line",
                    "holding_kind": "option_obligation",
                    "is_liability": True,
                    "instrument_core": {
                        "instrument_id": "option",
                        "instrument_name": "Written Call",
                        "instrument_type": "option",
                    },
                    "last_price": None,
                    "valuation_basis": "premium_liability",
                    "fair_value_coverage_status": "unavailable",
                    "liability_value_base": -20.0,
                },
                {
                    "line_id": "stale-line",
                    "holding_kind": "position",
                    "instrument_core": {
                        "instrument_id": "stale",
                        "instrument_name": "Stale Quote",
                        "instrument_type": "equity",
                    },
                    "last_price": None,
                    "valuation_basis": "market_quote",
                    "fair_value_coverage_status": "partial",
                    "market_value_base": 30.0,
                },
            ],
            "totals": {"nav": 160.0},
        },
        portfolio_id=PORTFOLIO_ID,
        as_of_date=date(2026, 4, 15),
        transactions=[],
    )

    rows = {row["line_id"]: row for row in response["rows"]}
    assert rows["equity-line"]["risk_eligible"] is True
    assert rows["equity-line"]["performance_eligible"] is True
    for line_id in ("fcn-line", "option-obligation-line", "stale-line"):
        assert rows[line_id]["risk_eligible"] is False
        assert rows[line_id]["risk_budget_eligible"] is False
        assert rows[line_id]["performance_eligible"] is False
        assert rows[line_id]["analytics_scope"] == "operational_only"
        assert rows[line_id]["analytics_scope_system_exclusion_reason"]

    summary = response["analytics_scope_summary"]
    assert summary["modeled_gross_exposure"] == 100.0
    assert summary["excluded_carrying_value"] == 80.0
    assert summary["excluded_liability"] == 20.0
    assert summary["coverage_ratio"] == 0.5


def test_workspace_marks_cash_outside_system_valuation_contract(monkeypatch) -> None:
    policy = {
        "scope_status": "resolved",
        "taxonomy_id": "planning-taxonomy",
        "taxonomy_node_id": "cash",
        "resolved_policy_node_id": ROOT_POLICY_NODE_ID,
        "inherited_from_node_id": ROOT_POLICY_NODE_ID,
        "analytics_scope_policy_id": "scope-policy-root",
        "scope_policy_version": 1,
        "configuration_version": 1,
        "taxonomy_selection_version": 1,
        "risk_eligible": True,
        "risk_budget_eligible": True,
        "performance_scope": "ordinary",
        "valuation_basis_policy": "market",
        "exclusion_reason": None,
    }
    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        lambda *_args, **_kwargs: {"cash:USD": dict(policy)},
    )
    response = workspace_route._enrich_holdings_analytics_scope(
        {
            "rows": [
                {
                    "line_id": "cash-line",
                    "holding_kind": "cash",
                    "instrument_core": {
                        "instrument_id": "cash:USD",
                        "instrument_name": "Cash (USD)",
                        "instrument_type": "cash",
                    },
                    "market_value_base": 100.0,
                    "valuation_basis": "cash",
                    "fair_value_coverage_status": "complete",
                    "last_price": None,
                }
            ],
            "totals": {"nav": 100.0},
        },
        portfolio_id=PORTFOLIO_ID,
        as_of_date=date(2026, 4, 15),
        transactions=[],
    )

    row = response["rows"][0]
    assert row["risk_eligible"] is False
    assert row["risk_budget_eligible"] is False
    assert row["performance_eligible"] is False
    assert row["analytics_scope_valuation_eligible"] is False
    assert row["analytics_scope_system_exclusion_reason"] == (
        "Cash and settlement exposure is disclosed outside covariance risk."
    )
    assert response["analytics_scope_summary"]["cash_unallocated_exposure"] == 100.0


def test_workspace_cash_scope_breakdown_never_sums_across_currencies(
    monkeypatch,
) -> None:
    ordinary_scope = {
        "performance_scope": "ordinary",
        "risk_eligible": True,
        "risk_budget_eligible": True,
    }
    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        lambda *_args, **_kwargs: {
            "asset-usd": dict(ordinary_scope),
            "asset-hkd": dict(ordinary_scope),
        },
    )

    response = workspace_route._enrich_holdings_analytics_scope(
        {"rows": [], "totals": {"nav": 0.0}},
        portfolio_id=PORTFOLIO_ID,
        as_of_date=date(2026, 4, 15),
        transactions=[
            {
                "transaction_type": "buy",
                "trade_date": "2026-04-01",
                "settlement_date": "2026-04-02",
                "settlement_cash_account_id": "cash-usd",
                "instrument_id": "asset-usd",
                "gross_amount": 100.0,
                "fees": 1.0,
                "taxes": 0.0,
                "currency": "usd",
            },
            {
                "transaction_type": "buy",
                "trade_date": "2026-04-01",
                "settlement_date": "2026-04-02",
                "settlement_cash_account_id": "cash-hkd",
                "instrument_id": "asset-hkd",
                "gross_amount": 780.0,
                "fees": 2.0,
                "taxes": 0.0,
                "currency": "HKD",
            },
        ],
    )

    breakdown = {
        (item["performance_scope"], item["currency"]): item
        for item in response["analytics_scope_summary"]["cash_scope_breakdown"]
    }
    assert breakdown[("ordinary", "USD")]["net_cash_effect"] == -101.0
    assert breakdown[("ordinary", "USD")]["absolute_cash_activity"] == 101.0
    assert breakdown[("ordinary", "HKD")]["net_cash_effect"] == -782.0
    assert breakdown[("ordinary", "HKD")]["absolute_cash_activity"] == 782.0


def test_workspace_scopes_independent_fcn_and_stock_cash_facts(
    monkeypatch,
) -> None:
    ordinary_scope = {
        "performance_scope": "ordinary",
        "risk_eligible": True,
        "risk_budget_eligible": True,
    }
    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        lambda *_args, **_kwargs: {
            instrument_id: dict(ordinary_scope)
            for instrument_id in ("fcn-1", "equity-1")
        },
    )

    response = workspace_route._enrich_holdings_analytics_scope(
        {"rows": [], "totals": {"nav": 0.0}},
        portfolio_id=PORTFOLIO_ID,
        as_of_date=date(2026, 4, 15),
        transactions=[
            {
                "transaction_id": "fcn-open",
                "transaction_type": "buy",
                "instrument_id": "fcn-1",
                "instrument_type": "fcn",
                "trade_date": "2026-04-01",
                "settlement_date": "2026-04-02",
                "settlement_cash_account_id": "cash-usd",
                "gross_amount": 100.0,
                "fees": 1.0,
                "taxes": 0.0,
                "currency": "USD",
            },
            {
                "transaction_id": "fcn-close",
                "transaction_type": "maturity_redemption",
                "lifecycle_event_type": "fcn_knock_in",
                "instrument_id": "fcn-1",
                "instrument_type": "fcn",
                "trade_date": "2026-04-14",
                "settlement_date": "2026-04-15",
                "settlement_cash_account_id": "cash-usd",
                "gross_amount": 110.0,
                "fees": 2.0,
                "taxes": 3.0,
                "currency": "USD",
            },
            {
                "transaction_id": "stock-delivery",
                "transaction_type": "buy",
                "instrument_id": "equity-1",
                "instrument_type": "equity",
                "trade_date": "2026-04-14",
                "settlement_date": "2026-04-15",
                "settlement_cash_account_id": "cash-usd",
                "gross_amount": 110.0,
                "fees": 4.0,
                "taxes": 1.0,
                "currency": "USD",
            },
        ],
    )

    assert response["analytics_scope_summary"]["cash_scope_breakdown"] == [
        {
            "performance_scope": "derivative_lifecycle",
            "currency": "USD",
            "net_cash_effect": 4.0,
            "absolute_cash_activity": 206.0,
        },
        {
            "performance_scope": "ordinary",
            "currency": "USD",
            "net_cash_effect": -115.0,
            "absolute_cash_activity": 115.0,
        },
    ]
