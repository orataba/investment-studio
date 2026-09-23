from __future__ import annotations

from copy import deepcopy

import pytest

from portfolio_app.db.models import TaxonomyConfigurationRevisionModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.taxonomy_configuration import current_taxonomy_configuration, taxonomy_configuration_version

PORTFOLIO_ID = "investment-studio"


def _create_planning_tree(client, *, name: str) -> dict[str, str]:
    response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies", json={"name": name, "taxonomy_type": "risk_sleeve"})
    assert response.status_code == 200, response.text
    taxonomy_id = response.json()["taxonomy_id"]
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}"
    parent = client.post(f"{base}/nodes", json={"node_name": "Market Assets"}).json()["taxonomy_node_id"]
    child = client.post(f"{base}/nodes", json={"node_name": "Listed Equity", "parent_taxonomy_node_id": parent}).json()["taxonomy_node_id"]
    sibling = client.post(f"{base}/nodes", json={"node_name": "Listed Funds", "parent_taxonomy_node_id": parent}).json()["taxonomy_node_id"]
    assignment = client.post(f"{base}/assignments", json={"target_scope": "instrument", "target_entity_id": "equity-us-abbv", "taxonomy_node_id": child})
    assert assignment.status_code == 200, assignment.text
    return {"taxonomy_id": taxonomy_id, "parent_id": parent, "child_id": child, "sibling_id": sibling, "assignment_id": assignment.json()["assignment_id"]}


