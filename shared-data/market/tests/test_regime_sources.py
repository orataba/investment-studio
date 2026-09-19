from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
import gzip

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.regime_sources import RegimeSources, DataHubIntermittentPermissionError
from studio_market.numeric.schema import batches

UTC = timezone.utc

@pytest.fixture
def source(tmp_path):
    settings = MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path/'data')
    store = NumericStore(settings)
    store.create_schema_for_testing()
    value = RegimeSources(settings, store=store, role="collector")
    yield value
    value.close()


def response(payload, endpoint="fixture", params=None):
    return SimpleNamespace(payload=payload, body=json.dumps(payload).encode(), endpoint=endpoint, params=params or {}, received_at=datetime.now(UTC))


def test_etf_reads_distinct_raw_and_provider_adjusted_ohlc(source):
    calls = []
    class Fmp:
        def get_json(self, endpoint, params):
            calls.append(endpoint)
            raw = endpoint.endswith("non-split-adjusted")
            return response([dict(symbol="SPY", date="2024-01-02", adjOpen=100 if raw else 49.999,
                adjHigh=102 if raw else 51.001, adjLow=99 if raw else 49.499,
                adjClose=101 if raw else 50.5, volume=1000)], endpoint, params)
        def close(self): pass
    source.collector._client = Fmp()
    params = {"symbol":"SPY", "from":"2024-01-02", "to":"2024-01-02"}
    raw = source.fmp_payload("historical-price-eod/non-split-adjusted", params)
    adjusted = source.fmp_payload("historical-price-eod/dividend-adjusted", params)
    assert len(calls) == 2
    assert raw[0]["adjOpen"] == 100
    assert adjusted[0]["adjOpen"] == 49.999
    row = source.store.query("raw_eod_daily")["rows"][0]
    assert row["adjustment_factor"] == .5
    assert row["raw_ref"] != row["adjusted_raw_ref"]
    assert source.store.query("raw_eod_daily", as_of="2024-01-03T00:00:00Z")["total"] == 0
    source.provenance.clear()
    source.fmp_payload("historical-price-eod/non-split-adjusted", params)
    source.fmp_payload("historical-price-eod/dividend-adjusted", params)
    assert len(calls) == 2  # Cache preserves both reads without new acquisition.
    assert {capture["batch_id"] for capture in source.provenance} == {row["batch_id"]}
    assert all(capture["raw_ref"] == row["raw_ref"] and capture["adjusted_raw_ref"] == row["adjusted_raw_ref"] for capture in source.provenance)


