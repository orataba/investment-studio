from __future__ import annotations

from datetime import date

from sqlalchemy import select

from portfolio_app.db.models import AnalyticsScopePolicyRecordModel, PortfolioCalculationStateModel, TaxonomyConfigurationRevisionModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.api.routes import workspace as workspace_route
from portfolio_app.services import daily_snapshots
from portfolio_app.services.analytics_scope import (
    ROOT_POLICY_NODE_ID, UNASSIGNED_POLICY_NODE_ID, analytics_policy_version,
    resolve_instrument_analytics_scopes, current_taxonomy_configuration,
)

PORTFOLIO_ID = "investment-studio"


def _create_planning_tree(client, *, name: str, select_current: bool = True) -> dict[str, str]:
    response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies", json={
        "name": name, "taxonomy_type": "risk_sleeve", "primary_assignment_scope": "instrument",
        "planning_enabled": True, "budgeting_level": "weight_and_risk_budget"})
    assert response.status_code == 200, response.text
    taxonomy_id = response.json()["taxonomy_id"]
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}"
    parent = client.post(f"{base}/nodes", json={"node_name": "Market Assets"}).json()["taxonomy_node_id"]
    child = client.post(f"{base}/nodes", json={"node_name": "Listed Equity", "parent_taxonomy_node_id": parent}).json()["taxonomy_node_id"]
    sibling = client.post(f"{base}/nodes", json={"node_name": "Listed Funds", "parent_taxonomy_node_id": parent}).json()["taxonomy_node_id"]
    assignment = client.post(f"{base}/assignments", json={"target_scope": "instrument",
        "target_entity_id": "equity-us-abbv", "taxonomy_node_id": child})
    assert assignment.status_code == 200, assignment.text
    if select_current:
        selected = client.put(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/default-planning", json={"taxonomy_id": taxonomy_id})
        assert selected.status_code == 200, selected.text
    return {"taxonomy_id": taxonomy_id, "parent_id": parent, "child_id": child,
            "sibling_id": sibling, "assignment_id": assignment.json()["assignment_id"]}


def _replace_policy(client, tree, *, eligible: bool, reason: str | None = None):
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}/analytics-scope-policies/{tree['parent_id']}", json={
        "risk_eligible": eligible, "risk_budget_eligible": eligible,
        "performance_scope": "ordinary" if eligible else "operational_only",
        "valuation_basis": "market" if eligible else "carrying", "exclusion_reason": reason})
    assert response.status_code == 200, response.text
    assert "effective_from" not in response.json() and "effective_to" not in response.json()
    return response.json()


def test_scope_defaults_fail_closed_and_inherit_nearest_ancestor(client):
    tree = _create_planning_tree(client, name="Scope Inheritance")
    initial = resolve_instrument_analytics_scopes(PORTFOLIO_ID, instrument_ids=["equity-us-abbv", "fund-us-watch"])
    assert initial["equity-us-abbv"]["resolved_policy_node_id"] == ROOT_POLICY_NODE_ID
    assert initial["equity-us-abbv"]["taxonomy_node_id"] == tree["child_id"]
    assert initial["equity-us-abbv"]["risk_budget_eligible"] is True
    assert initial["fund-us-watch"]["resolved_policy_node_id"] == UNASSIGNED_POLICY_NODE_ID
    assert initial["fund-us-watch"]["risk_eligible"] is False
    policy = _replace_policy(client, tree, eligible=False, reason="Operational carrying exposure")
    current = resolve_instrument_analytics_scopes(PORTFOLIO_ID, instrument_ids=["equity-us-abbv"])["equity-us-abbv"]
    assert current["analytics_scope_policy_id"] == policy["analytics_scope_policy_id"]
    assert current["inherited_from_node_id"] == tree["parent_id"]
    assert current["risk_eligible"] is False


