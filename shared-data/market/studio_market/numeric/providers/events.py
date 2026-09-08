"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

import math

from datetime import date, datetime, timezone

from typing import Callable, Iterable, Mapping

def normalize_earnings_surprises(
    body: bytes,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if body.strip() == b"[]":
        return []
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"symbol", "date", "epsActual", "epsEstimated", "lastUpdated"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError("FMP earnings surprises CSV has unexpected columns")
    rows: list[dict[str, object]] = []
    for item in reader:
        symbol = _text(item.get("symbol"), upper=True)
        event_date = _date(item.get("date"))
        if symbol not in allowed_symbols or event_date is None:
            continue
        row: dict[str, object] = {
            "symbol": symbol,
            "event_date": event_date,
            "eps_actual": _number(item.get("epsActual")),
            "eps_estimated": _number(item.get("epsEstimated")),
            "last_updated_date": _date(item.get("lastUpdated")),
        }
        _finish_row(
            row,
            source_dataset="fmp_earnings_surprises_bulk",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def normalize_insider_trades(
    payload: object,
    *,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    items = _payload_rows(payload, "insider-trading/latest")
    rows: list[dict[str, object]] = []
    for item in items:
        symbol = _text(item.get("symbol"), upper=True)
        filing_date = _date(item.get("filingDate"))
        transaction_date = _date(item.get("transactionDate"))
        if (
            symbol not in allowed_symbols
            or filing_date is None
            or transaction_date is None
        ):
            continue
        row: dict[str, object] = {
            "symbol": symbol,
            "company_cik": _text(item.get("companyCik")),
            "reporting_cik": _text(item.get("reportingCik")),
            "reporting_name": _text(item.get("reportingName")),
            "filing_date": filing_date,
            "transaction_date": transaction_date,
            "transaction_type": _text(item.get("transactionType")),
            "acquisition_or_disposition": _text(
                item.get("acquisitionOrDisposition")
            ),
            "direct_or_indirect": _text(item.get("directOrIndirect")),
            "form_type": _text(item.get("formType")),
            "security_name": _text(item.get("securityName")),
            "securities_transacted": _number(item.get("securitiesTransacted")),
            "securities_owned": _number(item.get("securitiesOwned")),
            "price": _number(item.get("price")),
            "owner_type": _text(item.get("typeOfOwner")),
            "filing_url": _text(item.get("url")),
        }
        _finish_row(
            row,
            source_dataset="fmp_insider_trading_latest",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def normalize_institutional_filings(
    payload: object,
    *,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    items = _payload_rows(payload, "institutional-ownership/latest")
    rows: list[dict[str, object]] = []
    for item in items:
        manager_cik = _text(item.get("cik"))
        report_date = _date(item.get("date"))
        filing_date = _date(item.get("filingDate"))
        if not manager_cik or report_date is None or filing_date is None:
            continue
        row: dict[str, object] = {
            "manager_cik": manager_cik,
            "manager_name": _text(item.get("name")),
            "report_date": report_date,
            "filing_date": filing_date,
            "accepted_at": _datetime(item.get("acceptedDate")),
            "form_type": _text(item.get("formType")),
            "filing_url": _text(item.get("link")),
            "final_url": _text(item.get("finalLink")),
        }
        _finish_row(
            row,
            source_dataset="fmp_institutional_ownership_latest",
            raw_sha256=raw_sha256,
            collected_at=collected_at,
        )
        rows.append(row)
    return rows

def _finish_row(
    row: dict[str, object],
    *,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
) -> None:
    row["row_content_sha256"] = hashlib.sha256(
        json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    row["source_dataset"] = source_dataset
    row["raw_sha256"] = raw_sha256
    row["collected_at"] = collected_at

def _payload_rows(payload: object, endpoint: str) -> list[Mapping[str, object]]:
    if not isinstance(payload, list) or any(
        not isinstance(item, Mapping) for item in payload
    ):
        raise ValueError(f"FMP {endpoint} returned an unexpected payload")
    return payload

def _text(value: object, *, upper: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.upper() if upper else text

def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

def _number(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