def test_etf_dividend_rebuild_resolves_only_its_own_captures_and_preserves_pit(source, monkeypatch):
    import studio_market.numeric.collect as collection

    def stored(day, factor):
        return dict(symbol="SPY", date=day, open=100, high=102, low=99, close=101,
            adjusted_open=100*factor, adjusted_high=102*factor,
            adjusted_low=99*factor, adjusted_close=101*factor, volume=1000)

    source.store.ingest("raw_eod_daily", [[stored(date(2024, 1, 2), .6)]],
        source="fixture", observed_at=datetime(2024, 1, 4, tzinfo=UTC))
    monkeypatch.setattr(collection, "history_start", lambda *args: date(2024, 1, 1))
    captures = []

    class Fmp:
        def get_json(self, endpoint, params):
            captures.append((endpoint, params))
            raw = endpoint.endswith("non-split-adjusted")
            factor = 1 if raw else (.5 if params["from"] == "2024-01-02" else .4)
            payload = [dict(symbol="SPY", date=day, adjOpen=100*factor,
                adjHigh=102*factor, adjLow=99*factor, adjClose=101*factor, volume=1000)
                for day in ("2024-01-01", "2024-01-02", "2024-01-03")
                if params["from"] <= day <= params["to"]]
            result = response(payload, endpoint, params)
            result.received_at = datetime(2024, 2, 1, 10, tzinfo=UTC) + timedelta(seconds=len(captures))
            return result
        def close(self): pass

    source.collector._client = Fmp()
    acquire = source.collector.raw_eod

    def acquire_with_concurrent_capture(*args):
        result = acquire(*args)
        # A separate collector's newer version must not change this acquisition.
        source.store.ingest("raw_eod_daily", [[stored(date(2024, 1, 2), .1)]],
            source="concurrent", observed_at=datetime(2024, 3, 1, tzinfo=UTC))
        return result

    monkeypatch.setattr(source.collector, "raw_eod", acquire_with_concurrent_capture)
    params = {"symbol": "SPY", "from": "2024-01-02", "to": "2024-01-03"}
    raw = source.fmp_payload("historical-price-eod/non-split-adjusted", params)
    adjusted = source.fmp_payload("historical-price-eod/dividend-adjusted", params)

    assert len(captures) == 4  # Initial pair, followed by the dividend rebuild pair.
    assert len(raw) == len(adjusted) == 2
    assert {row["date"] for row in raw} == {"2024-01-02", "2024-01-03"}
    assert all(row["adjClose"] == 101 for row in raw)
    assert all(row["adjClose"] == pytest.approx(40.4) for row in adjusted)
    initial, rebuilt = source.collector.results
    assert {capture["batch_id"] for capture in source.provenance} == {rebuilt["batch_id"]}
    assert all(capture["raw_ref"] and capture["adjusted_raw_ref"] for capture in source.provenance)

    selected = [initial["batch_id"], rebuilt["batch_id"]]
    filters = dict(symbols=["SPY"], start=params["from"], end=params["to"], batch_ids=selected)
    prior = source.store.query("raw_eod_daily", **filters, as_of="2024-02-01T10:00:02Z")["rows"]
    final = source.store.query("raw_eod_daily", **filters)["rows"]
    assert {row["batch_id"] for row in prior} == {initial["batch_id"]}
    assert all(row["adjusted_close"] == 50.5 for row in prior)
    assert {row["batch_id"] for row in final} == {rebuilt["batch_id"]}
    assert all(row["source_id"].startswith(f"numeric:{rebuilt['batch_id']}:") for row in final)
    assert source.store.query("raw_eod_daily", **filters, versions=True)["total"] == 4
    assert source.store.query("raw_eod_daily", batch_ids=[])["total"] == 0
    with source.store.engine.connect() as connection:
        receipt = connection.execute(select(batches.c.details).where(batches.c.id == rebuilt["batch_id"])).scalar_one()
    assert "SPY" in receipt["price_revision_completed"]


def test_fx_uses_raw_shared_series_and_preserves_response(source):
    class Fmp:
        def get_json(self, endpoint, params):
            return response([dict(symbol="USDHKD", date="2024-01-02", close=7.8123)], endpoint, params)
        def close(self): pass
    source.collector._client = Fmp()
    rows = source.fmp_payload("historical-price-eod/full", {"symbol":"USDHKD","from":"2024-01-02","to":"2024-01-02"})
    assert rows[0]["close"] == 7.8123 and rows[0]["symbol"] == "USDHKD"
    stored = source.store.query("regime_market_daily")["rows"][0]
    assert stored["series_id"] == "fmp:raw:USDHKD"
    assert stored["ohlc_adjustment"] == "provider_raw"
    assert (source.settings.data_root / stored["raw_ref"]).is_file()


def test_official_hk_history_and_holiday_values_are_archived(source, monkeypatch):
    import studio_market.numeric.regime_sources as module
    hsi = {"indexCode":"00011.00", "indexLevels-5y":[[1704153600000, 3200.5]]}
    monkeypatch.setattr(module, "get_public_bytes", lambda url: response(hsi, url))
    source.hk_public_payload("https://www.hsi.com.hk/data/eng/indexes/00011.00/chart.json")
    assert source.store.query("regime_market_daily",symbols=["hsil:HSCI.HI"])["rows"][0]["close"] == 3200.5
    monkeypatch.setattr(module, "get_public_bytes", lambda url: response({"isHoliday": True,"1 Month": "0"}, url))
    source.hk_public_payload("https://www.hkab.org.hk/api/hibor?year=2024&month=01&day=01")
    hibor = source.store.query("regime_market_daily", symbols=["hkab:hibor"])["rows"][0]
    assert hibor["hibor_1m"] is None and hibor["is_holiday"] is True