def test_current_assignments_targets_and_immutable_configuration_audit(client):
    tree = _create_planning_tree(client, name="Current Primary")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    response = client.post(f"{base}/target-sets", json={"comparator_taxonomy_node_id": tree["child_id"], "target_set_type": "saa", "name": "Original Target",
        "lines": [{"target_member_type": "instrument", "target_member_id": "equity-us-abbv", "target_value": 1.0}]})
    assert response.status_code == 200, response.text
    old = current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"])
    assert client.patch(f"{base}/target-sets/{response.json()['target_set_id']}", json={"name": "Current Target"}).status_code == 200
    assert client.patch(f"{base}/assignments/{tree['assignment_id']}", json={"taxonomy_node_id": tree["sibling_id"]}).status_code == 200
    current = current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"])
    assert current["taxonomy_assignments"][0]["taxonomy_node_id"] == tree["sibling_id"]
    assert current["target_sets"] == []  # The now-empty old sleeve has no current targets.
    assert current["configuration_version"] > old["configuration_version"]
    with get_session_factory()() as session:
        archived = session.get(TaxonomyConfigurationRevisionModel, old["taxonomy_configuration_revision_id"])
        assert archived.configuration_json["target_sets"][0]["name"] == "Original Target"
        assert archived.configuration_json["target_set_lines"][0]["target_value"] == 1.0
        assert archived.configuration_json["taxonomy_assignments"][0]["taxonomy_node_id"] == tree["child_id"]
        assert archived.superseded_by_revision_id is not None
    _create_planning_tree(client, name="Current Secondary")
    catalog = client.get(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies").json()
    assert "default_planning_taxonomy_id" not in catalog
    assert len(catalog["target_resolution"]) == len(catalog["taxonomies"])


@pytest.mark.parametrize("operation", ["archive", "delete"])
def test_removing_classification_preserves_configuration_audit(client, operation):
    tree = _create_planning_tree(client, name=f"Remove {operation}")
    taxonomy_id = tree["taxonomy_id"]
    original = current_taxonomy_configuration(PORTFOLIO_ID, taxonomy_id)
    with get_session_factory()() as session:
        archived_payload = deepcopy(session.get(TaxonomyConfigurationRevisionModel, original["taxonomy_configuration_revision_id"]).configuration_json)
    version = taxonomy_configuration_version(PORTFOLIO_ID)
    endpoint = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{taxonomy_id}"
    response = client.delete(endpoint) if operation == "delete" else client.patch(endpoint, json={"status": "archived"})
    assert response.status_code == 200, response.text
    assert taxonomy_configuration_version(PORTFOLIO_ID) > version
    with get_session_factory()() as session:
        old = session.get(TaxonomyConfigurationRevisionModel, original["taxonomy_configuration_revision_id"])
        assert old.configuration_json == archived_payload
        assert old.superseded_by_revision_id
    if operation == "delete":
        assert current_taxonomy_configuration(PORTFOLIO_ID, taxonomy_id) is None


def test_copy_keeps_independent_classification_identity_and_configuration(client):
    tree = _create_planning_tree(client, name="Copy Current Configuration")
    original = current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"])
    copied = client.post(f"/api/portfolios/{PORTFOLIO_ID}/copy")
    assert copied.status_code == 200, copied.text
    copy_id = copied.json()["portfolio_id"]
    catalog = client.get(f"/api/portfolios/{copy_id}/taxonomies").json()
    copy_taxonomy_id = next(t["taxonomy_id"] for t in catalog["taxonomies"] if t["name"] == "Copy Current Configuration")
    assert copy_taxonomy_id != tree["taxonomy_id"]
    configuration = current_taxonomy_configuration(copy_id, copy_taxonomy_id)
    assert configuration["taxonomy"]["portfolio_id"] == copy_id
    assert configuration["taxonomy_configuration_revision_id"] != original["taxonomy_configuration_revision_id"]
    assignment = next(item for item in configuration["taxonomy_assignments"] if item["target_entity_id"] == "equity-us-abbv")
    assert assignment["taxonomy_node_id"] != tree["child_id"]
    assert assignment["taxonomy_node_id"] in {node["taxonomy_node_id"] for node in configuration["taxonomy_nodes"]}
    assert "default_planning_taxonomy_id" not in catalog


def test_classification_changes_preserve_actual_holdings_risk_and_nav(client):
    tree = _create_planning_tree(client, name="Classification Is Independent Of Risk")
    def holdings():
        response = client.get("/api/workspace/holdings", params={"portfolio_id": PORTFOLIO_ID, "as_of_date": "2026-04-15"})
        assert response.status_code == 200, response.text
        payload = response.json()
        security = next(row for row in payload["rows"] if (row.get("instrument_core") or {}).get("instrument_id") == "equity-us-abbv")
        assert security["risk_eligible"] is True
        assert security["modeling_status"] == "eligible"
        assert "analytics_scope_summary" not in payload
        return payload
    before = holdings()
    removed = client.delete(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}/assignments/{tree['assignment_id']}")
    assert removed.status_code == 200, removed.text
    after = holdings()
    assert after["totals"]["nav"] == pytest.approx(before["totals"]["nav"])
    assert after["risk_coverage_summary"] == before["risk_coverage_summary"]


def test_targets_and_limits_save_atomically_and_limit_only_keeps_target_version(client):
    tree = _create_planning_tree(client, name="Atomic editor")
    base = f"/api/portfolios/{PORTFOLIO_ID}"
    endpoint = f"{base}/taxonomies/{tree['taxonomy_id']}/target-configuration"
    limit = {"expected_revision": 0, "effective_from": "2026-04-15",
        "enabled_taxonomy_ids": [tree["taxonomy_id"]],
        "limits": [{"scope": "taxonomy", "taxonomy_id": tree["taxonomy_id"],
                    "entity_id": tree["parent_id"], "limit_weight": 0.6}]}
    target = {"target_set_type": "saa", "name": "Root", "lines": [
        {"target_member_type": "taxonomy_node", "target_member_id": tree["parent_id"], "target_value": 1}]}
    saved = client.put(endpoint, json={"expected_configuration_version": taxonomy_configuration_version(PORTFOLIO_ID), "root_allocation_basis": "risk_budget", "target_sets": [target], "concentration": limit})
    assert saved.status_code == 200, saved.text
    version = taxonomy_configuration_version(PORTFOLIO_ID)
    original = current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"])
    settings = client.get(f"{base}/concentration/settings").json()
    assert settings["revision"] == 1 and settings["limits"][0]["limit_weight"] == .6
    # A stale concentration edit rolls the target and basis changes back too.
    failed = client.put(endpoint, json={"expected_configuration_version": taxonomy_configuration_version(PORTFOLIO_ID), "root_allocation_basis": "weight", "target_sets": [{**target, "name": "Must roll back"}], "concentration": limit})
    assert failed.status_code == 409, failed.text
    assert current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"]) == original
    assert taxonomy_configuration_version(PORTFOLIO_ID) == version
    limit["expected_revision"] = 1
    limit["limits"][0]["limit_weight"] = 0
    saved = client.put(endpoint, json={"expected_configuration_version": taxonomy_configuration_version(PORTFOLIO_ID), "concentration": limit})
    assert saved.status_code == 200, saved.text
    assert taxonomy_configuration_version(PORTFOLIO_ID) == version
    assert current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"]) == original
    assert client.get(f"{base}/concentration/settings").json()["limits"][0]["limit_weight"] == 0


@pytest.mark.parametrize("basis", ["weight", "risk_budget"])
def test_two_scalar_stages_share_basis_and_root_cash_is_independent(client, basis):
    tree = _create_planning_tree(client, name="Shared basis")
    endpoint = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}/target-configuration"
    stages = [{"target_set_type": stage, "name": stage, "lines": [
        {"target_member_type": "taxonomy_node", "target_member_id": tree["parent_id"], "target_value": 1},
        {"target_member_type": "cash_bucket", "target_member_id": "__cash__", "target_value": reserve},
    ]} for stage, reserve in [("saa", .2), ("taa", .1)]]
    saved = client.put(endpoint, json={"expected_configuration_version": taxonomy_configuration_version(PORTFOLIO_ID), "root_allocation_basis": basis, "target_sets": stages})
    assert saved.status_code == 200, saved.text
    current = current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"])
    assert current["taxonomy"]["root_allocation_basis"] == basis
    assert {row["target_set_type"] for row in current["target_sets"]} == {"saa", "taa"}
    assert all("target_weight" not in row and "target_risk_share" not in row for row in current["target_set_lines"])
    assert all("weight_enabled" not in row and "risk_budget_enabled" not in row for row in current["target_sets"])
    invalid = deepcopy(stages[0])
    invalid["lines"].append({"target_member_type": "derivative_bucket", "target_member_id": "__derivatives__", "target_value": .1})
    rejected = client.put(endpoint, json={"expected_configuration_version": taxonomy_configuration_version(PORTFOLIO_ID), "target_sets": [invalid]})
    assert rejected.status_code in (400, 422), rejected.text
    assert current_taxonomy_configuration(PORTFOLIO_ID, tree["taxonomy_id"]) == current


def test_target_contract_has_no_global_default_or_dual_dimension_inputs(client):
    schema = client.app.openapi()
    obsolete = {"default_planning_taxonomy_id", "planning_enabled", "budgeting_level", "weight_enabled", "risk_budget_enabled", "target_weight", "target_risk_share", "root_default_target_dimension", "default_target_dimension"}
    for name, model in schema["components"]["schemas"].items():
        if name.startswith(("Taxonomy", "TargetSet")):
            assert obsolete.isdisjoint(model.get("properties", {})), name
    assert not any(path.endswith("/default-planning") for path in schema["paths"])
