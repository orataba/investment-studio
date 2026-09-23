from __future__ import annotations

from datetime import date
from copy import deepcopy

import pytest
from sqlalchemy import select

from portfolio_app.api.routes import taxonomies as taxonomies_routes
from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    ResearchRunRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyConfigurationRevisionModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory

def test_market_profile_enrichment_reuses_one_registry_batch(monkeypatch):
    records = [
        {
            "instrument_id": "fund-a",
            "status": "active",
            "instrument_ref": {"instrument_type": "private_fund"},
        },
        {
            "instrument_id": "fund-b",
            "status": "active",
            "instrument_ref": {"instrument_type": "public_fund"},
        },
        {
            "instrument_id": "cash:cny",
            "status": "active",
            "instrument_ref": {"instrument_type": "cash"},
        },
    ]
    details = {
        "fund-a": {"instrument_id": "fund-a", "series": []},
        "fund-b": {"instrument_id": "fund-b", "series": []},
    }
    batch_calls: list[list[str]] = []
    profile_details: list[dict[str, object]] = []

    def load_details(instrument_ids):
        batch_calls.append(list(instrument_ids))
        return details

    def frequency_profile(instrument_ids, *, end_date, detail_loader):
        assert end_date == date(2026, 8, 25)
        assert [detail_loader(instrument_id) for instrument_id in instrument_ids] == [
            details["fund-a"],
            details["fund-b"],
        ]
        return {"resolved_frequency": "daily", "coverage_state": "complete"}

    def market_profile(detail, **_kwargs):
        profile_details.append(detail)
        return {
            "instrument_trend_basis": "daily",
            "instrument_risk_frequency": "daily",
            "instrument_return_series_all": {"points": []},
        }

    monkeypatch.setattr(taxonomies_routes, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(
        taxonomies_routes,
        "calculation_frequency_profile_for_instruments",
        frequency_profile,
    )
    monkeypatch.setattr(
        taxonomies_routes,
        "build_instrument_holdings_market_profile_from_detail",
        market_profile,
    )

    enriched, risk_basis = taxonomies_routes._enrich_universe_market_profiles(
        records,
        as_of_date=date(2026, 8, 25),
    )

    assert batch_calls == [["fund-a", "fund-b"]]
    assert profile_details == [details["fund-a"], details["fund-b"]]
    assert risk_basis["coverage_state"] == "complete"
    assert enriched[0]["instrument_risk_frequency"] == "daily"
    assert "instrument_return_series_all" not in enriched[2]


def test_taxonomy_api_has_one_current_configuration_and_keeps_market_data_cutoff(client):
    schema = client.app.openapi()
    removed_fields = {"effective_from", "effective_to", "planning_as_of_date", "current_planning"}
    for name, model in schema["components"]["schemas"].items():
        if name.startswith(("Taxonomy", "TargetSet", "DefaultPlanningTaxonomy")):
            assert not removed_fields.intersection(model.get("properties", {})), name
    assert not any("analytics-scope-policies" in path for path in schema["paths"])
    assert not any(name.startswith(("AnalyticsScopePolicy", "AnalyticsTaxonomySelection"))
        for name in schema["components"]["schemas"])
    catalog_path = "/api/portfolios/{portfolio_id}/taxonomies"
    for path, operations in schema["paths"].items():
        if "/taxonomies" not in path:
            continue
        for operation in operations.values():
            assert not removed_fields.intersection(
                parameter["name"] for parameter in operation.get("parameters", [])
            ), path
    assert "as_of_date" in {
        parameter["name"]
        for parameter in schema["paths"][catalog_path]["get"]["parameters"]
    }

    base = "/api/portfolios/investment-studio/taxonomies"
    created = client.post(base, json={"name": "Current classification"})
    assert created.status_code == 200, created.text
    taxonomy_id = created.json()["taxonomy_id"]
    saved = client.patch(f"{base}/{taxonomy_id}", json={"name": "Updated classification"})
    assert saved.status_code == 200, saved.text
    historical = client.get(base, params={"as_of_date": "2000-01-01"})
    current = client.get(base)
    assert historical.status_code == current.status_code == 200
    assert historical.json() == current.json()
    assert next(item for item in historical.json()["taxonomies"]
                if item["taxonomy_id"] == taxonomy_id)["name"] == "Updated classification"


def test_taxonomy_create_node_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Risk Sleeves",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
            "purpose": "Current planning lens",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_payload = taxonomy_response.json()
    assert taxonomy_payload["name"] == "Risk Sleeves"
    assert taxonomy_payload["root_allocation_basis"] == "weight"

    node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_payload['taxonomy_id']}/nodes",
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
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_payload['taxonomy_id']}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_payload["taxonomy_node_id"],
        },
    )
    assert assignment_response.status_code == 200
    assignment_payload = assignment_response.json()
    assert assignment_payload["target_entity_id"] == "equity-us-abbv"

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["taxonomies"]) == 1
    assert len(catalog_payload["taxonomy_nodes"]) == 1
    assert len(catalog_payload["taxonomy_assignments"]) == 1
    assert catalog_payload["taxonomy_assignments"][0]["assignment_id"] == assignment_payload["assignment_id"]