def test_policy_replacement_retains_audit_with_one_current_policy_and_full_rebuild(client):
    tree = _create_planning_tree(client, name="Policy Audit")
    first = _replace_policy(client, tree, eligible=False, reason="Initial exclusion")
    second = _replace_policy(client, tree, eligible=True)
    final = _replace_policy(client, tree, eligible=False, reason="Corrected classification")
    with get_session_factory()() as session:
        records = session.scalars(select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == PORTFOLIO_ID,
            AnalyticsScopePolicyRecordModel.taxonomy_id == tree["taxonomy_id"],
            AnalyticsScopePolicyRecordModel.taxonomy_node_id == tree["parent_id"]
        ).order_by(AnalyticsScopePolicyRecordModel.policy_version)).all()
        assert [record.analytics_scope_policy_id for record in records] == [first["analytics_scope_policy_id"], second["analytics_scope_policy_id"], final["analytics_scope_policy_id"]]
        assert [record.superseded_by_policy_id for record in records] == [second["analytics_scope_policy_id"], final["analytics_scope_policy_id"], None]
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO_ID)
        assert state.daily_snapshot_status == "stale" and state.dirty_from is None
    resolved = resolve_instrument_analytics_scopes(PORTFOLIO_ID, instrument_ids=["equity-us-abbv"])
    assert resolved["equity-us-abbv"]["analytics_scope_policy_id"] == final["analytics_scope_policy_id"]


def test_current_selection_assignment_targets_and_immutable_configuration_audit(client):
    first = _create_planning_tree(client, name="Current Primary")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{first['taxonomy_id']}"
    response = client.post(f"{base}/target-sets", json={"comparator_taxonomy_node_id": first["child_id"],
        "target_set_type": "saa", "name": "Original Target", "weight_enabled": True,
        "lines": [{"target_member_type": "instrument", "target_member_id": "equity-us-abbv", "target_weight": 1.0}]})
    assert response.status_code == 200, response.text
    old = current_taxonomy_configuration(PORTFOLIO_ID, first["taxonomy_id"])
    assert client.patch(f"{base}/target-sets/{response.json()['target_set_id']}", json={"name": "Current Target"}).status_code == 200
    assert client.patch(f"{base}/assignments/{first['assignment_id']}", json={"taxonomy_node_id": first["sibling_id"]}).status_code == 200
    current = current_taxonomy_configuration(PORTFOLIO_ID, first["taxonomy_id"])
    assert current["taxonomy_assignments"][0]["taxonomy_node_id"] == first["sibling_id"]
    assert current["target_sets"][0]["name"] == "Current Target"
    assert current["configuration_version"] > old["configuration_version"]
    with get_session_factory()() as session:
        archived = session.get(TaxonomyConfigurationRevisionModel, old["taxonomy_configuration_revision_id"])
        assert archived.configuration_json["target_sets"][0]["name"] == "Original Target"
        assert archived.configuration_json["taxonomy_assignments"][0]["taxonomy_node_id"] == first["child_id"]
        assert archived.superseded_by_revision_id is not None
    second = _create_planning_tree(client, name="Current Secondary", select_current=False)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/default-planning", json={"taxonomy_id": second["taxonomy_id"]})
    assert response.status_code == 200, response.text
    scope = resolve_instrument_analytics_scopes(PORTFOLIO_ID, instrument_ids=["equity-us-abbv"])["equity-us-abbv"]
    assert scope["taxonomy_id"] == second["taxonomy_id"] and scope["taxonomy_node_id"] == second["child_id"]


def test_inactive_and_deleted_taxonomy_fail_closed(client):
    tree = _create_planning_tree(client, name="Inactive Scope")
    endpoint = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    assert client.patch(endpoint, json={"status": "archived"}).status_code == 200
    assert resolve_instrument_analytics_scopes(PORTFOLIO_ID, taxonomy_id=tree["taxonomy_id"], instrument_ids=["equity-us-abbv"])["equity-us-abbv"]["scope_status"] == "missing"
    assert client.delete(endpoint).status_code == 200
    assert current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"]) is None
    assert resolve_instrument_analytics_scopes(PORTFOLIO_ID, taxonomy_id=tree["taxonomy_id"], instrument_ids=["equity-us-abbv"])["equity-us-abbv"]["scope_status"] == "missing"


