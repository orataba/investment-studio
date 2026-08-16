from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from portfolio_app.db.models import (
    AnalyticsScopePolicyRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyConfigurationRevisionModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.analytics_scope import (
    ROOT_POLICY_NODE_ID,
    UNASSIGNED_POLICY_NODE_ID,
)

EFFECTIVE_FROM = "2026-01-01"


def test_taxonomy_create_node_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Risk Sleeves",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
            "purpose": "Current planning lens",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_payload = taxonomy_response.json()
    assert taxonomy_payload["taxonomy_id"].startswith("tax-risk-sleeves")
    assert taxonomy_payload["planning_enabled"] is True
    assert taxonomy_payload["root_default_target_dimension"] == "weight"

    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_payload['taxonomy_id']}/nodes",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Defensive",
            "node_code": "DEF",
            "sort_order": 0,
        },
    )
    assert node_response.status_code == 200
    node_payload = node_response.json()
    assert node_payload["taxonomy_id"] == taxonomy_payload["taxonomy_id"]
    assert node_payload["is_terminal"] is True

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_payload['taxonomy_id']}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_payload["taxonomy_node_id"],
            "effective_from": "2026-01-01",
        },
    )
    assert assignment_response.status_code == 200
    assignment_payload = assignment_response.json()
    assert assignment_payload["target_entity_id"] == "equity-us-abbv"

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["taxonomies"]) == 1
    assert len(catalog_payload["taxonomy_nodes"]) == 1
    assert len(catalog_payload["taxonomy_assignments"]) == 1
    assert catalog_payload["taxonomy_assignments"][0]["assignment_id"] == assignment_payload["assignment_id"]


def test_taxonomy_assignment_create_ignores_descriptive_imported_ids(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Imported Assignment IDs",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Core", "sort_order": 0},
    )
    assert node_response.status_code == 200
    node_id = node_response.json()["taxonomy_node_id"]

    session_factory = get_session_factory()
    with session_factory() as session:
        session.add_all(
            [
                TaxonomyAssignmentRecordModel(
                    assignment_id="assign-tax-portfolio-010737-of",
                    taxonomy_id=taxonomy_id,
                    target_scope="instrument",
                    target_entity_id="fund-us-agg",
                    taxonomy_node_id=node_id,
                    status="active",
                ),
                # A numeric-leading descriptive suffix makes SQLite exercise
                # the same semantic bug: the old CAST-based allocator treated
                # this as sequence number 9999 instead of ignoring it.
                TaxonomyAssignmentRecordModel(
                    assignment_id="assign-9999-imported",
                    taxonomy_id=taxonomy_id,
                    target_scope="instrument",
                    target_entity_id="fund-us-watch",
                    taxonomy_node_id=node_id,
                    status="active",
                ),
            ]
        )
        session.commit()

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )

    assert assignment_response.status_code == 200
    assert assignment_response.json()["assignment_id"] == "assign-0001"


def test_taxonomy_catalog_includes_portfolio_instrument_universe(client):
    initial_catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert initial_catalog_response.status_code == 200
    initial_catalog_payload = initial_catalog_response.json()
    initial_universe = {
        item["instrument_id"]: item
        for item in initial_catalog_payload["instrument_universe"]
    }
    assert "equity-us-abbv" in initial_universe
    assert initial_universe["equity-us-abbv"]["transaction_count"] > 0

    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Watchlist Taxonomy",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Research", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]
    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200
    assignment_id = assignment_response.json()["assignment_id"]

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert universe["fund-us-watch"]["source"] == "taxonomy"
    assert universe["fund-us-watch"]["holding_state"] == "not_held"
    assert universe["fund-us-watch"]["transaction_count"] == 0

    archived_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={"effective_from": EFFECTIVE_FROM,"status": "archived"},
    )
    assert archived_response.status_code == 200
    archived_catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert archived_catalog_response.status_code == 200
    archived_universe = {
        item["instrument_id"]: item
        for item in archived_catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in archived_universe

    restored_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={"effective_from": EFFECTIVE_FROM,"status": "active"},
    )
    assert restored_response.status_code == 200
    delete_response = client.delete(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        params={"effective_from": EFFECTIVE_FROM},
    )
    assert delete_response.status_code == 200
    deleted_catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert deleted_catalog_response.status_code == 200
    deleted_universe = {
        item["instrument_id"]: item
        for item in deleted_catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in deleted_universe


def test_taxonomy_adds_registry_instrument_to_manual_universe(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies/instrument-universe",
        json={"instrument_id": "fund-us-watch"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["instrument_id"] == "fund-us-watch"
    assert payload["instrument_ref"]["instrument_name"] == "Watchlist Fund"
    assert payload["source"] == "manual"
    assert payload["holding_state"] == "not_held"
    assert payload["transaction_count"] == 0

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert universe["fund-us-watch"]["source"] == "manual"
    assert universe["fund-us-watch"]["instrument_ref"]["instrument_name"] == "Watchlist Fund"


def test_taxonomy_deletes_manual_watch_instrument(client):
    create_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies/instrument-universe",
        json={"instrument_id": "fund-us-watch"},
    )
    assert create_response.status_code == 200

    delete_response = client.delete("/api/portfolios/portfolio-ops/taxonomies/instrument-universe/fund-us-watch")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in universe


def test_taxonomy_delete_node_rejects_parent_with_children(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Sector Map",
            "taxonomy_type": "sector",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    parent_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Equity", "sort_order": 0},
    )
    parent_node_id = parent_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Healthcare",
            "parent_taxonomy_node_id": parent_node_id,
            "sort_order": 1,
        },
    )
    assert child_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes/{parent_node_id}",
        params={"effective_from": EFFECTIVE_FROM},
    )
    assert delete_response.status_code == 400
    assert "child nodes" in delete_response.json()["detail"]


