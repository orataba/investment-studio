"""Provider normalization retained and owned by Investment Studio."""
from __future__ import annotations

import csv

import hashlib

import io

import json

import math

import re

import time

from datetime import date, datetime, timedelta, timezone

from typing import Any, Callable, Iterable, Mapping

FINANCIAL_PERIODS = ("Q1", "Q2", "Q3", "Q4", "FY")

STATEMENT_ENDPOINTS = {
    "income": ("income-statement-bulk", "income-statement"),
    "balance_sheet": (
        "balance-sheet-statement-bulk",
        "balance-sheet-statement",
    ),
    "cash_flow": ("cash-flow-statement-bulk", "cash-flow-statement"),
}

NORMALIZED_METADATA_FIELDS = {
    "date",
    "symbol",
    "reportedCurrency",
    "cik",
    "filingDate",
    "acceptedDate",
    "fiscalYear",
    "calendarYear",
    "period",
    "link",
    "finalLink",
}

ACCESSION_PATTERN = re.compile(r"(\d{10}-\d{2}-\d{6})")

def normalize_financial_csv(
    body: bytes,
    *,
    statement_type: str,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
    source_dataset: str,
    expected_year: int | None = None,
    expected_period: str | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("FMP financial bulk CSV is not UTF-8") from None
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "date",
        "symbol",
        "reportedCurrency",
        "filingDate",
        "acceptedDate",
        "fiscalYear",
        "period",
    }
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("FMP financial bulk CSV is missing required columns")
    return _normalize_financial_items(
        reader,
        statement_type=statement_type,
        allowed_symbols=allowed_symbols,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
        source_dataset=source_dataset,
        expected_year=expected_year,
        expected_period=expected_period,
    )

