from datetime import date, datetime, timezone
from types import SimpleNamespace

from studio_market.config import MarketSettings
from studio_market.numeric.collect import collect
from studio_market.numeric.store import NumericStore
from studio_market.numeric import official_etf
from studio_market.numeric.providers.http_client import PublicHttpError, PublicResponse


def test_issuer_failure_preserves_independent_fund_capture_and_failed_summary(tmp_path, monkeypatch):
    settings = MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "market")
    store = NumericStore(settings)
    store.create_schema_for_testing()
    day = date(2026, 6, 30)
    received = datetime(2026, 9, 12, tzinfo=timezone.utc)
    called = []
    monkeypatch.setattr(official_etf, "NPORT_FUNDS", [SimpleNamespace(symbol="BND", series_id="test-series")])
    monkeypatch.setattr(official_etf, "PERIODIC_FUNDS", [])

    def download(url, params=None, **kwargs):
        called.append(url)
        if url == official_etf.GLD_ARCHIVE_URL:
            raise PublicHttpError("public download failed after 3 attempts: SSLError")
        return PublicResponse(url, params or {}, received, b"retained independent SEC filing")

    monkeypatch.setattr(official_etf, "get_public_bytes", download)
    monkeypatch.setattr(official_etf, "parse_nport_feed", lambda *args, **kwargs: [
        SimpleNamespace(filing_date=date(2026, 8, 31), document_url="https://www.sec.gov/fixture.xml", accession_number="fixture")
    ])
    monkeypatch.setattr(official_etf, "parse_nport_filing", lambda *args, **kwargs: [
        {"etf_symbol":"BND", "report_date":day, "holding_key":"bond-1", "market_value":100.0,
         "source_available_at":datetime(2026, 8, 31, tzinfo=timezone.utc), "collected_at":kwargs["collected_at"]}
    ])
    try:
        result = collect(settings, groups=["official_etf"], start=day, end=date(2026, 9, 12))
        assert result["status"] == "failed"
        stage = result["stages"][0]
        assert [(item["symbol"], item["status"]) for item in stage["symbols"]] == [("GLD", "failed"), ("BND", "ready")]
        assert stage["symbols"][0]["error_type"] == "PublicHttpError"
        assert called[-1] == "https://www.sec.gov/fixture.xml"
        retained = store.query("etf_disclosures", symbols=["BND"])["rows"]
        assert len(retained) == 1
        assert retained[0]["report_date"] == "2026-06-30"
        assert retained[0]["observed_at"].startswith("2026-09-12")
        assert (settings.data_root / retained[0]["raw_ref"]).is_file()
        assert len(result["batches"]) == 1
    finally:
        store.close()


def test_explicit_fund_scope_does_not_query_other_issuers(tmp_path, monkeypatch):
    collector = SimpleNamespace(archive=lambda *args: None)
    called = []
    monkeypatch.setattr(official_etf, "PERIODIC_FUNDS", [])
    monkeypatch.setattr(official_etf, "parse_nport_feed", lambda *args, **kwargs: [])
    def download(url, params=None, **kwargs):
        called.append((url, params))
        return SimpleNamespace(body=b"<feed/>")
    monkeypatch.setattr(official_etf, "get_public_bytes", download)
    result = official_etf.collect_official_etf(collector, date(2026, 1, 1), date(2026, 9, 12), ["BND"])
    assert result == {"status":"ready", "symbols":[{"symbol":"BND", "status":"ready"}]}
    assert len(called) == 1
    assert called[0][1]["CIK"] == official_etf.NPORT_FUNDS[0].series_id