def test_taxonomy_delete_node_rejects_assigned_node(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Issuer Groups",
            "taxonomy_type": "issuer",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Healthcare", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes/{node_id}",
        params={"effective_from": EFFECTIVE_FROM},
    )
    assert delete_response.status_code == 400
    assert "assignments" in delete_response.json()["detail"]


def test_taxonomy_rejects_adding_child_under_assigned_node(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Sleeve Tree",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Core", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Child Sleeve",
            "parent_taxonomy_node_id": node_id,
        },
    )
    assert child_response.status_code == 400
    assert "already has assignments" in child_response.json()["detail"]


def test_taxonomy_create_rejects_non_security_scope(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Account Planning",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "account",
            "planning_enabled": True,
        },
    )
    assert response.status_code == 422
    assert "instrument" in str(response.json())


def test_default_planning_taxonomy_can_be_set_and_cleared(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Core Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    set_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM,"taxonomy_id": taxonomy_id},
    )
    assert set_response.status_code == 200
    assert set_response.json()["default_planning_taxonomy_id"] == taxonomy_id

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["default_planning_taxonomy_id"] == taxonomy_id

    clear_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM,"taxonomy_id": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["default_planning_taxonomy_id"] is None


def test_selecting_imported_planning_taxonomy_initializes_analytics_state(client):
    taxonomy_id = "tax-imported-planning"
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            TaxonomyRecordModel(
                taxonomy_id=taxonomy_id,
                portfolio_id="portfolio-ops",
                name="Imported Planning Axis",
                taxonomy_type="risk_sleeve",
                purpose=None,
                primary_assignment_scope="instrument",
                planning_enabled=True,
                budgeting_level="weight_and_risk_budget",
                root_default_target_dimension="weight",
                status="active",
                source_template_ref=None,
            )
        )
        session.commit()

    root_policy_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/"
        f"{taxonomy_id}/analytics-scope-policies/{ROOT_POLICY_NODE_ID}",
        json={
            "effective_from": EFFECTIVE_FROM,
            "risk_eligible": True,
            "risk_budget_eligible": True,
            "performance_scope": "ordinary",
            "valuation_basis": "market",
            "exclusion_reason": None,
        },
    )
    assert root_policy_response.status_code == 200, root_policy_response.text

    response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM, "taxonomy_id": taxonomy_id},
    )
    assert response.status_code == 200, response.text

    with session_factory() as session:
        policies = session.scalars(
            select(AnalyticsScopePolicyRecordModel).where(
                AnalyticsScopePolicyRecordModel.portfolio_id == "portfolio-ops",
                AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
            )
        ).all()
        configuration = session.scalar(
            select(TaxonomyConfigurationRevisionModel).where(
                TaxonomyConfigurationRevisionModel.portfolio_id == "portfolio-ops",
                TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
            )
        )

    policies_by_node = {policy.taxonomy_node_id: policy for policy in policies}
    assert set(policies_by_node) == {
        ROOT_POLICY_NODE_ID,
        UNASSIGNED_POLICY_NODE_ID,
    }
    assert policies_by_node[ROOT_POLICY_NODE_ID].risk_eligible is True
    assert policies_by_node[ROOT_POLICY_NODE_ID].risk_budget_eligible is True
    assert policies_by_node[UNASSIGNED_POLICY_NODE_ID].risk_eligible is False
    assert policies_by_node[UNASSIGNED_POLICY_NODE_ID].performance_scope == "unallocated"
    assert configuration is not None
    assert configuration.effective_from == date.fromisoformat(EFFECTIVE_FROM)


