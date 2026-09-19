from fastapi.testclient import TestClient

from home_api.main import app
from test_auth import identity, client_as


def test_browser_diagnostics_require_session_origin_and_safe_shape(identity, monkeypatch):
    from home_api.api.routes import diagnostics
    events = []
    monkeypatch.setattr(diagnostics, "emit", lambda event, **fields: events.append((event, fields)))
    payload = {"app": "portfolio", "events": [{"kind": "request", "path": "/api/portfolios", "duration_ms": 40,
                                              "request_id": "original-request", "status": 200}]}
    anonymous = TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})
    assert anonymous.post("/api/diagnostics/events", json=payload).status_code == 401
    client = client_as()
    assert client.post("/api/diagnostics/events", json=payload, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert client.post("/api/diagnostics/events", json=payload).status_code == 204
    assert events[-1][1]["client_request_id"] == "original-request"
    assert events[-1][1]["user_id"]
    payload["events"][0]["path"] = "/activate?token=private"
    assert client.post("/api/diagnostics/events", json=payload).status_code == 422
    payload["events"][0]["path"] = "/api/portfolios"
    payload["events"][0]["body"] = "private data"
    assert client.post("/api/diagnostics/events", json=payload).status_code == 422
