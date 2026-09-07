from fastapi.testclient import TestClient
from types import SimpleNamespace

import pytest

from watchlist_app.api.routes import instruments
from watchlist_app.api.routes import funds
from watchlist_app.main import app


def test_reference_data_uses_the_watchlist_read_api(monkeypatch):
    snapshot = {"instrument_id": "asset-1", "sections": {"profile": {"name": "Example"}}}
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: snapshot)
    response = TestClient(app).get("/api/instruments/asset-1/reference-data")
    assert response.status_code == 200
    assert response.json() == snapshot
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: None)
    assert TestClient(app).get("/api/instruments/missing/reference-data").status_code == 404


@pytest.mark.parametrize("reported,expected", [(504, "504"), (None, "—")])
def test_etf_summary_uses_reported_reference_count_not_saved_holding_rows(monkeypatch, reported, expected):
    instrument = SimpleNamespace(instrument_type="etf", metadata_json={})
    monkeypatch.setattr(funds, "_require_instrument", lambda *args: instrument)
    monkeypatch.setattr(funds, "_schedule_instrument_refresh", lambda *args, **kwargs: None)
    monkeypatch.setattr(funds.read_model_repository, "get_summary", lambda *args: SimpleNamespace(payload_json={"key_stats": [{"label": "Holdings", "value": "—"}]}))
    monkeypatch.setattr(funds.attribute_repository, "get_values_for_asset", lambda *args: [])
    monkeypatch.setattr(funds.taxonomy_repository, "get_assignment", lambda *args, **kwargs: None)
    session = SimpleNamespace(get=lambda *args: SimpleNamespace(value_json={"provider": "fmp", "sections": {"fund_info": {"holdingsCount": reported}, "holdings": [{}] * 100}}))
    response = funds.get_instrument_summary("spy", session)
    assert response["key_stats"] == [{"label": "Holdings", "value": expected}]