def test_default_planning_taxonomy_rejects_non_planning_taxonomy(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Sector Lens",
            "taxonomy_type": "sector",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM,"taxonomy_id": taxonomy_id},
    )
    assert response.status_code == 400
    assert "planning-enabled" in response.json()["detail"]


def test_taxonomy_update_node_and_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Editable Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Root One", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Root Two", "sort_order": 1},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Leaf", "parent_taxonomy_node_id": root_node_id, "sort_order": 2},
    )
    child_node_id = child_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": child_node_id,
            "effective_from": "2026-01-01",
        },
    )
    assignment_id = assignment_response.json()["assignment_id"]

    update_taxonomy_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Editable Planning Axis 2",
            "purpose": "Updated purpose",
            "root_default_target_dimension": "risk_budget",
        },
    )
    assert update_taxonomy_response.status_code == 200
    assert update_taxonomy_response.json()["name"] == "Editable Planning Axis 2"
    assert update_taxonomy_response.json()["purpose"] == "Updated purpose"
    assert update_taxonomy_response.json()["root_default_target_dimension"] == "risk_budget"

    update_node_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes/{child_node_id}",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Leaf Renamed",
            "parent_taxonomy_node_id": other_root_node_id,
            "sort_order": 3,
        },
    )
    assert update_node_response.status_code == 200
    assert update_node_response.json()["node_name"] == "Leaf Renamed"
    assert update_node_response.json()["parent_taxonomy_node_id"] == other_root_node_id
    assert update_node_response.json()["sort_order"] == 3

    update_assignment_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={"effective_from": EFFECTIVE_FROM,
            "status": "archived",
        },
    )
    assert update_assignment_response.status_code == 200
    assert update_assignment_response.json()["status"] == "archived"

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    moved_node = next(node for node in catalog_payload["taxonomy_nodes"] if node["taxonomy_node_id"] == child_node_id)
    assert moved_node["parent_taxonomy_node_id"] == other_root_node_id
    updated_assignment = next(
        item for item in catalog_payload["taxonomy_assignments"] if item["assignment_id"] == assignment_id
    )
    assert updated_assignment["status"] == "archived"


def test_security_taxonomy_rejects_cash_bucket_assignments(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Planning With Cash",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    cash_node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Liquidity Reserve",
            "default_target_dimension": "risk_budget",
        },
    )
    assert cash_node_response.status_code == 200
    cash_node_payload = cash_node_response.json()
    assert cash_node_payload["default_target_dimension"] == "risk_budget"

    update_node_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes/{cash_node_payload['taxonomy_node_id']}",
        json={"effective_from": EFFECTIVE_FROM,"default_target_dimension": "weight"},
    )
    assert update_node_response.status_code == 200
    assert update_node_response.json()["default_target_dimension"] == "weight"

    assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": cash_node_payload["taxonomy_node_id"],
        },
    )
    assert assignment_response.status_code == 422
    assert "instrument" in str(assignment_response.json())


def test_target_set_rejects_non_normalized_weight_totals(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Levered Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Levered Root Scope",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": first_child_id,
                    "target_weight": 1.2,
                    "target_risk_share": 0.8,
                },
                {
                    "taxonomy_node_id": second_child_id,
                    "target_weight": 0.3,
                    "target_risk_share": 0.2,
                },
            ],
        },
    )
    assert target_set_response.status_code == 400
    assert "target_weight values must sum to 100%." in str(target_set_response.json())


def test_target_set_rejects_non_normalized_risk_share_totals(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Risk Share Validation Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Invalid Risk Share",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": first_child_id,
                    "target_weight": 1.2,
                    "target_risk_share": 0.8,
                },
                {
                    "taxonomy_node_id": second_child_id,
                    "target_weight": 0.3,
                    "target_risk_share": 0.35,
                },
            ],
        },
    )
    assert target_set_response.status_code == 400
    assert "sum to 100%" in target_set_response.json()["detail"]


