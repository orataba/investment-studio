"""Read existing sector ETF facts from the local market research database."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb


SECTOR_ETF_TICKERS = frozenset(
    {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"}
)
_DATASETS = ["fmp_etf_current", "fmp_us_company_profiles", "fmp_analyst_estimates", "fmp_us_eod"]


def _rows(connection, query: str, parameters: list) -> list[dict]:
    cursor = connection.execute(query, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _holding_type(holding: dict, profile: dict | None) -> str:
    name = (holding.get("holding_name") or "").upper()
    symbol = holding.get("holding_symbol") or ""
    if name.startswith("CONTRA "):
        return "other"
    if name in {"US DOLLAR", "POUND STERLING"}:
        return "cash"
    if "MONEY MARKET" in name:
        return "fund"
    # These are the exchange contract codes actually present in the sector ETFs.
    if re.fullmatch(r"(?:IX[A-Z]|XA[RS])[HMUZ]\d{1,2}", symbol):
        return "future"
    if profile:
        return "fund" if profile.get("is_etf") or profile.get("is_fund") else "equity"
    return "unclassified"


def _temporal_json(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported market data value: {type(value).__name__}")


def read_sector_market_data(database_path: str | Path, ticker: str) -> dict:
    """Return source facts, distinct source clocks and visible coverage gaps.

    Connection/schema errors propagate to the caller. Missing data inside readable
    source relations is described in ``gaps``. No refresh or database writes occur.
    Estimates retain all available target periods; they are not revision signals.
    """
    ticker = ticker.strip().upper()
    if ticker not in SECTOR_ETF_TICKERS:
        raise ValueError("Only the 11 US Select Sector SPDR ETFs are supported")
    read_at = datetime.now(timezone.utc)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        info = _rows(connection, "SELECT * FROM etf_info WHERE symbol = ?", [ticker])
        holdings = _rows(
            connection,
            "SELECT * FROM etf_holdings_current WHERE etf_symbol = ? "
            "ORDER BY weight_percent DESC NULLS LAST, holding_key",
            [ticker],
        )
        holding_symbols = sorted({h["holding_symbol"] for h in holdings if h["holding_symbol"]})
        profiles = {
            row["symbol"]: row for row in _rows(
                connection,
                "SELECT * FROM company_profiles WHERE symbol IN (SELECT unnest(?))",
                [holding_symbols],
            )
        }
        estimates = _rows(
            connection,
            """SELECT symbol, estimate_period, target_period_end,
                revenue_low, revenue_high, revenue_avg,
                ebitda_low, ebitda_high, ebitda_avg, ebit_low, ebit_high, ebit_avg,
                net_income_low, net_income_high, net_income_avg, eps_low, eps_high, eps_avg,
                num_analysts_revenue, num_analysts_eps,
                source_dataset, raw_sha256, collected_at, historical_use
            FROM analyst_estimates_current
            WHERE symbol IN (SELECT unnest(?)) AND estimate_period IN ('annual', 'quarter')
            ORDER BY symbol, estimate_period, target_period_end""",
            [list(profiles)],
        )
        price_rows = _rows(
            connection,
            """SELECT *, min(date) OVER (PARTITION BY symbol) AS first_price_date,
                count(*) OVER (PARTITION BY symbol) AS price_observations
            FROM us_eod_daily WHERE symbol IN (SELECT unnest(?))
            QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY date DESC) = 1""",
            [sorted({ticker, *profiles})],
        )
        dataset_status = _rows(
            connection,
            "SELECT dataset, status, last_success_at, last_data_date, row_count "
            "FROM dataset_state WHERE dataset IN (SELECT unnest(?)) ORDER BY dataset",
            [_DATASETS],
        )

    prices = {}
    for row in price_rows:
        coverage = {
            "first_date": row.pop("first_price_date"),
            "last_date": row["date"],
            "observations": row.pop("price_observations"),
        }
        prices[row["symbol"]] = {"latest_price": row, "price_coverage": coverage}
    estimates_by_symbol: dict[str, dict[str, list]] = {}
    for row in estimates:
        # The retained FMP bulk estimate response has no currency column. The
        # profile's trading currency is a different fact and must not fill it.
        row.update(currency=None, currency_status="not_supplied")
        periods = estimates_by_symbol.setdefault(row["symbol"], {"annual": [], "quarter": []})
        periods[row["estimate_period"]].append(row)

    gaps = []
    if not info:
        gaps.append({"kind": "missing_etf_info", "symbol": ticker})
    if not holdings:
        gaps.append({"kind": "missing_holdings", "symbol": ticker})
    if ticker not in prices:
        gaps.append({"kind": "missing_price", "symbol": ticker})
    for holding in holdings:
        symbol = holding["holding_symbol"]
        profile = profiles.get(symbol)
        kind = _holding_type(holding, profile)
        periods = estimates_by_symbol.get(symbol, {"annual": [], "quarter": []})
        holding.update(
            holding_type=kind,
            company_profile=profile,
            annual_estimates=periods["annual"],
            quarterly_estimates=periods["quarter"],
            **prices.get(symbol, {"latest_price": None, "price_coverage": None}),
        )
        if kind == "unclassified":
            gaps.append({"kind": "unclassified_holding", "symbol": symbol,
                         "holding_name": holding["holding_name"]})
        if kind == "equity":
            if symbol not in prices:
                gaps.append({"kind": "missing_price", "symbol": symbol})
            for period, rows in periods.items():
                if not any(row["target_period_end"] >= read_at.date() for row in rows):
                    gaps.append({"kind": f"no_forward_{period}_estimates", "symbol": symbol})

    result = {
        "ticker": ticker,
        "source": {
            "provider": "FMP",
            "database_path": str(database_path),
            "read_at": read_at,
            "relations": ["etf_info", "etf_holdings_current", "company_profiles",
                          "analyst_estimates_current", "us_eod_daily", "dataset_state"],
            "semantics": {
                "weight_percent": "Percentage points of the complete reported ETF snapshot; includes non-equity holdings.",
                "close": "Split-adjusted close; excludes dividend adjustment.",
                "adjusted_close": "Dividend-adjusted close.",
                "estimates": "Latest observed value per company, period type and target period; not a measured revision.",
                "estimate_currency": "Not supplied by the retained FMP estimate response; profile currency is the quote currency.",
                "target_period_end": "Forecast fiscal period, not publication or acquisition date.",
                "collected_at": "Acquisition time of this retained source record, distinct from read_at and dataset last_success_at.",
                "price_coverage": "Observed first/last dates and row count; does not certify a gap-free trading calendar.",
            },
        },
        "etf": {"info": info[0] if info else None,
                **prices.get(ticker, {"latest_price": None, "price_coverage": None})},
        "holdings": holdings,
        "dataset_status": dataset_status,
        "gaps": gaps,
    }
    return json.loads(json.dumps(result, default=_temporal_json, allow_nan=False))
