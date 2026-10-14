from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import zipfile

import pytest
from sqlalchemy import func, select

from studio_market.config import MarketSettings
from studio_market.numeric.providers.fmp import FmpHttpError, FmpResponse, FmpResponseError
from studio_market.text.fmp import FmpTextCollector, _record
from studio_market.text.bundle import canonical_json, digest
from studio_market.text.publication import publish_fmp_text
from studio_market.text.schema import collection_receipts, metadata, versions
from studio_market.text.store import TextStore


class Client:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail
        self.text = "Prepared remarks. " * 2000 + "Analyst Q&A: complete ending."

    def close(self):
        pass

    def get_json(self, endpoint, params):
        self.calls.append((endpoint, params))
        if self.fail == endpoint:
            raise FmpHttpError(403)
        symbol = params.get("symbol") or params["symbols"]
        day = datetime.now(UTC).date()
        if endpoint == "earning-call-transcript-dates":
            payload = [{"date": (day - timedelta(days=80)).isoformat(), "fiscalYear": day.year, "quarter": 2}]
        elif endpoint == "earning-call-transcript":
            payload = [{"symbol": symbol, "year": params["year"], "period": f"Q{params['quarter']}",
                        "date": (day - timedelta(days=80)).isoformat(), "content": self.text}]
        elif endpoint == "sec-filings-search/symbol":
            payload = [{"symbol": symbol, "formType": "8-K", "filingDate": day.isoformat(),
                        "acceptedDate": day.isoformat() + " 15:30:00", "link": "https://sec.gov/index.htm",
                        "finalLink": "https://sec.gov/report.htm"}]
        else:
            payload = [{"symbol": symbol, "title": "Earnings announcement", "publishedDate": day.isoformat() + " 16:05:00",
                        "url": "https://issuer.example/results", "text": "Provider excerpt", "publisher": "Issuer"}]
        return FmpResponse(endpoint, dict(params), datetime.now(UTC), json.dumps(payload).encode(), payload)


@pytest.fixture
def store(tmp_path):
    result = TextStore(database_url="sqlite://", data_root=tmp_path)
    metadata.create_all(result.engine)
    yield result
    result.close()


def collect(store, client=None, subjects=None):
    client = client or Client()
    collector = FmpTextCollector(None, store=store, client=client)
    result = collector.collect(subjects or [{"symbol": "AAPL", "kind": "company"}])
    return result, client


def test_provider_types_complete_call_and_exact_entity_directory(store):
    result, client = collect(store)
    assert result["status"] == "ready" and len(result["sources"]) == 4
    page = store.search(entities=["fmp:AAPL"], channel_ids=["fmp:earnings_transcripts"])
    assert page["total"] == 1
    transcript = page["rows"][0]
    assert transcript["content_text"] == client.text
    assert len(transcript["content_text"]) > 12000
    assert transcript["content_completeness"] == "full_text"
    assert transcript["published_at"] is None  # Call date is not provider publication date.
    assert transcript["occurred_at"] is not None
    news = store.search(channel_ids=["fmp:stock_news"])["rows"][0]
    assert news["information_type"] == "company_news"
    assert news["content_completeness"] == "source_excerpt"
    assert len(news["published_at"]) == 10  # Unknown timezone was not invented.
    filing = store.search(channel_ids=["fmp:sec_filings"])["rows"][0]
    assert filing["content_completeness"] == "title_only"
    assert filing["provenance"]["content_scope"] == "metadata"
    assert "apikey" not in Path(transcript["raw_path"]).read_text()


def test_repeat_observation_does_not_invent_new_versions(store):
    _, client = collect(store)
    first_ids = {row["source_id"] for row in store.search()["rows"]}
    client.calls.clear()
    result, _ = collect(store, client)
    assert {row["source_id"] for row in store.search()["rows"]} == first_ids
    assert result["source_ids"] == []
    assert any(endpoint == "earning-call-transcript" for endpoint, _ in client.calls)
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(collection_receipts)) == 8


def test_older_baseline_call_correction_is_acquired_on_the_next_collection(store):
    _, client = collect(store)
    original = store.search(channel_ids=["fmp:earnings_transcripts"])["rows"][0]
    client.text += "\nProvider correction to an answer."
    result, _ = collect(store, client)
    revised = store.search(channel_ids=["fmp:earnings_transcripts"])["rows"][0]
    assert revised["document_id"] == original["document_id"]
    assert revised["source_id"] != original["source_id"]
    assert revised["source_id"] in result["source_ids"]
    assert revised["content_text"].endswith("Provider correction to an answer.")
    assert store.read(original["source_id"])["content_text"] == original["content_text"]


