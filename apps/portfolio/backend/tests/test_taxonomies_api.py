from __future__ import annotations

import pytest


def test_taxonomy_create_node_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_payload['taxonomy_id']}/nodes",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_payload['taxonomy_id']}/assignments",
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

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["taxonomies"]) == 1
    assert len(catalog_payload["taxonomy_nodes"]) == 1
    assert len(catalog_payload["taxonomy_assignments"]) == 1
    assert catalog_payload["taxonomy_assignments"][0]["assignment_id"] == assignment_payload["assignment_id"]


def test_taxonomy_delete_node_rejects_parent_with_children(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Sector Map",
            "taxonomy_type": "sector",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    parent_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Equity", "sort_order": 0},
    )
    parent_node_id = parent_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Healthcare",
            "parent_taxonomy_node_id": parent_node_id,
            "sort_order": 1,
        },
    )
    assert child_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes/{parent_node_id}"
    )
    assert delete_response.status_code == 400
    assert "child nodes" in delete_response.json()["detail"]


def test_taxonomy_delete_node_rejects_assigned_node(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Issuer Groups",
            "taxonomy_type": "issuer",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Healthcare", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes/{node_id}"
    )
    assert delete_response.status_code == 400
    assert "assignments" in delete_response.json()["detail"]


def test_taxonomy_rejects_adding_child_under_assigned_node(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Sleeve Tree",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Child Sleeve",
            "parent_taxonomy_node_id": node_id,
        },
    )
    assert child_response.status_code == 400
    assert "already has assignments" in child_response.json()["detail"]


def test_taxonomy_create_rejects_planning_enabled_non_instrument_scope(client):
    response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Account Planning",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "account",
            "planning_enabled": True,
        },
    )
    assert response.status_code == 422
    assert "instrument assignment scope" in str(response.json())


def test_default_planning_taxonomy_can_be_set_and_cleared(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Core Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    set_response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert set_response.status_code == 200
    assert set_response.json()["default_planning_taxonomy_id"] == taxonomy_id

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["default_planning_taxonomy_id"] == taxonomy_id

    clear_response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["default_planning_taxonomy_id"] is None


def test_default_planning_taxonomy_rejects_non_planning_taxonomy(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Sector Lens",
            "taxonomy_type": "sector",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert response.status_code == 400
    assert "planning-enabled" in response.json()["detail"]


def test_taxonomy_update_node_and_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Editable Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Root One", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Root Two", "sort_order": 1},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Leaf", "parent_taxonomy_node_id": root_node_id, "sort_order": 2},
    )
    child_node_id = child_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": child_node_id,
            "effective_from": "2026-01-01",
        },
    )
    assignment_id = assignment_response.json()["assignment_id"]

    update_taxonomy_response = client.patch(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes/{child_node_id}",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={
            "effective_to": "2026-12-31",
        },
    )
    assert update_assignment_response.status_code == 200
    assert update_assignment_response.json()["effective_to"] == "2026-12-31"

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    moved_node = next(node for node in catalog_payload["taxonomy_nodes"] if node["taxonomy_node_id"] == child_node_id)
    assert moved_node["parent_taxonomy_node_id"] == other_root_node_id
    updated_assignment = next(
        item for item in catalog_payload["taxonomy_assignments"] if item["assignment_id"] == assignment_id
    )
    assert updated_assignment["effective_to"] == "2026-12-31"


def test_planning_taxonomy_node_default_target_and_cash_bucket_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Planning With Cash",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    cash_node_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Cash Reserve",
            "default_target_dimension": "risk_budget",
        },
    )
    assert cash_node_response.status_code == 200
    cash_node_payload = cash_node_response.json()
    assert cash_node_payload["default_target_dimension"] == "risk_budget"

    update_node_response = client.patch(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes/{cash_node_payload['taxonomy_node_id']}",
        json={"default_target_dimension": "weight"},
    )
    assert update_node_response.status_code == 200
    assert update_node_response.json()["default_target_dimension"] == "weight"

    assignment_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": cash_node_payload["taxonomy_node_id"],
        },
    )
    assert assignment_response.status_code == 200
    assert assignment_response.json()["target_scope"] == "cash_bucket"


