"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import math

import xml.etree.ElementTree as ET

from datetime import date, datetime

from typing import Callable

def normalize_treasury_xml(
    body: bytes,
    *,
    field_map: dict[str, str],
    start_date: date,
    end_date: date,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ValueError("Treasury XML is invalid") from None
    rows: list[dict[str, object]] = []
    for entry in root.iter():
        if _local_name(entry.tag) != "entry":
            continue
        values = {
            _local_name(element.tag): (element.text or "").strip()
            for element in entry.iter()
            if element.text and element.text.strip()
        }
        observation_date = _date(values.get("NEW_DATE"))
        if observation_date is None or not start_date <= observation_date <= end_date:
            continue
        for provider_field, series_id in field_map.items():
            value = _number(values.get(provider_field))
            if value is None:
                continue
            rows.append(
                {
                    "series_id": series_id,
                    "date": observation_date,
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": None,
                    "value": value,
                    "unit": "percent",
                    "source_dataset": source_dataset,
                    "raw_sha256": raw_sha256,
                    "collected_at": collected_at,
                }
            )
    if not rows:
        raise ValueError("Treasury XML contained no usable observations")
    return rows

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

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


TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)

NOMINAL_FIELDS = {
    "BC_1MONTH": "US_TREASURY_1M",
    "BC_1_5MONTH": "US_TREASURY_1_5M",
    "BC_2MONTH": "US_TREASURY_2M",
    "BC_3MONTH": "US_TREASURY_3M",
    "BC_4MONTH": "US_TREASURY_4M",
    "BC_6MONTH": "US_TREASURY_6M",
    "BC_1YEAR": "US_TREASURY_1Y",
    "BC_2YEAR": "US_TREASURY_2Y",
    "BC_3YEAR": "US_TREASURY_3Y",
    "BC_5YEAR": "US_TREASURY_5Y",
    "BC_7YEAR": "US_TREASURY_7Y",
    "BC_10YEAR": "US_TREASURY_10Y",
    "BC_20YEAR": "US_TREASURY_20Y",
    "BC_30YEAR": "US_TREASURY_30Y",
}

REAL_FIELDS = {
    "TC_5YEAR": "US_TREASURY_REAL_5Y",
    "TC_7YEAR": "US_TREASURY_REAL_7Y",
    "TC_10YEAR": "US_TREASURY_REAL_10Y",
    "TC_20YEAR": "US_TREASURY_REAL_20Y",
    "TC_30YEAR": "US_TREASURY_REAL_30Y",
}
