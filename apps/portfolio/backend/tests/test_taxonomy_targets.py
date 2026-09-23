"""One scalar target contract shared by classification, risk and Research."""
from copy import deepcopy

import pytest

from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets


@pytest.fixture(autouse=True)
def isolated_portfolio_store():
    """This pure configuration projection never opens a database."""
    yield


def configuration(*, root_basis="risk_budget", child_basis="risk_budget"):
    return {
        "taxonomy": {"taxonomy_id": "tax", "root_allocation_basis": root_basis},
        "taxonomy_nodes": [
            {"taxonomy_node_id": "a", "node_name": "A", "allocation_basis": child_basis},
            {"taxonomy_node_id": "b", "node_name": "B", "allocation_basis": "weight"},
        ],
        "taxonomy_assignments": [
            {"taxonomy_node_id": node, "target_scope": "instrument", "target_entity_id": instrument}
            for node, instrument in [("a", "x"), ("a", "y"), ("b", "z")]
        ],
        "target_sets": [{"target_set_id": "root", "target_set_type": "saa"},
                        {"target_set_id": "child", "target_set_type": "saa", "comparator_taxonomy_node_id": "a"}],
        "target_set_lines": [
            {"target_set_id": set_id, "target_member_type": kind, "target_member_id": member, "target_value": value}
            for set_id, kind, member, value in [("root", "taxonomy_node", "a", .6), ("root", "taxonomy_node", "b", .4),
                                               ("root", "cash_bucket", "__cash__", .2),
                                               ("child", "instrument", "x", .25), ("child", "instrument", "y", .75)]
        ],
    }


def members(result):
    return {row["member_id"]: row for row in result["member_targets"]}


def test_effective_tactical_inherits_a_whole_scope_and_global_budget_preserves_products():
    config = configuration()
    original = deepcopy(config)
    result = resolve_taxonomy_targets(config)
    assert config == original
    assert result["errors"] == []
    rows = members(result)
    assert rows["x"]["tactical_source"] == "saa"
    assert rows["x"]["tactical_value"] == .25
    assert rows["x"]["tactical_global_risk_target"] == pytest.approx(.15)
    assert rows["y"]["tactical_global_risk_target"] == pytest.approx(.45)
    assert rows["z"]["tactical_global_risk_target"] == pytest.approx(.4)
    assert rows["__cash__"]["target_basis"] == "weight"
    assert rows["__cash__"]["tactical_value"] == .2
    assert rows["__cash__"]["tactical_global_risk_target"] is None


def test_partial_tactical_does_not_fall_back_to_complete_strategic_or_mix_members():
    config = configuration()
    config["target_sets"].append({"target_set_id": "taa", "target_set_type": "taa"})
    config["target_set_lines"].append({"target_set_id": "taa", "target_member_type": "taxonomy_node", "target_member_id": "a", "target_value": .8})
    result = resolve_taxonomy_targets(config)
    root = result["scope_targets"][0]
    assert root["taa"]["status"] == "invalid"
    assert root["taa"]["source_stage"] == "taa"
    assert root["taa"]["inherited"] is False
    assert members(result)["b"]["tactical_value"] is None
    assert members(result)["a"]["tactical_global_risk_target"] is None


def test_tactical_all_empty_inherits_but_explicit_zero_is_not_missing():
    config = configuration()
    config["target_sets"].append({"target_set_id": "taa", "target_set_type": "taa"})
    empty = resolve_taxonomy_targets(config)
    assert empty["scope_targets"][0]["taa"]["inherited"] is True
    config["target_set_lines"].extend([
        {"target_set_id": "taa", "target_member_type": "taxonomy_node", "target_member_id": key, "target_value": value}
        for key, value in [("a", 0.), ("b", 1.)]
    ])
    result = resolve_taxonomy_targets(config)
    assert result["errors"] == []
    assert members(result)["a"]["tactical_value"] == 0.
    assert members(result)["x"]["tactical_global_risk_target"] == 0.


@pytest.mark.parametrize("root_basis,child_basis,expected_a", [("weight", "risk_budget", None), ("risk_budget", "weight", .6)])
def test_weight_branch_breaks_global_risk_target_even_when_risk_resumes_below(root_basis, child_basis, expected_a):
    result = resolve_taxonomy_targets(configuration(root_basis=root_basis, child_basis=child_basis))
    rows = members(result)
    assert rows["a"]["tactical_global_risk_target"] == expected_a
    assert rows["x"]["tactical_global_risk_target"] is None
    assert rows["x"]["tactical_value"] == .25


def test_cash_is_nav_reserve_outside_weight_vector_and_all_cash_is_explicit():
    config = configuration(root_basis="weight")
    result = resolve_taxonomy_targets(config)
    assert result["errors"] == []
    for line in config["target_set_lines"]:
        if line["target_set_id"] == "root":
            line["target_value"] = 1. if line["target_member_type"] == "cash_bucket" else 0.
    result = resolve_taxonomy_targets(config)
    assert result["errors"] == []
    assert all(row["tactical_global_risk_target"] is None for row in result["member_targets"])


def test_cash_only_tactical_cannot_silently_inherit_strategic_security_allocation():
    config = configuration()
    config["target_sets"].append({"target_set_id": "taa", "target_set_type": "taa"})
    config["target_set_lines"].append({"target_set_id": "taa", "target_member_type": "cash_bucket", "target_member_id": "__cash__", "target_value": .3})
    assert resolve_taxonomy_targets(config)["scope_targets"][0]["taa"]["status"] == "invalid"


def test_multiple_active_vectors_are_ambiguous_instead_of_selected_by_id():
    config = configuration()
    config["target_sets"].append({"target_set_id": "root-new", "target_set_type": "saa"})
    result = resolve_taxonomy_targets(config)
    assert result["scope_targets"][0]["saa"]["status"] == "invalid"
    assert "multiple active" in result["errors"][0]


def test_selected_research_scope_budget_is_explicitly_relative_to_that_scope():
    result = resolve_taxonomy_targets(configuration(), scope_node_id="a")
    assert set(members(result)) == {"x", "y"}
    assert members(result)["x"]["tactical_global_risk_target"] == .25
