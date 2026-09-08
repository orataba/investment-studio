from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.regime_sources import RegimeSources, DataHubIntermittentPermissionError

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
            return SimpleNamespace(content=json.dumps(payloads.pop(0)).encode(),raise_for_status=lambda:None)
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


def test_replica_reads_delivered_rows_without_credentials_or_network(source, monkeypatch):
    source.store.ingest("regime_market_daily", [[{"series_id":"fmp:raw:USDHKD", "date":date(2024,1,2), "provider_symbol":"USDHKD", "close":7.8}]], source="fmp")
    source.role = "replica"
    monkeypatch.setattr(source.collector, "raw_eod", lambda *args: pytest.fail("replica acquired external data"))
    rows = source.fmp_payload("historical-price-eod/full", {"symbol":"USDHKD","from":"2024-01-02","to":"2024-01-02"})
    assert rows[0]["close"] == 7.8
    assert source.provenance[0]["batch_id"] == rows[0]["batch_id"]
    assert source.fmp_payload("historical-price-eod/non-split-adjusted", {"symbol":"SPY","from":"2024-01-02","to":"2024-01-02"}) == []