def test_datahub_key_is_separate_and_full_response_precedes_read(source, monkeypatch, tmp_path):
    from dataclasses import replace
    from curl_cffi import requests
    key = tmp_path / "datahub-key"
    key.write_text("fixture-only-key")
    source.settings = replace(source.settings, datahub_api_key_file=key)
    captured = []
    payloads = [{"code":0,"data":{"fields":["ts_code","trade_date","adj_factor"],"items":[["510300.SH","20240102",1.75]],"has_more":False}}, {"code":40203,"msg":"private body"}]
    class Session:
        def __init__(self, **kwargs): assert kwargs == {"trust_env":False}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,url,**kwargs):
            captured.append((url,kwargs))
            return SimpleNamespace(status_code=200,content=json.dumps(payloads.pop(0)).encode(),raise_for_status=lambda:None)
    monkeypatch.setattr(requests,"Session",Session)
    rows, more = source.datahub_page("fund_adj",{"ts_code":"510300.SH","fields":"ts_code,trade_date,adj_factor"})
    assert rows == [{"ts_code":"510300.SH","trade_date":"20240102","adj_factor":1.75}]
    assert more is False
    assert captured[0][1]["headers"]["X-API-Key"] == "fixture-only-key"
    assert source.store.query("regime_market_daily")["rows"][0]["series_id"] == "datahub:fund_adj:510300.SH"
    with pytest.raises(DataHubIntermittentPermissionError, match="intermittent") as caught:
        source.datahub_page("fund_adj",{})
    assert "private body" not in str(caught.value)
    with pytest.raises(ValueError, match="daily endpoints"):
        source.datahub_page("fut_mins",{})


def test_datahub_rest_page_size_and_actual_capture_preserve_pagination(source, monkeypatch, tmp_path):
    from dataclasses import replace
    from curl_cffi import requests
    key = tmp_path / "datahub-key"
    key.write_text("fixture-only-key")
    source.settings = replace(source.settings, datahub_api_key_file=key)
    requested = {"ts_code": "510300.SH", "fields": "ts_code,trade_date,adj_factor", "limit": 6000}
    wire = []
    pages = [(["20240103", "20240102"], True), (["20240101"], False)]
    class Session:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            wire.append(dict(kwargs["params"]))
            dates, more = pages.pop(0)
            body = {"code": 0, "data": {"fields": ["ts_code", "trade_date", "adj_factor"],
                    "items": [["510300.SH", day, 1.75] for day in dates], "has_more": more}}
            return SimpleNamespace(status_code=200, content=json.dumps(body).encode(), raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "Session", Session)
    rows = []
    while True:
        page, more = source.datahub_page("fund_adj", {**requested, "offset": len(rows)})
        rows.extend(page)
        if not more:
            break
    assert [row["trade_date"] for row in rows] == ["20240103", "20240102", "20240101"]
    assert [params["offset"] for params in wire] == [0, 2]
    assert all(params["limit"] == 5000 for params in wire)
    assert requested["limit"] == 6000
    with source.store.engine.connect() as connection:
        captures = connection.execute(select(batches.c.details)).scalars().all()
    assert {json.dumps(capture["parameters"], sort_keys=True) for capture in captures} == {
        json.dumps(params, sort_keys=True) for params in wire}
    assert source.store.query("regime_market_daily")["total"] == 3

    # The local replica has no gateway cap and keeps its original paging order.
    source.role = "replica"
    monkeypatch.setattr(requests, "Session", lambda **kwargs: pytest.fail("replica acquired data"))
    first, more = source.datahub_page("fund_adj", {**requested, "offset": 0, "limit": 2})
    last, last_more = source.datahub_page("fund_adj", {**requested, "offset": 2, "limit": 6000})
    assert first + last == rows
    assert more and not last_more


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 408, 429, 500, 503])
def test_datahub_http_failures_preserve_only_transient_transport_errors(source, monkeypatch, tmp_path, status):
    from dataclasses import replace
    from curl_cffi import requests
    from curl_cffi.requests.exceptions import RequestException
    key = tmp_path / "datahub-key"
    key.write_text("fixture-only-key")
    source.settings = replace(source.settings, datahub_api_key_file=key)
    failure = RequestException("private provider response")
    failure.response = SimpleNamespace(status_code=status)
    def fail():
        raise failure
    class Session:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=status, content=b"private provider response", raise_for_status=fail)
    monkeypatch.setattr(requests, "Session", Session)
    deterministic = status < 500 and status not in {408, 429}
    with pytest.raises(ValueError if deterministic else RequestException) as caught:
        source.datahub_page("index_daily", {"ts_code": "000985.CSI", "limit": 6000})
    if deterministic:
        assert str(caught.value) == f"DataHub daily request rejected with HTTP {status}"
    else:
        assert caught.value is failure
    with source.store.engine.connect() as connection:
        assert connection.execute(select(batches.c.id)).all() == []


