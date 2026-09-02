from __future__ import annotations

from fastapi.testclient import TestClient

from platform_app.main import app


def test_homepage_apps_include_regime(monkeypatch) -> None:
    from platform_app.api.routes import apps

    class StubSettings:
        watchlist_url = "https://watchlist.example.test"
        portfolio_url = "https://portfolio.example.test"
        regime_url = "https://regime.example.test"
        watchlist_api_url = "http://127.0.0.1:8100"
        portfolio_api_url = "http://127.0.0.1:8101"

    monkeypatch.setattr(apps, "get_settings", lambda: StubSettings())

    response = TestClient(app).get("/api/apps")

    assert response.status_code == 200
    assert [item["app_id"] for item in response.json()["apps"]] == [
        "watchlist",
        "portfolio",
        "database_dashboard",
        "regime",
    ]
    assert response.json()["apps"][3]["url"] == "https://regime.example.test"
