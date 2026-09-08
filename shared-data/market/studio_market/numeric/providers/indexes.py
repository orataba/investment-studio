

"""Provider normalization and request contracts owned by Investment Studio."""

from __future__ import annotations

import hashlib

import json

from dataclasses import dataclass

from datetime import date, datetime

from typing import Callable, Iterable, Mapping

@dataclass(frozen=True)
class IndexSpec:
    index_id: str
    current_endpoint: str
    history_endpoint: str

INDEX_SPECS = (
    IndexSpec("SP500", "sp500-constituent", "historical-sp500-constituent"),
    IndexSpec("NASDAQ100", "nasdaq-constituent", "historical-nasdaq-constituent"),
    IndexSpec(
        "DOW30",
        "dowjones-constituent",
        "historical-dowjones-constituent",
    ),
)

def normalize_current_membership(
    payload: object,
    *,
    endpoint: str,
) -> dict[str, str]:
    if not isinstance(payload, list):
        raise ValueError(f"FMP {endpoint} response is not a list")
    members: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        symbol = _symbol(item.get("symbol"))
        if symbol:
            members[symbol] = _text(item.get("name")) or symbol
    if not members:
        raise ValueError(f"FMP {endpoint} returned no usable members")
    return members

def normalize_index_events(
    payload: object,
    *,
    index_id: str,
    source_dataset: str,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("FMP historical index constituent response is not a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        event_date = _date(item.get("date"))
        if event_date is None:
            continue
        row: dict[str, object] = {
            "index_id": index_id,
            "event_date": event_date,
            "added_symbol": _symbol(item.get("symbol")),
            "added_name": _text(item.get("addedSecurity")),
            "removed_symbol": _symbol(item.get("removedTicker")),
            "removed_name": _text(item.get("removedSecurity")),
            "reason": _text(item.get("reason")),
        }
        row["row_content_sha256"] = _row_hash(row)
        row["source_dataset"] = source_dataset
        row["raw_sha256"] = raw_sha256
        row["collected_at"] = collected_at
        rows.append(row)
    return rows

def reconstruct_index_membership(
    current_members: Mapping[str, str],
    events: Iterable[Mapping[str, object]],
    *,
    as_of: date,
) -> dict[str, str]:
    members = dict(current_members)
    later_events = sorted(
        (
            event
            for event in events
            if isinstance(event.get("event_date"), date)
            and event["event_date"] > as_of
        ),
        key=lambda event: event["event_date"],
        reverse=True,
    )
    for event in later_events:
        added_symbol = event.get("added_symbol")
        removed_symbol = event.get("removed_symbol")
        if isinstance(added_symbol, str) and added_symbol:
            members.pop(added_symbol, None)
        if isinstance(removed_symbol, str) and removed_symbol:
            removed_name = event.get("removed_name")
            members[removed_symbol] = (
                str(removed_name).strip() if removed_name else removed_symbol
            )
    return members

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
