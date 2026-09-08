"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import io

import json

import math

import time

from datetime import date, datetime, time as wall_time, timedelta

from typing import Any, Callable

from zoneinfo import ZoneInfo

def normalize_us_eod_rows(
    body: bytes,
    *,
    allowed_symbols: set[str],
    expected_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("FMP EOD bulk CSV is not UTF-8") from None
    reader = csv.DictReader(io.StringIO(text))
    required = {"symbol", "date", "open", "low", "high", "close", "adjClose", "volume"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("FMP EOD bulk CSV is missing required columns")

    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol not in allowed_symbols:
            continue
        try:
            observation_date = date.fromisoformat(str(item.get("date", ""))[:10])
            open_value = _finite_number(item.get("open"))
            high_value = _finite_number(item.get("high"))
            low_value = _finite_number(item.get("low"))
            close_value = _finite_number(item.get("close"))
            adjusted_close = _finite_number(item.get("adjClose"))
            volume = _finite_number(item.get("volume"))
        except (TypeError, ValueError):
            continue
        if observation_date != expected_date:
            continue
        if min(open_value, high_value, low_value, close_value, adjusted_close) <= 0:
            continue
        if volume < 0 or high_value < max(open_value, low_value, close_value):
            continue
        if low_value > min(open_value, high_value, close_value):
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": observation_date,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "adjusted_close": adjusted_close,
                "volume": volume,
                "source_dataset": "fmp_eod_bulk",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def normalize_us_eod_symbol_rows(
    full_payload: Any,
    adjusted_payload: Any,
    *,
    expected_symbol: str,
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    symbol = expected_symbol.strip().upper()
    if not isinstance(full_payload, list):
        raise ValueError(f"FMP full EOD response is not a list for {symbol}")
    if not isinstance(adjusted_payload, list):
        raise ValueError(f"FMP adjusted EOD response is not a list for {symbol}")

    adjusted_by_date: dict[date, float] = {}
    for item in adjusted_payload:
        if not isinstance(item, dict):
            raise ValueError(f"FMP adjusted EOD contains an invalid row for {symbol}")
        provider_symbol = str(item.get("symbol") or symbol).strip().upper()
        if provider_symbol != symbol:
            raise ValueError(
                f"FMP adjusted EOD symbol mismatch: {provider_symbol} != {symbol}"
            )
        observation_date = _optional_date(item.get("date"))
        if observation_date is None:
            raise ValueError(f"FMP adjusted EOD has an invalid date for {symbol}")
        if observation_date < start_date or observation_date > end_date:
            continue
        adjusted_close = _finite_number(item.get("adjClose"))
        if adjusted_close <= 0:
            continue
        prior = adjusted_by_date.get(observation_date)
        if prior is not None and prior != adjusted_close:
            raise ValueError(
                f"FMP adjusted EOD has conflicting rows for {symbol} "
                f"on {observation_date}"
            )
        adjusted_by_date[observation_date] = adjusted_close

    by_date: dict[date, dict[str, object]] = {}
    for item in full_payload:
        if not isinstance(item, dict):
            raise ValueError(f"FMP full EOD contains an invalid row for {symbol}")
        provider_symbol = str(item.get("symbol") or symbol).strip().upper()
        if provider_symbol != symbol:
            raise ValueError(
                f"FMP full EOD symbol mismatch: {provider_symbol} != {symbol}"
            )
        observation_date = _optional_date(item.get("date"))
        if observation_date is None:
            raise ValueError(f"FMP full EOD has an invalid date for {symbol}")
        if observation_date < start_date or observation_date > end_date:
            continue
        if observation_date not in adjusted_by_date:
            raise ValueError(
                f"FMP adjusted close is missing for {symbol} on {observation_date}"
            )
        try:
            open_value = _finite_number(item.get("open"))
            high_value = _finite_number(item.get("high"))
            low_value = _finite_number(item.get("low"))
            close_value = _finite_number(item.get("close"))
            volume = _finite_number(item.get("volume"))
        except (TypeError, ValueError):
            continue
        adjusted_close = adjusted_by_date[observation_date]
        if min(open_value, high_value, low_value, close_value, adjusted_close) <= 0:
            continue
        if volume < 0 or high_value < max(open_value, low_value, close_value):
            continue
        if low_value > min(open_value, high_value, close_value):
            continue
        row = {
            "symbol": symbol,
            "date": observation_date,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "adjusted_close": adjusted_close,
            "volume": volume,
            "source_dataset": "fmp_historical_price_eod_direct",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
        prior = by_date.get(observation_date)
        if prior is not None and prior != row:
            raise ValueError(
                f"FMP full EOD has conflicting rows for {symbol} "
                f"on {observation_date}"
            )
        by_date[observation_date] = row
    return [by_date[key] for key in sorted(by_date)]

def normalize_dividend_rows(
    payload: Any,
    *,
    allowed_symbols: set[str],
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP dividend calendar response is not a list")
    by_identity: dict[tuple[str, date, str], dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        ex_date = _optional_date(item.get("date"))
        if symbol not in allowed_symbols or ex_date is None:
            continue
        if ex_date < start_date or ex_date > end_date:
            continue
        adjusted_dividend = _optional_number(item.get("adjDividend"))
        dividend = _optional_number(item.get("dividend"))
        if adjusted_dividend is None and dividend is None:
            continue
        frequency = str(item.get("frequency") or "Unknown").strip() or "Unknown"
        row = {
            "symbol": symbol,
            "ex_date": ex_date,
            "declaration_date": _optional_date(item.get("declarationDate")),
            "record_date": _optional_date(item.get("recordDate")),
            "payment_date": _optional_date(item.get("paymentDate")),
            "adjusted_dividend": adjusted_dividend,
            "dividend": dividend,
            "yield_percent": _optional_number(item.get("yield")),
            "frequency": frequency,
            "source_dataset": "fmp_dividends_calendar",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
        identity = (symbol, ex_date, frequency)
        current = by_identity.get(identity)
        if current is None or _completeness(row) > _completeness(current):
            by_identity[identity] = row
    return sorted(
        by_identity.values(),
        key=lambda row: (row["ex_date"], row["symbol"], row["frequency"]),
    )

def normalize_split_rows(
    payload: Any,
    *,
    allowed_symbols: set[str],
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP split calendar response is not a list")
    by_identity: dict[tuple[str, date], dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        event_date = _optional_date(item.get("date"))
        numerator = _optional_number(item.get("numerator"))
        denominator = _optional_number(item.get("denominator"))
        if symbol not in allowed_symbols or event_date is None:
            continue
        if event_date < start_date or event_date > end_date:
            continue
        if numerator is None or denominator is None or min(numerator, denominator) <= 0:
            continue
        by_identity[(symbol, event_date)] = {
            "symbol": symbol,
            "event_date": event_date,
            "numerator": numerator,
            "denominator": denominator,
            "split_type": _optional_text(item.get("splitType")),
            "source_dataset": "fmp_splits_calendar",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
    return sorted(
        by_identity.values(),
        key=lambda row: (row["event_date"], row["symbol"]),
    )

def _finite_number(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number is not finite")
    return number

def _optional_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return _finite_number(value)
    except (TypeError, ValueError):
        return None

def _optional_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

def _completeness(row: dict[str, object]) -> int:
    return sum(
        row.get(field) is not None
        for field in (
            "declaration_date",
            "record_date",
            "payment_date",
            "adjusted_dividend",
            "dividend",
            "yield_percent",
        )
    )