def test_taxonomy_assignment_create_ignores_descriptive_imported_ids(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Imported Assignment IDs",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
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
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )

    assert assignment_response.status_code == 200
    assert assignment_response.json()["assignment_id"].startswith("tax-assignment-")


def test_taxonomy_catalog_includes_portfolio_instrument_universe(client):
    initial_catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert initial_catalog_response.status_code == 200
    initial_catalog_payload = initial_catalog_response.json()
    initial_universe = {
        item["instrument_id"]: item
        for item in initial_catalog_payload["instrument_universe"]
    }
    assert "equity-us-abbv" in initial_universe
    assert initial_universe["equity-us-abbv"]["transaction_count"] > 0

    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Watchlist Taxonomy",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Research", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]
    assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "fund-us-watch",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200
    assignment_id = assignment_response.json()["assignment_id"]

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert universe["fund-us-watch"]["source"] == "taxonomy"
    assert universe["fund-us-watch"]["holding_state"] == "not_held"
    assert universe["fund-us-watch"]["transaction_count"] == 0

    archived_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={"status": "archived"},
    )
    assert archived_response.status_code == 200
    archived_catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert archived_catalog_response.status_code == 200
    archived_universe = {
        item["instrument_id"]: item
        for item in archived_catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in archived_universe

    restored_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={"status": "active"},
    )
    assert restored_response.status_code == 200
    delete_response = client.delete(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments/{assignment_id}"
    )
    assert delete_response.status_code == 200
    deleted_catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert deleted_catalog_response.status_code == 200
    deleted_universe = {
        item["instrument_id"]: item
        for item in deleted_catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in deleted_universe


def test_taxonomy_adds_registry_instrument_to_manual_universe(client):
    response = client.post(
        "/api/portfolios/investment-studio/taxonomies/instrument-universe",
        json={"instrument_id": "fund-us-watch"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["instrument_id"] == "fund-us-watch"
    assert payload["instrument_ref"]["instrument_name"] == "Watchlist Fund"
    assert payload["source"] == "manual"
    assert payload["holding_state"] == "not_held"
    assert payload["transaction_count"] == 0

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert universe["fund-us-watch"]["source"] == "manual"
    assert universe["fund-us-watch"]["instrument_ref"]["instrument_name"] == "Watchlist Fund"


def test_taxonomy_deletes_manual_watch_instrument(client):
    create_response = client.post(
        "/api/portfolios/investment-studio/taxonomies/instrument-universe",
        json={"instrument_id": "fund-us-watch"},
    )
    assert create_response.status_code == 200

    delete_response = client.delete("/api/portfolios/investment-studio/taxonomies/instrument-universe/fund-us-watch")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    universe = {
        item["instrument_id"]: item
        for item in catalog_response.json()["instrument_universe"]
    }
    assert "fund-us-watch" not in universe


def test_taxonomy_delete_node_removes_its_children(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Sector Map",
            "taxonomy_type": "sector",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    parent_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Equity", "sort_order": 0},
    )
    parent_node_id = parent_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Healthcare",
            "parent_taxonomy_node_id": parent_node_id,
            "sort_order": 1,
        },
    )
    assert child_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes/{parent_node_id}"
    )
    assert delete_response.status_code == 200
    catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
    assert not [node for node in catalog["taxonomy_nodes"] if node["taxonomy_id"] == taxonomy_id]


