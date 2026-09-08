"""Research consumes shared observations without a registered company per constituent."""
from datetime import UTC, datetime

import pytest

from watchlist_app.services import sector_market_data as market
from watchlist_app.services.sector_estimates import read_estimate_evidence


@pytest.fixture
def market_store(client):
    store = market.numeric_store()
    def ingest(name, rows, clock="2026-09-05T08:00:00+00:00"):
        return store.ingest(name, [rows], source="fixture", observed_at=datetime.fromisoformat(clock))
    ingest("etf_info", [{"symbol": "XLK", "name": "Technology ETF"}])
    ingest("etf_holdings", [
        {"etf_symbol": "XLK", "holding_key": "AAA", "holding_symbol": "AAA", "holding_name": "Alpha", "weight_percent": 99.9, "snapshot_date": "2026-09-05"},
        {"etf_symbol": "XLK", "holding_key": "cash", "holding_symbol": "", "holding_name": "US DOLLAR", "weight_percent": 0.1, "snapshot_date": "2026-09-05"},
    ])
    ingest("company_profiles", [{"symbol": "AAA", "company_name": "Alpha", "currency": "USD", "is_etf": False, "is_fund": False}])
    ingest("us_eod_daily", [{"symbol": symbol, "date": "2026-09-04", "open": 100, "high": 103, "low": 99, "close": 102, "adjusted_close": 101, "volume": 100} for symbol in ["AAA", "XLK"]])
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": frequency, "target_period_end": "2026-12-31", "revenue_avg": 100, "eps_avg": 2, "num_analysts_revenue": 10, "num_analysts_eps": 9} for frequency in ["annual", "quarter"]])
    return store, ingest


def test_shared_holdings_keep_cash_price_basis_and_unknown_currency(market_store):
    store, _ = market_store
    raw = market.read_sector_market_data(None, " xlk ")
    assert raw["ticker"] == "XLK"
    assert len(raw["holdings"]) == 2
    assert sum(row["weight_percent"] for row in raw["holdings"]) == 100
    equity, cash = raw["holdings"]
    assert cash["holding_type"] == "cash"
    assert equity["company_profile"]["currency"] == "USD"
    assert equity["annual_estimates"][0]["currency"] is None
    assert equity["latest_price"]["close"] == 102
    assert equity["latest_price"]["adjusted_close"] == 101
    assert raw["source"]["collected_at"] != raw["source"]["read_at"]
    assert all(store.read_source(sid) for sid in raw["source"]["source_ids"])


def test_shared_latest_and_historical_cutoff_do_not_mix_captures(market_store):
    _, ingest = market_store
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": "annual", "target_period_end": "2026-12-31", "revenue_avg": 150}], "2026-09-06T08:00:00+00:00")
    earlier = market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 5, 12, tzinfo=UTC))
    assert earlier["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 100
    assert market.read_sector_market_data(None, "XLK")["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 150
    assert market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 4, tzinfo=UTC)) is None
    assert market.read_sector_market_data(None, "XLE") is None
    with pytest.raises(ValueError, match="11"):
        market.read_sector_market_data(None, "SPY")
    with pytest.raises(ValueError, match="timezone"):
        market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 5))


def test_two_captures_compare_without_running_research_or_registering_constituents(market_store):
    _, ingest = market_store
    baseline = read_estimate_evidence(None, "xlk")
    assert baseline["status"] == "baseline"
    ingest("financial_statements", [{"symbol": "AAA", "statement_type": "income", "period_end": "2026-06-30", "fiscal_year": 2026, "fiscal_period": "Q2", "reported_currency": "USD"}], "2026-09-04T08:00:00+00:00")
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": frequency, "target_period_end": "2026-12-31", "revenue_avg": 110, "eps_avg": 2, "num_analysts_revenue": 10, "num_analysts_eps": 9} for frequency in ["annual", "quarter"]], "2026-09-06T08:00:00+00:00")
    result = read_estimate_evidence(None, "xlk", as_of=datetime(2026, 9, 6, 12, tzinfo=UTC))
    assert result["status"] == "comparable"
    assert len(result["changes"]) == 2
    assert all(row["delta"] == 10 and row["currency"] == "USD" for row in result["changes"])
    assert result["source_ids"]
    earlier = read_estimate_evidence(None, "xlk", as_of=datetime(2026, 9, 5, 12, tzinfo=UTC))
    assert earlier["status"] == "baseline" and not earlier["changes"]
    assert read_estimate_evidence(None, "512880")["status"] == "unsupported"
