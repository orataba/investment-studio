from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from studio_data.services.fmp import crypto
from studio_data.services import market_data_ops


@pytest.fixture
def stores(monkeypatch):
    instrument = {"instrument_type": "crypto", "currency": "USD",
                  "source_settings": {"source_mode": "api", "source_api_profile": "fmp"},
                  "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "fmp:BTCUSD"}]}
    monkeypatch.setattr(crypto, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda _: instrument)
    points, bars = Mock(return_value=1), Mock(return_value=1)
    def publish(*, instrument_id, market_data_rows, price_bar_rows):
        return (points(instrument_id=instrument_id, rows=market_data_rows),
                bars(instrument_id=instrument_id, rows=price_bar_rows))
    monkeypatch.setattr(crypto, "upsert_price_history", publish)
    monkeypatch.setattr(crypto, "update_refresh_status", lambda **kwargs: kwargs)
    return instrument, points, bars


def test_spot_uses_completed_utc_days_including_weekends_and_replays_revisions(stores):
    _, points, bars = stores
    source_rows = [{"date": day, "series_id": "BTCUSD", "open": 80, "high": 100, "low": 70,
                    "close": close, "volume": 1000000, "source_id": f"numeric:batch:{i}"}
                   for i, (day, close) in enumerate([("2020-01-01", 81), ("2026-09-06", 90), ("2026-09-08", 99)])]
    store = Mock()
    store.query.return_value = {"rows": source_rows, "total": len(source_rows)}
    result = crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8, 10, tzinfo=UTC))
    assert store.query.call_args.kwargs["end"] == "2026-09-07"
    assert store.query.call_args.args[0] == "market_series_daily"
    assert [row["as_of_date"] for row in points.call_args.kwargs["rows"]] == ["2020-01-01", "2026-09-06"]
    assert all(row["quote_basis"] == "close" and row["currency"] == "USD" for row in points.call_args.kwargs["rows"])
    assert all("volume" not in row and "volume_unit" not in row for row in bars.call_args.kwargs["rows"])
    assert "latest observation 2026-09-06, requested cutoff 2026-09-07 (UTC)" in result["message"]
    assert result["status"] == "blocked"


@pytest.mark.parametrize("change", [{"currency": "USDT"}, {"instrument_type": "etf"}, {"identifiers": []}])
def test_rejects_a_different_quote_or_vehicle_before_writing(stores, change):
    instrument, points, bars = stores
    instrument.update(change)
    with pytest.raises(ValueError, match="BTC/USD spot"):
        crypto.refresh_fmp_crypto_eod("btcusd", store=Mock())
    points.assert_not_called()
    bars.assert_not_called()


@pytest.mark.parametrize("bad_close", ["NaN", "Infinity", None, 0, -1])
def test_invalid_source_close_cannot_publish_a_partial_baseline(stores, bad_close):
    _, points, bars = stores
    store = Mock()
    store.query.return_value = {"rows": [
        {"date": "2026-09-05", "series_id": "BTCUSD", "close": 80},
        {"date": "2026-09-06", "series_id": "BTCUSD", "close": bad_close},
    ], "total": 2}
    with pytest.raises(ValueError, match="valid close"):
        crypto.refresh_fmp_crypto_eod("btcusd", store=store)
    points.assert_not_called()
    bars.assert_not_called()


@pytest.mark.parametrize("source_rows", [[], [{"date": "2026-09-08", "series_id": "BTCUSD", "close": 100}]])
def test_empty_completed_series_is_blocked_without_writes(stores, source_rows):
    _, points, bars = stores
    store = Mock()
    store.query.return_value = {"rows": source_rows, "total": len(source_rows)}
    result = crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))
    assert result["status"] == "blocked"
    assert "no completed BTC/USD spot observations" in result["message"]
    points.assert_not_called()
    bars.assert_not_called()


@pytest.mark.parametrize("hour, expected_cutoff", [(7, "2026-09-06"), (8, "2026-09-07")])
def test_completed_day_boundary_uses_utc_not_the_local_date(stores, hour, expected_cutoff):
    store = Mock()
    store.query.return_value = {"rows": [], "total": 0}
    crypto.refresh_fmp_crypto_eod("btcusd", store=store,
        now=datetime(2026, 9, 8, hour, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert store.query.call_args.kwargs["end"] == expected_cutoff


def test_projection_clock_cannot_silently_assume_the_host_timezone(stores):
    store = Mock()
    with pytest.raises(ValueError, match="clock must include a timezone"):
        crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8))
    store.query.assert_not_called()


def test_unchanged_nonempty_series_is_a_successful_no_change(stores):
    _, points, bars = stores
    points.return_value = bars.return_value = 0
    store = Mock()
    store.query.return_value = {"rows": [{"date": "2026-09-07", "series_id": "BTCUSD", "close": 100}], "total": 1}
    result = crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))
    assert result["status"] == "no_new_data"


def test_real_shared_store_projects_latest_revisions_without_losing_old_source_versions(stores, tmp_path):
    _, points, _ = stores
    store = crypto.NumericStore(crypto.MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / "source"))
    store.create_schema_for_testing()
    original = {"date": "2020-01-01", "series_id": "BTCUSD", "open": 80,
                "high": 100, "low": 70, "close": 81}
    try:
        store.ingest("market_series_daily", [[original]], source="fmp",
            observed_at=datetime(2026, 9, 6, tzinfo=UTC))
        crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))
        assert points.call_args.kwargs["rows"][0]["value"] == Decimal("81")
        store.ingest("market_series_daily", [[{**original, "close": 82}]], source="fmp",
            observed_at=datetime(2026, 9, 7, tzinfo=UTC))
        crypto.refresh_fmp_crypto_eod("btcusd", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))
        assert points.call_args.kwargs["rows"][0]["value"] == Decimal("82")
        assert store.query("market_series_daily", versions=True)["total"] == 2
        assert store.query("market_series_daily", as_of="2026-09-06T12:00:00+00:00")["rows"][0]["close"] == 81
    finally:
        store.close()


@pytest.mark.parametrize("source", ["configured", "fmp"])
def test_regular_refresh_routes_crypto_to_its_spot_projection(stores, monkeypatch, source):
    project = Mock(return_value={"instrument_id": "btcusd"})
    monkeypatch.setattr(crypto, "refresh_fmp_crypto_eod", project)
    assert market_data_ops.refresh_market_data(instrument_id="btcusd", updated_by="test", source=source) == {"instrument_id": "btcusd"}
    project.assert_called_once_with("btcusd", full_history=False)
