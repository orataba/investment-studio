from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from studio_market.config import MarketSettings
from studio_market.futu.catalog import current_catalog_snapshot, sync_catalog
from studio_market.futu.collect import FutuHistoryError
from studio_market.futu.quotes import FutuQuoteError
from studio_market.numeric import NumericStore
from studio_market.numeric.schema import batches, current


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    result.create_schema_for_testing()
    yield result
    result.close()


def capture(params, payload):
    return SimpleNamespace(endpoint="fixture", params=params, body=json.dumps(payload).encode(),
                           received_at=datetime.now(UTC))


def listings(*, markets):
    rows = [dict(symbol=symbol, exchange_ticker=ticker, futu_stock_type="ETF", instrument_type="etf",
                 currency=currency, currency_basis="exchange_directory", exchange_code="XHKG", name="ETF")
            for symbol, ticker, currency in [("HK.02822", "2822.HK", "HKD"), ("HK.09822", "9822.HK", "USD")]]
    return rows, [capture({}, rows)]


class Quotes:
    def __init__(self, *, missing=(), bad_currency=False, fail=False):
        self.missing = missing
        self.bad_currency = bad_currency
        self.fail = fail

    def basic_info_many(self, symbols):
        if self.fail:
            raise FutuQuoteError("Futu permission denied")
        rows = [dict(symbol=symbol, stock_id=int(symbol[3:]), stock_type="ETF", listing_date="2010-01-01",
                     name="ETF", sc_name="基金", state="NORMAL") for symbol in symbols if symbol not in self.missing]
        if self.bad_currency:
            rows[0]["currency"] = "CNY"
        return SimpleNamespace(rows=rows, captures=(capture({"code_list": symbols}, rows),))


def sync(store, quotes):
    return sync_catalog(store.settings, markets=("hk",), store=store, client=quotes, listing_loader=listings)


def test_preload_preserves_counter_currencies_and_does_not_claim_history_coverage(store):
    result = sync(store, Quotes())
    assert result["active_count"] == 2
    rows = store.latest("futu_security_catalog")["rows"]
    assert {(row["exchange_ticker"], row["currency"]) for row in rows} == {("2822.HK", "HKD"), ("9822.HK", "USD")}
    assert all(row["currency_verified"] and row["active"] for row in rows)
    assert all(row["coverage_status"] == "symbol_recognized_history_not_yet_verified" for row in rows)
    assert store.query("futu_raw_eod_daily")["rows"] == []


def test_complete_sync_publishes_explicit_withdrawal_and_audit_versions(store):
    sync(store, Quotes())
    result = sync(store, Quotes(missing={"HK.09822"}))
    assert result["active_count"] == 1 and result["withdrawn_count"] == 1
    latest = store.latest("futu_security_catalog")["rows"]
    assert [row["symbol"] for row in latest if row["active"]] == ["HK.02822"]
    assert store.query("futu_security_catalog", symbols=["HK.09822"], versions=True)["total"] == 2


@pytest.mark.parametrize("quotes,exception", [
    (Quotes(fail=True), FutuQuoteError),
    (Quotes(bad_currency=True), FutuHistoryError),
    (Quotes(missing={"HK.02822", "HK.09822"}), FutuHistoryError),
])
def test_failed_sync_keeps_previous_catalog(store, quotes, exception):
    sync(store, Quotes())
    before = store.latest("futu_security_catalog")["rows"]
    with pytest.raises(exception):
        sync(store, quotes)
    assert store.latest("futu_security_catalog")["rows"] == before


def complete_catalog(store):
    def both_markets(*, markets):
        assert set(markets) == {"hk", "us"}
        rows, _ = listings(markets=("hk",))
        rows.append(dict(rows[0], symbol="US.SPY", exchange_ticker="SPY", currency="USD", exchange_code="ARCX"))
        return rows, [capture({}, rows)]

    class BothQuotes(Quotes):
        def basic_info_many(self, symbols):
            result = super().basic_info_many([symbol for symbol in symbols if symbol.startswith("HK.")])
            rows = [*result.rows, dict(symbol="US.SPY", stock_id=99, stock_type="ETF", listing_date="1993-01-29", name="SPY", state="NORMAL")]
            return SimpleNamespace(rows=rows, captures=(capture({"code_list": symbols}, rows),))

    return sync_catalog(store.settings, store=store, client=BothQuotes(), listing_loader=both_markets)


def test_release_reuse_requires_a_complete_current_retained_snapshot(store):
    assert current_catalog_snapshot(store.settings, store=store) is None
    completed = complete_catalog(store)
    reused = current_catalog_snapshot(store.settings, store=store)
    assert reused["batch_id"] == completed["batch_id"]
    assert reused["status"] == "ready" and reused["reused"] is True
    assert reused["active_count"] == reused["row_count"] == 3
    # A later HK-only capture cannot stand in for the required HK+US snapshot.
    sync(store, Quotes())
    assert current_catalog_snapshot(store.settings, store=store) is None


def test_failed_acquisition_is_not_hidden_by_an_earlier_same_day_complete_catalog(store):
    completed = complete_catalog(store)
    before = store.latest("futu_security_catalog")["rows"]
    with pytest.raises(FutuQuoteError):
        sync_catalog(store.settings, store=store, client=Quotes(fail=True), listing_loader=listings)
    assert store.latest("futu_security_catalog")["rows"] == before
    assert current_catalog_snapshot(store.settings, store=store) is None
    with store.engine.connect() as connection:
        latest = connection.execute(select(batches).where(batches.c.dataset == "futu_security_catalog")
                                    .order_by(batches.c.started_at.desc()).limit(1)).mappings().one()
    assert latest["id"] != completed["batch_id"] and latest["status"] == "failed"
    assert latest["published_at"] is None


@pytest.mark.parametrize("invalid", [
    "failed", "writing", "stale_capture", "stale_publication", "future_capture",
    "active_count", "candidate_count", "missing_capture_metadata", "missing_current_row",
])
def test_release_reuse_rejects_incomplete_or_noncurrent_catalog_evidence(store, invalid):
    completed = complete_catalog(store)
    batch_id = completed["batch_id"]
    with store.engine.begin() as connection:
        batch = connection.execute(select(batches).where(batches.c.id == batch_id)).mappings().one()
        details = dict(batch["details"])
        changes = {}
        if invalid in {"failed", "writing"}:
            changes["status"] = invalid
        elif invalid == "stale_publication":
            changes["published_at"] = datetime.now(UTC) - timedelta(days=1)
        elif invalid in {"stale_capture", "future_capture"}:
            parts = [dict(part) for part in details["source_parts"]]
            parts[0]["observed_at"] = (datetime.now(UTC) + timedelta(days=-1 if invalid == "stale_capture" else 1)).isoformat()
            details["source_parts"] = parts
        elif invalid == "missing_capture_metadata":
            del details["source_parts"]
        elif invalid in {"active_count", "candidate_count"}:
            details[invalid] += 1
        elif invalid == "missing_current_row":
            connection.execute(delete(current).where(current.c.dataset == "futu_security_catalog", current.c.symbol == "US.SPY"))
        connection.execute(update(batches).where(batches.c.id == batch_id).values(details=details, **changes))
    assert current_catalog_snapshot(store.settings, store=store) is None


def test_release_reuse_checks_actual_batch_rows_not_only_metadata(store, monkeypatch):
    complete_catalog(store)
    original = store.query

    def truncated(*args, **kwargs):
        result = original(*args, **kwargs)
        return {**result, "rows": result["rows"][:-1]}

    monkeypatch.setattr(store, "query", truncated)
    assert current_catalog_snapshot(store.settings, store=store) is None
