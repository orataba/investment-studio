"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

import math

from datetime import date, datetime

from typing import Callable, Mapping

def normalize_analyst_estimates(
    body: bytes,
    *,
    allowed_symbols: set[str],
    estimate_period: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    reader = _csv_reader(body, {"symbol", "date", "revenueAvg", "epsAvg"})
    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = str(item.get("symbol") or "").strip().upper()
        target_period_end = _date(item.get("date"))
        if symbol not in allowed_symbols or target_period_end is None:
            continue
        normalized: dict[str, object] = {
            "symbol": symbol,
            "estimate_period": estimate_period,
            "target_period_end": target_period_end,
            "revenue_low": _number(item.get("revenueLow")),
            "revenue_high": _number(item.get("revenueHigh")),
            "revenue_avg": _number(item.get("revenueAvg")),
            "ebitda_low": _number(item.get("ebitdaLow")),
            "ebitda_high": _number(item.get("ebitdaHigh")),
            "ebitda_avg": _number(item.get("ebitdaAvg")),
            "ebit_low": _number(item.get("ebitLow")),
            "ebit_high": _number(item.get("ebitHigh")),
            "ebit_avg": _number(item.get("ebitAvg")),
            "net_income_low": _number(item.get("netIncomeLow")),
            "net_income_high": _number(item.get("netIncomeHigh")),
            "net_income_avg": _number(item.get("netIncomeAvg")),
            "eps_low": _number(item.get("epsLow")),
            "eps_high": _number(item.get("epsHigh")),
            "eps_avg": _number(item.get("epsAvg")),
            "num_analysts_revenue": _integer(item.get("numAnalystsRevenue")),
            "num_analysts_eps": _integer(item.get("numAnalystsEps")),
            "provider_row_json": json.dumps(
                dict(item), sort_keys=True, separators=(",", ":")
            ),
        }
        _finish_versioned_row(
            normalized,
            source_dataset="fmp_analyst_estimates_bulk",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(normalized)
    return rows

def normalize_price_targets(
    body: bytes,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    reader = _csv_reader(body, {"symbol", "lastYearAvgPriceTarget"})
    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol not in allowed_symbols:
            continue
        normalized: dict[str, object] = {
            "symbol": symbol,
            "last_month_count": _integer(item.get("lastMonthCount")),
            "last_month_avg_price_target": _number(
                item.get("lastMonthAvgPriceTarget")
            ),
            "last_quarter_count": _integer(item.get("lastQuarterCount")),
            "last_quarter_avg_price_target": _number(
                item.get("lastQuarterAvgPriceTarget")
            ),
            "last_year_count": _integer(item.get("lastYearCount")),
            "last_year_avg_price_target": _number(
                item.get("lastYearAvgPriceTarget")
            ),
            "all_time_count": _integer(item.get("allTimeCount")),
            "all_time_avg_price_target": _number(
                item.get("allTimeAvgPriceTarget")
            ),
            "publishers_json": _text(item.get("publishers")),
        }
        _finish_versioned_row(
            normalized,
            source_dataset="fmp_price_target_summary_bulk",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(normalized)
    return rows

def normalize_rating_consensus(
    body: bytes,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    reader = _csv_reader(body, {"symbol", "consensus"})
    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol not in allowed_symbols:
            continue
        normalized: dict[str, object] = {
            "symbol": symbol,
            "strong_buy": _integer(item.get("strongBuy")),
            "buy": _integer(item.get("buy")),
            "hold": _integer(item.get("hold")),
            "sell": _integer(item.get("sell")),
            "strong_sell": _integer(item.get("strongSell")),
            "consensus": _text(item.get("consensus")),
        }
        _finish_versioned_row(
            normalized,
            source_dataset="fmp_upgrades_downgrades_consensus_bulk",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(normalized)
    return rows

def _finish_versioned_row(
    row: dict[str, object],
    *,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
) -> None:
    row["row_content_sha256"] = _row_hash(row)
    row["source_dataset"] = source_dataset
    row["raw_sha256"] = raw_sha256
    row["collected_at"] = collected_at

def _csv_reader(
    body: bytes,
    required: set[str],
) -> csv.DictReader[str]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError("FMP analyst bulk CSV has unexpected columns")
    return reader

def _row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _number(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None

def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