def test_taxonomy_delete_node_unassigns_instruments(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Issuer Groups",
            "taxonomy_type": "issuer",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Healthcare", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    delete_response = client.delete(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes/{node_id}"
    )
    assert delete_response.status_code == 200
    catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
    assert not [item for item in catalog["taxonomy_assignments"] if item["taxonomy_id"] == taxonomy_id]
    assert any(item["instrument_id"] == "equity-us-abbv" for item in catalog["instrument_universe"])


def test_taxonomy_delete_subtree_preserves_sibling_budgets_and_saved_research(client):
    portfolio_id = "investment-studio"
    base = f"/api/portfolios/{portfolio_id}/taxonomies"
    created = client.post(base, json={"name": "Deletion scope"})
    assert created.status_code == 200, created.text
    taxonomy_id = created.json()["taxonomy_id"]
    path = f"{base}/{taxonomy_id}"

    def node(name, parent=None):
        response = client.post(f"{path}/nodes", json={"node_name": name, "parent_taxonomy_node_id": parent})
        assert response.status_code == 200, response.text
        return response.json()["taxonomy_node_id"]

    top = node("Top")
    removed = node("Removed", top)
    child = node("Child", removed)
    survivor = node("Survivor", top)
    assignment = client.post(f"{path}/assignments", json={"target_scope": "instrument",
        "target_entity_id": "equity-us-abbv", "taxonomy_node_id": child})
    assert assignment.status_code == 200, assignment.text

    def target(scope, members, member_type="taxonomy_node"):
        response = client.post(f"{path}/target-sets", json={
            "name": f"Scope {scope}", "comparator_taxonomy_node_id": scope,
            "target_set_type": "saa", "lines": [{"target_member_type": member_type, "target_member_id": member_id,
                       "target_value": share} for member_id, share in members],
        })
        assert response.status_code == 200, response.text
        return response.json()["target_set_id"]

    root_target = target(None, [(top, 1)])
    top_target = target(top, [(removed, 0.4), (survivor, 0.6)])
    removed_target = target(removed, [(child, 1)])
    child_target = target(child, [("equity-us-abbv", 1)], "instrument")
    with get_session_factory()() as session:
        revisions = session.scalars(select(TaxonomyConfigurationRevisionModel).where(
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id)).all()
        old_configurations = {row.taxonomy_configuration_revision_id: deepcopy(row.configuration_json) for row in revisions}
        old_request_id = session.get(PortfolioCalculationStateModel, portfolio_id).refresh_request_id
        saved_snapshot = deepcopy(revisions[-1].configuration_json)
        session.add(ResearchRunRecordModel(research_run_id="saved-before-node-delete", portfolio_id=portfolio_id,
            planning_taxonomy_id=taxonomy_id, detail_json={"configuration_snapshot": saved_snapshot}))
        settings = session.get(ResearchSettingsRecordModel, portfolio_id)
        if settings is None:
            settings = ResearchSettingsRecordModel(portfolio_id=portfolio_id)
            session.add(settings)
        settings.planning_taxonomy_id = taxonomy_id
        settings.comparator_taxonomy_node_id = child
        settings.frozen_taxonomy_node_ids_json = [removed, survivor]
        settings.top_sleeve_weight_bounds_json = [{"taxonomy_node_id": removed, "min_weight": 0.1},
                                                {"taxonomy_node_id": survivor, "max_weight": 0.8}]
        session.commit()

    deleted = client.delete(f"{path}/nodes/{removed}")
    assert deleted.status_code == 200, deleted.text
    catalog = client.get(base).json()
    assert {item["taxonomy_node_id"] for item in catalog["taxonomy_nodes"] if item["taxonomy_id"] == taxonomy_id} == {top, survivor}
    target_ids = {item["target_set_id"] for item in catalog["target_sets"]}
    assert {root_target, top_target}.issubset(target_ids)
    assert not {removed_target, child_target}.intersection(target_ids)
    lines = [item for item in catalog["target_set_lines"] if item["target_set_id"] == top_target]
    assert len(lines) == 1 and lines[0]["target_member_id"] == survivor and lines[0]["target_value"] == 0.6
    assert any(item["target_set_id"] == top_target for item in catalog["target_set_integrity_issues"])
    with get_session_factory()() as session:
        revisions = session.scalars(select(TaxonomyConfigurationRevisionModel).where(
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id)).all()
        assert len(revisions) == len(old_configurations) + 1
        for row in revisions:
            if row.taxonomy_configuration_revision_id in old_configurations:
                assert row.configuration_json == old_configurations[row.taxonomy_configuration_revision_id]
        calculation = session.get(PortfolioCalculationStateModel, portfolio_id)
        assert calculation.refresh_request_id != old_request_id
        assert calculation.daily_snapshot_status == "stale"
        assert calculation.dirty_from is None
        assert session.get(ResearchRunRecordModel, "saved-before-node-delete").detail_json == {"configuration_snapshot": saved_snapshot}
        settings = session.get(ResearchSettingsRecordModel, portfolio_id)
        assert settings.comparator_taxonomy_node_id is None
        assert settings.frozen_taxonomy_node_ids_json == [survivor]
        assert settings.top_sleeve_weight_bounds_json == [{"taxonomy_node_id": survivor, "max_weight": 0.8}]

    deleted = client.delete(f"{path}/nodes/{survivor}")
    assert deleted.status_code == 200, deleted.text
    catalog = client.get(base).json()
    assert next(item for item in catalog["taxonomy_nodes"] if item["taxonomy_node_id"] == top)["is_terminal"] is True
    assert not any(item["target_set_id"] == top_target for item in catalog["target_sets"])
    assert any(item["target_set_id"] == root_target for item in catalog["target_sets"])


