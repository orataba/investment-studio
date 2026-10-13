"""Wind prices use explicit identity, closed sessions and atomic publication."""
from datetime import UTC, date, datetime
import gzip
import json
from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core.db_models import Instrument, InstrumentRegistryBase
from investment_studio_instrument_core.return_semantics import (
    analytical_return_quote_bases, confirmed_total_return_quote_bases,
)
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.wind import WindError
from studio_data.services import wind_index as wind


NOW = datetime(2026, 10, 13, 8, tzinfo=UTC)
DAYS = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]


def response(days=DAYS):
    columns = ["TIME", "OPEN", "MATCH", "HIGH", "LOW", "TURNOVER", "VOLUME"]
    rows = [[day + "T00:00:00.000+08:00", "6000.00", "6001.25", "6002", "5999", "1500000000000", "100000000000"]
            for day in days]
    return {"content": [{"type": "text", "text": json.dumps({"data": {
        "columns": [{"name": name, "type": "string"} for name in columns], "rows": rows,
        "unit": {"TURNOVER 单位：": "元"}}, "error": None}, ensure_ascii=False)}], "isError": False}


def mutate(payload, function):
    inner = json.loads(payload["content"][0]["text"])
    function(inner["data"])
    payload["content"][0]["text"] = json.dumps(inner, ensure_ascii=False)
    return payload


