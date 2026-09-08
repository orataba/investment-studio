"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

import math

import threading

from concurrent.futures import ThreadPoolExecutor, as_completed

from dataclasses import dataclass

from datetime import date, datetime, timezone

from typing import Callable, Iterable, Mapping

def normalize_grade_events(
    payload: object,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in _payload_rows(payload, "grades"):
        symbol = _symbol(item.get("symbol")) or requested_symbol
        event_date = _date(item.get("date"))
        if symbol != requested_symbol or event_date is None:
            continue
        raw_action = _text(item.get("action"))
        row: dict[str, object] = {
            "symbol": symbol,
            "event_date": event_date,
            "grading_company": _text(item.get("gradingCompany")),
            "raw_action": raw_action,
            "normalized_action": _normalize_action(raw_action),
            "previous_grade": _text(item.get("previousGrade")),
            "new_grade": _text(item.get("newGrade")),
        }
        _finish_row(
            row,
            source_dataset="fmp_analyst_grade_history",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def normalize_grade_snapshots(
    payload: object,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in _payload_rows(payload, "grades-historical"):
        symbol = _symbol(item.get("symbol")) or requested_symbol
        snapshot_date = _date(item.get("date"))
        if symbol != requested_symbol or snapshot_date is None:
            continue
        row: dict[str, object] = {
            "symbol": symbol,
            "snapshot_date": snapshot_date,
            "strong_buy": _integer(item.get("analystRatingsStrongBuy")),
            "buy": _integer(item.get("analystRatingsBuy")),
            "hold": _integer(item.get("analystRatingsHold")),
            "sell": _integer(item.get("analystRatingsSell")),
            "strong_sell": _integer(item.get("analystRatingsStrongSell")),
        }
        _finish_row(
            row,
            source_dataset="fmp_analyst_grade_snapshot_history",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def normalize_quant_ratings(
    payload: object,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in _payload_rows(payload, "ratings-historical"):
        symbol = _symbol(item.get("symbol")) or requested_symbol
        if symbol != requested_symbol:
            continue
        row = _normalize_quant_rating_item(item)
        if row is None:
            continue
        _finish_row(
            row,
            source_dataset="fmp_quant_rating_history",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def normalize_quant_ratings_csv(
    body: bytes,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"symbol", "date", "rating", "priceToEarningsScore"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError("FMP rating bulk CSV has unexpected columns")
    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = _symbol(item.get("symbol"))
        if symbol not in allowed_symbols:
            continue
        row = _normalize_quant_rating_item(item)
        if row is None:
            continue
        _finish_row(
            row,
            source_dataset="fmp_quant_rating_bulk",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def _normalize_quant_rating_item(
    item: Mapping[str, object],
) -> dict[str, object] | None:
    symbol = _symbol(item.get("symbol"))
    rating_date = _date(item.get("date"))
    if symbol is None or rating_date is None:
        return None
    return {
        "symbol": symbol,
        "rating_date": rating_date,
        "rating": _text(item.get("rating")),
        "overall_score": _number(item.get("overallScore")),
        "discounted_cash_flow_score": _number(
            item.get("discountedCashFlowScore")
        ),
        "return_on_equity_score": _number(item.get("returnOnEquityScore")),
        "return_on_assets_score": _number(item.get("returnOnAssetsScore")),
        "debt_to_equity_score": _number(item.get("debtToEquityScore")),
        "price_to_earnings_score": _number(item.get("priceToEarningsScore")),
        "price_to_book_score": _number(item.get("priceToBookScore")),
    }

def _normalize_action(value: str | None) -> str:
    action = (value or "").strip().lower()
    if "upgrade" in action:
        return "upgrade"
    if "downgrade" in action:
        return "downgrade"
    if action in {"maintain", "reiterate", "reiterated"}:
        return "maintain"
    if any(token in action for token in ("init", "resume", "coverage")):
        return "initiation_or_resume"
    return "unknown"

def _payload_rows(payload: object, endpoint: str) -> list[Mapping[str, object]]:
    if not isinstance(payload, list):
        raise ValueError(f"FMP {endpoint} response is not a list")
    return [item for item in payload if isinstance(item, Mapping)]

def _finish_row(
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

def _row_hash(row: Mapping[str, object]) -> str:
    encoded = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _symbol(value: object) -> str | None:
    text = _text(value)
    return text.upper() if text else None

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
