from datetime import UTC, date, datetime
import json

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric import registered
from studio_market.numeric.providers.fmp import FmpResponse


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path / "data"))
    result.create_schema_for_testing()
    yield result
    result.close()


def stamp(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


def save(store, dataset, rows, day=1):
    return store.ingest(dataset, [[dict(row, collected_at=stamp(day)) for row in rows]], source="fixture")


def test_projection_uses_typed_facts_exact_revision_and_pit_clock(store):
    save(store, "company_profiles", [{"symbol": "AAPL", "company_name": "Apple", "sector": "Technology"}])
    statement = dict(symbol="AAPL", statement_type="income", period_end=date(2025, 12, 31), fiscal_year=2025,
                     fiscal_period="FY", statement_content_sha256="old")
    save(store, "financial_statements", [statement])
    save(store, "financial_facts", [dict(statement, line_item="revenue", value=100), dict(statement, line_item="goneMetric", value=50)])
    save(store, "financial_statements", [dict(statement, statement_content_sha256="new")], day=3)
    save(store, "financial_facts", [dict(statement, statement_content_sha256="new", line_item="revenue", value=90)], day=3)
    save(store, "provider_reference", [{"symbol": "AAPL", "section": section, "payload_json": json.dumps([{"ratio": 10}])} for section in ("key_metrics", "ratios")])
    old = registered.read_reference_data(store.settings, "AAPL", "equity", as_of=stamp(2))
    current = registered.read_reference_data(store.settings, "AAPL", "equity")
    assert old["sections"]["profile"]["companyName"] == "Apple"
    assert old["sections"]["financials"][0]["revenue"] == 100
    assert current["sections"]["financials"][0]["revenue"] == 90
    assert "goneMetric" not in current["sections"]["financials"][0]
    assert current["observed_at"] == stamp(3).isoformat()
    assert current["source"]["section_sources"]["profile"][0]["source_id"].startswith("numeric:")
    assert registered.read_reference_data(store.settings, "AAPL", "equity")["observed_at"] == current["observed_at"]


def test_etf_projection_keeps_disclosed_holdings_dates_and_empty_snapshots(store):
    save(store, "etf_info", [{"symbol": "SPY", "name": "SPDR", "assets_under_management": 100, "provider_updated_at": "2026-08-31"}])
    save(store, "etf_holdings", [{"etf_symbol": "SPY", "holding_key": "AAPL", "holding_symbol": "AAPL", "holding_name": "Apple",
                                  "snapshot_date": date(2026, 8, 31), "weight_percent": 5, "provider_updated_at": "2026-08-31"}])
    old = registered.read_reference_data(store.settings, "SPY", "etf")
    assert old["sections"]["holdings"][0]["asset"] == "AAPL"
    assert old["sections"]["holdings"][0]["updatedAt"] == "2026-08-31"
    assert old["sections"]["fund_info"]["assetsUnderManagement"] == 100
    store.ingest("etf_holdings", [], source="fixture", observed_at=stamp(2), details={"snapshot_scopes": [{"symbol": "SPY", "snapshot_at": stamp(2).isoformat()}]})
    empty = registered.read_reference_data(store.settings, "SPY", "etf")
    assert empty["sections"]["holdings"] == []
    assert "holdings" not in empty["section_errors"]
    assert empty["observed_at"] == stamp(2).isoformat()
    assert registered.read_reference_data(store.settings, "SPY", "etf", as_of=stamp(1))["sections"]["holdings"]


def test_registered_collection_routes_asset_types_and_does_not_repeat_covered_us(store, monkeypatch):
    save(store, "security_directory", [{"symbol": "AAPL"}, {"symbol": "SPY"}])
    save(store, "company_profiles", [{"symbol": "AAPL", "company_name": "Apple"}])
    save(store, "financial_statements", [dict(symbol="AAPL", statement_type="income", period_end=date(2025, 12, 31), fiscal_year=2025, fiscal_period="FY")])
    save(store, "etf_info", [{"symbol": "SPY", "name": "SPDR"}])
    save(store, "etf_holdings", [{"etf_symbol": "SPY", "holding_key": "AAPL", "holding_symbol": "AAPL", "snapshot_date": date(2026, 9, 1)}])
    typed_calls, endpoint_calls = [], []
    class Client:
        def get_json(self, endpoint, params=None):
            endpoint_calls.append((endpoint, params))
            payload = [{"symbol": "^GSPC", "name": "S&P 500"}, {"symbol": "^IXIC", "name": "NASDAQ"}] if endpoint == "index-list" else []
            if endpoint == "dividends":
                payload = [{"symbol": params["symbol"], "date": "2026-09-01", "dividend": 1.5, "adjDividend": 1.5}]
            return FmpResponse(endpoint, params or {}, stamp(4), json.dumps(payload).encode(), payload)
        def close(self):
            pass
    real_collector = registered.Collector
    collector = real_collector(store.settings, store=store, client=Client())
    for name in ("profiles", "financials", "etf"):
        monkeypatch.setattr(collector, name, lambda start, end, symbols, name=name: typed_calls.append((name, symbols)))
    monkeypatch.setattr(registered, "Collector", lambda settings: collector)
    result = registered.refresh_registered(store.settings, instruments=[
        {"symbol": "AAPL", "instrument_type": "equity"}, {"symbol": "SPY", "instrument_type": "etf"},
        {"symbol": "9988.HK", "instrument_type": "equity"}, {"symbol": "510300.SS", "instrument_type": "etf"},
        {"symbol": "^GSPC", "instrument_type": "index"}, {"symbol": "^IXIC", "instrument_type": "index"}])
    assert result["status"] == "ready"
    assert result["price_revision_symbols"] == ["9988.HK"]
    assert typed_calls == [("etf", ["510300.SS"]), ("profiles", ["9988.HK"]), ("financials", ["9988.HK"])]
    assert sum(endpoint == "index-list" for endpoint, _ in endpoint_calls) == 1
    assert not any(endpoint in {"dividends", "splits"} and params["symbol"] == "AAPL" for endpoint, params in endpoint_calls)
    assert registered.read_reference_data(store.settings, "^GSPC", "index")["sections"]["index_info"]["name"] == "S&P 500"
    assert registered.refresh_registered(store.settings, instruments=[{"symbol": "9988.HK", "instrument_type": "equity"}])["price_revision_symbols"] == []


def test_empty_financial_response_keeps_raw_lineage_and_is_coverage_not_error(store, tmp_path):
    import zipfile
    from studio_market.numeric.replication import export_bundle

    assert "financials" in registered.read_reference_data(store.settings, "6082.HK", "equity")["section_errors"]
    response = FmpResponse("income-statement", {"symbol": "6082.HK", "period": "annual", "limit": "3"}, stamp(4), b"[]", [])
    collector = registered.Collector(store.settings, store=store)
    collector._financial_response(response, "income", {"6082.HK"})
    result = registered.read_reference_data(store.settings, "6082.HK", "equity")
    assert result["sections"]["financials"] == []
    assert "financials" not in result["section_errors"]
    capture = result["source"]["section_coverage"]["financials"]
    assert capture["status"] == "unavailable"
    assert capture["observed_at"] == stamp(4).isoformat()
    assert result["source"]["section_sources"]["financials"][0]["batch_id"] == capture["batch_id"]
    assert (store.settings.data_root / capture["raw_ref"]).is_file()
    # An empty response becomes known at publication; it is not retroactive evidence.
    assert "financials" in registered.read_reference_data(store.settings, "6082.HK", "equity", as_of=stamp(3))["section_errors"]
    archive = tmp_path / "empty.zip"
    export_bundle(store.settings, archive, batch_ids=[capture["batch_id"]])
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert capture["raw_ref"] in bundle.namelist()
        assert manifest["batches"][0]["details"]["response_empty"] is True
    # Zero normalized rows without a confirmed empty provider payload remain an error.
    store.ingest("financial_statements", [], source="fmp", details={"endpoint": "income-statement", "parameters": response.params})
    assert "financials" in registered.read_reference_data(store.settings, "6082.HK", "equity")["section_errors"]
