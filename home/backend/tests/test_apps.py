import json

from fastapi.testclient import TestClient

from home_api.core.settings import Settings
from home_api.main import app


def test_home_uses_catalog_and_deployment_url_overrides(monkeypatch):
    from home_api.api.routes import apps

    settings = Settings(app_urls={"regime": "https://regime.example.test"})
    monkeypatch.setattr(apps, "get_settings", lambda: settings)
    payload = TestClient(app).get("/api/apps").json()
    assert payload["product_name"] == "Investment Studio"
    assert [item["app_id"] for item in payload["apps"]] == ["watchlist", "portfolio", "regime"]
    assert payload["apps"][2]["url"] == "https://regime.example.test"


def test_apps_can_be_added_and_removed_without_route_changes(tmp_path, monkeypatch):
    from home_api.api.routes import apps

    catalog = tmp_path / "apps.json"
    settings = Settings(apps_file=catalog)
    monkeypatch.setattr(apps, "get_settings", lambda: settings)
    catalog.write_text(json.dumps([{
        "app_id": "research", "name": "Research", "url": "https://research.example.test",
        "eyebrow": "Research", "description": "An independently maintained app.",
    }]))
    client = TestClient(app)
    assert [item["app_id"] for item in client.get("/api/apps").json()["apps"]] == ["research"]
    catalog.write_text("[]")
    assert client.get("/api/apps").json()["apps"] == []