def test_target_set_scope_uses_current_assignment_members(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Effective Scope Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Leaf Sleeve", "sort_order": 0},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]

    first_assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": root_node_id,
            "effective_from": "2026-01-01",
            "effective_to": "2026-03-31",
        },
    )
    assert first_assignment_response.status_code == 200

    second_assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": root_node_id,
            "effective_from": "2026-04-01",
        },
    )
    assert second_assignment_response.status_code == 200

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Future Member Scope",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_weight": 0.4,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_weight": 0.6,
                }
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    saved_lines = [
        item
        for item in catalog_response.json()["target_set_lines"]
        if item["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    assert saved_lines == [
        {
            "target_line_id": saved_lines[0]["target_line_id"],
            "target_set_id": target_set_response.json()["target_set_id"],
            "taxonomy_node_id": None,
            "target_member_type": "instrument",
            "target_member_id": "equity-us-abbv",
            "target_weight": 0.4,
            "target_risk_share": None,
            "notes": None,
        },
        {
            "target_line_id": saved_lines[1]["target_line_id"],
            "target_set_id": target_set_response.json()["target_set_id"],
            "taxonomy_node_id": None,
            "target_member_type": "instrument",
            "target_member_id": "fund-us-agg",
            "target_weight": 0.6,
            "target_risk_share": None,
            "notes": None,
        }
    ]


def test_taxonomy_catalog_reports_active_target_set_scope_drift(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Integrity Scope Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    sleeve_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Absolute Return", "sort_order": 0},
    )
    sleeve_id = sleeve_response.json()["taxonomy_node_id"]
    first_assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": sleeve_id,
        },
    )
    assert first_assignment_response.status_code == 200

    saa_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": sleeve_id,
            "target_set_type": "saa",
            "name": "Absolute Return SAA",
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_risk_share": 1.0,
                }
            ],
        },
    )
    assert saa_response.status_code == 200
    taa_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": sleeve_id,
            "target_set_type": "taa",
            "name": "Archived Absolute Return TAA",
            "risk_budget_enabled": True,
            "status": "archived",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_risk_share": 1.0,
                }
            ],
        },
    )
    assert taa_response.status_code == 200
    initially_valid_catalog = client.get("/api/portfolios/portfolio-ops/taxonomies").json()
    assert initially_valid_catalog["target_set_integrity_issues"] == []

    second_assignment_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
        json={"effective_from": EFFECTIVE_FROM,
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": sleeve_id,
        },
    )
    assert second_assignment_response.status_code == 200

    drifted_catalog = client.get("/api/portfolios/portfolio-ops/taxonomies").json()
    assert drifted_catalog["target_set_integrity_issues"] == [
        {
            "taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": sleeve_id,
            "scope_label": "Absolute Return",
            "target_set_id": saa_response.json()["target_set_id"],
            "target_set_type": "saa",
            "target_set_name": "Absolute Return SAA",
            "issue_code": "invalid_active_target_set",
            "message": "Target set lines must cover every direct member in the selected scope.",
        }
    ]

    repaired_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets/{saa_response.json()['target_set_id']}",
        json={"effective_from": EFFECTIVE_FROM,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_risk_share": 0.5,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_risk_share": 0.5,
                },
            ]
        },
    )
    assert repaired_response.status_code == 200
    repaired_catalog = client.get("/api/portfolios/portfolio-ops/taxonomies").json()
    assert repaired_catalog["target_set_integrity_issues"] == []


