from copy import deepcopy
from datetime import date, datetime, UTC
from types import SimpleNamespace

import pytest

from briefing_app.evidence import build_input, coverage_summary, related_equity_snapshot


def test_coverage_preserves_discovery_402_before_any_capture_run():
    discovery = {
        "route_id": "x-a16z-post", "status": "failed",
        "failure_reason": "approved event_view fetch failed closed with HTTP 402: https://api.x.com/2/users/by/username/a16z",
        "last_attempted_discovery_at": "2026-09-07T13:33:39.922562+00:00",
        "last_successful_discovery_at": None, "cooldown_until": None,
    }
    collection = {
        "status": "partial", "started_at": "2026-09-07T13:33:39+00:00",
        "completed_at": "2026-09-07T13:35:15+00:00", "routes_attempted": 55,
        "routes_succeeded": 13, "routes_failed": 14, "routes_skipped": 28,
    }
    coverage = {"bundle_count": 3, "export_coverage": {"latest_collection": collection},
                "sources": [{"channel_id": "x-a16z", "latest_run": None, "latest_discovery": discovery}]}
    original = deepcopy(coverage)

    result = coverage_summary(coverage)

    assert result["sources"][0]["latest_discovery"] == discovery
    assert result["sources"][0]["latest_run"]["status"] is None
    assert result["latest_collection"] == collection
    assert coverage == original


def test_absent_discovery_is_not_invented_as_a_success():
    result = coverage_summary({"sources": [{"channel_id": "example", "latest_run": {"status": "succeeded"}}]})

    assert result["sources"][0]["latest_discovery"] is None
    assert result["sources"][0]["latest_run"]["status"] == "succeeded"
    assert result["latest_collection"] is None


@pytest.fixture
def equity_evidence(tmp_path):
    from studio_market.config import MarketSettings
    from studio_market.numeric import NumericStore

    store = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    store.create_schema_for_testing()
    def stamp(day, hour=0):
        return datetime(2026, 9, day, hour, tzinfo=UTC)
    profiles = [{"symbol": symbol, "company_name": symbol, "cik": cik, "is_etf": False, "is_fund": False,
                 "collected_at": stamp(1)} for symbol, cik in (("AAPL", "0000320193"), ("AAPL.B", "0000320193"),
                 ("0700.HK", None), ("MSFT", "0000789019"), ("LATE", "0000000001"))]
    profiles[-1]["collected_at"] = stamp(9)
    store.ingest("company_profiles", [profiles], source="fixture")
    def price(symbol, day, close, observed=None, **extra):
        return {"symbol": symbol, "date": date(2026, 9, day), "close": close, "adjusted_close": close * .8,
                "collected_at": observed or stamp(day + 1), **extra}
    store.ingest("us_eod_daily", [[price(symbol, day, close) for symbol in ("AAPL", "AAPL.B", "MSFT")
                                   for day, close in ((3, 100), (4, 110))]], source="fixture")
    store.ingest("us_eod_daily", [[price("AAPL", 4, 999, stamp(9)),
        price("AAPL.B", 4, 888, stamp(7), available_at=stamp(8, 1))]], source="fixture")
    store.ingest("raw_eod_daily", [[price("0700.HK", day, close, stamp(day, 10))
                                     for day, close in ((3, 190), (4, 200), (7, 210))]], source="fixture")
    documents = [{"source_id": "text:current", "window_scope": "current", "title": "Microsoft gained 50%",
        "entities": [{"entity_id": identity, "kind": "issuer"} for identity in (
            "issuer:cik:0000320193", "issuer:hkex:stock_code:00700", "issuer:hkex:stock_code:01810", "LATE", "exchange:hkex")]},
        {"source_id": "text:late", "window_scope": "late_received", "entities": [{"entity_id": "MSFT", "kind": "issuer"}]}]
    yield store, documents
    store.close()


def test_related_equities_use_explicit_identities_and_cutoff_visible_closes(equity_evidence):
    store, documents = equity_evidence
    original = deepcopy(documents)
    result = related_equity_snapshot(store, documents, report_type="daily", period_start=date(2026, 9, 8),
        period_end=date(2026, 9, 8), cutoff=datetime(2026, 9, 8, 0, 30, tzinfo=UTC))

    rows = {row["symbol"]: row for row in result["market_rows"]}
    assert set(rows) == {"AAPL", "AAPL.B", "0700.HK"}
    assert rows["AAPL"]["return_pct"] == pytest.approx(10)
    assert rows["AAPL.B"]["end_close"] == 110  # available_at, as well as observed_at, is binding.
    assert rows["AAPL"]["end_date"] == "2026-09-04"
    assert rows["AAPL"]["return_basis"] == "split_adjusted_price"
    assert rows["0700.HK"]["return_pct"] == pytest.approx(5)
    assert rows["0700.HK"]["return_basis"] == "unadjusted_price"
    bound = {source["source_id"] for source in result["sources"]}
    for row in rows.values():
        assert row["asset_type"] == "equity"
        assert row["related_document_source_ids"] == ["text:current"]
        assert set(row["source_ids"] + row["identity_source_ids"]) <= bound
    assert {row["symbol"] for row in result["coverage"] if row["status"] == "unresolved_security"} == {
        "issuer:hkex:stock_code:01810", "LATE"}
    assert documents == original


def test_related_equity_weekly_requires_a_close_inside_the_week(equity_evidence):
    store, documents = equity_evidence
    result = related_equity_snapshot(store, documents, report_type="weekly", period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 8), cutoff=datetime(2026, 9, 8, 0, 30, tzinfo=UTC))

    assert [row["symbol"] for row in result["market_rows"]] == ["0700.HK"]
    assert result["market_rows"][0]["start_date"] == "2026-09-04"
    assert result["market_rows"][0]["end_date"] == "2026-09-07"
    assert {row["symbol"] for row in result["coverage"] if row["status"] == "insufficient_history"} == {"AAPL", "AAPL.B"}


def test_build_input_freezes_related_rows_and_all_their_sources(equity_evidence, monkeypatch):
    from studio_market import text
    from studio_market.numeric import reporting
    import briefing_app.evidence as evidence

    store, documents = equity_evidence
    for document in documents:
        document["content_completeness"] = "full"
    monkeypatch.setenv("INVESTMENT_STUDIO_MARKET_DATABASE_URL", store.settings.database_url)
    monkeypatch.setattr(text, "TextStore", lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(evidence, "collect_text", lambda *_: (documents, {}))
    monkeypatch.setattr(reporting, "build_report_snapshot", lambda *_, **__: {
        "sources": [], "market_rows": [], "macro_rows": [], "coverage": [], "status": "insufficient_data"})

    frozen = build_input("daily", datetime(2026, 9, 8, 0, 30, tzinfo=UTC),
        SimpleNamespace(data_root=store.settings.data_root, timezone="Asia/Shanghai", edition_role="local"))

    assert len(frozen["market_rows"]) == 3
    assert frozen["numeric_status"] == "ready"
    bound = {source["source_id"]: source for source in frozen["sources"]}
    for index, row in enumerate(frozen["market_rows"]):
        assert bound[f"market-row:{index}"]["return_pct"] == row["return_pct"]
        assert all(bound[key]["source_type"] == "numeric" for key in row["source_ids"] + row["identity_source_ids"])
