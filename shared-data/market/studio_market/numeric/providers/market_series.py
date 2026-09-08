"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import io

import json

import math

from collections.abc import Callable, Mapping

from datetime import date, datetime, time, timedelta, timezone

from typing import Any

from zoneinfo import ZoneInfo

HONG_KONG = ZoneInfo("Asia/Hong_Kong")

FMP_PROVIDER_SYMBOLS: Mapping[str, str] = {
    "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD",
    "DXY": "DX-Y.NYB",
}

FMP_SERIES_IDS = tuple(FMP_PROVIDER_SYMBOLS)

def normalize_fmp_market_series(
    payload: Any,
    *,
    series_id: str,
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if series_id not in FMP_SERIES_IDS:
        raise ValueError(f"unsupported FMP market series: {series_id}")
    if not isinstance(payload, list):
        raise ValueError(f"FMP {series_id} history is not a list")
    by_date: dict[date, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError(f"FMP {series_id} history contains a malformed row")
        expected_symbol = FMP_PROVIDER_SYMBOLS[series_id].upper()
        provider_symbol = str(item.get("symbol") or expected_symbol).strip().upper()
        if provider_symbol != expected_symbol:
            raise ValueError(
                f"FMP market-series identity mismatch: "
                f"{provider_symbol} != {expected_symbol}"
            )
        observed = _iso_date(item.get("date"))
        if observed is None or observed < start_date or observed > end_date:
            continue
        open_value = _positive_number(item.get("open"), f"{series_id} open", observed)
        high_value = _positive_number(item.get("high"), f"{series_id} high", observed)
        low_value = _positive_number(item.get("low"), f"{series_id} low", observed)
        close_value = _positive_number(item.get("close"), f"{series_id} close", observed)
        high_value = max(high_value, open_value, low_value, close_value)
        low_value = min(low_value, open_value, high_value, close_value)
        volume = _optional_number(item.get("volume"), minimum=0.0)
        vwap = _optional_number(item.get("vwap"), minimum=0.0, strict=True)
        row = {
            "series_id": series_id,
            "date": observed,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume,
            "vwap": vwap,
            "source_dataset": "fmp_historical_price_eod_full",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
        prior = by_date.get(observed)
        if prior is not None and prior != row:
            raise ValueError(f"FMP {series_id} has conflicting rows on {observed}")
        by_date[observed] = row
    return [by_date[key] for key in sorted(by_date)]

def normalize_hsil_close_series(
    body: bytes,
    *,
    series_id: str,
    code: str,
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"Hang Seng Indexes returned invalid JSON for {series_id}") from None
    if not isinstance(payload, Mapping) or str(payload.get("indexCode", "")).strip() != code:
        raise ValueError(f"Hang Seng Indexes identity mismatch for {series_id}")
    levels = payload.get("indexLevels-5y")
    if not isinstance(levels, list) or not levels:
        raise ValueError(f"Hang Seng Indexes returned no chart data for {series_id}")
    by_date: dict[date, dict[str, object]] = {}
    for item in levels:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"Hang Seng Indexes returned a malformed point for {series_id}")
        try:
            epoch_seconds = float(item[0]) / 1000.0
            observed = datetime.fromtimestamp(
                epoch_seconds,
                tz=timezone.utc,
            ).astimezone(HONG_KONG).date()
        except (TypeError, ValueError, OSError, OverflowError):
            raise ValueError(f"Hang Seng Indexes returned an invalid date for {series_id}") from None
        if observed < start_date or observed > end_date:
            continue
        close = _positive_number(item[1], f"{series_id} close", observed)
        row = _close_only_row(
            series_id,
            observed,
            close,
            source_dataset="hang_seng_indexes_official_chart",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        prior = by_date.get(observed)
        if prior is not None and prior != row:
            raise ValueError(f"Hang Seng Indexes has conflicting rows on {observed}")
        by_date[observed] = row
    return [by_date[key] for key in sorted(by_date)]

def _close_only_row(
    series_id: str,
    observed: date,
    close: float,
    *,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
) -> dict[str, object]:
    return {
        "series_id": series_id,
        "date": observed,
        "open": None,
        "high": None,
        "low": None,
        "close": close,
        "volume": None,
        "vwap": None,
        "source_dataset": source_dataset,
        "raw_sha256": raw_sha256,
        "collected_at": collected_at,
    }

def _iso_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _positive_number(value: object, label: str, observed: date) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} is invalid on {observed}") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} is not positive on {observed}")
    return number

def _optional_number(
    value: object,
    *,
    minimum: float,
    strict: bool = False,
) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < minimum or (strict and number <= minimum):
        return None
    return number


HSIL_CHART_URL = "https://www.hsi.com.hk/data/eng/indexes/{code}/chart.json"

HK_SECTOR_CODES: Mapping[str, str] = {
    "HSCIEN.HI": "00011.01",
    "HSCIMT.HI": "00011.02",
    "HSCIIN.HI": "00011.03",
    "HSCITC.HI": "00011.06",
    "HSCIUT.HI": "00011.07",
    "HSCIFN.HI": "00011.08",
    "HSCIPC.HI": "00011.09",
    "HSCIIT.HI": "00011.10",
    "HSCICO.HI": "00011.11",
    "HSCICD.HI": "00011.12",
    "HSCICS.HI": "00011.13",
    "HSCIH.HI": "00011.14",
}

_HK_NAMES = {
    "HSCIEN.HI": "Hang Seng Composite Energy Index",
    "HSCIMT.HI": "Hang Seng Composite Materials Index",
    "HSCIIN.HI": "Hang Seng Composite Industrials Index",
    "HSCICD.HI": "Hang Seng Composite Consumer Discretionary Index",
    "HSCICS.HI": "Hang Seng Composite Consumer Staples Index",
    "HSCIH.HI": "Hang Seng Composite Healthcare Index",
    "HSCIFN.HI": "Hang Seng Composite Financials Index",
    "HSCIIT.HI": "Hang Seng Composite Information Technology Index",
    "HSCITC.HI": "Hang Seng Composite Telecommunications Index",
    "HSCIUT.HI": "Hang Seng Composite Utilities Index",
    "HSCIPC.HI": "Hang Seng Composite Properties & Construction Index",
    "HSCICO.HI": "Hang Seng Composite Conglomerates Index",
}

MARKET_SERIES_DEFINITIONS = (
    {
        "series_id": "XAUUSD",
        "provider_symbol": "XAUUSD",
        "name": "Gold Spot / U.S. Dollar",
        "asset_class": "commodity_spot",
        "market": "global",
        "currency": "USD",
        "calendar": "provider_forex_daily",
        "timezone": "America/New_York",
        "unit": "USD per troy ounce",
        "price_semantics": "raw_provider_ohlcv_v1",
        "historical_use": "provider_history_with_current_revisions",
    },
    {
        "series_id": "BTCUSD",
        "provider_symbol": "BTCUSD",
        "name": "Bitcoin / U.S. Dollar",
        "asset_class": "crypto_spot",
        "market": "global",
        "currency": "USD",
        "calendar": "calendar_day",
        "timezone": "UTC",
        "unit": "USD per BTC",
        "price_semantics": "raw_provider_ohlcv_v1",
        "historical_use": "provider_history_with_current_revisions",
    },
    {
        "series_id": "DXY",
        "provider_symbol": "DX-Y.NYB",
        "name": "U.S. Dollar Index",
        "asset_class": "currency_index",
        "market": "global",
        "currency": "USD",
        "calendar": "provider_us_dollar_index_daily",
        "timezone": "America/New_York",
        "unit": "index points",
        "price_semantics": "raw_provider_ohlcv_v1",
        "historical_use": "provider_history_with_current_revisions",
    },
    *(
        {
            "series_id": series_id,
            "provider_symbol": code,
            "name": _HK_NAMES[series_id],
            "asset_class": "equity_index",
            "market": "HK",
            "currency": "HKD",
            "calendar": "XHKG",
            "timezone": "Asia/Hong_Kong",
            "unit": "index points",
            "price_semantics": "provider_index_close_v1",
            "historical_use": "wind_immutable_seed_then_hsil_official",
        }
        for series_id, code in HK_SECTOR_CODES.items()
    ),
)