def test_materialized_current_scope_parity_and_configuration_copy(client):
    tree = _create_planning_tree(client, name="Materialized Scope")
    _replace_policy(client, tree, eligible=True)
    params = {"portfolio_id": PORTFOLIO_ID, "as_of_date": "2026-04-15"}
    before = client.get("/api/workspace/holdings", params=params)
    assert before.status_code == 200, before.text
    daily_snapshots.mark_portfolio_daily_snapshots_stale(PORTFOLIO_ID)
    refresh = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(PORTFOLIO_ID)
    assert refresh["source_generation_status"] == "stable"
    assert refresh["source_generation_before"]["analytics_policy_version"] == analytics_policy_version(PORTFOLIO_ID)
    after = client.get("/api/workspace/holdings", params=params)
    def scopes(payload):
        return {row["line_id"]: tuple(row.get(key) for key in ("taxonomy_id", "taxonomy_node_id", "scope_policy_version", "configuration_version", "risk_eligible")) for row in payload["rows"]}
    assert scopes(before.json()) == scopes(after.json())
    copied = client.post(f"/api/portfolios/{PORTFOLIO_ID}/copy")
    assert copied.status_code == 200, copied.text
    copy_id = copied.json()["portfolio_id"]
    copy_scope = resolve_instrument_analytics_scopes(copy_id, instrument_ids=["equity-us-abbv"])["equity-us-abbv"]
    assert copy_scope["risk_eligible"] is True
    assert copy_scope["taxonomy_node_id"] != tree["child_id"]
    assert copy_scope["taxonomy_node_id"].startswith(tree["child_id"])


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
    resolved_instrument_ids: list[str] = []

    def resolve_scopes(*_args, **kwargs):
        resolved_instrument_ids.extend(kwargs["instrument_ids"])
        return {
            instrument_id: {"instrument_id": instrument_id, **policy}
            for instrument_id in ("equity", "stale")
        }

    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        resolve_scopes,
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
                    "holding_kind": "derivative_contract",
                    "instrument_id": None,
                    "instrument_core": None,
                    "derivative_contract_id": "fcn",
                    "derivative_contract": {"contract_type": "fcn"},
                    "last_price": None,
                    "valuation_basis": "carried_cost",
                    "fair_value_coverage_status": "unavailable",
                    "carrying_value_base": 50.0,
                },
                {
                    "line_id": "option-obligation-line",
                    "holding_kind": "option_obligation",
                    "is_liability": True,
                    "instrument_id": None,
                    "instrument_core": None,
                    "derivative_contract_id": "option",
                    "derivative_contract": {"contract_type": "option"},
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
    assert resolved_instrument_ids == ["equity", "stale"]
    assert rows["equity-line"]["holding_category"] == "securities"
    assert rows["stale-line"]["holding_category"] == "securities"
    assert rows["fcn-line"]["holding_category"] == "derivatives"
    assert rows["option-obligation-line"]["holding_category"] == "derivatives"
    assert rows["equity-line"]["risk_eligible"] is True
    assert rows["equity-line"]["performance_eligible"] is True
    for line_id in ("fcn-line", "option-obligation-line", "stale-line"):
        assert rows[line_id]["risk_eligible"] is False
        assert rows[line_id]["risk_budget_eligible"] is False
        assert rows[line_id]["performance_eligible"] is False
        assert rows[line_id]["analytics_scope_system_exclusion_reason"]
    for line_id in ("fcn-line", "option-obligation-line"):
        assert rows[line_id]["instrument_id"] is None
        assert rows[line_id]["analytics_scope"] == "derivative_lifecycle"
        assert rows[line_id]["scope_status"] == "system_excluded"
    assert rows["stale-line"]["analytics_scope"] == "operational_only"

    summary = response["analytics_scope_summary"]
    assert summary["modeled_gross_exposure"] == 100.0
    assert summary["excluded_carrying_value"] == 80.0
    assert summary["excluded_liability"] == 20.0
    assert summary["coverage_ratio"] == 0.5


def test_derivative_contract_id_never_enters_registry_or_taxonomy_scope(monkeypatch) -> None:
    requested_ids: list[str] = []

    def resolve_scopes(*_args, **kwargs):
        requested_ids.extend(kwargs["instrument_ids"])
        return {}

    monkeypatch.setattr(
        workspace_route,
        "resolve_instrument_analytics_scopes",
        resolve_scopes,
    )
    workspace = {
        "rows": [
            {
                "line_id": "registry-security-id",
                "position_reference_id": "registry-security-id",
                "instrument_id": None,
                "instrument_core": None,
                "derivative_contract_id": "registry-security-id",
                "derivative_contract": {"contract_type": "option"},
                "holding_kind": "derivative_contract",
                "valuation_basis": "carried_cost",
                "fair_value_coverage_status": "unavailable",
                "carrying_value_base": 100.0,
            }
        ],
        "totals": {"nav": 100.0},
    }

    response = workspace_route._enrich_holdings_analytics_scope(
        workspace,
        portfolio_id=PORTFOLIO_ID,
        as_of_date=date(2026, 4, 15),
        transactions=[],
    )

    assert requested_ids == []
    assert workspace_route._instrument_ids_from_holdings_workspace(response) == []
    row = response["rows"][0]
    assert row["instrument_id"] is None
    assert row["derivative_contract_id"] == "registry-security-id"
    assert row["holding_category"] == "derivatives"


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
    assert row["holding_category"] == "cash_and_settlement"
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
            "equity-1": dict(ordinary_scope)
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
                "instrument_id": None,
                "derivative_contract_id": "fcn-1",
                "derivative_contract": {
                    "derivative_contract_id": "fcn-1",
                    "portfolio_id": PORTFOLIO_ID,
                    "account_id": "broker-usd",
                    "contract_name": "FCN 1",
                    "contract_type": "fcn",
                    "currency": "USD",
                    "terms": {
                        "notional": "100",
                        "annual_coupon_rate_pct": None,
                        "issue_date": "2026-04-01",
                        "final_observation_date": None,
                        "maturity_date": "2026-04-15",
                        "issuer": "Test Issuer",
                        "counterparty": "Test Broker",
                        "underlyings": [
                            {
                                "instrument_id": "equity-1",
                                "initial_reference_price": None,
                                "strike_level_pct": None,
                                "knock_in_level_pct": None,
                                "knock_out_level_pct": None,
                                "deliverable": True,
                            }
                        ],
                    },
                    "created_at": "2026-04-01T00:00:00Z",
                },
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
                "instrument_id": None,
                "derivative_contract_id": "fcn-1",
                "derivative_contract": {
                    "derivative_contract_id": "fcn-1",
                    "portfolio_id": PORTFOLIO_ID,
                    "account_id": "broker-usd",
                    "contract_name": "FCN 1",
                    "contract_type": "fcn",
                    "currency": "USD",
                    "terms": {
                        "notional": "100",
                        "annual_coupon_rate_pct": None,
                        "issue_date": "2026-04-01",
                        "final_observation_date": None,
                        "maturity_date": "2026-04-15",
                        "issuer": "Test Issuer",
                        "counterparty": "Test Broker",
                        "underlyings": [
                            {
                                "instrument_id": "equity-1",
                                "initial_reference_price": None,
                                "strike_level_pct": None,
                                "knock_in_level_pct": None,
                                "knock_out_level_pct": None,
                                "deliverable": True,
                            }
                        ],
                    },
                    "created_at": "2026-04-01T00:00:00Z",
                },
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
