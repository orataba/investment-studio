from fastapi.testclient import TestClient

from watchlist_app.api.routes import instruments
from watchlist_app.main import app


def test_reference_data_uses_the_watchlist_read_api(monkeypatch):
    snapshot = {"instrument_id": "asset-1", "sections": {"profile": {"name": "Example"}}}
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: snapshot)
    response = TestClient(app).get("/api/instruments/asset-1/reference-data")
    assert response.status_code == 200
    assert response.json() == snapshot
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: None)
    assert TestClient(app).get("/api/instruments/missing/reference-data").status_code == 404
