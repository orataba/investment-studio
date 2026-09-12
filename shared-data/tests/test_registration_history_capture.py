from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector
from studio_market.numeric.providers.fmp import FmpResponse, FmpHttpError
from studio_data.services.fmp import eod_capture
from studio_data.services.fmp.client import FmpApiError


NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


class FakeFmp:
    def __init__(self, dates=("2026-09-08",), fail_after=None):
        self.dates = dates
        self.calls = []
        self.fail_after = fail_after

    def get_json(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise FmpHttpError(503)
        adjusted = endpoint.endswith("/dividend-adjusted")
        value = 99 if adjusted else 100
        payload = [{"symbol": params["symbol"], "date": day, "adjOpen": value, "adjHigh": value + 1, "adjLow": value - 1, "adjClose": value, "volume": 1000} for day in self.dates if params["from"] <= day <= params["to"]]
        return FmpResponse(endpoint, params, NOW, json.dumps(payload).encode(), payload)


@pytest.fixture
def capture_env(tmp_path, monkeypatch):
    settings = MarketSettings(f"sqlite+pysqlite:///{tmp_path / 'market.db'}", tmp_path / "objects")
    store = NumericStore(settings)
    store.create_schema_for_testing()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: settings)
    statuses = []
    monkeypatch.setattr(eod_capture, "update_refresh_status", lambda **kwargs: statuses.append(kwargs))
    yield settings, store, statuses
    store.close()


def ensure(store, collector, *, now=NOW):
    eod_capture.ensure_fmp_security_history("shv", symbol="SHV", exchange_code="XNAS", store=store, collector=collector, now=now)


def test_first_registration_archives_full_history_then_reuses_current_data(capture_env):
    settings, store, statuses = capture_env
    client = FakeFmp(dates=("2001-01-03", "2026-09-08"))
    collector = Collector(settings, store=store, client=client)
    ensure(store, collector)
    assert client.calls[0][1]["from"] == "2000-01-03"
    assert client.calls[-1][1]["to"] == "2026-09-08"
    assert {endpoint for endpoint, _ in client.calls} == {"historical-price-eod/non-split-adjusted", "historical-price-eod/dividend-adjusted"}
    rows = store.query("raw_eod_daily", symbols=["SHV"], limit=100)["rows"]
    assert len(rows) == 2
    for row in rows:
        assert row["close"] == 100
        assert row["adjusted_close"] == 99
        assert row["observed_at"].startswith("2026-09-09")
        assert row["batch_id"]
        for field in ("raw_ref", "adjusted_raw_ref"):
            assert json.loads(gzip.decompress((settings.data_root / row[field]).read_bytes()))
    assert statuses == []
    calls = len(client.calls)
    ensure(store, collector)
    assert len(client.calls) == calls


def test_existing_history_fetches_only_latest_overlap_and_missing_closes(capture_env):
    settings, store, statuses = capture_env
    store.ingest("raw_eod_daily", [[{"symbol": "SHV", "date": "2026-09-04", "open": 100, "high": 101, "low": 99, "close": 100, "adjusted_close": 99, "volume": 1000}]], source="fixture", observed_at=NOW)
    client = FakeFmp(dates=("2026-09-04", "2026-09-08"))
    ensure(store, Collector(settings, store=store, client=client))
    assert len(client.calls) == 2
    assert client.calls[0][1] == {"symbol": "SHV", "from": "2026-09-04", "to": "2026-09-08"}
    assert statuses == []


@pytest.mark.parametrize("dates", [(), ("2026-09-04",)])
def test_empty_or_stale_history_does_not_report_ready(capture_env, dates):
    settings, store, statuses = capture_env
    client = FakeFmp(dates=dates)
    with pytest.raises(FmpApiError, match="not ready"):
        ensure(store, Collector(settings, store=store, client=client))
    assert statuses[-1]["status"] == "failed"


def test_partial_collection_failure_is_not_reported_ready(capture_env):
    settings, store, statuses = capture_env
    client = FakeFmp(dates=("2001-01-03",), fail_after=2)
    with pytest.raises(FmpApiError, match="HTTP 503"):
        ensure(store, Collector(settings, store=store, client=client))
    assert store.query("raw_eod_daily", symbols=["SHV"])["total"] == 1
    assert statuses[-1]["status"] == "failed"
    # Retained successful chunks are reusable; retry resumes at their last row.
    retry = FakeFmp(dates=("2001-01-03", "2026-09-08"))
    ensure(store, Collector(settings, store=store, client=retry))
    assert retry.calls[0][1]["from"] == "2001-01-03"


def test_holiday_uses_last_actual_exchange_session(capture_env):
    settings, store, statuses = capture_env
    store.ingest("raw_eod_daily", [[{"symbol": "SHV", "date": "2026-09-04", "open": 100, "high": 101, "low": 99, "close": 100, "adjusted_close": 99, "volume": 1000}]], source="fixture", observed_at=NOW)
    client = FakeFmp()
    ensure(store, Collector(settings, store=store, client=client), now=datetime(2026, 9, 8, 8, tzinfo=UTC))
    assert client.calls == []
    assert statuses == []


