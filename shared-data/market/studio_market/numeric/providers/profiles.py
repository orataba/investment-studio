"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

from datetime import date, datetime

from typing import Callable

def normalize_company_profiles(
    body: bytes,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> tuple[list[dict[str, object]], int]:
    if body.strip() == b"[]":
        return [], 0
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"symbol", "companyName", "exchange", "cik", "isin", "cusip"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError("FMP profile bulk CSV has unexpected columns")
    rows: list[dict[str, object]] = []
    provider_rows = 0
    for item in reader:
        provider_rows += 1
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol not in allowed_symbols:
            continue
        normalized: dict[str, object] = {
            "symbol": symbol,
            "company_name": _text(item.get("companyName")),
            "currency": _text(item.get("currency")),
            "cik": _identifier(item.get("cik")),
            "isin": _identifier(item.get("isin")),
            "cusip": _identifier(item.get("cusip")),
            "exchange": _text(item.get("exchange")),
            "exchange_full_name": _text(item.get("exchangeFullName")),
            "sector": _text(item.get("sector")),
            "industry": _text(item.get("industry")),
            "country": _text(item.get("country")),
            "website": _text(item.get("website")),
            "description": _text(item.get("description")),
            "ceo": _text(item.get("ceo")),
            "full_time_employees": _integer(item.get("fullTimeEmployees")),
            "ipo_date": _date(item.get("ipoDate")),
            "is_etf": _boolean(item.get("isEtf")),
            "is_fund": _boolean(item.get("isFund")),
            "is_adr": _boolean(item.get("isAdr")),
            "is_actively_trading": _boolean(item.get("isActivelyTrading")),
        }
        normalized["row_content_sha256"] = _row_hash(normalized)
        normalized["source_dataset"] = "fmp_profile_bulk"
        normalized["raw_sha256"] = raw_sha256
        normalized["collected_at"] = collected_at
        rows.append(normalized)
    return rows, provider_rows

def _row_hash(row: dict[str, object]) -> str:
    body = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()

def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

def _identifier(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"", "N/A", "NA", "NONE", "NULL", "000000000"}:
        return None
    return text

def _integer(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None

def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _boolean(value: object) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    return None