def test_correction_and_reversion_keep_history_and_observation_clocks(store):
    client = Client()
    response = client.get_json("news/press-releases", {"symbols": "AAPL"})
    original = _record(response, response.payload[0], "AAPL", "fmp:press_releases")
    first = store.capture_provider_document(*original)
    cutoff = datetime.now(UTC)
    second_response = client.get_json("news/press-releases", {"symbols": "AAPL"})
    second_response.payload[0]["text"] = "Corrected provider excerpt"
    second = store.capture_provider_document(*_record(second_response, second_response.payload[0], "AAPL", "fmp:press_releases"))
    response = client.get_json("news/press-releases", {"symbols": "AAPL"})
    third = store.capture_provider_document(*_record(response, response.payload[0], "AAPL", "fmp:press_releases"))
    assert len({first["source_id"], second["source_id"], third["source_id"]}) == 3
    assert store.read(first["document_id"], as_of=cutoff)["source_id"] == first["source_id"]


def test_failure_isolated_and_cursor_not_advanced(store):
    client = Client(fail="news/press-releases")
    result, _ = collect(store, client)
    assert result["status"] == "partial"
    failed = next(row for row in result["sources"] if row["channel_id"] == "fmp:press_releases")
    assert failed["status"] == "failed" and failed["details"]["http_status"] == 403
    assert store.last_successful_collection("AAPL", "fmp:press_releases") is None
    assert store.search(channel_ids=["fmp:earnings_transcripts"])["total"] == 1
    before = datetime.now(UTC)
    client.fail = None
    collect(store, client)
    prior = store.get_fmp_coverage(["AAPL"], as_of=before)
    assert next(row for row in prior["sources"] if row["channel_id"] == "fmp:press_releases")["status"] == "failed"


def test_fund_does_not_request_company_calls_or_filings(store):
    result, client = collect(store, subjects=[{"symbol": "XLK", "kind": "fund"}])
    assert len(result["sources"]) == 2
    assert {endpoint for endpoint, _ in client.calls} == {"news/press-releases", "news/stock"}


def test_publication_and_replica_replay_keep_full_text_and_independent_coverage(store, tmp_path):
    collect(store)
    publication = publish_fmp_text(store)
    assert publication["documents"] == 4
    assert publish_fmp_text(store)["status"] == "current"
    replica = TextStore(database_url="sqlite://", data_root=tmp_path / "replica")
    metadata.create_all(replica.engine)
    try:
        before_receipt = datetime.now(UTC)
        replica.import_bundle(publication["path"], expected_sha256=publication["sha256"])
        assert replica.search()["total"] == 4
        assert replica.search(as_of=before_receipt)["total"] == 0
        assert all(row["status"] == "not_collected" for row in replica.get_fmp_coverage(["AAPL"], as_of=before_receipt)["sources"])
        assert all(row["status"] == "ready" for row in replica.get_fmp_coverage(["AAPL"])["sources"])
        assert replica.import_bundle(publication["path"])["duplicate"] is True
        assert replica.get_coverage()["sources"] == []  # FMP does not replace MI coverage.
    finally:
        replica.close()


def test_reused_immutable_coverage_identity_rejects_conflicting_bundle(store, tmp_path):
    collect(store)
    publication = publish_fmp_text(store)
    with zipfile.ZipFile(publication["path"]) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents["manifest.json"])
    manifest.pop("bundle_id")
    manifest["coverage"]["fmp_sources"][0]["status"] = "failed"
    manifest["bundle_id"] = "mi-text-" + digest(canonical_json(manifest))
    contents["manifest.json"] = canonical_json(manifest)
    conflicting = tmp_path / "conflict.zip"
    with zipfile.ZipFile(conflicting, "w") as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    before = store.imported_bundle_ids()
    with pytest.raises(ValueError, match="immutable collection receipt"):
        store.import_bundle(conflicting)
    assert store.imported_bundle_ids() == before
    assert all(row["status"] == "ready" for row in store.get_fmp_coverage(["AAPL"])["sources"])


def test_pagination_repetition_never_certifies_complete_window(store):
    client = Client()
    original = client.get_json
    def repeated(endpoint, params):
        result = original(endpoint, params)
        if endpoint == "news/press-releases":
            rows = result.payload * 250
            return FmpResponse(endpoint, params, datetime.now(UTC), json.dumps(rows).encode(), rows)
        return result
    client.get_json = repeated
    result, _ = collect(store, client)
    failed = next(row for row in result["sources"] if row["channel_id"] == "fmp:press_releases")
    assert failed["status"] == "partial" and failed["document_count"] == 250
    assert store.last_successful_collection("AAPL", "fmp:press_releases") is None


def test_provider_symbol_and_period_mismatch_are_not_attached_to_requested_company(store):
    client = Client()
    response = client.get_json("earning-call-transcript", {"symbol": "AAPL", "year": 2026, "quarter": 3})
    with pytest.raises(FmpResponseError, match="financial period"):
        _record(response, response.payload[0], "AAPL", "fmp:earnings_transcripts", fiscal_year=2026, quarter=2)