def test_replica_reads_delivered_rows_without_credentials_or_network(source, monkeypatch):
    source.store.ingest("regime_market_daily", [[{"series_id":"fmp:raw:USDHKD", "date":date(2024,1,2), "provider_symbol":"USDHKD", "close":7.8}]], source="fmp")
    source.role = "replica"
    monkeypatch.setattr(source.collector, "raw_eod", lambda *args: pytest.fail("replica acquired external data"))
    rows = source.fmp_payload("historical-price-eod/full", {"symbol":"USDHKD","from":"2024-01-02","to":"2024-01-02"})
    assert rows[0]["close"] == 7.8
    assert source.provenance[0]["batch_id"] == rows[0]["batch_id"]
    assert source.fmp_payload("historical-price-eod/non-split-adjusted", {"symbol":"SPY","from":"2024-01-02","to":"2024-01-02"}) == []


def test_raw_partial_dates_continue_and_replica_preserves_only_relevant_evidence(source):
    class Fmp:
        observed = datetime(2026, 9, 8, tzinfo=UTC)
        payload = [dict(symbol="USDHKD", date="2026-09-07", close=7.8),
                   dict(symbol="USDHKD", date="2026-09-06", close=0)]
        def get_json(self, endpoint, params):
            value = response(self.payload, endpoint, params)
            value.received_at = self.observed
            return value
        def close(self): pass
    client = Fmp()
    source.collector._client = client
    params = {"symbol": "USDHKD", "from": "2026-09-06", "to": "2026-09-08"}
    first = source.fmp_payload("historical-price-eod/full", params)
    original_rows = source.store.query("regime_market_daily")["rows"]
    assert [r["date"] for r in first] == ["2026-09-07"]
    partial = source.provenance[-1]
    assert partial["validation"]["status"] == "partial"
    assert partial["validation"]["accepted_row_count"] == 1
    assert json.loads(gzip.decompress((source.settings.data_root / partial["raw_ref"]).read_bytes())) == client.payload
    client.observed = datetime(2026, 9, 9, tzinfo=UTC)
    client.payload.append(dict(symbol="USDHKD", date="2026-09-08", close=7.9, volume=None))
    assert len(source.fmp_payload("historical-price-eod/full", params)) == 2
    source.role = "replica"
    source.provenance.clear()
    assert len(source.fmp_payload("historical-price-eod/full", params)) == 2
    assert source.provenance[0]["validation"]["status"] == "partial"

    source.role = "collector"
    client.observed = datetime(2026, 9, 10, tzinfo=UTC)
    client.payload[1]["close"] = 7.7
    source.fmp_payload("historical-price-eod/full", params)
    source.role = "replica"
    source.provenance.clear()
    assert len(source.fmp_payload("historical-price-eod/full", params)) == 3
    assert all("validation" not in capture for capture in source.provenance)
    assert source.store.query("regime_market_daily", as_of="2026-09-08T23:59:00Z")["rows"] == original_rows
    with source.store.engine.connect() as connection:
        original = connection.execute(select(batches.c.details).where(batches.c.id == partial["batch_id"])).scalar_one()
    assert original["validation"]["status"] == "partial"


@pytest.mark.parametrize("bad", [dict(close=0), dict(low=-1), dict(low=None), dict(volume=-1), dict(close=7.9)])
def test_raw_invalid_or_conflicting_duplicate_rejects_entire_known_day(source, bad):
    row = dict(symbol="USDHKD", date="2026-09-06", close=7.8)
    class Fmp:
        def get_json(self, endpoint, params):
            return response([row, {**row, **bad}, row, {**row, "date":"2026-09-07"}], endpoint, params)
        def close(self): pass
    source.collector._client = Fmp()
    rows = source.fmp_payload("historical-price-eod/full", {"symbol":"USDHKD","from":"2026-09-06","to":"2026-09-07"})
    assert [row["date"] for row in rows] == ["2026-09-07"]
    assert source.provenance[-1]["validation"]["rejected_rows"][0]["row_index"] == 1


@pytest.mark.parametrize("bad", [dict(symbol="USDHKD",date="2026-09-06",close=0),
                                  dict(symbol="OTHER",date="2026-09-06",close=7.8),
                                  dict(symbol="USDHKD",date="invalid",close=7.8)])