def test_root_risk_budget_excludes_cash_and_rejects_cash_risk_values(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Root Cash Integrity Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    defensive_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Defensive", "sort_order": 0},
    )
    growth_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Growth", "sort_order": 1},
    )

    risky_lines = [
        {
            "taxonomy_node_id": defensive_response.json()["taxonomy_node_id"],
            "target_weight": None,
            "target_risk_share": 0.4,
        },
        {
            "taxonomy_node_id": growth_response.json()["taxonomy_node_id"],
            "target_weight": None,
            "target_risk_share": 0.6,
        },
    ]
    for cash_risk_share in (0.0, 0.1):
        invalid_cash_response = client.post(
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
            json={"effective_from": EFFECTIVE_FROM,
                "target_set_type": "saa",
                "name": "Invalid Root Risk Budget",
                "weight_enabled": False,
                "risk_budget_enabled": True,
                "lines": [
                    *risky_lines,
                    {
                        "target_member_type": "cash_bucket",
                        "target_member_id": "__cash__",
                        "target_weight": None,
                        "target_risk_share": cash_risk_share,
                    },
                ],
            },
        )
        assert invalid_cash_response.status_code == 400
        assert "leave target_risk_share empty" in invalid_cash_response.json()["detail"]

    invalid_cash_placeholder_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Invalid Root Cash Placeholder",
            "weight_enabled": False,
            "risk_budget_enabled": True,
            "lines": [
                *risky_lines,
                {
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": None,
                    "target_risk_share": None,
                },
            ],
        },
    )
    assert invalid_cash_placeholder_response.status_code == 400
    assert "not part of risk-budget-only" in invalid_cash_placeholder_response.json()["detail"]

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Root Risk Budget",
            "weight_enabled": False,
            "risk_budget_enabled": True,
            "lines": risky_lines,
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["target_set_integrity_issues"] == []

    tactical_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Tactical", "sort_order": 2},
    )
    assert tactical_response.status_code == 200
    structurally_drifted_catalog = client.get("/api/portfolios/portfolio-ops/taxonomies").json()
    assert structurally_drifted_catalog["target_set_integrity_issues"] == [
        {
            "taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "scope_label": "Top Level",
            "target_set_id": target_set_response.json()["target_set_id"],
            "target_set_type": "saa",
            "target_set_name": "Root Risk Budget",
            "issue_code": "invalid_active_target_set",
            "message": "Target set lines must cover every direct member in the selected scope.",
        }
    ]


def test_deleting_default_planning_taxonomy_clears_pointer(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Bridgewater Planning Axis",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    set_response = client.put(
        "/api/portfolios/portfolio-ops/taxonomies/default-planning",
        json={"effective_from": EFFECTIVE_FROM,"taxonomy_id": taxonomy_id},
    )
    assert set_response.status_code == 200

    delete_response = client.delete(f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}", params={"effective_from": EFFECTIVE_FROM})
    assert delete_response.status_code == 200

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["default_planning_taxonomy_id"] is None


def test_target_set_create_update_and_catalog_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Core", "sort_order": 0},
    )
    satellite_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Satellite", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    satellite_node_id = satellite_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    saa_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Top Level SAA",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": core_node_id,
                    "target_weight": 0.7,
                    "target_risk_share": 0.6,
                },
                {
                    "taxonomy_node_id": satellite_node_id,
                    "target_weight": 0.3,
                    "target_risk_share": 0.4,
                },
            ],
        },
    )
    assert saa_response.status_code == 200
    saa_target_set_id = saa_response.json()["target_set_id"]

    taa_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "taa",
            "name": "Core Sleeve TAA",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_weight": 0.55,
                    "target_risk_share": 0.5,
                },
                {
                    "taxonomy_node_id": income_node_id,
                    "target_weight": 0.45,
                    "target_risk_share": 0.5,
                },
            ],
        },
    )
    assert taa_response.status_code == 200

    update_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets/{saa_target_set_id}",
        json={"effective_from": EFFECTIVE_FROM,
            "lines": [
                {
                    "taxonomy_node_id": core_node_id,
                    "target_weight": 0.68,
                    "target_risk_share": 0.58,
                },
                {
                    "taxonomy_node_id": satellite_node_id,
                    "target_weight": 0.32,
                    "target_risk_share": 0.42,
                },
            ],
        },
    )
    assert update_response.status_code == 200
    assert "effective_to" not in update_response.json()

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["target_sets"]) == 2
    assert len(catalog_payload["target_set_lines"]) == 4
    updated_saa = next(item for item in catalog_payload["target_sets"] if item["target_set_id"] == saa_target_set_id)
    assert "effective_to" not in updated_saa
    root_lines = [item for item in catalog_payload["target_set_lines"] if item["target_set_id"] == saa_target_set_id]
    assert sum(item["target_weight"] for item in root_lines) == pytest.approx(1.0)