def normalize_financial_payload(
    payload: Any,
    *,
    statement_type: str,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
    source_dataset: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(payload, list):
        raise ValueError("FMP normalized financial response is not a list")
    return _normalize_financial_items(
        payload,
        statement_type=statement_type,
        allowed_symbols=allowed_symbols,
        raw_sha256=raw_sha256,
        collected_at=collected_at,
        source_dataset=source_dataset,
    )

def normalize_as_reported_payload(
    payload: Any,
    *,
    expected_symbol: str,
    raw_sha256: str,
    collected_at: datetime,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(payload, list):
        raise ValueError("FMP as-reported response is not a list")
    by_identity: dict[
        tuple[str, date, int, str],
        tuple[dict[str, object], list[dict[str, object]]],
    ] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        period_end = _optional_date(item.get("date"))
        fiscal_year = _optional_int(item.get("fiscalYear"))
        fiscal_period = str(item.get("period") or "").strip().upper()
        data = item.get("data")
        if (
            symbol != expected_symbol
            or period_end is None
            or fiscal_year is None
            or fiscal_period not in FINANCIAL_PERIODS
            or not isinstance(data, Mapping)
        ):
            continue
        if period_end > collected_at.date() or abs(period_end.year - fiscal_year) > 1:
            continue
        if start_date is not None and period_end < start_date:
            continue
        if end_date is not None and period_end > end_date:
            continue

        values = {
            str(concept): _text_value(value)
            for concept, value in data.items()
            if value is not None
        }
        if not values:
            continue
        content_hash = _content_hash(
            {
                "symbol": symbol,
                "period_end": period_end.isoformat(),
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "reported_currency": _optional_text(item.get("reportedCurrency")),
                "facts": values,
            }
        )
        statement = {
            "symbol": symbol,
            "period_end": period_end,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "reported_currency": _optional_text(item.get("reportedCurrency")),
            "statement_content_sha256": content_hash,
            "source_dataset": "fmp_financial_statement_full_as_reported",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
        facts = [
            {
                "statement_content_sha256": content_hash,
                "concept": concept,
                "value_text": value_text,
                "numeric_value": _optional_number(value_text),
            }
            for concept, value_text in sorted(values.items())
        ]
        identity = (symbol, period_end, fiscal_year, fiscal_period)
        current = by_identity.get(identity)
        if current is None or len(facts) > len(current[1]):
            by_identity[identity] = statement, facts

    statements: list[dict[str, object]] = []
    facts: list[dict[str, object]] = []
    for identity in sorted(by_identity):
        statement, statement_facts = by_identity[identity]
        statements.append(statement)
        facts.extend(statement_facts)
    return statements, facts

def normalize_sec_filing_payload(
    payload: Any,
    *,
    expected_symbol: str,
    start_date: date,
    end_date: date,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP SEC filing response is not a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        filing_date = _optional_date(item.get("filingDate"))
        form_type = str(item.get("formType") or "").strip().upper()
        if (
            symbol != expected_symbol
            or filing_date is None
            or not start_date <= filing_date <= end_date
            or not form_type
        ):
            continue
        index_url = _optional_text(item.get("link"))
        document_url = _optional_text(item.get("finalLink"))
        accession_number = _accession_number(index_url, document_url)
        filing_key = accession_number or _content_hash(
            {
                "symbol": symbol,
                "filing_date": filing_date.isoformat(),
                "form_type": form_type,
                "index_url": index_url,
                "document_url": document_url,
            }
        )
        content = {
            "filing_key": filing_key,
            "symbol": symbol,
            "cik": _optional_text(item.get("cik")),
            "filing_date": filing_date.isoformat(),
            "accepted_at": (
                _optional_datetime(item.get("acceptedDate")).isoformat()
                if _optional_datetime(item.get("acceptedDate")) is not None
                else None
            ),
            "form_type": form_type,
            "accession_number": accession_number,
            "index_url": index_url,
            "document_url": document_url,
        }
        rows.append(
            {
                "filing_key": filing_key,
                "symbol": symbol,
                "cik": content["cik"],
                "filing_date": filing_date,
                "accepted_at": _optional_datetime(item.get("acceptedDate")),
                "form_type": form_type,
                "accession_number": accession_number,
                "index_url": index_url,
                "document_url": document_url,
                "filing_content_sha256": _content_hash(content),
                "source_dataset": "fmp_sec_filings_search_symbol",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
            }
        )
    return rows

def _normalize_financial_items(
    items: Iterable[Mapping[str, object]],
    *,
    statement_type: str,
    allowed_symbols: set[str],
    raw_sha256: str,
    collected_at: datetime,
    source_dataset: str,
    expected_year: int | None = None,
    expected_period: str | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if statement_type not in STATEMENT_ENDPOINTS:
        raise ValueError(f"unsupported financial statement type: {statement_type}")
    by_identity: dict[
        tuple[str, str, date, int, str],
        tuple[dict[str, object], list[dict[str, object]]],
    ] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        period_end = _optional_date(item.get("date"))
        fiscal_year = _optional_int(
            item.get("fiscalYear")
            if item.get("fiscalYear") is not None
            else item.get("calendarYear")
        )
        fiscal_period = str(item.get("period") or "").strip().upper()
        if (
            symbol not in allowed_symbols
            or period_end is None
            or fiscal_year is None
            or fiscal_period not in FINANCIAL_PERIODS
        ):
            continue
        if period_end > collected_at.date() or abs(period_end.year - fiscal_year) > 1:
            continue
        if expected_year is not None and fiscal_year != expected_year:
            continue
        if expected_period is not None and fiscal_period != expected_period:
            continue

        values: dict[str, float] = {}
        for line_item, raw_value in item.items():
            if line_item in NORMALIZED_METADATA_FIELDS:
                continue
            value = _optional_number(raw_value)
            if value is not None:
                values[str(line_item)] = value
        if not values:
            continue

        reported_currency = _optional_text(item.get("reportedCurrency"))
        cik = _optional_text(item.get("cik"))
        filing_date = _optional_date(item.get("filingDate"))
        accepted_at = _optional_datetime(item.get("acceptedDate"))
        content_hash = _content_hash(
            {
                "statement_type": statement_type,
                "symbol": symbol,
                "period_end": period_end.isoformat(),
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "reported_currency": reported_currency,
                "cik": cik,
                "filing_date": filing_date.isoformat() if filing_date else None,
                "accepted_at": accepted_at.isoformat() if accepted_at else None,
                "facts": values,
            }
        )
        statement = {
            "statement_type": statement_type,
            "symbol": symbol,
            "period_end": period_end,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "reported_currency": reported_currency,
            "cik": cik,
            "filing_date": filing_date,
            "accepted_at": accepted_at,
            "statement_content_sha256": content_hash,
            "source_dataset": source_dataset,
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
        }
        facts = [
            {
                "statement_content_sha256": content_hash,
                "line_item": line_item,
                "value": value,
            }
            for line_item, value in sorted(values.items())
        ]
        identity = (
            statement_type,
            symbol,
            period_end,
            fiscal_year,
            fiscal_period,
        )
        current = by_identity.get(identity)
        if current is None or len(facts) > len(current[1]):
            by_identity[identity] = statement, facts

    statements: list[dict[str, object]] = []
    facts: list[dict[str, object]] = []
    for identity in sorted(by_identity):
        statement, statement_facts = by_identity[identity]
        statements.append(statement)
        facts.extend(statement_facts)
    return statements, facts

def _accession_number(*urls: str | None) -> str | None:
    for url in urls:
        if not url:
            continue
        match = ACCESSION_PATTERN.search(url)
        if match:
            return match.group(1)
    return None

def _content_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _optional_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in {"", "none", "null", "nan", "n/a", "-"}:
            return None
        text = text.replace(",", "")
    else:
        text = value
    try:
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None

def _optional_int(value: object) -> int | None:
    number = _optional_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)

def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _text_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)
