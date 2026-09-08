from datetime import date, datetime, timezone
import gzip
import json

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import collect
from studio_market.numeric.delivery import publish_pending
from studio_market.numeric.providers.fmp import FmpResponse
from studio_market.numeric.providers.market_series import normalize_fmp_market_series
from studio_market.numeric.replication import import_bundle
from studio_market.numeric.schema import batches


def price(day, **changes):
    return {"symbol": "DX-Y.NYB", "date": day, "open": 99, "high": 102,
            "low": 98, "close": 100, "volume": 0, **changes}


@pytest.fixture
def store(tmp_path):
    value = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / 'source'))
    value.create_schema_for_testing()
    yield value
    value.close()


def test_bad_date_retains_source_error_without_blocking_new_dates_or_pit(store, tmp_path, monkeypatch):
    class Client:
        day = 5
        payload = [price("2026-09-04"), price("2026-09-02", low=0, close=0), price("2026-09-03")]

        def get_json(self, endpoint, params):
            body = json.dumps(self.payload).encode()
            return FmpResponse(endpoint, params, datetime(2026, 9, self.day, tzinfo=timezone.utc), body, json.loads(body))

        def close(self):
            pass

    client = Client()
    monkeypatch.setattr("studio_market.numeric.collect.FmpClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(MarketSettings, "read_secret", lambda *args: "fixture")

    def acquire():
        result = collect(store.settings, groups=["market_series"], symbols=["DXY"],
                         start=date(2026, 9, 1), end=date(2026, 9, 7))
        assert result["status"] == result["stages"][0]["status"] == "failed"
        assert len(result["batches"]) == 1
        issue = result["stages"][0]["source_issues"][0]
        assert issue["rejected_rows"][0]["date"] == "2026-09-02"
        with store.engine.connect() as conn:
            capture = conn.execute(select(batches).where(batches.c.id == issue["batch_id"])).mappings().one()
        assert capture["status"] == "ready"
        assert capture["details"]["validation"]["status"] == "partial"
        assert capture["details"]["raw_ref"] == issue["raw_ref"]
        assert json.loads(gzip.decompress((store.settings.data_root / issue["raw_ref"]).read_bytes())) == client.payload
        return result

    acquire()
    original = store.query("market_series_daily", symbols=["DXY"])["rows"]
    assert {r["date"] for r in original} == {"2026-09-03", "2026-09-04"}
    client.day = 6
    client.payload[2]["close"] = 101
    client.payload.insert(0, price("2026-09-05"))
    acquire()
    client.day = 7
    acquire()
    latest = store.query("market_series_daily", symbols=["DXY"])
    assert latest["total"] == 3
    assert {r["date"] for r in latest["rows"]} == {"2026-09-03", "2026-09-04", "2026-09-05"}
    assert next(r for r in latest["rows"] if r["date"] == "2026-09-03")["close"] == 101
    assert store.query("market_series_daily", symbols=["DXY"], as_of="2026-09-05T23:59:00+00:00")["rows"] == original

    # A source failure still delivers its validated facts and immutable raw
    # evidence; replaying that delivery cannot duplicate facts or PIT versions.
    published = publish_pending(store.settings)
    replica = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'replica.db'}", tmp_path / 'replica'))
    replica.create_schema_for_testing()
    try:
        import_bundle(replica.settings, published["path"])
        import_bundle(replica.settings, published["path"])
        assert replica.query("market_series_daily", symbols=["DXY"])["total"] == 3
        assert replica.query("market_series_daily", versions=True)["total"] == store.query("market_series_daily", versions=True)["total"] == 8
        with replica.engine.connect() as conn:
            captures = conn.execute(select(batches.c.details)).scalars().all()
        assert all(c["validation"]["status"] == "partial" for c in captures)
        assert all((replica.settings.data_root / c["raw_ref"]).is_file() for c in captures)
    finally:
        replica.close()

    # A later corrected response can become complete without rewriting the
    # earlier partial capture or claiming that its rejected price was usable.
    client.day = 8
    client.payload[2].update(low=98, close=100)
    corrected = collect(store.settings, groups=["market_series"], symbols=["DXY"],
                        start=date(2026, 9, 1), end=date(2026, 9, 7))
    assert corrected["status"] == "ready"
    assert store.query("market_series_daily", symbols=["DXY"])["total"] == 4
    with store.engine.connect() as conn:
        captures = conn.execute(select(batches.c.details)).scalars().all()
    assert sum(c.get("validation", {}).get("status") == "partial" for c in captures) == 3
    assert store.query("market_series_daily", symbols=["DXY"], as_of="2026-09-05T23:59:00+00:00")["rows"] == original


def test_all_invalid_dates_publish_evidence_without_claiming_a_successful_source(store, monkeypatch):
    class Client:
        def get_json(self, endpoint, params):
            payload = [price("2026-09-06", low=0, close=0)]
            return FmpResponse(endpoint, params, datetime(2026, 9, 8, tzinfo=timezone.utc), json.dumps(payload).encode(), payload)

        def close(self):
            pass

    monkeypatch.setattr("studio_market.numeric.collect.FmpClient", lambda *args, **kwargs: Client())
    monkeypatch.setattr(MarketSettings, "read_secret", lambda *args: "fixture")
    result = collect(store.settings, groups=["market_series"], symbols=["DXY"],
                     start=date(2026, 9, 6), end=date(2026, 9, 7))
    assert result["status"] == result["stages"][0]["status"] == "failed"
    assert result["batches"][0]["row_count"] == 0
    assert store.query("market_series_daily", symbols=["DXY"])["total"] == 0
    with store.engine.connect() as conn:
        capture = conn.execute(select(batches.c.details)).scalar_one()
    assert capture["validation"]["status"] == "partial"
    assert capture["validation"]["accepted_row_count"] == 0
    assert (store.settings.data_root / capture["raw_ref"]).is_file()


@pytest.mark.parametrize("bad", [None, {"date": "2026-09-04", "symbol": "OTHER"}, price("invalid")])
def test_unlocatable_or_wrong_identity_response_is_rejected_as_a_whole(bad):
    with pytest.raises(ValueError):
        normalize_fmp_market_series([price("2026-09-03"), bad], series_id="DXY",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 7), raw_sha256="fixture",
            collected_at=datetime(2026, 9, 8, tzinfo=timezone.utc))


@pytest.mark.parametrize("same_day", [price("2026-09-03", close=101), price("2026-09-03", low=0)])
def test_one_conflicting_or_invalid_duplicate_rejects_the_entire_date(same_day):
    rows, rejected = normalize_fmp_market_series(
        [price("2026-09-03"), same_day, price("2026-09-03"), price("2026-09-04")],
        series_id="DXY", start_date=date(2026, 9, 1), end_date=date(2026, 9, 7),
        raw_sha256="fixture", collected_at=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert [r["date"].isoformat() for r in rows] == ["2026-09-04"]
    assert rejected[0]["date"] == "2026-09-03"
    assert rejected[0]["row_index"] == 1
