"""Collect sector ETF research inputs into the project's reference snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re

from studio_data.services.fmp import FmpApiError, FmpClient


def _stamp(raw: dict, dataset: str) -> dict:
    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "source_dataset": dataset,
        "raw_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest(),
    }


def _holding_type(name: str, symbol: str, profile: dict | None = None) -> str:
    name = name.upper()
    if name.startswith("CONTRA "):
        return "other"
    if name in {"US DOLLAR", "POUND STERLING"}:
        return "cash"
    if "MONEY MARKET" in name:
        return "fund"
    if re.fullmatch(r"(?:IX[A-Z]|XA[RS])[HMUZ]\d{1,2}", symbol):
        return "future"
    if profile:
        return "fund" if profile.get("isEtf") or profile.get("isFund") else "equity"
    return "unclassified"


def _estimate(raw: dict, period: str, reporting_currency: str | None) -> dict:
    # FMP supplies the fiscal target date, but no estimate publication date or currency.
    row = {"symbol": raw["symbol"], "estimate_period": period,
           "target_period_end": raw["date"], "currency": reporting_currency,
           "currency_status": "inferred_from_reporting_currency" if reporting_currency else "not_supplied",
           "historical_use": "observed_snapshot",
           **_stamp(raw, "fmp_analyst_estimates")}
    for source, target in (("revenue", "revenue"), ("ebitda", "ebitda"), ("ebit", "ebit"),
                           ("netIncome", "net_income"), ("eps", "eps")):
        for statistic in ("Low", "High", "Avg"):
            row[f"{target}_{statistic.lower()}"] = raw.get(f"{source}{statistic}")
    row.update(num_analysts_revenue=raw.get("numAnalystsRevenue"), num_analysts_eps=raw.get("numAnalystsEps"))
    return row


def _prices(client: FmpClient, symbol: str, start: str, end: str) -> dict:
    rows = client.split_adjusted_eod(symbol, start_date=start, end_date=end)
    adjusted = client.historical_eod(symbol, adjusted=True, start_date=start, end_date=end)
    if any(row.get("symbol") != symbol for row in [*rows, *adjusted]):
        raise ValueError(f"FMP prices contain a different company than {symbol}.")
    rows = [row for row in rows if start <= row["date"] <= end]
    adjusted = [row for row in adjusted if start <= row["date"] <= end]
    if not rows:
        return {"latest_price": None, "price_coverage": None}
    latest = max(rows, key=lambda row: row["date"])
    adjusted_by_date = {row["date"]: row for row in adjusted}
    adjusted_row = adjusted_by_date.get(latest["date"], {})
    return {
        "latest_price": {**latest, "adjusted_close": adjusted_row.get("adjClose"),
                         **_stamp({"close": latest, "adjusted": adjusted_row}, "fmp_us_eod")},
        "price_coverage": {"first_date": min(row["date"] for row in rows),
                           "last_date": latest["date"], "observations": len(rows)},
    }


def collect_sector_market_data(ticker: str, *, info: dict | None, holdings: list[dict], client: FmpClient) -> dict:
    """Keep the complete holdings report, with bounded concurrent provider reads.

    Constituent facts belong to this ETF reference snapshot, not to the tracked
    instrument registry. Missing source sections are explicit gaps, never read
    from a previous snapshot or another project's database.
    """
    observed_at = datetime.now(UTC)
    end = observed_at.date().isoformat()
    start = (observed_at.date() - timedelta(days=30)).isoformat()
    gaps: list[dict] = []
    normalized = []
    for index, raw in enumerate(holdings):
        if raw.get("symbol") != ticker:
            raise ValueError(f"FMP holdings contain a different ETF than {ticker}.")
        symbol, name = str(raw.get("asset") or ""), str(raw.get("name") or "")
        normalized.append({
            "holding_key": f"{symbol}:{raw.get('isin') or index}",
            "holding_symbol": symbol, "holding_name": name,
            "weight_percent": raw.get("weightPercentage"),
            "shares_number": raw.get("sharesNumber"), "market_value": raw.get("marketValue"),
            "as_of_date": None, "snapshot_date": end, "provider_updated_at": raw.get("updatedAt"),
            "holding_type": _holding_type(name, symbol), **_stamp(raw, "fmp_etf_current"),
        })
    normalized.sort(key=lambda row: (-(row["weight_percent"] or 0), row["holding_key"]))
    if not info:
        gaps.append({"kind": "missing_etf_info", "symbol": ticker})
    if not holdings:
        gaps.append({"kind": "missing_holdings", "symbol": ticker})

    def collect(symbol: str) -> tuple[str, dict, list[dict]]:
        result = {"company_profile": None, "annual_estimates": [], "quarterly_estimates": [],
                  "latest_price": None, "price_coverage": None}
        errors = []
        with client.independent_session() as worker:
            def profile_with_reporting_currency():
                profile = worker.profile(symbol)
                statements = worker.income_statements(symbol, limit=1)
                statement = statements[0] if statements else None
                if statement and statement.get("symbol") != symbol:
                    raise ValueError(f"FMP financial statement contains a different company than {symbol}.")
                return {**profile, **_stamp(profile, "fmp_us_company_profiles"),
                        "reporting_currency": statement.get("reportedCurrency") if statement else None,
                        "reporting_currency_source": {"statement_date": statement["date"],
                            **_stamp(statement, "fmp_income_statement")} if statement else None}

            sections = [("prices", lambda: _prices(worker, symbol, start, end))]
            if symbol != ticker:
                sections = [("company_profile", profile_with_reporting_currency),
                            ("annual_estimates", lambda: worker.analyst_estimates(symbol, period="annual")),
                            ("quarterly_estimates", lambda: worker.analyst_estimates(symbol, period="quarter")), *sections]
            for section, loader in sections:
                try:
                    value = loader()
                    if section == "prices":
                        result.update(value)
                    elif section == "company_profile":
                        result[section] = value
                    else:
                        if any(row.get("symbol") != symbol for row in value):
                            raise ValueError(f"FMP estimates contain a different company than {symbol}.")
                        period = "annual" if section == "annual_estimates" else "quarter"
                        reporting_currency = (result["company_profile"] or {}).get("reporting_currency")
                        result[section] = sorted((_estimate(row, period, reporting_currency) for row in value), key=lambda row: row["target_period_end"])
                except (FmpApiError, ValueError) as error:
                    errors.append({"kind": "collection_failed", "symbol": symbol, "section": section, "message": str(error)})
        return symbol, result, errors

    symbols = sorted({ticker, *(row["holding_symbol"] for row in normalized
                               if row["holding_symbol"] and row["holding_type"] == "unclassified")})
    # Each worker owns an HTTP session; four concurrent requests bound provider load.
    with ThreadPoolExecutor(max_workers=4) as pool:
        companies = {}
        for symbol, facts, errors in pool.map(collect, symbols):
            companies[symbol] = facts
            gaps.extend(errors)
    empty = {"company_profile": None, "annual_estimates": [], "quarterly_estimates": [],
             "latest_price": None, "price_coverage": None}
    for holding in normalized:
        symbol = holding["holding_symbol"]
        holding.update(companies.get(symbol, empty))
        holding["holding_type"] = _holding_type(holding["holding_name"], symbol, holding["company_profile"])
        if holding["holding_type"] == "unclassified":
            gaps.append({"kind": "unclassified_holding", "symbol": symbol, "holding_name": holding["holding_name"]})
        if holding["holding_type"] == "equity":
            if not holding["latest_price"]:
                gaps.append({"kind": "missing_price", "symbol": symbol})
            for period, key in (("annual", "annual_estimates"), ("quarter", "quarterly_estimates")):
                if not any(row["target_period_end"] >= end for row in holding[key]):
                    gaps.append({"kind": f"no_forward_{period}_estimates", "symbol": symbol})
    etf_prices = {key: companies[ticker][key] for key in ("latest_price", "price_coverage")}
    if not etf_prices["latest_price"]:
        gaps.append({"kind": "missing_price", "symbol": ticker})
    completed_at = datetime.now(UTC).isoformat()
    counts = {
        "fmp_etf_current": len(normalized) + bool(info),
        "fmp_us_company_profiles": sum(bool(row["company_profile"]) for row in companies.values()),
        "fmp_analyst_estimates": sum(len(row["annual_estimates"]) + len(row["quarterly_estimates"]) for row in companies.values()),
        "fmp_us_eod": sum((row["price_coverage"] or {}).get("observations", 0) for row in companies.values()),
    }
    dataset_by_section = {"prices": "fmp_us_eod", "company_profile": "fmp_us_company_profiles",
                          "annual_estimates": "fmp_analyst_estimates", "quarterly_estimates": "fmp_analyst_estimates"}
    failed_datasets = {dataset_by_section[gap["section"]] for gap in gaps if gap["kind"] == "collection_failed"}
    if not info or not holdings:
        failed_datasets.add("fmp_etf_current")
    return {
        "ticker": ticker,
        "source": {"provider": "FMP", "storage": "instrument_data.instrument_reference_snapshot",
                   "collected_at": completed_at,
                   "semantics": {
                       "weight_percent": "Percentage points of the complete ETF holdings response, including non-equity positions.",
                       "snapshot_date": "Date we observed the holdings response; not a provider holdings report date. provider_updated_at is retained separately.",
                       "close": "Split-adjusted close; excludes dividend adjustment.",
                       "adjusted_close": "Dividend-adjusted close, matched to the same price date.",
                       "price_coverage": "Observed dates and count within the requested recent 30 calendar days; not full history or a gap-free calendar.",
                       "estimates": "Latest observed values; not measured revisions. First 20 source periods per annual/quarter query, including past and future targets.",
                       "estimate_currency": "Inferred from the company's latest FMP income-statement reportedCurrency, with statement source retained; not inferred from listing or quote currency. The estimates endpoint itself omits currency.",
                       "target_period_end": "Forecast fiscal period, not publication or acquisition date.",
                       "collected_at": "Source acquisition time; read_at is assigned only when research reads the saved snapshot.",
                   }},
        "etf": {"info": {**info, **_stamp(info, "fmp_etf_current")} if info else None, **etf_prices},
        "holdings": normalized,
        "dataset_status": [{"dataset": dataset, "status": "partial" if dataset in failed_datasets else "success",
                            "last_success_at": None if dataset in failed_datasets else completed_at, "row_count": count}
                           for dataset, count in counts.items()],
        "gaps": gaps,
    }
