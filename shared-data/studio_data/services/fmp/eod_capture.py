"""Targeted acquisition for registered FMP equities and ETFs.

Registration and later instrument refreshes use the existing collector when
their shared numerical history is missing, stale, or awaiting a price revision.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import exchange_calendars
from investment_studio_instrument_core.listing_contract import session_calendar_name

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector, closed_symbol_date, failure_summary
from studio_market.numeric.price_revisions import history_start, pending_revisions
from studio_data.services.fmp.client import FmpApiError
from studio_data.services.instrument_store import update_refresh_status


def ensure_fmp_security_history(
    instrument_id: str,
    *,
    symbol: str,
    exchange_code: str,
    store: NumericStore | None = None,
    collector: Collector | None = None,
    now: datetime | None = None,
) -> None:
    market = store
    capture = collector
    try:
        settings = MarketSettings.from_environment()
        if market is None:
            market = NumericStore(settings)
        if capture is None:
            capture = Collector(settings, store=market)
        closed = closed_symbol_date(symbol, now or datetime.now(UTC))
        # The collector's cutoff observes time zones and market closes; the same
        # exchange calendar used by Studio removes holidays from the due date.
        expected = exchange_calendars.get_calendar(session_calendar_name(exchange_code)).date_to_session(
            closed.isoformat(), direction="previous",
        ).date()
        latest = market.latest("raw_eod_daily", symbols=[symbol], limit=1)["rows"]
        latest_date = date.fromisoformat(str(latest[0]["date"])) if latest else None
        revisions = pending_revisions(
            market, dataset="raw_eod_daily", symbols=[symbol], end=expected,
        )
        if latest_date is None or latest_date < expected or revisions:
            begin = latest_date or history_start(market, "raw_eod_daily", symbol)
            outcome = capture.raw_eod(begin, closed, [symbol])
            if outcome.get("status") != "ready":
                failures = [item for item in outcome.get("symbols", []) if item.get("status") == "failed"]
                details = "; ".join(str(item.get("error") or "collection failed") for item in failures)
                raise FmpApiError(f"Shared FMP history collection failed for {symbol}: {details or 'incomplete collection'}")
            latest = market.latest("raw_eod_daily", symbols=[symbol], limit=1)["rows"]
            latest_date = date.fromisoformat(str(latest[0]["date"])) if latest else None
        if pending_revisions(market, dataset="raw_eod_daily", symbols=[symbol], end=expected):
            raise FmpApiError(f"Shared FMP history for {symbol} still has pending adjustment revisions.")
        if latest_date is None or latest_date < expected:
            observed = latest_date.isoformat() if latest_date else "none"
            raise FmpApiError(
                f"Shared FMP history for {symbol} is not ready: latest {observed}, expected closed session {expected.isoformat()}."
            )
    except Exception as error:
        # Provider responses/URLs can contain credentials. Collector summaries
        # and our own readiness errors are safe to persist and return to the UI.
        message = str(error) if isinstance(error, FmpApiError) else f"Shared FMP history collection failed for {symbol}: {failure_summary(error)['error']}"
        update_refresh_status(
            instrument_id=instrument_id, status="failed", message=message,
            updated_by="fmp_security_sync", mode="api",
        )
        raise FmpApiError(message) from error
    finally:
        if collector is None and capture is not None and capture._client is not None:
            capture._client.close()
        if store is None and market is not None:
            market.close()
