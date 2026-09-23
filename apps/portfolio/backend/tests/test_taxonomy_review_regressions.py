from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from portfolio_app.db.models import ConcentrationPolicyRevisionModel, PortfolioInstrumentUniverseRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_store import copy_portfolio
from portfolio_app.services.taxonomy_configuration import current_taxonomy_configuration, taxonomy_configuration_version
from .test_taxonomy_configuration import PORTFOLIO_ID, _create_planning_tree


def _target(client, base, scope, members):
    response = client.post(f"{base}/target-sets", json={
        "comparator_taxonomy_node_id": scope, "target_set_type": "saa", "name": "Strategic",
        "lines": [{"target_member_type": kind, "target_member_id": member, "target_value": value}
                  for kind, member, value in members],
    })
    assert response.status_code == 200, response.text
    return response.json()["target_set_id"]


def test_stale_target_editor_cannot_overwrite_new_targets_or_save_its_limits(client):
    tree = _create_planning_tree(client, name="Concurrent targets")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    version = taxonomy_configuration_version(PORTFOLIO_ID)
    draft = {"expected_configuration_version": version, "root_allocation_basis": "risk_budget"}
    assert client.put(f"{base}/target-configuration", json=draft).status_code == 200
    committed = current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id'])
    draft.update(root_allocation_basis="weight", concentration={
        "expected_revision": 0, "effective_from": "2026-04-15", "enabled_taxonomy_ids": [tree['taxonomy_id']],
        "limits": [{"scope": "taxonomy", "taxonomy_id": tree['taxonomy_id'], "entity_id": tree['parent_id'], "limit_weight": .5}],
    })
    response = client.put(f"{base}/target-configuration", json=draft)
    assert response.status_code == 409, response.text
    assert current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id']) == committed
    assert client.get(f"/api/portfolios/{PORTFOLIO_ID}/concentration/settings").json()["revision"] == 0


@pytest.mark.parametrize("operation", ["move", "remove"])
def test_departing_last_instrument_removes_uneditable_targets_and_retains_audit(client, operation):
    tree = _create_planning_tree(client, name="Membership cleanup")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    target_id = _target(client, base, tree['child_id'], [("instrument", "equity-us-abbv", 1)])
    before = current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id'])
    endpoint = f"{base}/assignments/{tree['assignment_id']}"
    response = (client.patch(endpoint, json={"taxonomy_node_id": tree['sibling_id']})
                if operation == "move" else client.delete(endpoint))
    assert response.status_code == 200, response.text
    after = current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id'])
    assert all(item['target_set_id'] != target_id for item in after['target_sets'])
    from portfolio_app.db.models import TaxonomyConfigurationRevisionModel
    with get_session_factory()() as session:
        original = session.get(TaxonomyConfigurationRevisionModel, before['taxonomy_configuration_revision_id'])
        assert original.configuration_json['target_set_lines'][0]['target_value'] == 1


def test_move_configured_sleeve_keeps_its_internal_targets_and_other_budgets(client):
    tree = _create_planning_tree(client, name="Move configured sleeve")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    root = client.post(f"{base}/nodes", json={"node_name": "Destination"}).json()['taxonomy_node_id']
    _target(client, base, tree['parent_id'], [("taxonomy_node", tree['child_id'], .4), ("taxonomy_node", tree['sibling_id'], .6)])
    internal = _target(client, base, tree['child_id'], [("instrument", "equity-us-abbv", 1)])
    response = client.patch(f"{base}/nodes/{tree['child_id']}", json={"parent_taxonomy_node_id": root})
    assert response.status_code == 200, response.text
    after = current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id'])
    assert any(row['target_set_id'] == internal for row in after['target_sets'])
    assert {(row['target_member_id'], row['target_value']) for row in after['target_set_lines']} == {
        (tree['sibling_id'], .6), ('equity-us-abbv', 1),
    }


def test_removing_last_root_sleeve_cleans_root_targets(client):
    tree = _create_planning_tree(client, name="Remove final root")
    base = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/{tree['taxonomy_id']}"
    _target(client, base, None, [("taxonomy_node", tree['parent_id'], 1)])
    assert client.delete(f"{base}/nodes/{tree['parent_id']}").status_code == 200
    after = current_taxonomy_configuration(PORTFOLIO_ID, tree['taxonomy_id'])
    assert after['target_sets'] == [] and after['target_set_lines'] == []


def test_copy_keeps_dated_limits_future_schedule_deleted_identities_and_manual_candidates(client):
    tree = _create_planning_tree(client, name="Copy limits")
    old_taxonomy, old_node = "deleted-taxonomy", "deleted-node"
    settings = {"schema_version": 2, "enabled_taxonomy_ids": [tree['taxonomy_id'], old_taxonomy],
        "limits": [
            {"scope": "taxonomy", "taxonomy_id": tree['taxonomy_id'], "entity_id": tree['parent_id'], "limit_weight": .5},
            {"scope": "taxonomy", "taxonomy_id": old_taxonomy, "entity_id": old_node, "limit_weight": .2},
            {"scope": "security", "taxonomy_id": None, "entity_id": "equity-us-abbv", "limit_weight": .1},
        ], "fcn_allocations": [], "migration_audit": {"original_settings": {"original": True}}}
    candidate_response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/taxonomies/instrument-universe",
                                     json={"instrument_id": "fund-us-watch"})
    assert candidate_response.status_code == 200, candidate_response.text
    with get_session_factory()() as session:
        for revision, effective in [(1, date(2026, 4, 1)), (2, date(2027, 1, 1))]:
            session.add(ConcentrationPolicyRevisionModel(portfolio_id=PORTFOLIO_ID, revision=revision,
                effective_from=effective, settings_json=deepcopy(settings), created_by="Reviewer", created_at="2026-04-01"))
        session.commit()
    copied = copy_portfolio(PORTFOLIO_ID)
    copy_id = copied['portfolio_id']
    catalog = client.get(f"/api/portfolios/{copy_id}/taxonomies").json()
    taxonomy_id = next(row['taxonomy_id'] for row in catalog['taxonomies'] if row['name'] == 'Copy limits')
    parent_id = next(row['taxonomy_node_id'] for row in catalog['taxonomy_nodes'] if row['taxonomy_id'] == taxonomy_id and row['node_name'] == 'Market Assets')
    with get_session_factory()() as session:
        revisions = session.scalars(select(ConcentrationPolicyRevisionModel).where(
            ConcentrationPolicyRevisionModel.portfolio_id == copy_id).order_by(ConcentrationPolicyRevisionModel.revision)).all()
        assert [row.effective_from for row in revisions] == [date(2026, 4, 1), date(2027, 1, 1)]
        assert revisions[0].settings_json == revisions[1].settings_json
        result = revisions[0].settings_json
        assert result['copied_from_portfolio_id'] == PORTFOLIO_ID
        assert result['limits'][0]['taxonomy_id'] == taxonomy_id
        assert result['limits'][0]['entity_id'] == parent_id
        assert result['limits'][1]['taxonomy_id'] not in {old_taxonomy, tree['taxonomy_id']}
        assert result['limits'][1]['entity_id'] != old_node
        assert result['limits'][2] == settings['limits'][2]
        assert result['migration_audit'] == settings['migration_audit']
        assert session.get(ConcentrationPolicyRevisionModel, (PORTFOLIO_ID, 1)).settings_json == settings
        assert session.get(PortfolioInstrumentUniverseRecordModel, (copy_id, "fund-us-watch")).source == "manual"
