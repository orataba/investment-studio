"""Shared public facts for registered assets; application refreshes only read here."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path

from sqlalchemy import select

from studio_market.config import MarketSettings
from .collect import Collector, failure_summary
from .providers.fmp import FmpError
from .providers.us_market import normalize_dividend_rows, normalize_split_rows
from .schema import batches
from .store import NumericStore, cutoff_instant, instant


SUPPLEMENTS = {
    "equity": (("key_metrics", "key-metrics"), ("ratios", "ratios")),
    "etf": (("sector_weights", "etf/sector-weightings"), ("country_weights", "etf/country-weightings")),
    "public_fund": (("sector_weights", "etf/sector-weightings"), ("country_weights", "etf/country-weightings")),
    "index": (("index_info", "index-list"),),
}


def refresh_registered(settings: MarketSettings, *, instruments: list[dict]) -> dict:
    """Collect only uncovered typed facts and registered-only supplements.

    The registry supplies public provider symbols and asset types, never holdings,
    portfolio weights or PM research. Covered US typed families use their regular
    full-market collectors; this job fills non-US and missing-symbol coverage.
    """
    targets = sorted({(str(row["symbol"]).strip().upper(), str(row["instrument_type"])) for row in instruments})
    if any(not symbol or kind not in SUPPLEMENTS for symbol, kind in targets):
        raise ValueError("Registered FMP reference requires a symbol and equity/etf/public_fund/index type")
    collector = Collector(settings)
    today = datetime.now(UTC).date()
    start = today - timedelta(days=7)
    results = []
    price_revision_symbols = set()
    try:
        us = {row["symbol"] for row in collector.store.latest("security_directory", limit=100000)["rows"]}
        covered_etfs = {row["symbol"] for row in json.loads((Path(__file__).parent / "providers/us_etf_coverage.json").read_text())["symbols"]}
        index_response = None
        for symbol, kind in targets:
            errors = {}
            def run(section, action):
                try:
                    action()
                except (ValueError, FmpError) as exc:
                    errors[section] = failure_summary(exc)["error"]

            def missing(dataset):
                return not collector.store.query(dataset, symbols=[symbol], limit=1)["rows"]

            if kind == "equity":
                if symbol not in us or missing("company_profiles"):
                    run("profile", lambda: collector.profiles(start, today, [symbol]))
                if symbol not in us or missing("financial_statements"):
                    run("financials", lambda: collector.financials(start, today, [symbol]))
                # Whole-market corporate-action collectors own the US directory.
                if symbol not in us:
                    for dataset, endpoint, normalizer in (("dividends", "dividends", normalize_dividend_rows), ("stock_splits", "splits", normalize_split_rows)):
                        def actions(dataset=dataset, endpoint=endpoint, normalizer=normalizer):
                            date_field = "ex_date" if dataset == "dividends" else "event_date"
                            fields = ("dividend", "adjusted_dividend") if dataset == "dividends" else ("numerator", "denominator")
                            previous = {row[date_field]: tuple(row.get(field) for field in fields)
                                for row in collector.store.query(dataset, symbols=[symbol], limit=100000)["rows"]}
                            response = collector.fmp.get_json(endpoint, {"symbol": symbol, "limit": 100})
                            clock, ref = collector.archive(response)
                            rows = normalizer(response.payload, allowed_symbols={symbol}, start_date=date.min, end_date=date.max, **clock)
                            changes = []
                            for row in rows:
                                row["source_dataset"] = "fmp_" + endpoint
                                if previous.get(row[date_field].isoformat()) != tuple(row.get(field) for field in fields):
                                    changes.append({'symbol':symbol, 'effective_date':row[date_field].isoformat()})
                                    if row[date_field] <= today:price_revision_symbols.add(symbol)
                            collector.publish(dataset, rows, response, ref, price_revision_requests=changes)
                        run(dataset, actions)
            elif kind in {"etf", "public_fund"}:
                if symbol not in covered_etfs or missing("etf_info") or not collector.store.observations("etf_holdings", symbols=[symbol], limit=1)["observations"]:
                    run("fund", lambda: collector.etf(start, today, [symbol]))
            for section, endpoint in SUPPLEMENTS[kind]:
                def supplement(section=section, endpoint=endpoint):
                    nonlocal index_response
                    if section == "index_info":
                        if index_response is None:
                            index_response = collector.fmp.get_json(endpoint)
                        response = index_response
                    else:
                        params = {"symbol": symbol}
                        if kind == "equity":
                            params["limit"] = 5
                        response = collector.fmp.get_json(endpoint, params)
                    if not isinstance(response.payload, list) or not all(isinstance(row, dict) for row in response.payload):
                        raise ValueError(f"FMP {section} response is not a list")
                    payload = response.payload
                    if section == "index_info":
                        payload = next((row for row in payload if row.get("symbol") == symbol), None)
                        if payload is None:
                            raise ValueError(f"FMP index catalog has no {symbol}")
                    elif any(row.get("symbol", symbol) != symbol for row in payload):
                        raise ValueError(f"FMP {section} response belongs to another symbol")
                    clock, ref = collector.archive(response)
                    collector.publish("provider_reference", [{"symbol": symbol, "section": section,
                        "payload_json": json.dumps(payload, ensure_ascii=False), **clock}], response, ref)
                run(section, supplement)
            results.append({"symbol": symbol, "instrument_type": kind, "status": "failed" if errors else "ready", "errors": errors})
        return {"status": "partial" if any(row["errors"] for row in results) else "ready", "instruments": results,
                "batches": collector.results, "price_revision_symbols": sorted(price_revision_symbols)}
    finally:
        collector.store.close()
        if collector._client is not None:
            collector._client.close()


def _camel(value: str) -> str:
    first, *parts = value.split("_")
    return first + "".join(part.title() for part in parts)


def _view(row: dict, aliases: dict | None = None) -> dict:
    """Active app reference fields, with explicit source-version clocks retained."""
    aliases = aliases or {}
    excluded = {"batch_id", "raw_ref", "raw_sha256", "source_dataset", "row_content_sha256", "fact_key", "historical_use", "collected_at"}
    clocks = {"source_id", "observed_at", "available_at"}
    return {key if key in clocks else aliases.get(key, _camel(key)): value for key, value in row.items() if key not in excluded and not key.startswith("_")}


def read_reference_data(settings: MarketSettings, symbol: str, instrument_type: str, *, as_of: datetime | None = None) -> dict:
    """Project shared observations without contacting a provider or moving clocks."""
    symbol = symbol.strip().upper()
    if instrument_type not in SUPPLEMENTS:
        raise ValueError(f"No public FMP reference projection for {instrument_type}")
    store = NumericStore(settings)
    sections, errors, lineage, coverage = {}, {}, {}, {}
    try:
        def rows(dataset, section, *, limit=1000):
            result = store.query(dataset, symbols=[symbol], as_of=as_of, limit=limit)["rows"]
            lineage[section] = [{key: row[key] for key in ("source_id", "observed_at", "available_at") if key in row} for row in result]
            return result

        def first(dataset, section, aliases=None):
            values = rows(dataset, section, limit=1)
            if values:
                sections[section] = _view(values[0], aliases)
            else:
                errors[section] = "共享数据尚无此标的的已保存记录"

        if instrument_type == "equity":
            first("company_profiles", "profile")
            statements = [row for row in rows("financial_statements", "financials", limit=100)
                          if row["statement_type"] == "income" and row["fiscal_period"] == "FY"][:5]
            facts = rows("financial_facts", "financial_facts", limit=100000)
            versions = {row["statement_content_sha256"] for row in statements}
            facts = [row for row in facts if row.get("statement_content_sha256") in versions]
            lineage["financials"] = [{key: row[key] for key in ("source_id", "observed_at", "available_at")} for row in statements]
            lineage["financial_facts"] = [{key: row[key] for key in ("source_id", "observed_at", "available_at")} for row in facts]
            sections["financials"] = []
            for statement in statements:
                values = {row["line_item"]: row["value"] for row in facts
                          if row.get("statement_content_sha256") == statement["statement_content_sha256"]}
                sections["financials"].append(_view(statement, {"period_end": "date", "fiscal_period": "period", "accepted_at": "acceptedDate"}) | values)
            if not statements:
                query = select(batches).where(
                    batches.c.dataset == "financial_statements", batches.c.status == "ready",
                    batches.c.details["endpoint"].as_string() == "income-statement",
                    batches.c.details["parameters"]["symbol"].as_string() == symbol,
                    batches.c.details["parameters"]["period"].as_string() == "annual",
                ).order_by(batches.c.published_at.desc()).limit(1)
                if as_of is not None:
                    query = query.where(batches.c.published_at <= cutoff_instant(as_of))
                with store.engine.connect() as connection:
                    capture = connection.execute(query).mappings().first()
                if capture and capture["row_count"] == 0 and capture["details"].get("response_empty") is True:
                    captured = {"batch_id": capture["id"], "raw_ref": capture["details"]["raw_ref"],
                                "observed_at": instant(capture["details"]["observed_at"]).isoformat(),
                                "available_at": instant(capture["published_at"]).isoformat()}
                    lineage["financials"] = [captured]
                    coverage["financials"] = {"status": "unavailable", "reason": "供应商已完成查询，但未提供年度利润表", **captured}
                else:
                    errors["financials"] = "共享数据尚无已保存的年度利润表"
            sections["dividends"] = [_view(row, {"ex_date": "date", "adjusted_dividend": "adjDividend", "yield_percent": "yield"}) for row in rows("dividends", "dividends", limit=20)]
            sections["splits"] = [_view(row, {"event_date": "date"}) for row in rows("stock_splits", "splits", limit=20)]
        elif instrument_type in {"etf", "public_fund"}:
            first("etf_info", "fund_info", {"provider_updated_at": "updatedAt", "average_volume": "avgVolume", "cusip": "securityCusip"})
            holdings = rows("etf_holdings", "holdings", limit=100000)
            sections["holdings"] = [_view(row, {"holding_symbol": "asset", "holding_name": "name", "weight_percent": "weightPercentage",
                "shares": "sharesNumber", "provider_updated_at": "updatedAt", "cusip": "securityCusip"}) for row in holdings]
            if not holdings:
                observations = store.observations("etf_holdings", symbols=[symbol], as_of=as_of, limit=1)["observations"]
                if observations:
                    lineage["holdings"] = [{"observed_at": row["observed_at"], "batch_id": row["batch_id"]} for row in observations]
                else:
                    errors["holdings"] = "共享数据尚无已保存的持仓披露快照"
        supplements = {row["section"]: row for row in store.query("provider_reference", symbols=[symbol], as_of=as_of)["rows"]}
        for section, _ in SUPPLEMENTS[instrument_type]:
            row = supplements.get(section)
            if row is None:
                errors[section] = "共享数据尚无此补充项的已保存记录"
            else:
                sections[section] = json.loads(row["payload_json"])
                lineage[section] = [{key: row[key] for key in ("source_id", "observed_at", "available_at")}]
        clocks = [datetime.fromisoformat(row["observed_at"]) for values in lineage.values() for row in values]
        return {"sections": sections, "section_errors": errors, "observed_at": max(clocks).astimezone(UTC).isoformat() if clocks else None,
                "source": {"storage": "studio_market", "section_sources": lineage, "section_coverage": coverage}}
    finally:
        store.close()
