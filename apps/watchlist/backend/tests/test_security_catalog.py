"""Search is read-only; registration requires an explicit team member request."""
import pytest
from studio_identity import Principal

from investment_studio_instrument_core.security_catalog import SecurityCatalogError, SecuritySearchResponse
from watchlist_app.api.routes import securities
from tests.conftest import TEST_SHARED_INSTRUMENTS
from tests.test_account_research_access import accounts, as_user  # noqa: F401


PAYLOAD = {"instrument_type": "etf", "catalog_provider": "fmp", "catalog_symbol": "SHV"}
RESULT = {**PAYLOAD, "symbol": "SHV", "name": "iShares Short Treasury Bond ETF",
          "exchange_code": "XNAS", "exchange_label": "NASDAQ", "market": "US", "currency": "USD"}


def test_identity_search_uses_the_lightweight_supported_registry_scope(accounts, monkeypatch):
    from watchlist_app.services import shared_instrument_registry
    from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES
    client, _, _ = accounts
    as_user(client, "reader")
    calls = []
    monkeypatch.setattr(shared_instrument_registry.shared_store, "search_instrument_identities",
                        lambda factory, **kwargs: calls.append(kwargs) or [{"instrument_id": "GOOGL"}])
    response = client.get("/api/instruments/search", params={"q": "GOOGL", "limit": 12})
    assert response.status_code == 200, response.text
    assert response.json() == [{"instrument_id": "GOOGL"}]
    assert calls == [{"search": "GOOGL", "limit": 12, "instrument_types": LOCAL_DETAIL_INSTRUMENT_TYPES}]


def test_directory_search_is_available_to_readers_without_registering(accounts, monkeypatch):
    client, _, _ = accounts
    as_user(client, "reader")
    calls = []
    def search(query, limit, *, session_factory):
        assert session_factory is securities.get_session_factory()
        calls.append((query, limit))
        return SecuritySearchResponse(results=[RESULT], catalog_errors={"equity": "Catalog unavailable"})
    monkeypatch.setattr(securities, "search_catalog", search)
    monkeypatch.setattr(securities, "materialize_catalog_security", lambda _: pytest.fail("search registered a security"))
    response = client.get("/api/securities/search", params={"q": " SHV ", "limit": 25})
    assert response.status_code == 200, response.text
    assert calls == [("SHV", 25)]
    assert response.json()["results"][0]["existing_instrument_id"] is None
    assert response.json()["catalog_errors"] == {"equity": "Catalog unavailable"}
    assert client.get("/api/securities/search", params={"q": " "}).status_code == 422


def test_explicit_registration_returns_canonical_record_and_can_join_list(accounts, monkeypatch):
    client, _, _ = accounts
    as_user(client, "alice")
    calls = []
    monkeypatch.setattr(securities, "materialize_catalog_security",
                        lambda payload: calls.append(payload.model_dump()) or TEST_SHARED_INSTRUMENTS["fund-us-agg"])
    response = client.post("/api/securities/materialize", json={**PAYLOAD, "catalog_symbol": " shv "})
    assert response.status_code == 200, response.text
    assert calls == [PAYLOAD]
    assert response.json()["instrument_id"] == "fund-us-agg"
    assert "market_data" not in response.json()
    created = client.post("/api/watchlists", json={"name": "债券 ETF"}).json()
    added = client.post(f"/api/watchlists/{created['watchlist_id']}/items", json={"instrument_ids": [response.json()["instrument_id"]]})
    assert added.status_code == 200, added.text
    assert added.json()["accepted_count"] == 1


@pytest.mark.parametrize("actor", ["reader", "delegated", "service"])
def test_readers_and_delegated_or_service_identities_cannot_register(accounts, monkeypatch, actor):
    client, principals, _ = accounts
    principals["delegated"] = Principal("alice", "经理甲", "default", resource_scope={"kind": "run", "id": "test"})
    principals["service"] = Principal(None, "Research service", "default", kind="service", service_id="research", scopes=("watchlist:research",))
    as_user(client, actor)
    monkeypatch.setattr(securities, "materialize_catalog_security", lambda _: pytest.fail("unauthorized registration"))
    assert client.post("/api/securities/materialize", json=PAYLOAD).status_code == 403


@pytest.mark.parametrize("payload", [{**PAYLOAD, "instrument_type": "crypto"}, {**PAYLOAD, "catalog_symbol": " "}, {**PAYLOAD, "refresh_eod": False}])
def test_registration_rejects_unsupported_or_forged_inputs(client, monkeypatch, payload):
    monkeypatch.setattr(securities, "materialize_catalog_security", lambda _: pytest.fail("invalid registration"))
    assert client.post("/api/securities/materialize", json=payload).status_code == 422


def test_registration_reports_uncertain_write_without_claiming_success(client, monkeypatch):
    def timed_out(_):
        raise SecurityCatalogError("证券登记响应超时，登记可能已完成；请重新搜索确认。", 504)
    monkeypatch.setattr(securities, "materialize_catalog_security", timed_out)
    response = client.post("/api/securities/materialize", json=PAYLOAD)
    assert response.status_code == 504
    assert "登记可能已完成" in response.json()["detail"]
