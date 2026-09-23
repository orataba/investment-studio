from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from portfolio_app.db.models import ConcentrationPolicyRevisionModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.concentration_settings import read_concentration_settings
from portfolio_app.services.portfolio_store import copy_portfolio


PORTFOLIO_ID = "investment-studio"
TAXONOMIES_PATH = f"/api/portfolios/{PORTFOLIO_ID}/taxonomies"
SETTINGS_PATH = f"/api/portfolios/{PORTFOLIO_ID}/concentration/settings"


def _taxonomy(client):
    response = client.post(TAXONOMIES_PATH, json={"name": "Industry"})
    assert response.status_code == 200, response.text
    return response.json()["taxonomy_id"]


def _node(client, taxonomy_id, *, parent_id=None, name="Technology"):
    response = client.post(f"{TAXONOMIES_PATH}/{taxonomy_id}/nodes", json={
        "node_name": name, "parent_taxonomy_node_id": parent_id,
    })
    assert response.status_code == 200, response.text
    return response.json()["taxonomy_node_id"]


def _limit_row(client, taxonomy_id, node_id):
    response = client.get(f"/api/portfolios/{PORTFOLIO_ID}/concentration", params={"as_of_date": "2026-04-15"})
    assert response.status_code == 200, response.text
    scope = next(item for item in response.json()["scopes"] if item["taxonomy_id"] == taxonomy_id)
    return next(item for item in scope["rows"] if item["entity_id"] == node_id)


@pytest.mark.parametrize("replace_taxonomy", [False, True], ids=["node", "taxonomy"])
def test_recreated_classification_does_not_rebind_saved_concentration_limits(client, replace_taxonomy):
    old_taxonomy = _taxonomy(client)
    old_node = _node(client, old_taxonomy)
    response = client.put(SETTINGS_PATH, json={
        "expected_revision": 0,
        "effective_from": "2026-04-01",
        "enabled_taxonomy_ids": [old_taxonomy],
        "limits": [
            {"scope": "taxonomy", "taxonomy_id": old_taxonomy,
             "entity_id": old_node, "limit_weight": 0.1},
        ],
        "fcn_allocations": [],
    })
    assert response.status_code == 200, response.text
    saved_settings = response.json()
    with get_session_factory()() as session:
        original_revision = deepcopy(session.get(ConcentrationPolicyRevisionModel, (PORTFOLIO_ID, 1)).settings_json)
    assert _limit_row(client, old_taxonomy, old_node)["limit_weight"] == pytest.approx(0.1)

    delete_path = (f"{TAXONOMIES_PATH}/{old_taxonomy}" if replace_taxonomy
                   else f"{TAXONOMIES_PATH}/{old_taxonomy}/nodes/{old_node}")
    deleted = client.delete(delete_path)
    assert deleted.status_code == 200, deleted.text
    new_taxonomy = _taxonomy(client) if replace_taxonomy else old_taxonomy
    new_node = _node(client, new_taxonomy)
    assert new_node != old_node
    if replace_taxonomy:
        assert new_taxonomy != old_taxonomy
    new_row = _limit_row(client, new_taxonomy, new_node)
    assert new_row["limit_weight"] is None

    assert client.get(SETTINGS_PATH).json() == saved_settings
    assert read_concentration_settings(PORTFOLIO_ID, as_of_date=date(2026, 4, 15)) == saved_settings
    with get_session_factory()() as session:
        revisions = list(session.scalars(select(ConcentrationPolicyRevisionModel).where(
            ConcentrationPolicyRevisionModel.portfolio_id == PORTFOLIO_ID)))
        assert len(revisions) == 1
        assert revisions[0].settings_json == original_revision


def test_copy_portfolio_creates_distinct_taxonomy_and_node_identities_with_valid_parent_links(client):
    source_taxonomy = _taxonomy(client)
    source_parent = _node(client, source_taxonomy, name="Equities")
    source_child = _node(client, source_taxonomy, parent_id=source_parent)
    copied = copy_portfolio(PORTFOLIO_ID)
    assert copied is not None
    response = client.get(f"/api/portfolios/{copied['portfolio_id']}/taxonomies")
    assert response.status_code == 200, response.text
    catalog = response.json()
    copied_taxonomy = next(item for item in catalog["taxonomies"] if item["name"] == "Industry")
    assert copied_taxonomy["taxonomy_id"] != source_taxonomy
    copied_nodes = {item["node_name"]: item for item in catalog["taxonomy_nodes"]
                    if item["taxonomy_id"] == copied_taxonomy["taxonomy_id"]}
    assert {item["taxonomy_node_id"] for item in copied_nodes.values()}.isdisjoint({source_parent, source_child})
    assert copied_nodes["Technology"]["parent_taxonomy_node_id"] == copied_nodes["Equities"]["taxonomy_node_id"]
