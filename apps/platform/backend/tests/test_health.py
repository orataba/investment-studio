from __future__ import annotations

from fastapi.testclient import TestClient

from platform_app.main import app


def test_health_is_a_database_independent_liveness_check(monkeypatch) -> None:
    from platform_app.api.routes import health
    from platform_app.services import readiness as readiness_service

    class StubSettings:
        app_name = "Portfolio Operations Platform API"
        environment = "test"

    monkeypatch.setattr(health, "get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        readiness_service,
        "_read_instrument_registry_database_heads",
        lambda session: (_ for _ in ()).throw(
            AssertionError("health must not inspect the database")
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "Portfolio Operations Platform API",
        "environment": "test",
    }
