"""Project shared numerical facts into the sector research input contract."""
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
import re

from investment_studio_instrument_core.listing_contract import SECTOR_ETF_TICKERS
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore


@lru_cache(maxsize=1)
def numeric_store():
    return NumericStore(MarketSettings.from_environment())


def all_rows(store, dataset, *, symbols, as_of, **filters):
    rows, offset = [], 0
    while True:
        page = store.query(dataset, symbols=symbols, as_of=as_of, limit=10000, offset=offset, **filters)
        rows.extend(page["rows"])
        offset += len(page["rows"])
        if offset >= page["total"]:
            return rows


def source_fields(row):
    return {**row, "collected_at": row.get("observed_at"),
            "source_dataset": row.get("source_dataset") or row.get("dataset")}


def reporting_statements(store, symbols, as_of):
    result = {}
    for row in all_rows(store, "financial_statements", symbols=symbols, as_of=as_of):
        if row["statement_type"] != "income":
            continue
        symbol = row["symbol"]
        if symbol not in result or row["period_end"] > result[symbol]["period_end"]:
            result[symbol] = row
    return result


def enrich_estimate(row, statement):
    currency = row.get("currency") or (statement or {}).get("reported_currency")
    return {**source_fields(row), "currency": currency,
            "currency_status": "provider_supplied" if row.get("currency") else
                "inferred_from_reporting_currency" if currency else "not_supplied",
            "currency_source": {"source_id": statement["source_id"], "statement_date": statement["period_end"],
                                "collected_at": statement["observed_at"]} if statement and not row.get("currency") else None}


def holding_type(name, symbol, profile):
    name = (name or "").upper()
    if name.startswith("CONTRA "):
        return "other"
    if name in {"US DOLLAR", "POUND STERLING"}:
        return "cash"
    if "MONEY MARKET" in name:
        return "fund"
    if re.fullmatch(r"(?:IX[A-Z]|XA[RS])[HMUZ]\d{1,2}", symbol or ""):
        return "future"
    if profile:
        return "fund" if profile.get("is_etf") or profile.get("is_fund") else "equity"
    return "unclassified"


def read_sector_market_data(session, ticker: str, *, as_of: datetime | None = None) -> dict | None:
    ticker = ticker.strip().upper()
    if ticker not in SECTOR_ETF_TICKERS:
        raise ValueError("Only the 11 US Select Sector SPDR ETFs are supported")
    cutoff = as_of or datetime.now(UTC)
    if cutoff.tzinfo is None:
        raise ValueError("Sector reference cutoff must include a timezone.")
    store = numeric_store()
    holdings = all_rows(store, "etf_holdings", symbols=[ticker], as_of=cutoff)
    info_rows = store.latest("etf_info", symbols=[ticker], as_of=cutoff)["rows"]
    if not holdings and not info_rows:
        return None
    symbols = sorted({ticker, *(row["holding_symbol"] for row in holdings if row.get("holding_symbol"))})
    profiles = {row["symbol"]: source_fields(row) for row in store.latest(
        "company_profiles", symbols=symbols, as_of=cutoff, limit=100000)["rows"]}
    prices = {row["symbol"]: source_fields(row) for row in store.latest(
        "us_eod_daily", symbols=symbols, as_of=cutoff, limit=100000)["rows"]}
    statements = reporting_statements(store, symbols, cutoff)
    estimates = defaultdict(list)
    for row in all_rows(store, "analyst_estimates", symbols=symbols, as_of=cutoff):
        estimates[row["symbol"]].append(enrich_estimate(row, statements.get(row["symbol"])))
    gaps, normalized = [], []
    for row in holdings:
        symbol = row.get("holding_symbol")
        profile = profiles.get(symbol)
        statement = statements.get(symbol)
        if profile:
            profile = {**profile, "reporting_currency": (statement or {}).get("reported_currency"),
                       "reporting_currency_source": {"source_id": statement["source_id"],
                           "statement_date": statement["period_end"], "collected_at": statement["observed_at"]} if statement else None}
        kind = holding_type(row.get("holding_name"), symbol, profile)
        item = {**source_fields(row), "holding_type": kind, "shares_number": row.get("shares"),
                "as_of_date": row.get("report_date"), "company_profile": profile,
                "annual_estimates": sorted((r for r in estimates[symbol] if r["estimate_period"] == "annual"), key=lambda r:r["target_period_end"]),
                "quarterly_estimates": sorted((r for r in estimates[symbol] if r["estimate_period"] == "quarter"), key=lambda r:r["target_period_end"]),
                "latest_price": prices.get(symbol)}
        normalized.append(item)
        if kind == "unclassified":
            gaps.append({"kind": "unclassified_holding", "symbol": symbol})
        if kind == "equity":
            if not item["latest_price"]:
                gaps.append({"kind": "missing_price", "symbol": symbol})
            for period, field in (("annual", "annual_estimates"), ("quarter", "quarterly_estimates")):
                if not any(r["target_period_end"] >= cutoff.date().isoformat() for r in item[field]):
                    gaps.append({"kind": f"no_forward_{period}_estimates", "symbol": symbol})
    normalized.sort(key=lambda r: (-(r.get("weight_percent") or 0), r["holding_key"]))
    if not holdings:
        gaps.append({"kind": "missing_holdings", "symbol": ticker})
    if not info_rows:
        gaps.append({"kind": "missing_etf_info", "symbol": ticker})
    if ticker not in prices:
        gaps.append({"kind": "missing_price", "symbol": ticker})
    facts = [*holdings, *info_rows, *profiles.values(), *prices.values(), *statements.values(),
             *(row for rows in estimates.values() for row in rows)]
    return {"ticker": ticker,
        "source": {"provider": "FMP", "storage": "market_data + Parquet", "as_of": cutoff.isoformat(),
                   "collected_at": max((row["observed_at"] for row in facts), default=None),
                   "read_at": datetime.now(UTC).isoformat(),
                   "source_ids": sorted({row["source_id"] for row in facts}),
                   "semantics": {"close": "Split-adjusted close; excludes dividend adjustment.",
                       "adjusted_close": "Split and dividend adjusted close, matched to the same market date.",
                       "snapshot_date": "Observed holdings date; a provider report date is retained separately when supplied.",
                       "estimates": "Complete annual/quarter observations retained independently of Watchlist membership.",
                       "estimate_currency": "Provider currency or explicit inference from an available income statement; never quote currency.",
                       "target_period_end": "Forecast fiscal period, not publication or acquisition time."}},
        "etf": {"info": source_fields(info_rows[0]) if info_rows else None, "latest_price": prices.get(ticker)},
        "holdings": normalized,
        "dataset_status": [{"dataset": dataset, "status": "available" if rows else "missing", "row_count": len(rows)}
                           for dataset, rows in (("etf_holdings", holdings), ("etf_info", info_rows),
                               ("company_profiles", profiles), ("us_eod_daily", prices), ("analyst_estimates", estimates))],
        "gaps": gaps}
