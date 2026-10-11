"""Targeted acquisition for registered FMP equities and ETFs.

Registration and later instrument refreshes use the existing collector when
their shared numerical history is missing, stale, or awaiting a price revision.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import UTC, date, datetime, timedelta

from investment_studio_instrument_core.listing_contract import session_calendar_name

from studio_market.calendars import closed_market_sessions
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector, closed_symbol_date, failure_summary
from studio_market.numeric.price_revisions import history_start, pending_revisions
from studio_market.numeric.hkex_sessions import ensure_session_report, read_no_trade_evidence
from studio_data.services.fmp.client import FmpApiError
from studio_data.services.instrument_store import update_refresh_status


class FmpHistoryIncomplete(FmpApiError):
    """Collection committed real prices, but the due session window has gaps.

    Projection may publish those observations while preserving failed coverage.
    Transport failures and unfinished adjustment revisions do not use this path.
    """

    def __init__(self, message: str, *, no_trade_evidence: list[dict]):
        super().__init__(message)
        self.no_trade_evidence = no_trade_evidence


def _history_through(market: NumericStore, symbol: str, end: date) -> list[dict]:
    history = market.query("raw_eod_daily", symbols=[symbol], end=end.isoformat(), limit=100000)
    rows = history["rows"]
    if len(rows) != history["total"]:
        raise FmpApiError(f"Shared FMP history for {symbol} is incomplete: {len(rows)} of {history['total']} rows read.")
    return sorted(rows, key=lambda row: str(row["date"]))


def ensure_fmp_security_history(
    instrument_id: str,
    *,
    symbol: str,
    exchange_code: str,
    currency: str | None = None,
    store: NumericStore | None = None,
    collector: Collector | None = None,
    now: datetime | None = None,
) -> list[dict]:
    market = store
    capture = collector
    clock = now or datetime.now(UTC)
    try:
        settings = MarketSettings.from_environment()
        if market is None:
            market = NumericStore(settings)
        if capture is None:
            capture = Collector(settings, store=market)
        expected = closed_symbol_date(symbol, clock, exchange_code=exchange_code)
        rows = _history_through(market, symbol, expected)
        latest_date = date.fromisoformat(str(rows[-1]["date"])) if rows else None
        # Freeze the refresh responsibility window before acquisition. A newly
        # returned latest close must not hide another missing session in it.
        previous_latest = latest_date
        revisions = pending_revisions(
            market, dataset="raw_eod_daily", symbols=[symbol], end=expected,
        )
        if latest_date is None or latest_date < expected or revisions:
            begin = latest_date or history_start(market, "raw_eod_daily", symbol)
            outcome = capture.raw_eod(begin, expected, [symbol], now=clock, exchanges={symbol: exchange_code})
            if outcome.get("status") != "ready":
                failures = [item for item in outcome.get("symbols", []) if item.get("status") == "failed"]
                details = "; ".join(str(item.get("error") or "collection failed") for item in failures)
                raise FmpApiError(f"Shared FMP history collection failed for {symbol}: {details or 'incomplete collection'}")
            rows = _history_through(market, symbol, expected)
            latest_date = date.fromisoformat(str(rows[-1]["date"])) if rows else None
        if pending_revisions(market, dataset="raw_eod_daily", symbols=[symbol], end=expected):
            raise FmpApiError(f"Shared FMP history for {symbol} still has pending adjustment revisions.")
        # Initial registration validates its latest tail, not an invented
        # lifetime/listing boundary. Historical path eligibility remains the
        # responsibility of consumers' requested-window checks.
        coverage_start = previous_latest or latest_date or expected
        sessions = closed_market_sessions(session_calendar_name(exchange_code), coverage_start, expected, clock)
        if sessions is None:
            raise FmpApiError(f"Shared FMP history for {symbol} has no verified session calendar.")
        dates = [date.fromisoformat(str(row["date"])) for row in rows]
        observed_dates = set(dates)
        missing = [day for day in sessions if day not in observed_dates]
        evidence = []
        unresolved = []
        for day in missing:
            # Only dated exchange evidence can excuse an absent trade. Match
            # the last real close preceding THIS day, including interior gaps.
            preceding = bisect_left(dates, day) - 1
            confirmed = []
            if exchange_code == "XHKG" and preceding >= 0 and currency:
                try:
                    ensure_session_report(market, day)
                except Exception as error:
                    # HKEX's dated quotation can publish after the first EOD
                    # run. Identify the unavailable evidence instead of calling
                    # a public-report 404 an FMP price download failure.
                    detail = failure_summary(error)["error"]
                    raise FmpApiError(
                        f"HKEX daily quotation evidence for {symbol} on {day.isoformat()} "
                        f"is unavailable: {detail}. No-trade status remains unconfirmed; "
                        "rerun after the exchange report is available."
                    ) from error
                confirmed = read_no_trade_evidence(
                    market, symbol=symbol, currency=currency, last_close=rows[preceding].get("close"),
                    after=day - timedelta(days=1), through=day,
                )
            if confirmed:
                evidence.extend(confirmed)
            else:
                unresolved.append(day)
        if unresolved or latest_date is None:
            observed = latest_date.isoformat() if latest_date else "none"
            detail = ", ".join(day.isoformat() for day in unresolved[:10])
            if len(unresolved) > 10:
                detail += ", ..."
            raise FmpHistoryIncomplete(
                f"Shared FMP history for {symbol} is not ready: latest {observed}, expected closed session {expected.isoformat()}. "
                f"Missing {len(unresolved)} required session(s) in this refresh window: {detail}.",
                no_trade_evidence=evidence,
            )
        return evidence
    except Exception as error:
        # Provider responses/URLs can contain credentials. Collector summaries
        # and our own readiness errors are safe to persist and return to the UI.
        message = str(error) if isinstance(error, FmpApiError) else f"Shared FMP history collection failed for {symbol}: {failure_summary(error)['error']}"
        update_refresh_status(
            instrument_id=instrument_id, status="failed", message=message,
            updated_by="fmp_security_sync", mode="api",
        )
        if isinstance(error, FmpApiError):
            raise
        raise FmpApiError(message) from error
    finally:
        if collector is None and capture is not None and capture._client is not None:
            capture._client.close()
        if store is None and market is not None:
            market.close()