def test_empty_or_unidentifiable_raw_capture_retains_failure_and_corrected_replica_clears_it(source, bad):
    class Fmp:
        observed = datetime(2026, 9, 8, tzinfo=UTC)
        payload = [bad]
        def get_json(self, endpoint, params):
            value = response(self.payload, endpoint, params)
            value.received_at = self.observed
            return value
        def close(self): pass
    client = Fmp()
    source.collector._client = client
    params = {"symbol":"USDHKD","from":"2026-09-06","to":"2026-09-07"}
    if bad.get("close") == 0:
        assert source.fmp_payload("historical-price-eod/full", params) == []
    else:
        with pytest.raises(ValueError):
            source.fmp_payload("historical-price-eod/full", params)
    source.role = "replica"
    source.provenance.clear()
    assert source.fmp_payload("historical-price-eod/full", params) == []
    assert source.provenance[0]["validation"]["status"] in {"failed", "partial"}
    assert (source.settings.data_root / source.provenance[0]["raw_ref"]).is_file()
    source.provenance.clear()
    assert source.fmp_payload("historical-price-eod/full", {**params,"from":"2026-09-08","to":"2026-09-08"}) == []
    assert source.provenance == []
    source.role = "collector"
    client.payload = [dict(symbol="USDHKD", date="2026-09-06", close=7.8)]
    client.observed = datetime(2026, 9, 9, tzinfo=UTC)
    source.fmp_payload("historical-price-eod/full", params)
    source.role = "replica"
    source.provenance.clear()
    assert len(source.fmp_payload("historical-price-eod/full", params)) == 1
    assert all("validation" not in capture for capture in source.provenance)


def _capture_raw(source, rows, *, start="2026-09-06", end="2026-09-07", observed=8, symbol="USDHKD"):
    class Fmp:
        def get_json(self, endpoint, params):
            value = response(rows, endpoint, params)
            value.received_at = datetime(2026, 9, observed, tzinfo=UTC)
            return value
        def close(self): pass
    source.role = "collector"
    source.collector._client = Fmp()
    return source.fmp_payload("historical-price-eod/full", {"symbol":symbol,"from":start,"to":end})


def _read_raw(source, start="2026-09-06", end="2026-09-07"):
    source.role = "replica"
    source.provenance.clear()
    return source.fmp_payload("historical-price-eod/full", {"symbol":"USDHKD","from":start,"to":end})


def test_replica_scopes_nonempty_partial_and_correction_to_actual_requested_dates(source):
    good = dict(symbol="USDHKD", date="2026-09-07", close=7.8)
    bad = {**good,"date":"2026-09-06","close":0}
    _capture_raw(source, [bad,good])
    assert len(_read_raw(source,"2026-09-07","2026-09-07")) == 1
    assert all("validation" not in capture for capture in source.provenance)
    _capture_raw(source,[{**bad,"close":7.7}],end="2026-09-06",observed=9)
    assert len(_read_raw(source)) == 2
    assert len(source.provenance) == 2  # The old valid day still uses its actual batch.
    assert all("validation" not in capture for capture in source.provenance)


def test_replica_retains_new_nonempty_partial_even_when_only_old_fact_is_selected(source):
    good = dict(symbol="USDHKD", date="2026-09-06", close=7.8)
    _capture_raw(source,[good])
    _capture_raw(source,[{**good,"close":0},{**good,"date":"2026-09-07"}],observed=9)
    assert len(_read_raw(source,"2026-09-06","2026-09-06")) == 1
    assert len(source.provenance) == 2
    assert sum(c.get("validation",{}).get("status") == "partial" for c in source.provenance) == 1


@pytest.mark.parametrize("bad", [dict(symbol="USDHKD", date="2026-09-06", close=0),
                                  dict(symbol="USDHKD", date="invalid", close=7.8)])
def test_complete_empty_same_series_capture_resolves_weekend_warning_but_other_series_cannot(source, bad):
    if bad["date"] == "invalid":
        with pytest.raises(ValueError):
            _capture_raw(source,[bad],end="2026-09-06")
    else:
        _capture_raw(source,[bad],end="2026-09-06")
    _capture_raw(source,[],end="2026-09-06",observed=9,symbol="USDCNH")
    assert _read_raw(source,"2026-09-06","2026-09-06") == []
    assert source.provenance[0]["validation"]["status"] in {"failed","partial"}
    _capture_raw(source,[],end="2026-09-06",observed=9)
    assert _read_raw(source,"2026-09-06","2026-09-06") == []
    assert all("validation" not in capture for capture in source.provenance)
