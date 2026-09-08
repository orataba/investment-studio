"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import math

from datetime import date, datetime

from typing import Any

def normalize_security_directory(
    payload: Any,
    *,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP company screener response is not a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("FMP company screener row is invalid")
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": _text(item.get("companyName")),
                "exchange": _text(item.get("exchange")),
                "exchange_short_name": _text(item.get("exchangeShortName")),
                "country": _text(item.get("country")),
                "sector": _text(item.get("sector")),
                "industry": _text(item.get("industry")),
                "is_etf": _bool(item.get("isEtf")),
                "is_fund": _bool(item.get("isFund")),
                "is_actively_trading": _bool(item.get("isActivelyTrading")),
                "market_cap": _number(item.get("marketCap")),
                "price": _number(item.get("price")),
                "beta": _number(item.get("beta")),
                "last_annual_dividend": _number(item.get("lastAnnualDividend")),
                "volume": _number(item.get("volume")),
                "source_dataset": "fmp_company_screener",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def normalize_delisted_securities(
    payload: Any,
    *,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP delisted response is not a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("FMP delisted row is invalid")
        symbol = str(item.get("symbol", "")).strip().upper()
        delisted_date = _date(item.get("delistedDate"))
        if not symbol or delisted_date is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "company_name": _text(item.get("companyName")),
                "exchange": _text(item.get("exchange")),
                "ipo_date": _date(item.get("ipoDate")),
                "delisted_date": delisted_date,
                "source_dataset": "fmp_delisted_companies",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def normalize_symbol_changes(
    payload: Any,
    *,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP symbol change response is not a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("FMP symbol change row is invalid")
        event_date = _date(item.get("date"))
        old_symbol = str(item.get("oldSymbol", "")).strip().upper()
        new_symbol = str(item.get("newSymbol", "")).strip().upper()
        if event_date is None or not old_symbol or not new_symbol:
            continue
        rows.append(
            {
                "event_date": event_date,
                "old_symbol": old_symbol,
                "new_symbol": new_symbol,
                "company_name": _text(item.get("companyName")),
                "source_dataset": "fmp_symbol_change",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None

def _date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None

def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
