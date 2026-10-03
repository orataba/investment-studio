"""Hang Seng's public website price-index history contract.

These are the original PI identifiers from the website's allIndexes metadata,
not its separately identified gross/net total-return indexes or rebased charts.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math


HSIL_INDEX_CODES: Mapping[str, str] = {
    "HSCI.HI": "00011.00",
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


def hsil_history_url(symbol: str) -> str:
    try:
        code = HSIL_INDEX_CODES[symbol]
    except KeyError:
        raise ValueError(f"Unsupported Hang Seng price index: {symbol}") from None
    return (
        "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy"
        "/v1/product-data/pub/indexes/metadata/v2"
        f"?data=valueHistoryPub&language=en-hk&indexCodes={code}"
    )


def validate_hsil_close_rows(rows: object, *, symbol: str) -> list[dict]:
    """Validate complete close history before any consumer date filtering."""
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{symbol}: Hang Seng price history is empty or malformed")
    by_date = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{symbol}: malformed Hang Seng history row")
        value = row.get("date")
        try:
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError
            observed = date.fromisoformat(value)
            if observed.isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError(f"{symbol}: invalid Hang Seng observation date") from None
        raw_close = row.get("close")
        try:
            if isinstance(raw_close, bool) or not isinstance(raw_close, (str, int, float)):
                raise ValueError
            close = float(raw_close)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{symbol}: invalid Hang Seng close on {observed}") from None
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"{symbol}: Hang Seng close must be finite and positive on {observed}")
        if observed in by_date:
            raise ValueError(f"{symbol}: duplicate Hang Seng observation date {observed}")
        by_date[observed] = {"date": observed, "close": close}
    return [by_date[observed] for observed in sorted(by_date)]


def parse_hsil_history(payload: object, *, symbol: str) -> list[dict]:
    """Read one exact PI response from the official valueHistoryPub endpoint."""
    hsil_history_url(symbol)  # Reject undeclared identities before parsing.
    if not isinstance(payload, Mapping) or payload.get("code") != "0":
        raise ValueError(f"{symbol}: Hang Seng history response did not succeed")
    indexes = payload.get("data")
    if not isinstance(indexes, list) or len(indexes) != 1:
        raise ValueError(f"{symbol}: expected exactly one Hang Seng price index")
    index = indexes[0]
    if not isinstance(index, Mapping) or index.get("indexCode") != HSIL_INDEX_CODES[symbol]:
        raise ValueError(f"{symbol}: Hang Seng Indexes identity mismatch")
    # Current history responses omit indexType. The exact allowlisted code is
    # the PI identity; reject a contradictory type if the provider adds one.
    if "indexType" in index and index["indexType"] != "PI":
        raise ValueError(f"{symbol}: Hang Seng history is not a price index")
    return validate_hsil_close_rows(index.get("valueHistory"), symbol=symbol)
