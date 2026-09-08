"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

import math

from datetime import date, datetime, timedelta

def normalize_cboe_vix(
    body: bytes,
    *,
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"DATE", "OPEN", "HIGH", "LOW", "CLOSE"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError("Cboe VIX CSV has unexpected columns")
    rows: list[dict[str, object]] = []
    for item in reader:
        observation_date = datetime.strptime(item["DATE"], "%m/%d/%Y").date()
        if observation_date < start_date or observation_date > end_date:
            continue
        open_value = _number(item["OPEN"], "VIX OPEN", observation_date)
        high_value = _number(item["HIGH"], "VIX HIGH", observation_date)
        low_value = _number(item["LOW"], "VIX LOW", observation_date)
        close_value = _number(item["CLOSE"], "VIX CLOSE", observation_date)
        if min(open_value, high_value, low_value, close_value) <= 0:
            raise ValueError(f"Cboe VIX value is not positive on {observation_date}")
        rows.append(
            {
                "series_id": "CBOE_VIX",
                "date": observation_date,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "value": close_value,
                "unit": "index",
                "source_dataset": "cboe_official_vix_history",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def normalize_fred_series(
    body: bytes,
    *,
    source_series_id: str,
    series_id: str,
    unit: str,
    start_date: date,
    end_date: date,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
    minimum: float | None,
) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    fieldnames = set(reader.fieldnames or [])
    date_column = next(
        (name for name in ("observation_date", "DATE", "date") if name in fieldnames),
        None,
    )
    if date_column is None or source_series_id not in fieldnames:
        raise ValueError(f"FRED {source_series_id} CSV has unexpected columns")

    rows: list[dict[str, object]] = []
    for item in reader:
        raw_value = str(item.get(source_series_id, "")).strip()
        if not raw_value or raw_value == ".":
            continue
        observation_date = date.fromisoformat(str(item[date_column])[:10])
        if observation_date < start_date or observation_date > end_date:
            continue
        value = _number(raw_value, source_series_id, observation_date)
        if minimum is not None and value < minimum:
            raise ValueError(
                f"FRED {source_series_id} is below {minimum} on {observation_date}"
            )
        rows.append(
            {
                "series_id": series_id,
                "date": observation_date,
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "value": value,
                "unit": unit,
                "source_dataset": source_dataset,
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def _number(value: object, label: str, observation_date: date) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} is not numeric on {observation_date}") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite on {observation_date}")
    return number


CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

FRED_CONTEXT_SERIES = (
    {
        "source_series_id": "BAMLH0A0HYM2",
        "series_id": "US_HY_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLC0A0CM",
        "series_id": "US_IG_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLC0A1CAAA",
        "series_id": "US_IG_AAA_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLC0A2CAA",
        "series_id": "US_IG_AA_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLC0A3CA",
        "series_id": "US_IG_A_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLC0A4CBBB",
        "series_id": "US_IG_BBB_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLH0A1HYBB",
        "series_id": "US_HY_BB_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLH0A2HYB",
        "series_id": "US_HY_B_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "BAMLH0A3HYC",
        "series_id": "US_HY_CCC_OAS",
        "unit": "percent",
        "minimum": 0.0,
        "provider_history": "rolling_three_years_since_2026-04",
    },
    {
        "source_series_id": "EFFR",
        "series_id": "US_EFFECTIVE_FED_FUNDS",
        "unit": "percent",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
    {
        "source_series_id": "SOFR",
        "series_id": "US_SOFR",
        "unit": "percent",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
    {
        "source_series_id": "T5YIE",
        "series_id": "US_BREAKEVEN_5Y",
        "unit": "percent",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
    {
        "source_series_id": "T10YIE",
        "series_id": "US_BREAKEVEN_10Y",
        "unit": "percent",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
    {
        "source_series_id": "T5YIFR",
        "series_id": "US_FORWARD_INFLATION_5Y5Y",
        "unit": "percent",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
    {
        "source_series_id": "DCOILWTICO",
        "series_id": "US_WTI_SPOT",
        "unit": "USD per barrel",
        "minimum": None,
        "provider_history": "provider_available_history",
    },
)
