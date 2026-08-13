from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from platform_app.main import app


def test_health_reports_email_readiness(monkeypatch) -> None:
    from platform_app.api.routes import health

    class StubSettings:
        app_name = "Portfolio Operations Platform API"
        environment = "test"
        email_sync_enabled = True
        email_sync_ready = False
        email_imap_folders = ["INBOX", "NAV"]
        email_imap_use_ssl = True
        email_history_start_date = date(2025, 12, 26)
        email_header_fetch_batch_size = 200
        email_message_fetch_batch_size = 20
        email_ingestion_lease_seconds = 1800
        email_imap_host = None
        email_imap_username = None
        email_imap_password = None
        datahub_ready = False
        datahub_tushare_api_url = (
            "http://datahubco.com/app-api/openapi/v1/tushare"
        )

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
        "imap_folders": ["INBOX", "NAV"],
        "imap_use_ssl": True,
        "history_start_date": "2025-12-26",
        "header_fetch_batch_size": 200,
        "message_fetch_batch_size": 20,
        "ingestion_lease_seconds": 1800,
        "missing_required_settings": [
            "PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_HOST",
            "PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_USERNAME",
            "PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD",
        ],
    }
    assert payload["datahub_tushare_sync"] == {
        "ready": False,
        "api_url": "http://datahubco.com/app-api/openapi/v1/tushare",
    }