@pytest.fixture
def registry(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
    InstrumentRegistryBase.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    record = core.create_instrument(sessions, instrument_name="万得全A", instrument_type="index", currency="CNY",
        exchange_code=None, identifiers=[dict(identifier_type="ticker", identifier_value="881001.WI", is_primary=True)])
    iid = record["instrument_id"]
    core.upsert_source_settings(sessions, instrument_id=iid, source_mode="api", source_email="",
        source_location="Wind index", source_api_profile="wind", source_email_rules=[],
        expected_frequency="daily", market_calendar="XSHG", return_semantics="price_return",
        history_start_date=date(2026, 10, 8))
    monkeypatch.setattr(wind, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(wind, "get_instrument", lambda identifier: core.get_instrument(sessions, identifier))
    monkeypatch.setattr(wind, "get_price_bars", lambda **kwargs: core.get_price_bars(sessions, **kwargs))
    monkeypatch.setattr(wind, "update_refresh_status", lambda **kwargs: core.update_refresh_status(sessions, **kwargs))
    monkeypatch.setattr(wind, "get_settings", lambda: SimpleNamespace(wind_api_key=SecretStr("fixture-secret")))
    provider = Mock(return_value=response())
    monkeypatch.setattr(wind, "query", provider)
    market = NumericStore(MarketSettings(database_url=f"sqlite:///{tmp_path / 'numeric.db'}", data_root=tmp_path / "market"))
    market.create_schema_for_testing()
    yield sessions, iid, market, provider
    market.close()
    engine.dispose()


def test_exact_retained_prices_are_usable_price_returns_not_total_returns(registry):
    sessions, iid, market, provider = registry
    result = wind.refresh_wind_index(iid, full_history=True, now=NOW, store=market)
    assert result["refresh_status"]["status"] == "refreshed"
    provider.assert_called_once_with("index_data", "get_index_kline", dict(windcode="881001.WI",
        begin_date="2026-10-08", end_date="2026-10-13", period="1d", aftype="2", count=0), key="fixture-secret")
    detail = core.get_instrument(sessions, iid)
    assert len(detail["market_data"]) == 4
    assert {point["quote_basis"] for point in detail["market_data"]} == {"close"}
    assert {point["provider"] for point in detail["market_data"]} == {wind.PROVIDER}
    contract = dict(instrument_type="index", quote_bases=detail["quote_selection_policy"]["total_return"],
                    source_settings=detail["source_settings"])
    assert analytical_return_quote_bases(**contract) == ["close"]
    assert confirmed_total_return_quote_bases(**contract) == []
    bars = core.get_price_bars(sessions, instrument_id=iid)
    assert len(bars) == 4
    assert all(bar["volume"] is None and bar["volume_unit"] is None and bar["adjustment_factor"] is None for bar in bars)
    assert {bar["turnover_unit"] for bar in bars} == {"CNY"}
    retained = market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"]
    assert len(retained) == 4
    assert all(row["source"] == wind.PROVIDER and row["return_semantics"] == "price_return" for row in retained)
    assert retained[0]["provider_fields"]["VOLUME"] == "100000000000"
    assert json.loads(gzip.decompress((market.settings.data_root / retained[0]["raw_ref"]).read_bytes())) == provider.return_value
    assert "fixture-secret" not in json.dumps(retained)


def test_incremental_overlaps_last_wind_close_and_revisions_are_idempotent(registry):
    sessions, iid, market, provider = registry
    wind.refresh_wind_index(iid, now=NOW, store=market)
    before = core.get_instrument(sessions, iid)
    provider.return_value = response([DAYS[-1]])
    unchanged = wind.refresh_wind_index(iid, now=NOW, store=market)
    assert provider.call_args.args[2]["begin_date"] == DAYS[-1]
    assert unchanged["refresh_status"]["status"] == "no_new_data"
    assert unchanged["market_data_updated_at"] == before["market_data_updated_at"]
    mutate(provider.return_value, lambda data: data["rows"][0].__setitem__(2, "6001.75"))
    revised = wind.refresh_wind_index(iid, now=NOW, store=market)
    assert revised["refresh_status"]["status"] == "refreshed"
    detail = core.get_instrument(sessions, iid)
    assert len(detail["market_data"]) == 4
    assert str(next(point["value"] for point in detail["market_data"] if point["as_of_date"] == DAYS[-1])) == "6001.75"
    assert len(market.query("market_series_daily", symbols=[wind.SYMBOL], versions=True)["rows"]) == 6


def test_before_close_ends_on_last_verified_session_and_rejects_intraday_row(registry):
    sessions, iid, market, provider = registry
    with pytest.raises(wind.WindIndexError, match="outside the requested closed XSHG"):
        wind.refresh_wind_index(iid, now=NOW.replace(hour=6), store=market)
    assert provider.call_args.args[2]["end_date"] == "2026-10-12"
    assert core.get_instrument(sessions, iid)["market_data"] == []
    provider.return_value = response(DAYS[:-1])
    assert wind.refresh_wind_index(iid, now=NOW.replace(hour=6), store=market)["refresh_status"]["status"] == "refreshed"


def test_missing_session_archives_original_but_cannot_fake_complete_migration(registry):
    sessions, iid, market, provider = registry
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis="close",
        as_of_date=date(2026, 10, 9), value="5000", currency="CNY", provider="platform_file_import", status="complete")
    before = core.get_instrument(sessions, iid)
    provider.return_value = response([DAYS[0], *DAYS[2:]])
    with pytest.raises(wind.WindIndexError, match="missing 1 closed XSHG session.*2026-10-09"):
        wind.refresh_wind_index(iid, full_history=True, now=NOW, store=market)
    after = core.get_instrument(sessions, iid)
    assert after["market_data"] == before["market_data"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert after["refresh_status"]["status"] == "blocked"
    assert list((market.settings.data_root / "numeric/raw/wind").rglob("*.gz"))
    assert market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"] == []


def test_migration_requires_explicit_full_history_and_preserves_raw_evidence(registry):
    sessions, iid, market, provider = registry
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis="close",
        as_of_date=date(2026, 10, 9), value="5000", currency="CNY", provider="platform_file_import", status="complete")
    with pytest.raises(wind.WindIndexError, match="explicit full_history"):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    provider.assert_not_called()
    wind.refresh_wind_index(iid, full_history=True, now=NOW, store=market)
    detail = core.get_instrument(sessions, iid)
    assert len(detail["market_data"]) == 4
    assert {row["provider"] for row in detail["market_data"]} == {wind.PROVIDER}


@pytest.mark.parametrize("change", [
    lambda data: data["rows"].append(data["rows"][0]),
    lambda data: data["rows"].reverse(),
    lambda data: data["columns"].__setitem__(1, data["columns"][2]),
    lambda data: data["rows"][0].__setitem__(0, "2026-10-08"),
    lambda data: data["rows"][0].__setitem__(0, "not-a-date"),
    lambda data: data["rows"][0].__setitem__(0, "2026-10-08T01:00:00+08:00"),
    lambda data: data["rows"][0].__setitem__(0, "2026-10-08T00:00:00+09:00"),
    lambda data: data["rows"][0].__setitem__(0, "2026-10-08T16:00:00+00:00"),
    lambda data: data["rows"][0].__setitem__(2, "NaN"),
    lambda data: data["rows"][0].__setitem__(2, "Infinity"),
    lambda data: data["rows"][0].__setitem__(2, "INVALID"),
    lambda data: data["rows"][0].__setitem__(2, None),
    lambda data: data["rows"][0].__setitem__(3, "6000"),
    lambda data: data["rows"][0].__setitem__(5, "-1"),
])
def test_malformed_rows_cannot_publish_any_prices_or_policy(registry, change):
    sessions, iid, market, provider = registry
    before = core.get_instrument(sessions, iid)
    mutate(provider.return_value, change)
    with pytest.raises(wind.WindIndexError):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    after = core.get_instrument(sessions, iid)
    for key in ("market_data", "market_data_updated_at", "quote_selection_policy", "calculation_inputs_updated_at"):
        assert after[key] == before[key]
    assert core.get_price_bars(sessions, instrument_id=iid) == []
    assert after["refresh_status"]["status"] == "blocked"


@pytest.mark.parametrize("field,value", [("source_mode", "manual"), ("source_api_profile", "fmp"),
    ("market_calendar", None), ("return_semantics", "unknown"), ("history_start_date", None),
    ("source_price_multiplier", "0.01")])
def test_missing_explicit_contract_blocks_before_any_paid_query(registry, field, value):
    sessions, iid, market, provider = registry
    with sessions() as session:
        target = session.get(Instrument, iid)
        target.source_settings_json = {**target.source_settings_json, field: value}
        session.commit()
    with pytest.raises(wind.WindIndexError):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    provider.assert_not_called()


def test_source_change_during_network_is_rechecked_under_publish_lock(registry):
    sessions, iid, market, provider = registry
    def changed(*args, **kwargs):
        with sessions() as session:
            target = session.get(Instrument, iid)
            target.source_settings_json = {**target.source_settings_json, "history_start_date": "2026-10-09"}
            session.commit()
        return response()
    provider.side_effect = changed
    with pytest.raises(wind.WindIndexError, match="source settings changed"):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    assert core.get_instrument(sessions, iid)["market_data"] == []
    assert len(market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"]) == 4


def test_price_and_bar_publication_roll_back_together(registry, monkeypatch):
    sessions, iid, market, _ = registry
    before = core.get_instrument(sessions, iid)
    monkeypatch.setattr(wind.shared_store, "_upsert_price_bars", Mock(side_effect=ValueError("private database details")))
    with pytest.raises(wind.WindIndexError, match="ValueError") as error:
        wind.refresh_wind_index(iid, now=NOW, store=market)
    assert "private database" not in str(error.value)
    after = core.get_instrument(sessions, iid)
    for key in ("market_data", "market_data_updated_at", "quote_selection_policy", "calculation_inputs_updated_at"):
        assert after[key] == before[key]


def test_known_provider_errors_are_blocked_without_retry_or_body_leak(registry):
    sessions, iid, market, provider = registry
    provider.side_effect = WindError("Wind 查询受到限流或额度限制（HTTP 429）；本次未取得数据。", 429)
    with pytest.raises(WindError):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    provider.assert_called_once()
    assert core.get_instrument(sessions, iid)["refresh_status"]["status"] == "blocked"


def test_existing_gap_requires_explicit_repair_before_new_paid_call(registry):
    sessions, iid, market, provider = registry
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis="close",
        as_of_date=date(2026, 10, 12), value="5000", currency="CNY", provider=wind.PROVIDER, status="complete")
    with pytest.raises(wind.WindIndexError, match="explicit full_history"):
        wind.refresh_wind_index(iid, now=NOW, store=market)
    provider.assert_not_called()


def test_capture_clock_is_actual_receipt_time_not_historical_request_cutoff(registry):
    _, iid, market, provider = registry
    received = []
    def acquired(*args, **kwargs):
        received.append(datetime.now(UTC))
        return response()
    provider.side_effect = acquired
    wind.refresh_wind_index(iid, now=NOW, store=market)
    row = market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"][0]
    assert datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")) >= received[0]
    assert row["date"] in DAYS


def test_unknown_units_and_missing_optional_values_are_retained_not_invented(registry):
    sessions, iid, market, provider = registry
    def unclear(data):
        data["unit"] = {}
        data["rows"][0][5] = "INVALID"
        data["rows"][0][6] = ""
    mutate(provider.return_value, unclear)
    wind.refresh_wind_index(iid, now=NOW, store=market)
    bars = core.get_price_bars(sessions, instrument_id=iid)
    assert all(bar["turnover"] is None and bar["volume"] is None for bar in bars)
    row = next(row for row in market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"] if row["date"] == DAYS[0])
    assert row["provider_fields"]["TURNOVER"] == "INVALID"
    assert row["provider_fields"]["VOLUME"] == ""


def test_full_history_cannot_overwrite_a_different_quote_basis(registry):
    sessions, iid, market, provider = registry
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis="adjusted_close",
        as_of_date=date(2026, 10, 9), value="5000", currency="CNY", provider="another_provider", status="complete")
    with pytest.raises(wind.WindIndexError, match="non-close"):
        wind.refresh_wind_index(iid, full_history=True, now=NOW, store=market)
    provider.assert_not_called()


def test_mixed_wind_midnight_labels_preserve_daily_session_and_original_clock(registry):
    sessions, iid, market, provider = registry
    mutate(provider.return_value, lambda data: data["rows"][1].__setitem__(0, "2026-10-09T00:00:00.000+00:00"))
    wind.refresh_wind_index(iid, full_history=True, now=NOW, store=market)
    assert [point["as_of_date"] for point in core.get_instrument(sessions, iid)["market_data"]] == DAYS
    row = next(row for row in market.query("market_series_daily", symbols=[wind.SYMBOL])["rows"] if row["date"] == DAYS[1])
    assert row["provider_fields"]["TIME"] == "2026-10-09T00:00:00.000+00:00"
    assert row["date_semantics"] == "provider_daily_session_label"
    assert any("not a disclosure clock" in limitation for limitation in row["limitations"])
