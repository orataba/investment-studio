from __future__ import annotations

from fastapi.testclient import TestClient

from platform_app.main import app


def test_health_reports_email_readiness(monkeypatch) -> None:
    from platform_app.api.routes import health

    class StubSettings:
        app_name = "Yungu Platform API"
        environment = "test"
        email_sync_enabled = True
        email_sync_ready = False
        email_imap_folder = "NAV"
        email_imap_use_ssl = True
        email_imap_max_messages = 20
        email_imap_host = None
        email_imap_username = None
        email_imap_password = None
        tushare_ready = False
        tushare_api_url = "https://api.tushare.pro"

    monkeypatch.setattr(health, "get_settings", lambda: StubSettings())

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["environment"] == "test"
    assert payload["email_sync"] == {
        "enabled": True,
        "ready": False,
        "imap_folder": "NAV",
        "imap_use_ssl": True,
        "max_messages": 20,
        "missing_required_settings": [
            "YUNGU_PLATFORM_EMAIL_IMAP_HOST",
            "YUNGU_PLATFORM_EMAIL_IMAP_USERNAME",
            "YUNGU_PLATFORM_EMAIL_IMAP_PASSWORD",
        ],
    }
    assert payload["tushare_sync"] == {
        "ready": False,
        "api_url": "https://api.tushare.pro",
    }
