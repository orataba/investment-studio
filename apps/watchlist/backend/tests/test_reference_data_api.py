from types import SimpleNamespace
from copy import deepcopy
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from watchlist_app.api.routes import instruments
from watchlist_app.api.routes import funds


@pytest.mark.parametrize("route_instrument", ["asset-1", "unrelated"])
def test_current_reference_separates_nav_source_and_scan_from_old_reference(monkeypatch, route_instrument):
    from investment_studio_instrument_core.db_models import InstrumentReferenceSnapshot
    from watchlist_app.services import shared_instrument_registry as registry
    snapshot = {"fetched_at": "2026-09-04T10:00:00Z", "source": {"refresh_status": "no_new_data", "refresh_message": "old check",
        "market_data_updated_at": "2026-09-04T10:00:00Z"}, "sections": {"profile": {"name": "Original"}}}
    nav = {"metric_family": "nav", "quote_basis": "official_nav", "currency": "CNY",
        "as_of_date": "2026-10-09", "status": "complete", "provider": "email-route:20562",
        "nav_lineage": {"evidence": {"email_candidate_route_id": 20562, "email_folder": "jingxuan1",
            "email_attachment_name": "nav-20261009.xls", "email_sent_at": "2026-10-12T08:27:08Z",
            "private_header": "must not be projected"}}}
    instrument = {"instrument_id": "asset-1", "currency": "CNY", "instrument_type": "private_fund",
        "source_settings": {"source_location": "INBOX", "email_sender_whitelist": "private@example.test"},
        "refresh_status": {"status": "no_new_data", "requested_at": "2026-10-12T19:06:28Z"},
        "market_data_updated_at": "2026-10-12T13:48:09Z", "market_data": [nav,
            {**nav, "as_of_date": "2026-10-12", "quote_basis": "total_return_nav"},
            {**nav, "as_of_date": "2026-10-13", "currency": "USD"}]}
    def get(model, key):
        assert model is InstrumentReferenceSnapshot
        return SimpleNamespace(value_json=snapshot)
    def scalar(statement):
        from sqlalchemy.dialects import postgresql
        query = statement.compile(dialect=postgresql.dialect())
        assert 'data_ingestion.email_nav_candidate_route' in str(query)
        assert set(query.params.values()) == {20562, "asset-1"}
        return datetime(2026, 10, 12, 13, 48, 13, tzinfo=UTC) if route_instrument == "asset-1" else None
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: instrument)
    monkeypatch.setattr(registry, "get_session_factory", lambda: lambda: nullcontext(SimpleNamespace(
        get=get, scalar=scalar, get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))))
    result = registry.get_shared_reference_data("asset-1")
    assert result["fetched_at"] == snapshot["fetched_at"]
    assert result["sections"] == snapshot["sections"]
    assert result["source"]["source_location"] == "INBOX"
    assert "refresh_status" not in result["source"]
    assert "refresh_message" not in result["source"]
    assert "market_data_updated_at" not in result["source"]
    assert "email_sender_whitelist" not in result["source"]
    current = result["current_data"]
    assert current["last_checked_at"] == "2026-10-12T19:06:28Z"
    assert current["check_status"] == "no_new_data"
    assert current["latest_nav"] == {"as_of_date": "2026-10-09", "provider": "email-route:20562",
        "source_folder": "jingxuan1", "source_attachment": "nav-20261009.xls",
        "source_sent_at": "2026-10-12T08:27:08Z",
        "imported_at": "2026-10-12T13:48:13+00:00" if route_instrument == "asset-1" else None}
    assert snapshot["source"]["market_data_updated_at"] == "2026-09-04T10:00:00Z"


def test_historical_reference_retains_frozen_source_without_current_nav(monkeypatch):
    from watchlist_app.services import shared_instrument_registry as registry
    snapshot = {"source": {"market_data_updated_at": "2026-09-04T10:00:00Z"},
        "sections": {"profile": {"name": "Original"}, "sector_market_data": {"old": True}}}
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: {"instrument_id": "asset-1"})
    monkeypatch.setattr(registry, "get_session_factory", lambda: lambda: nullcontext(
        SimpleNamespace(scalar=lambda _: SimpleNamespace(value_json=snapshot))))
    result = registry.get_shared_reference_data("asset-1", as_of=datetime(2026, 9, 5, tzinfo=UTC))
    assert result["source"] == snapshot["source"]
    assert "current_data" not in result
    assert result["sections"] == {"profile": {"name": "Original"}}


