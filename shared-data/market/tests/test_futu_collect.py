from datetime import UTC, date, datetime
import json
import gzip
from types import SimpleNamespace

import pytest

from studio_market.config import MarketSettings
from studio_market.futu.collect import FutuHistoryError, collect_history
from studio_market.futu.quotes import FutuQuoteError
from studio_market.numeric import NumericStore
from studio_market.numeric.replication import export_bundle, import_bundle


@pytest.fixture
def store(tmp_path):
    market = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    market.create_schema_for_testing()
    yield market
    market.close()


class Quotes:
    def __init__(self, *, days=("2026-10-06", "2026-10-07"), stock_id=82, confirmed_id=None, fail=False):
        self.days = days
        self.stock_id = stock_id
        self.confirmed_id = confirmed_id or stock_id
        self.fail = fail
        self.calls = []

    def capture(self, endpoint, params, payload):
        return SimpleNamespace(endpoint=endpoint, params=params, payload=payload,
                               received_at=datetime(2026, 10, 8, 2, 0, len(self.calls), tzinfo=UTC),
                               body=json.dumps(payload).encode())

    def basic_info(self, symbol):
        repeated = any(call[0] == "basic" for call in self.calls)
        self.calls.append(("basic", symbol))
        info = dict(symbol=symbol, code=symbol, stock_id=self.confirmed_id if repeated else self.stock_id,
                    stock_type="ETF", listing_date="2026-10-06", name="Test ETF")
        return SimpleNamespace(info=info, captures=(self.capture("stock-basicinfo", {"code_list": [symbol]}, info),))

    def history(self, symbol, start, end):
        self.calls.append(("history", symbol, start, end))
        if self.fail:
            raise FutuQuoteError("Futu history unavailable")
        rows = tuple(dict(symbol=symbol, date=day, open=10.0, high=11.0, low=9.0, close=10.5,
                          volume=1234, volume_unit="shares") for day in self.days)
        return SimpleNamespace(rows=rows, captures=(self.capture("history-kline", {"start": start.isoformat(), "end": end.isoformat()}, list(rows)),))

    def corporate_actions(self, symbol):
        self.calls.append(("actions", symbol))
        return SimpleNamespace(rows=(), captures=(self.capture("corporate-actions/rehab", {"divi_mode": "include_divi"}, {"rehabs": []}),))


def run(store, client, **kwargs):
    return collect_history(store.settings, "HK.02822", date(2000, 1, 1), date(2026, 10, 8),
                           store=store, client=client, now=datetime(2026, 10, 8, 2, tzinfo=UTC), **kwargs)


def test_capture_identity_clock_and_raw_price_meaning(store):
    quotes = Quotes()
    result = run(store, quotes)
    assert result["status"] == "ready"
    assert result["total_return_available"] is False
    assert quotes.calls[1] == ("history", "HK.02822", date(2026, 10, 6), date(2026, 10, 7))
    rows = store.query("futu_raw_eod_daily")["rows"]
    assert len(rows) == 2
    assert all(row["stock_id"] == 82 and row["ohlc_adjustment"] == "unadjusted" for row in rows)
    assert all("adjusted_close" not in row and "adjustment_factor" not in row for row in rows)
    assert all(row["observed_at"] == row["available_at"] for row in rows)
    assert all(row["collected_at"] < row["observed_at"] for row in rows)
    assert all(row["source_id"].startswith("numeric:") and row["raw_ref"].startswith("numeric/raw/futu/") for row in rows)
    assert store.query("raw_eod_daily")["rows"] == []
    assert store.query("futu_raw_eod_daily", as_of="2026-10-07T23:59:59+00:00")["rows"] == []


def test_scheduled_half_hour_after_hk_close_requests_today_instead_of_yesterday(store):
    quotes = Quotes(days=("2026-10-06", "2026-10-07", "2026-10-08"))
    result = collect_history(store.settings, "HK.02822", date(2026, 10, 6), date(2026, 10, 8),
        store=store, client=quotes, now=datetime(2026, 10, 8, 8, 30, tzinfo=UTC))
    assert quotes.calls[1][-1] == date(2026, 10, 8)
    assert result["end"] == result["last_price_date"] == "2026-10-08"


def test_historical_price_capture_does_not_shorten_current_action_coverage(store):
    result = collect_history(store.settings, "HK.02822", date(2026, 10, 6), date(2026, 10, 6),
        store=store, client=Quotes(days=("2026-10-06",)),
        now=datetime(2026, 10, 8, 2, tzinfo=UTC))
    snapshot = store.latest("futu_corporate_actions", symbols=["HK.02822"])["rows"][0]
    assert result["end"] == "2026-10-06"
    assert snapshot["coverage_end"] == "2026-10-07"
    # Today's still-open session cannot enter the coverage of either dataset.
    assert snapshot["coverage_end"] < "2026-10-08"


