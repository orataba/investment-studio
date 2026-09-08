"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import json

import math

from collections import Counter

from datetime import date, datetime

from typing import Any, Callable

def normalize_etf_info(
    payload: Any,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"FMP ETF info is empty for {requested_symbol}")
    item = payload[0]
    if not isinstance(item, dict):
        raise ValueError(f"FMP ETF info is invalid for {requested_symbol}")
    symbol = str(item.get("symbol", "")).strip().upper()
    if symbol != requested_symbol:
        raise ValueError(f"FMP ETF info symbol mismatch for {requested_symbol}")
    return [
        {
            "symbol": symbol,
            "name": _text(item.get("name")),
            "description": _text(item.get("description")),
            "isin": _text(item.get("isin")),
            "cusip": _text(item.get("securityCusip")),
            "asset_class": _text(item.get("assetClass")),
            "domicile": _text(item.get("domicile")),
            "website": _text(item.get("website")),
            "etf_company": _text(item.get("etfCompany")),
            "expense_ratio": _number(item.get("expenseRatio")),
            "assets_under_management": _number(item.get("assetsUnderManagement")),
            "average_volume": _number(item.get("avgVolume")),
            "inception_date": _date(item.get("inceptionDate")),
            "nav": _number(item.get("nav")),
            "nav_currency": _text(item.get("navCurrency")),
            "holdings_count": _integer(item.get("holdingsCount")),
            "is_actively_trading": _bool(item.get("isActivelyTrading")),
            "provider_updated_at": _text(item.get("updatedAt")),
            "sectors_json": json.dumps(item.get("sectorsList") or [], sort_keys=True),
            "source_dataset": "fmp_etf_info",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
    ]

def normalize_current_holdings(
    payload: Any,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError(f"FMP ETF holdings response is invalid for {requested_symbol}")
    rows: list[dict[str, object]] = []
    occurrences: Counter[str] = Counter()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"FMP ETF holding row is invalid for {requested_symbol}")
        etf_symbol = str(item.get("symbol", "")).strip().upper()
        if etf_symbol != requested_symbol:
            raise ValueError(f"FMP ETF holdings symbol mismatch for {requested_symbol}")
        updated_at = _text(item.get("updatedAt"))
        snapshot_date = _date(updated_at) or collected_at.date()
        base_key = _holding_key(
            item.get("asset"),
            item.get("isin"),
            item.get("securityCusip"),
            item.get("name"),
        )
        occurrences[base_key] += 1
        rows.append(
            {
                "etf_symbol": requested_symbol,
                "snapshot_date": snapshot_date,
                "holding_key": f"{base_key}#{occurrences[base_key]}",
                "holding_symbol": _upper_text(item.get("asset")),
                "holding_name": _text(item.get("name")),
                "isin": _text(item.get("isin")),
                "cusip": _text(item.get("securityCusip")),
                "shares": _number(item.get("sharesNumber")),
                "weight_percent": _number(item.get("weightPercentage")),
                "market_value": _number(item.get("marketValue")),
                "provider_updated_at": updated_at,
                "source_dataset": "fmp_etf_current_holdings",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def normalize_etf_disclosure(
    payload: Any,
    *,
    requested_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError(f"FMP ETF disclosure response is invalid for {requested_symbol}")
    rows: list[dict[str, object]] = []
    occurrences: Counter[str] = Counter()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"FMP ETF disclosure row is invalid for {requested_symbol}")
        report_date = _date(item.get("date"))
        if report_date is None:
            continue
        base_key = _holding_key(
            item.get("symbol"),
            item.get("isin"),
            item.get("cusip"),
            item.get("name"),
            item.get("title"),
            item.get("assetCat"),
        )
        occurrences[base_key] += 1
        rows.append(
            {
                "etf_symbol": requested_symbol,
                "report_date": report_date,
                "accepted_at": _naive_datetime(item.get("acceptedDate")),
                "holding_key": f"{base_key}#{occurrences[base_key]}",
                "holding_symbol": _upper_text(item.get("symbol")),
                "holding_name": _text(item.get("name")),
                "title": _text(item.get("title")),
                "registrant_cik": _text(item.get("cik")),
                "lei": _text(item.get("lei")),
                "cusip": _text(item.get("cusip")),
                "isin": _text(item.get("isin")),
                "balance": _number(item.get("balance")),
                "units": _text(item.get("units")),
                "currency": _text(item.get("cur_cd")),
                "value_usd": _number(item.get("valUsd")),
                "percent_value": _number(item.get("pctVal")),
                "payoff_profile": _text(item.get("payoffProfile")),
                "asset_category": _text(item.get("assetCat")),
                "issuer_category": _text(item.get("issuerCat")),
                "investment_country": _text(item.get("invCountry")),
                "is_restricted_security": _text(item.get("isRestrictedSec")),
                "fair_value_level": _text(item.get("fairValLevel")),
                "is_cash_collateral": _text(item.get("isCashCollateral")),
                "is_non_cash_collateral": _text(item.get("isNonCashCollateral")),
                "is_loan_by_fund": _text(item.get("isLoanByFund")),
                "source_dataset": "fmp_fund_disclosure",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
                "notional_value_usd": None,
                "position_type": None,
            }
        )
    return rows

def _holding_key(*values: object) -> str:
    parts = [str(value).strip().upper() for value in values if str(value or "").strip()]
    return "|".join(parts) or "UNIDENTIFIED"

def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _upper_text(value: object) -> str | None:
    text = _text(value)
    return text.upper() if text else None

def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None

def _date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None

def _naive_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)

def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