def test_taxonomy_rejects_adding_child_under_assigned_node(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Sleeve Tree",
            "taxonomy_type": "risk_sleeve",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    node_id = node_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": node_id,
        },
    )
    assert assignment_response.status_code == 200

    child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Child Sleeve",
            "parent_taxonomy_node_id": node_id,
        },
    )
    assert child_response.status_code == 400
    assert "already has assignments" in child_response.json()["detail"]


def test_taxonomy_create_rejects_non_security_scope(client):
    response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Account Planning",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "account",
        },
    )
    assert response.status_code == 422
    assert "instrument" in str(response.json())








def test_taxonomy_update_node_and_assignment_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Editable Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Root One", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Root Two", "sort_order": 1},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Leaf", "parent_taxonomy_node_id": root_node_id, "sort_order": 2},
    )
    child_node_id = child_response.json()["taxonomy_node_id"]

    assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": child_node_id,
        },
    )
    assignment_id = assignment_response.json()["assignment_id"]

    update_taxonomy_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}",
        json={
            "name": "Editable Planning Axis 2",
            "purpose": "Updated purpose",
            "root_allocation_basis": "risk_budget",
        },
    )
    assert update_taxonomy_response.status_code == 200
    assert update_taxonomy_response.json()["name"] == "Editable Planning Axis 2"
    assert update_taxonomy_response.json()["purpose"] == "Updated purpose"
    assert update_taxonomy_response.json()["root_allocation_basis"] == "risk_budget"

    update_node_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes/{child_node_id}",
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
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments/{assignment_id}",
        json={
            "status": "archived",
        },
    )
    assert update_assignment_response.status_code == 200
    assert update_assignment_response.json()["status"] == "archived"

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
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
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Planning With Cash",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    cash_node_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={
            "node_name": "Liquidity Reserve",
            "allocation_basis": "risk_budget",
        },
    )
    assert cash_node_response.status_code == 200
    cash_node_payload = cash_node_response.json()
    assert cash_node_payload["allocation_basis"] == "risk_budget"

    update_node_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes/{cash_node_payload['taxonomy_node_id']}",
        json={"allocation_basis": "weight"},
    )
    assert update_node_response.status_code == 200
    assert update_node_response.json()["allocation_basis"] == "weight"

    assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": cash_node_payload["taxonomy_node_id"],
        },
    )
    assert assignment_response.status_code == 422
    assert "instrument" in str(assignment_response.json())


def test_target_set_rejects_non_normalized_weight_totals(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Levered Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "target_set_type": "saa",
            "name": "Levered Root Scope",
            "lines": [
                {
                    "taxonomy_node_id": first_child_id,
                    "target_value": 0.9,
                },
                {
                    "taxonomy_node_id": second_child_id,
                    "target_value": 0.3,
                },
            ],
        },
    )
    assert target_set_response.status_code == 400
    assert "sum to 100%" in str(target_set_response.json())


