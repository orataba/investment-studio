"""Targeted acquisition for registered FMP equities and ETFs.

Registration and later instrument refreshes use the existing collector when
their shared numerical history is missing, stale, or awaiting a price revision.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import exchange_calendars
from investment_studio_instrument_core.listing_contract import session_calendar_name

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector, closed_symbol_date, failure_summary
from studio_market.numeric.price_revisions import history_start, pending_revisions
from studio_market.numeric.hkex_sessions import ensure_session_report, read_no_trade_evidence
from studio_data.services.fmp.client import FmpApiError
from studio_data.services.instrument_store import update_refresh_status


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
    try:
        settings = MarketSettings.from_environment()
        if market is None:
            market = NumericStore(settings)
        if capture is None:
            capture = Collector(settings, store=market)
        closed = closed_symbol_date(symbol, now or datetime.now(UTC))
        # The collector's cutoff observes time zones and market closes; the same
        # exchange calendar used by Studio removes holidays from the due date.
        calendar = exchange_calendars.get_calendar(session_calendar_name(exchange_code))
        expected = calendar.date_to_session(
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
            # An exchange session need not contain a trade in every security.
            # Only dated HKEX evidence for every missing tail session can
            # establish this; provider absence or suspension alone cannot.
            evidence = []
            if exchange_code == "XHKG" and latest_date is not None and currency:
                missing = [day.date() for day in calendar.sessions_in_range(
                    latest_date + timedelta(days=1), expected,
                )]
                for day in missing:
                    ensure_session_report(market, day)
                    confirmed = read_no_trade_evidence(
                        market, symbol=symbol, currency=currency, last_close=latest[0].get("close"),
                        after=day - timedelta(days=1), through=day,
                    )
                    if not confirmed:
                        break
                    evidence.extend(confirmed)
                if missing and len(evidence) == len(missing):
                    return evidence
            observed = latest_date.isoformat() if latest_date else "none"
            raise FmpApiError(
                f"Shared FMP history for {symbol} is not ready: latest {observed}, expected closed session {expected.isoformat()}."
            )
        return []
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