@pytest.mark.parametrize("omit_retained_history", [False, True])
def test_current_quote_with_pending_adjustment_requires_complete_history_rebuild(
    capture_env, omit_retained_history,
):
    settings, store, statuses = capture_env
    store.ingest("raw_eod_daily", [[{
        "symbol": "SHV", "date": day, "open": 100, "high": 101,
        "low": 99, "close": 100, "adjusted_close": 99, "volume": 1000,
    } for day in ("2001-01-03", "2026-09-08")]], source="fixture", observed_at=NOW)
    store.ingest("dividends", [], source="fixture", observed_at=NOW, details={
        "price_revision_requests": [{"symbol": "SHV", "effective_date": "2026-09-08"}],
    })
    client = FakeFmp(dates=("2026-09-08",) if omit_retained_history else ("2001-01-03", "2026-09-08"))
    collector = Collector(settings, store=store, client=client)
    if omit_retained_history:
        with pytest.raises(FmpApiError, match="collection failed"):
            ensure(store, collector)
        assert statuses[-1]["status"] == "failed"
    else:
        ensure(store, collector)
        assert statuses == []
    assert client.calls[2][1]["from"] == "2000-01-03"
    assert bool(eod_capture.pending_revisions(
        store, dataset="raw_eod_daily", symbols=["SHV"], end=date(2026, 9, 8),
    )) is omit_retained_history


def test_uncompleted_revision_is_not_ready_even_with_current_quote(capture_env, monkeypatch):
    settings, store, statuses = capture_env
    store.ingest("raw_eod_daily", [[{
        "symbol": "SHV", "date": "2026-09-08", "open": 100, "high": 101,
        "low": 99, "close": 100, "adjusted_close": 99, "volume": 1000,
    }]], source="fixture", observed_at=NOW)
    store.ingest("dividends", [], source="fixture", observed_at=NOW, details={
        "price_revision_requests": [{"symbol": "SHV", "effective_date": "2026-09-08"}],
    })
    collector = Collector(settings, store=store, client=FakeFmp())
    monkeypatch.setattr(collector, "raw_eod", lambda *_args: {"status": "ready"})
    with pytest.raises(FmpApiError, match="pending adjustment revisions"):
        ensure(store, collector)
    assert statuses[-1]["status"] == "failed"


def test_settings_failure_records_safe_status_without_leaking_values(capture_env, monkeypatch):
    _, _, statuses = capture_env
    def invalid_settings():
        raise ValueError("credential-value-must-not-appear")
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", invalid_settings)
    with pytest.raises(FmpApiError) as error:
        eod_capture.ensure_fmp_security_history("shv", symbol="SHV", exchange_code="XNAS")
    assert "credential-value-must-not-appear" not in str(error.value)
    assert statuses[-1]["status"] == "failed"
    assert statuses[-1]["message"] == str(error.value)


def test_collector_initialization_failure_closes_owned_store(capture_env, monkeypatch):
    _, store, statuses = capture_env
    close = Mock(wraps=store.close)
    monkeypatch.setattr(store, "close", close)
    monkeypatch.setattr(eod_capture, "NumericStore", lambda _settings: store)
    monkeypatch.setattr(eod_capture, "Collector", Mock(side_effect=RuntimeError("initialization")))
    with pytest.raises(FmpApiError, match="RuntimeError"):
        eod_capture.ensure_fmp_security_history("shv", symbol="SHV", exchange_code="XNAS")
    close.assert_called_once()
    assert statuses[-1]["status"] == "failed"


@pytest.mark.parametrize("instrument_type", ["equity", "etf"])
@pytest.mark.parametrize("injected_store", [False, True])
def test_instrument_refresh_acquires_prices_unless_store_is_explicitly_injected(
    capture_env, monkeypatch, instrument_type, injected_store,
):
    from studio_data.services.equities import service as equities
    from studio_data.services.etfs import service as etfs
    from studio_data.services.fmp import eod

    settings, store, _ = capture_env
    instrument = {
        "instrument_id": "shv", "instrument_type": instrument_type,
        "currency": "USD", "exchange_code": "XNAS",
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "fmp:SHV"}],
    }
    monkeypatch.setattr(eod, "get_instrument", lambda _id: instrument)
    monkeypatch.setattr(etfs, "get_instrument", lambda _id: instrument)
    client = FakeFmp()
    collector = Collector(settings, store=store, client=client)
    monkeypatch.setattr(eod, "ensure_fmp_security_history", lambda _id, **_kwargs: ensure(store, collector))
    monkeypatch.setattr(eod, "NumericStore", lambda _settings: store)
    publish = Mock(return_value=(1, 1))
    monkeypatch.setattr(eod, "upsert_price_history", publish)
    monkeypatch.setattr(eod, "update_refresh_status", lambda **kwargs: kwargs)

    refresh = equities.refresh_equity_eod if instrument_type == "equity" else etfs.refresh_etf_eod
    result = refresh("shv", store=store) if injected_store else refresh("shv")
    if injected_store:
        assert client.calls == []
        publish.assert_not_called()
        assert result["status"] == "no_new_data"
    else:
        assert client.calls
        assert result["status"] == "refreshed"
        rows = publish.call_args.kwargs["market_data_rows"]
        assert {row["quote_basis"]: row["value"] for row in rows} == {"close": 100, "adjusted_close": 99}
