from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import collect
from studio_market.numeric.providers.fmp import FmpResponse
from studio_market.numeric.providers.hsil import (
    HSIL_INDEX_CODES, hsil_history_url, parse_hsil_history,
)
from studio_market.numeric.providers.market_series import normalize_hsil_close_series
from studio_market.numeric.schema import batches


def history(symbol="HSCIEN.HI", rows=None):
    return {"code": "0", "message": "success", "data": [{
        "indexCode": HSIL_INDEX_CODES[symbol],
        "indexName": "Hang Seng Composite Industry Index - Energy",
        "valueHistory": rows if rows is not None else [
            {"date": "2026-09-30", "close": "14087.04"},
            {"date": "2026-10-02", "close": "13829.77"},
        ],
    }]}


@pytest.mark.parametrize("symbol,code", HSIL_INDEX_CODES.items())
def test_all_declared_price_indexes_use_the_official_public_history_route(symbol, code):
    url = urlsplit(hsil_history_url(symbol))
    assert url.scheme == "https" and url.netloc == "www.hsi.com.hk"
    assert url.path.endswith("/v1/product-data/pub/indexes/metadata/v2")
    assert parse_qs(url.query) == {
        "data": ["valueHistoryPub"], "language": ["en-hk"], "indexCodes": [code],
    }
    rows = parse_hsil_history(history(symbol), symbol=symbol)
    assert rows == [
        {"date": date(2026, 9, 30), "close": 14087.04},
        {"date": date(2026, 10, 2), "close": 13829.77},
    ]


@pytest.mark.parametrize("bad", [
    None, [], {}, {"code": "error", "data": []}, {"code": 0, "data": []},
    {"code": "0", "data": []}, {"code": "0", "data": [None]},
    {"code": "0", "data": [{"indexCode": "10069.01", "valueHistory": []}]},
    {"code": "0", "data": [{"indexCode": "00011.01", "indexType": "TRI_Grs"}]},
])
def test_unsuccessful_or_wrong_index_contract_is_not_a_price_history(bad):
    with pytest.raises(ValueError):
        parse_hsil_history(bad, symbol="HSCIEN.HI")


def test_extra_index_and_unknown_symbol_are_rejected():
    value = history()
    value["data"].append(deepcopy(value["data"][0]))
    with pytest.raises(ValueError, match="exactly one"):
        parse_hsil_history(value, symbol="HSCIEN.HI")
    with pytest.raises(ValueError, match="Unsupported"):
        hsil_history_url("HSCIEN-TOTAL RETURN")


@pytest.mark.parametrize("row", [
    None, ["2026-10-02", 100], {},
    {"date": "20261002", "close": 100},
    {"date": "2026-10-02T00:00:00Z", "close": 100},
    {"date": "2026-02-30", "close": 100},
    {"date": 1790870400000, "close": 100},
    *({"date": "2026-10-02", "close": value} for value in
      [None, True, False, 0, -1, "NaN", "inf", float("nan"), float("inf"), "not a price"]),
])
def test_invalid_date_or_close_rejects_the_entire_response(row):
    with pytest.raises(ValueError):
        parse_hsil_history(history(rows=[row]), symbol="HSCIEN.HI")


def test_empty_and_duplicate_history_rejected_even_outside_requested_range():
    with pytest.raises(ValueError, match="empty"):
        parse_hsil_history(history(rows=[]), symbol="HSCIEN.HI")
    rows = [{"date": "2026-10-02", "close": "13829.77"}] * 2
    with pytest.raises(ValueError, match="duplicate"):
        normalize_hsil_close_series(json.dumps(history(rows=rows)).encode(),
            series_id="HSCIEN.HI", code="00011.01", start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30), raw_sha256="fixture",
            collected_at=datetime(2026, 10, 3, tzinfo=timezone.utc))


def test_close_only_semantics_and_capture_time_survive_collection(tmp_path, monkeypatch):
    payload = history()
    body = json.dumps(payload).encode()
    captured = datetime(2026, 10, 3, 1, 2, tzinfo=timezone.utc)
    settings = MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / "data")
    store = NumericStore(settings)
    store.create_schema_for_testing()
    calls = []

    def fetch(url):
        calls.append(url)
        return FmpResponse(url, {}, captured, body, payload)

    monkeypatch.setattr("studio_market.numeric.collect.get_public_bytes", fetch)
    try:
        result = collect(settings, groups=["market_series"], symbols=["HSCIEN.HI"],
                         start=date(2026, 10, 1), end=date(2026, 10, 3))
        assert result["status"] == "ready"
        assert calls == [hsil_history_url("HSCIEN.HI")]
        rows = store.query("market_series_daily", symbols=["HSCIEN.HI"])["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["date"] == "2026-10-02" and row["close"] == 13829.77
        assert all(row[field] is None for field in ["open", "high", "low", "volume", "vwap"])
        assert row["source_dataset"] == "hang_seng_indexes_official_history"
        assert datetime.fromisoformat(row["observed_at"]) == captured
        assert datetime.fromisoformat(row["available_at"]) == captured
        assert store.query("market_series_daily", as_of="2026-10-02T23:59:59Z")["total"] == 0
        assert json.loads(gzip.decompress((settings.data_root / row["raw_ref"]).read_bytes())) == payload
        with store.engine.connect() as conn:
            detail = conn.execute(select(batches.c.details)).scalar_one()
        assert detail["endpoint"] == calls[0]
    finally:
        store.close()