def test_target_set_accepts_levered_weight_totals_with_normalized_risk_share(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Levered Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
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
    assert target_set_response.status_code == 200
    assert target_set_response.json()["target_set_type"] == "saa"

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    saved_lines = [
        item
        for item in catalog_response.json()["target_set_lines"]
        if item["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    assert sum(item["target_weight"] for item in saved_lines) == pytest.approx(1.5)
    assert sum(item["target_risk_share"] for item in saved_lines) == pytest.approx(1.0)


def test_target_set_rejects_non_normalized_risk_share_totals(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Risk Share Validation Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
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


def test_target_set_scope_uses_effective_assignment_periods(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Effective Scope Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Leaf Sleeve", "sort_order": 0},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]

    first_assignment_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": root_node_id,
            "effective_from": "2026-04-01",
        },
    )
    assert second_assignment_response.status_code == 200

    target_set_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
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
                    "target_member_id": "fund-us-agg",
                    "target_weight": 1.0,
                }
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
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
            "target_member_id": "fund-us-agg",
            "target_weight": 1.0,
            "target_risk_share": None,
            "notes": None,
        }
    ]


def test_deleting_default_planning_taxonomy_clears_pointer(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Bridgewater Planning Axis",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    set_response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert set_response.status_code == 200

    delete_response = client.delete(f"/api/portfolios/yungu/taxonomies/{taxonomy_id}")
    assert delete_response.status_code == 200

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["default_planning_taxonomy_id"] is None


def test_target_set_create_update_and_catalog_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    satellite_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Satellite", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    satellite_node_id = satellite_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    saa_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "taa",
            "name": "Core Sleeve TAA",
            "effective_from": "2026-04-01",
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets/{saa_target_set_id}",
        json={
            "effective_to": "2026-12-31",
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
    assert update_response.json()["effective_to"] == "2026-12-31"

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["target_sets"]) == 2
    assert len(catalog_payload["target_set_lines"]) == 4
    updated_saa = next(item for item in catalog_payload["target_sets"] if item["target_set_id"] == saa_target_set_id)
    assert updated_saa["effective_to"] == "2026-12-31"
    root_lines = [item for item in catalog_payload["target_set_lines"] if item["target_set_id"] == saa_target_set_id]
    assert sum(item["target_weight"] for item in root_lines) == pytest.approx(1.0)


def test_leaf_scope_target_set_accepts_instrument_and_cash_members(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Leaf Member Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Leaf Sleeve", "sort_order": 0},
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
        {
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": root_node_id,
        },
    ]:
        assignment_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
            json=payload,
        )
        assert assignment_response.status_code == 200

    target_set_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Leaf Sleeve Mix",
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
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/yungu/taxonomies")
    assert catalog_response.status_code == 200
    saved_lines = [
        item
        for item in catalog_response.json()["target_set_lines"]
        if item["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    assert {item["target_member_type"] for item in saved_lines} == {"instrument", "cash_bucket"}
    assert sum(item["target_weight"] for item in saved_lines) == pytest.approx(1.0)
    assert sum(
        item["target_risk_share"] for item in saved_lines if item["target_member_type"] != "cash_bucket"
    ) == pytest.approx(1.0)
    cash_line = next(item for item in saved_lines if item["target_member_type"] == "cash_bucket")
    assert cash_line["target_risk_share"] == pytest.approx(0.0)


def test_target_set_requires_full_scope_and_blocks_referenced_node_move(client):
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Scoped Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Other Root", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    invalid_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
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
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes/{growth_node_id}",
        json={"parent_taxonomy_node_id": other_root_node_id},
    )
    assert move_response.status_code == 400
    assert "target-set configuration" in move_response.json()["detail"]