def test_target_set_rejects_non_normalized_risk_share_totals(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Risk Share Validation Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    first_child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve One", "sort_order": 0},
    )
    second_child_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Sleeve Two", "sort_order": 1},
    )
    first_child_id = first_child_response.json()["taxonomy_node_id"]
    second_child_id = second_child_response.json()["taxonomy_node_id"]

    target_set_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "target_set_type": "saa",
            "name": "Invalid Risk Share",
            "lines": [
                {
                    "taxonomy_node_id": first_child_id,
                    "target_value": 0.9,
                },
                {
                    "taxonomy_node_id": second_child_id,
                    "target_value": 0.3,
                },
            ],
        },
    )
    assert target_set_response.status_code == 400
    assert "sum to 100%" in target_set_response.json()["detail"]


def test_target_set_scope_uses_current_assignment_members(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Current Scope Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Leaf Sleeve", "sort_order": 0},
    )
    root_node_id = root_response.json()["taxonomy_node_id"]

    first_assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": root_node_id,

        },
    )
    assert first_assignment_response.status_code == 200

    second_assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": root_node_id,
        },
    )
    assert second_assignment_response.status_code == 200

    target_set_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Current Member Scope",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 0.4,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_value": 0.6,
                }
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
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
            "target_value": 0.4,
            "notes": None,
        },
        {
            "target_line_id": saved_lines[1]["target_line_id"],
            "target_set_id": target_set_response.json()["target_set_id"],
            "taxonomy_node_id": None,
            "target_member_type": "instrument",
            "target_member_id": "fund-us-agg",
            "target_value": 0.6,
            "notes": None,
        }
    ]


def test_taxonomy_catalog_reports_active_target_set_scope_drift(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Integrity Scope Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]
    sleeve_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Absolute Return", "sort_order": 0},
    )
    sleeve_id = sleeve_response.json()["taxonomy_node_id"]
    first_assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "equity-us-abbv",
            "taxonomy_node_id": sleeve_id,
        },
    )
    assert first_assignment_response.status_code == 200

    saa_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": sleeve_id,
            "target_set_type": "saa",
            "name": "Absolute Return SAA",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 1.0,
                }
            ],
        },
    )
    assert saa_response.status_code == 200
    taa_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": sleeve_id,
            "target_set_type": "taa",
            "name": "Archived Absolute Return TAA",
            "status": "archived",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 1.0,
                }
            ],
        },
    )
    assert taa_response.status_code == 200
    initially_valid_catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
    assert initially_valid_catalog["target_set_integrity_issues"] == []

    second_assignment_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
        json={
            "target_scope": "instrument",
            "target_entity_id": "fund-us-agg",
            "taxonomy_node_id": sleeve_id,
        },
    )
    assert second_assignment_response.status_code == 200

    drifted_catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
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
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets/{saa_response.json()['target_set_id']}",
        json={
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 0.5,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_value": 0.5,
                },
            ]
        },
    )
    assert repaired_response.status_code == 200
    repaired_catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
    assert repaired_catalog["target_set_integrity_issues"] == []








def test_target_set_create_update_and_catalog_round_trip(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    satellite_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Satellite", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    satellite_node_id = satellite_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    saa_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "target_set_type": "saa",
            "name": "Top Level SAA",
            "lines": [
                {
                    "taxonomy_node_id": core_node_id,
                    "target_value": 0.7,
                },
                {
                    "taxonomy_node_id": satellite_node_id,
                    "target_value": 0.3,
                },
            ],
        },
    )
    assert saa_response.status_code == 200
    saa_target_set_id = saa_response.json()["target_set_id"]

    taa_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "taa",
            "name": "Core Sleeve TAA",
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_value": 0.55,
                },
                {
                    "taxonomy_node_id": income_node_id,
                    "target_value": 0.45,
                },
            ],
        },
    )
    assert taa_response.status_code == 200

    update_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets/{saa_target_set_id}",
        json={
            "lines": [
                {
                    "taxonomy_node_id": core_node_id,
                    "target_value": 0.68,
                },
                {
                    "taxonomy_node_id": satellite_node_id,
                    "target_value": 0.32,
                },
            ],
        },
    )
    assert update_response.status_code == 200
    assert "effective_to" not in update_response.json()

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert len(catalog_payload["target_sets"]) == 2
    assert len(catalog_payload["target_set_lines"]) == 4
    updated_saa = next(item for item in catalog_payload["target_sets"] if item["target_set_id"] == saa_target_set_id)
    assert "effective_to" not in updated_saa
    root_lines = [item for item in catalog_payload["target_set_lines"] if item["target_set_id"] == saa_target_set_id]
    assert sum(item["target_value"] for item in root_lines) == pytest.approx(1.0)