def test_current_nav_remains_available_without_a_reference_snapshot(monkeypatch):
    from watchlist_app.services import shared_instrument_registry as registry
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: {
        "instrument_id": "asset-1", "instrument_type": "private_fund", "currency": "CNY",
        "market_data": [{"metric_family": "nav", "quote_basis": "official_nav", "currency": "CNY",
            "as_of_date": "2026-10-09", "status": "complete", "provider": "manual"}]})
    monkeypatch.setattr(registry, "get_session_factory", lambda: lambda: nullcontext(SimpleNamespace(get=lambda *_: None)))
    result = registry.get_shared_reference_data("asset-1")
    assert result["fetched_at"] is None
    assert result["current_data"]["latest_nav"]["as_of_date"] == "2026-10-09"
    assert result["current_data"]["latest_nav"]["imported_at"] is None
    assert result["current_data"]["last_checked_at"] is None


@pytest.mark.parametrize("provider,source", [
    ("datahub:tushare", {"field_semantics": {"holdings": {"provider": "datahub:tushare", "api": "fund_portfolio"}}}),
    ("fmp", {"storage": "studio_market", "section_sources": {"holdings": [{"source_id": "numeric:saved:0"}]}}),
    ("unknown", {}),
])
def test_reference_source_metadata_survives_current_configuration_changes(monkeypatch, provider, source):
    from watchlist_app.services import shared_instrument_registry as registry
    snapshot = {"provider": provider, "fetched_at": "2026-09-04T10:00:00Z",
        "source": {**source, "source_api_profile": provider, "refresh_status": "old"},
        "sections": {"holdings": [{"weightPercentage": 5.2}]}}
    before = deepcopy(snapshot)
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: {"instrument_id": "asset-1",
        "source_settings": {"source_api_profile": "new-configuration", "source_location": "current"}})
    monkeypatch.setattr(registry, "get_session_factory", lambda: lambda: nullcontext(SimpleNamespace(
        get=lambda *_: SimpleNamespace(value_json=snapshot), scalar=lambda _: SimpleNamespace(value_json=snapshot))))
    current = registry.get_shared_reference_data("asset-1")
    assert current["provider"] == provider
    assert current["source"]["source_api_profile"] == "new-configuration"
    for key, value in source.items():
        assert current["source"][key] == value
    assert "refresh_status" not in current["source"]
    if provider != "datahub:tushare":
        assert "field_semantics" not in current["source"]
    historical = registry.get_shared_reference_data("asset-1", as_of=datetime(2026, 9, 5, tzinfo=UTC))
    assert historical == before
    assert snapshot == before


def test_reference_data_uses_the_watchlist_read_api(client, monkeypatch):
    snapshot = {"instrument_id": "asset-1", "sections": {"profile": {"name": "Example"}}}
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: snapshot)
    response = client.get("/api/instruments/asset-1/reference-data")
    assert response.status_code == 200
    assert response.json() == snapshot
    monkeypatch.setattr(instruments, "get_shared_reference_data", lambda _: None)
    assert client.get("/api/instruments/missing/reference-data").status_code == 404


@pytest.mark.parametrize("reported,expected", [(504, "504"), (None, "—")])
def test_etf_summary_uses_reported_reference_count_not_saved_holding_rows(monkeypatch, reported, expected):
    instrument = SimpleNamespace(instrument_type="etf", metadata_json={})
    monkeypatch.setattr(funds, "_require_instrument", lambda *args: instrument)
    monkeypatch.setattr(funds, "_schedule_instrument_refresh", lambda *args, **kwargs: None)
    monkeypatch.setattr(funds, "recalculation_freshness_overrides", lambda *args, **kwargs: {})
    monkeypatch.setattr(funds.read_model_repository, "get_summary", lambda *args: SimpleNamespace(payload_json={"key_stats": [{"label": "Holdings", "value": "—"}]}))
    monkeypatch.setattr(funds.attribute_repository, "get_values_for_asset", lambda *args: [])
    monkeypatch.setattr(funds.taxonomy_repository, "get_assignment", lambda *args, **kwargs: None)
    session = SimpleNamespace(get=lambda *args: SimpleNamespace(value_json={"provider": "fmp", "sections": {"fund_info": {"holdingsCount": reported}, "holdings": [{}] * 100}}))
    response = funds.get_instrument_summary("spy", session)
    assert response["key_stats"] == [{"label": "Holdings", "value": expected}]