def test_leaf_scope_target_set_accepts_only_assigned_instruments(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Leaf Member Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Leaf Sleeve", "sort_order": 0},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]

    for payload in [
        {
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": root_node_id,
        },
        {
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": root_node_id,
        },
    ]:
        assignment_response = client.post(
            f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/assignments",
            json={"effective_from": EFFECTIVE_FROM, **payload},
        )
        assert assignment_response.status_code == 200

    invalid_cash_risk_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Invalid Leaf Sleeve Mix",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_weight": 0.45,
                    "target_risk_share": 0.75,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_weight": 0.35,
                    "target_risk_share": 0.25,
                },
                {
                    "target_member_type": "cash_bucket",
                    "target_member_id": "cash-usd-main",
                    "target_weight": 0.2,
                    "target_risk_share": 0.0,
                },
            ],
        },
    )
    assert invalid_cash_risk_response.status_code == 400
    assert "direct members of the selected scope" in invalid_cash_risk_response.json()["detail"]

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Leaf Sleeve Mix",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_weight": 0.6,
                    "target_risk_share": 0.75,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_weight": 0.4,
                    "target_risk_share": 0.25,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/portfolio-ops/taxonomies")
    assert catalog_response.status_code == 200
    saved_lines = [
        item
        for item in catalog_response.json()["target_set_lines"]
        if item["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    assert {item["target_member_type"] for item in saved_lines} == {"instrument"}
    assert sum(item["target_weight"] for item in saved_lines) == pytest.approx(1.0)
    assert sum(
        item["target_risk_share"] for item in saved_lines
    ) == pytest.approx(1.0)


def test_root_targets_accept_fixed_cash_and_derivative_weights_without_risk(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "System Buckets Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    node_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,
            "node_name": "Risk Assets",
            "node_code": "RISK",
            "sort_order": 0,
        },
    )
    assert node_response.status_code == 200
    risk_node_id = node_response.json()["taxonomy_node_id"]

    invalid_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "Invalid System Buckets SAA",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": risk_node_id,
                    "target_weight": 0.8,
                    "target_risk_share": 1.0,
                },
                {
                    "target_member_type": "derivative_bucket",
                    "target_member_id": "__derivatives__",
                    "target_weight": 0.1,
                    "target_risk_share": 0.0,
                },
                {
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": 0.1,
                    "target_risk_share": None,
                },
            ],
        },
    )
    assert invalid_response.status_code == 400
    assert "leave target_risk_share empty" in invalid_response.json()["detail"]

    target_set_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "target_set_type": "saa",
            "name": "System Buckets SAA",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "taxonomy_node_id": risk_node_id,
                    "target_weight": 0.75,
                    "target_risk_share": 1.0,
                },
                {
                    "target_member_type": "derivative_bucket",
                    "target_member_id": "__derivatives__",
                    "target_weight": 0.1,
                    "target_risk_share": None,
                },
                {
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": 0.15,
                    "target_risk_share": None,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog = client.get("/api/portfolios/portfolio-ops/taxonomies").json()
    assert catalog["target_set_integrity_issues"] == []
    saved_lines = [
        line
        for line in catalog["target_set_lines"]
        if line["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    saved_by_member_id = {line["target_member_id"]: line for line in saved_lines}
    assert saved_by_member_id["__derivatives__"]["target_weight"] == pytest.approx(0.1)
    assert saved_by_member_id["__derivatives__"]["target_risk_share"] is None
    assert saved_by_member_id["__cash__"]["target_weight"] == pytest.approx(0.15)
    assert saved_by_member_id["__cash__"]["target_risk_share"] is None


def test_target_set_requires_full_scope_and_blocks_referenced_node_move(client):
    taxonomy_response = client.post(
        "/api/portfolios/portfolio-ops/taxonomies",
        json={"effective_from": EFFECTIVE_FROM,
            "name": "Scoped Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Core", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Other Root", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes",
        json={"effective_from": EFFECTIVE_FROM,"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    invalid_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "saa",
            "name": "Incomplete Scope",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_weight": 1.0,
                }
            ],
        },
    )
    assert invalid_response.status_code == 400
    assert "cover every direct member" in invalid_response.json()["detail"]

    valid_response = client.post(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/target-sets",
        json={"effective_from": EFFECTIVE_FROM,
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "saa",
            "name": "Core Scope",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_weight": 0.6,
                },
                {
                    "taxonomy_node_id": income_node_id,
                    "target_weight": 0.4,
                },
            ],
        },
    )
    assert valid_response.status_code == 200

    move_response = client.patch(
        f"/api/portfolios/portfolio-ops/taxonomies/{taxonomy_id}/nodes/{growth_node_id}",
        json={"effective_from": EFFECTIVE_FROM,"parent_taxonomy_node_id": other_root_node_id},
    )
    assert move_response.status_code == 400
    assert "target-set configuration" in move_response.json()["detail"]