def test_leaf_scope_target_set_accepts_only_assigned_instruments(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Leaf Member Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    root_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
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
    ]:
        assignment_response = client.post(
            f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/assignments",
            json={**payload},
        )
        assert assignment_response.status_code == 200

    invalid_cash_risk_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Invalid Leaf Sleeve Mix",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 0.45,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_value": 0.35,
                },
                {
                    "target_member_type": "cash_bucket",
                    "target_member_id": "cash-usd-main",
                    "target_value": 0.2,
                },
            ],
        },
    )
    assert invalid_cash_risk_response.status_code == 400
    assert "direct members of the selected scope" in invalid_cash_risk_response.json()["detail"]

    target_set_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": root_node_id,
            "target_set_type": "saa",
            "name": "Leaf Sleeve Mix",
            "lines": [
                {
                    "target_member_type": "instrument",
                    "target_member_id": "equity-us-abbv",
                    "target_value": 0.6,
                },
                {
                    "target_member_type": "instrument",
                    "target_member_id": "fund-us-agg",
                    "target_value": 0.4,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200

    catalog_response = client.get("/api/portfolios/investment-studio/taxonomies")
    assert catalog_response.status_code == 200
    saved_lines = [
        item
        for item in catalog_response.json()["target_set_lines"]
        if item["target_set_id"] == target_set_response.json()["target_set_id"]
    ]
    assert {item["target_member_type"] for item in saved_lines} == {"instrument"}
    assert sum(item["target_value"] for item in saved_lines) == pytest.approx(1.0)
    assert sum(
        item["target_value"] for item in saved_lines
    ) == pytest.approx(1.0)




def test_target_set_requires_full_scope_and_preserves_other_budgets_on_node_move(client):
    taxonomy_response = client.post(
        "/api/portfolios/investment-studio/taxonomies",
        json={
            "name": "Scoped Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
        },
    )
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    core_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Core", "sort_order": 0},
    )
    other_root_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Other Root", "sort_order": 1},
    )
    core_node_id = core_response.json()["taxonomy_node_id"]
    other_root_node_id = other_root_response.json()["taxonomy_node_id"]

    growth_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Growth", "parent_taxonomy_node_id": core_node_id, "sort_order": 2},
    )
    income_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes",
        json={"node_name": "Income", "parent_taxonomy_node_id": core_node_id, "sort_order": 3},
    )
    growth_node_id = growth_response.json()["taxonomy_node_id"]
    income_node_id = income_response.json()["taxonomy_node_id"]

    invalid_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "saa",
            "name": "Incomplete Scope",
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_value": 1.0,
                }
            ],
        },
    )
    assert invalid_response.status_code == 400
    assert "cover every direct member" in invalid_response.json()["detail"]

    valid_response = client.post(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/target-sets",
        json={
            "comparator_taxonomy_node_id": core_node_id,
            "target_set_type": "saa",
            "name": "Core Scope",
            "lines": [
                {
                    "taxonomy_node_id": growth_node_id,
                    "target_value": 0.6,
                },
                {
                    "taxonomy_node_id": income_node_id,
                    "target_value": 0.4,
                },
            ],
        },
    )
    assert valid_response.status_code == 200

    move_response = client.patch(
        f"/api/portfolios/investment-studio/taxonomies/{taxonomy_id}/nodes/{growth_node_id}",
        json={"parent_taxonomy_node_id": other_root_node_id},
    )
    assert move_response.status_code == 200, move_response.text
    catalog = client.get("/api/portfolios/investment-studio/taxonomies").json()
    lines = [line for line in catalog["target_set_lines"]
             if line["target_set_id"] == valid_response.json()["target_set_id"]]
    assert [(line["target_member_id"], line["target_value"]) for line in lines] == [(income_node_id, .4)]
    assert any(issue["target_set_id"] == valid_response.json()["target_set_id"]
               for issue in catalog["target_set_integrity_issues"])
