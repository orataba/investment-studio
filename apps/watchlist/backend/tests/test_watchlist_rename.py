import pytest


def test_rename_preserves_identity_members_views_and_description(client):
    created = client.post("/api/watchlists", json={"name": "Original", "description": "Keep this"}).json()
    path = f"/api/watchlists/{created['watchlist_id']}"
    added = client.post(f"{path}/items", json={"instrument_ids": ["savf63"]})
    assert added.status_code == 200, added.text
    before = client.get(path).json()
    renamed = client.patch(path, json={"name": "  重点研究  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "重点研究"
    after = client.get(path).json()
    assert after == {**before, "name": "重点研究"}
    assert next(row for row in client.get("/api/watchlists").json()
                if row["watchlist_id"] == created["watchlist_id"])["name"] == "重点研究"


@pytest.mark.parametrize("name", ["", " \t ", "名" * 201])
def test_create_and_rename_reject_invalid_names_without_changing_saved_name(client, name):
    record = client.post("/api/watchlists", json={"name": "Original"}).json()
    assert client.post("/api/watchlists", json={"name": name}).status_code == 400
    path = f"/api/watchlists/{record['watchlist_id']}"
    assert client.patch(path, json={"name": name}).status_code == 400
    assert client.get(path).json()["name"] == "Original"


def test_system_lists_and_unknown_lists_cannot_be_renamed(client):
    system_lists = [row for row in client.get("/api/watchlists").json() if row["owner_type"] == "system"]
    for row in system_lists:
        assert client.patch(f"/api/watchlists/{row['watchlist_id']}", json={"name": "Override"}).status_code == 400
    assert client.patch("/api/watchlists/not-found", json={"name": "Missing"}).status_code == 404


def test_initial_personal_view_override_rejects_duplicate_visible_name(client):
    record = client.post("/api/watchlists", json={"name": "Views"}).json()
    path = f"/api/watchlists/{record['watchlist_id']}"
    initial = client.get(path).json()
    assert client.post(f"{path}/views", json={"name": "My view"}).status_code == 200
    updated = client.put(f"{path}/views/{initial['default_view_id']}", json={"name": "  MY VIEW "})
    assert updated.status_code == 409
    assert client.get(path).json()["default_view_id"] == initial["default_view_id"]