@pytest.mark.parametrize("days,missing", [
    (("2026-10-06", "2026-10-07"), "2026-10-08"),
    (("2026-10-06", "2026-10-08"), "2026-10-07"),
])
def test_missing_new_closed_session_is_not_a_successful_partial_history(store, days, missing):
    quotes = Quotes(days=days)
    with pytest.raises(FutuHistoryError, match=f"missing 1 closed trading session.*{missing}"):
        collect_history(store.settings, "HK.02822", date(2026, 10, 6), date(2026, 10, 8),
            store=store, client=quotes, now=datetime(2026, 10, 8, 8, 30, tzinfo=UTC))
    assert store.query("futu_raw_eod_daily")["rows"] == []
    assert store.query("futu_corporate_actions")["rows"] == []
    assert store.query("futu_security_directory")["rows"] == []
    assert list((store.settings.data_root / "numeric/raw/futu").rglob("*.gz"))


@pytest.mark.parametrize("replacement,reason", [
    (Quotes(stock_id=83), "identity changed"),
    (Quotes(confirmed_id=83), "during history"),
    (Quotes(days=("2026-10-07",)), "retained dates"),
    (Quotes(days=()), "no daily prices"),
])
def test_failed_collection_does_not_replace_existing_prices(store, replacement, reason):
    run(store, Quotes())
    before = store.query("futu_raw_eod_daily")["rows"]
    with pytest.raises(FutuHistoryError, match=reason):
        run(store, replacement)
    assert store.query("futu_raw_eod_daily")["rows"] == before


def test_provider_failure_is_not_published(store):
    with pytest.raises(FutuQuoteError):
        run(store, Quotes(fail=True))
    assert store.query("futu_raw_eod_daily")["rows"] == []
    assert store.query("futu_security_directory")["rows"] == []


def test_inconsistent_ohlc_retains_original_evidence_and_reports_partial(store):
    class InconsistentQuotes(Quotes):
        def history(self, symbol, start, end):
            result = super().history(symbol, start, end)
            rows = [dict(row, open=8.5, ohlc_quality="inconsistent") for row in result.rows]
            return SimpleNamespace(rows=rows, captures=(self.capture(
                "history-kline", {"start": start.isoformat(), "end": end.isoformat()}, rows),))

    result = run(store, InconsistentQuotes())
    assert result["status"] == "partial"
    assert result["ohlc_inconsistent_rows"] == 2
    rows = store.query("futu_raw_eod_daily")["rows"]
    assert all(row["open"] == 8.5 and row["low"] == 9.0 and row["close"] == 10.5 for row in rows)
    assert all(row["ohlc_quality"] == "inconsistent" for row in rows)


def test_raw_evidence_and_lineage_survive_existing_bundle_protocol(store, tmp_path):
    run(store, Quotes())
    target = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'replica.db'}", tmp_path / "replica"))
    target.create_schema_for_testing()
    try:
        bundle = tmp_path / "futu.zip"
        export_bundle(store.settings, bundle, names=["futu_security_directory", "futu_raw_eod_daily", "futu_corporate_actions"])
        import_bundle(target.settings, bundle)
        original = store.query("futu_raw_eod_daily")["rows"]
        restored = target.query("futu_raw_eod_daily")["rows"]
        assert original == restored
        actions = target.latest("futu_corporate_actions", symbols=["HK.02822"])["rows"]
        assert actions == store.latest("futu_corporate_actions", symbols=["HK.02822"])["rows"]
        assert (target.settings.data_root / actions[0]["raw_ref"]).is_file()
        for row in restored:
            assert (target.settings.data_root / row["raw_ref"]).is_file()
    finally:
        target.close()


@pytest.mark.parametrize("stock_type,events,coverage", [
    ("ETF", (), "unknown_empty_etf"), ("STOCK", (), "covered_no_actions"),
    ("ETF", ({"ex_div_date": "2025-12-18", "action_types": ["DIVIDEND"],
              "per_cash_div": .38573, "desc_sc": "1股派股息0.38573HKD"},), "provider_reported"),
])
def test_full_action_snapshot_keeps_explicit_coverage_and_immutable_evidence(store, stock_type, events, coverage):
    class ActionQuotes(Quotes):
        def basic_info(self, symbol):
            result = super().basic_info(symbol)
            result.info["stock_type"] = stock_type
            return result
        def corporate_actions(self, symbol):
            self.calls.append(("actions", symbol))
            payload = {"ret_code": 0, "data": {"rehabs": list(events)}}
            return SimpleNamespace(rows=events, captures=(self.capture("corporate-actions/rehab", {"divi_mode": "include_divi"}, payload),))
    result = run(store, ActionQuotes())
    row = store.latest("futu_corporate_actions", symbols=["HK.02822"])["rows"][0]
    assert row["coverage_status"] == coverage
    assert row["coverage_basis"] == "provider_reported_since_listing"
    assert row["coverage_start"] == "2026-10-06" and row["coverage_end"] == "2026-10-07"
    assert row["stock_id"] == 82
    assert json.loads(row["events_json"]) == list(events)
    original = json.loads(gzip.decompress((store.settings.data_root / row["raw_ref"]).read_bytes()))
    assert original["data"]["rehabs"] == list(events)
    assert result["corporate_actions_batch_id"] == row["batch_id"]
    assert row["observed_at"] == row["available_at"]
