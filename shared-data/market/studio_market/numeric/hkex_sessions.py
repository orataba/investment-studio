"""Capture and read source-backed no-trade evidence without creating price bars."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from .delivery import file_lock
from .providers.hkex import daily_quotations_url, parse_daily_quotations
from .providers.http_client import get_public_bytes
from .raw import archive_response
from .schema import batches


DATASET = "hkex_security_sessions"


def ensure_session_report(store, day: date) -> None:
    """Reuse a complete daily capture across symbols and concurrent collectors."""
    with file_lock(store.settings.data_root / "numeric/.hkex-sessions.lock"):
        with store.engine.connect() as connection:
            captured = connection.scalar(select(batches.c.id).where(
                batches.c.dataset == DATASET, batches.c.status == "ready",
                batches.c.details["session_date"].as_string() == day.isoformat(),
            ).limit(1))
        if captured:
            return
        response = get_public_bytes(daily_quotations_url(day))
        rows = parse_daily_quotations(response.body, expected_date=day)
        _, raw_ref = archive_response(store.settings, "hkex", response.body)
        store.ingest(DATASET, [rows], source="hkex", observed_at=response.received_at,
                     raw_ref=raw_ref, details={"session_date": day.isoformat(),
                                              "endpoint": response.endpoint, "raw_ref": raw_ref})


def matching_no_trade_evidence(rows, *, symbol: str, currency: str, last_close: object) -> list[dict]:
    """Only an unchanged official close in the same currency can confirm freshness."""
    try:
        close = Decimal(str(last_close))
    except InvalidOperation:
        return []
    if not close.is_finite() or close <= 0:
        return []
    result = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row["date"]))
            quoted_close = Decimal(str(row.get("official_close")))
        except (KeyError, ValueError, InvalidOperation):
            continue
        if (quoted_close.is_finite() and row.get("symbol") == symbol and row.get("currency") == currency
                and row.get("session_status") == "no_trade" and quoted_close == close
                and row.get("source") == "hkex" and row.get("source_id")
                and str(row.get("raw_ref") or "").startswith("numeric/raw/hkex/")
                and row.get("source_url") == daily_quotations_url(day)):
            result.append(row)
    return result


def read_no_trade_evidence(store, *, symbol: str, currency: str, last_close: object,
                           after: date, through: date, as_of: datetime | None = None) -> list[dict]:
    # Current keys include the session date. This remains a bounded indexed
    # symbol read instead of scanning every security's archived daily report.
    result = store.latest(DATASET, symbols=[symbol], as_of=as_of, limit=100000)
    if result["total"] != len(result["rows"]):
        raise ValueError("HKEX session evidence was truncated")
    return matching_no_trade_evidence(
        [row for row in result["rows"] if after.isoformat() < str(row["date"]) <= through.isoformat()],
        symbol=symbol, currency=currency, last_close=last_close,
    )
