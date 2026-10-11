"""Capture Futu's raw daily prices without changing another provider's history."""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime

from studio_market.config import MarketSettings
from studio_market.calendars import closed_market_sessions, latest_closed_market_session
from studio_market.numeric.raw import archive_response
from studio_market.numeric.store import NumericStore

from .oauth import FutuOAuth
from .quotes import FutuQuoteClient, FutuQuoteError


class FutuHistoryError(RuntimeError):
    """A safe, actionable collection failure (never contains credentials)."""


@contextmanager
def _symbol_lock(settings, symbol):
    directory = settings.data_root / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"futu-{symbol}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _archive(settings, captures):
    parts = []
    for capture in captures:
        digest, ref = archive_response(settings, "futu", capture.body)
        parts.append({
            "endpoint": capture.endpoint,
            "parameters": capture.params,
            "raw_ref": ref,
            "raw_sha256": digest,
            "observed_at": capture.received_at,
        })
    return parts


def collect_history(
    settings: MarketSettings, symbol: str, start: date, end: date, *,
    store: NumericStore | None = None, client: FutuQuoteClient | None = None,
    now: datetime | None = None,
) -> dict:
    """Publish a validated price batch bound to retained identity/action captures.

    The provider's immutable stock ID and listing date bound ticker history.
    Changing that identity requires a reviewed migration, not automatic merging.
    Identity/actions can be archived first. Only the successful raw-price batch
    commits their binding for downstream returns; an interrupted capture is not
    a new source of return evidence. Raw quotes themselves do not supply a
    dividend-adjusted or total-return series.
    """
    # Validate before the symbol is used in a local lock path or network request.
    from .quotes import validate_symbol

    symbol = validate_symbol(symbol)
    if start > end:
        raise FutuHistoryError("Futu history start must not follow end.")
    observation_time = now or datetime.now(UTC)
    calendar_name = "XHKG" if symbol.startswith("HK.") else "XNYS"
    sessions = closed_market_sessions(calendar_name, start, end, observation_time)
    if sessions is None:
        raise FutuHistoryError("The Futu listing's closed trading sessions could not be verified.")
    if not sessions:
        raise FutuHistoryError("The requested Futu range has no closed market date yet.")
    stop = sessions[-1]
    # rehab is a current, complete-since-listing snapshot even when the requested
    # price window is historical. Binding that snapshot to the historical price
    # end would incorrectly withdraw newer retained total returns on replay.
    action_coverage_end = latest_closed_market_session(calendar_name, observation_time)
    market = store or NumericStore(settings)
    oauth = None
    quotes = client
    source_parts = []
    try:
        if quotes is None:
            oauth = FutuOAuth(settings.futu_config_dir)
            quotes = FutuQuoteClient(oauth.access_token)
        with _symbol_lock(settings, symbol):
            basic = quotes.basic_info(symbol)
            source_parts.extend(_archive(settings, basic.captures))
            identity = dict(basic.info)
            listing = identity.get("listing_date")
            if not listing:
                raise FutuHistoryError("Futu did not supply a listing date; security history needs identity review.")
            begin = max(start, date.fromisoformat(str(listing)))
            if begin > stop:
                raise FutuHistoryError("The requested Futu range precedes this security's listing.")
            prior = market.latest("futu_security_directory", symbols=[symbol], limit=1)["rows"]
            identity_fields = ("stock_id", "stock_type", "listing_date")
            if prior and any(prior[0].get(key) != identity.get(key) for key in identity_fields):
                raise FutuHistoryError("Futu security identity changed; review ticker continuity before collecting.")
            history = quotes.history(symbol, begin, stop)
            history_parts = _archive(settings, history.captures)
            source_parts.extend(history_parts)
            if not history.rows:
                raise FutuHistoryError("Futu returned no daily prices for this security and date range.")
            actions = quotes.corporate_actions(symbol)
            action_parts = _archive(settings, actions.captures)
            source_parts.extend(action_parts)
            if len(action_parts) != 1:
                raise FutuHistoryError("Futu corporate actions must bind to one complete response.")
            # A long collection must not span a symbol reassignment unnoticed.
            confirmed = quotes.basic_info(symbol)
            source_parts.extend(_archive(settings, confirmed.captures))
            if any(confirmed.info.get(key) != identity.get(key) for key in identity_fields):
                raise FutuHistoryError("Futu security identity changed during history collection.")
            retained = market.query("futu_raw_eod_daily", symbols=[symbol],
                                    start=begin.isoformat(), end=stop.isoformat(), limit=100000)
            if retained["total"] != len(retained["rows"]):
                raise FutuHistoryError("Retained Futu history exceeded the complete-read limit.")
            incoming_dates = {row["date"] for row in history.rows}
            if any(str(row["date"]) not in incoming_dates for row in retained["rows"]):
                raise FutuHistoryError("Futu omitted previously retained dates; no partial replacement was published.")
            missing_dates = [day.isoformat() for day in sessions if day >= begin and day.isoformat() not in incoming_dates]
            if missing_dates:
                raise FutuHistoryError(f"Futu is missing {len(missing_dates)} closed trading session(s): {', '.join(missing_dates)}. No price batch was published.")
            if any(row.get("stock_id") != identity["stock_id"] for row in retained["rows"]):
                raise FutuHistoryError("Retained Futu prices belong to a different security identity.")
            observed = max(part["observed_at"] for part in source_parts)
            rows = []
            for item in history.rows:
                row = dict(item)
                parts = [part for part in history_parts
                         if str(part["parameters"]["start"]) <= row["date"] <= str(part["parameters"]["end"])]
                if len(parts) != 1:
                    raise FutuHistoryError("Futu daily price does not bind to exactly one source response.")
                part = parts[0]
                row.update(
                    stock_id=identity["stock_id"],
                    source="futu:history-kline:unadjusted",
                    ohlc_adjustment="unadjusted", adjusted_close_adjustment="unavailable",
                    raw_ref=part["raw_ref"], raw_sha256=part["raw_sha256"],
                    collected_at=part["observed_at"], observed_at=observed,
                    available_at=observed,
                )
                rows.append(row)
            inconsistent_count = sum(row.get("ohlc_quality") == "inconsistent" for row in rows)
            directory = market.ingest(
                "futu_security_directory", [[dict(identity, symbol=symbol)]],
                source="futu:stock-basicinfo", observed_at=observed,
                raw_ref=source_parts[0]["raw_ref"],
                details={"symbol": symbol, "source_parts": [source_parts[0], source_parts[-1]]},
            )
            coverage = "provider_reported" if actions.rows else (
                "covered_no_actions" if identity["stock_type"] == "STOCK" else "unknown_empty_etf")
            action_part = action_parts[0]
            action_result = market.ingest(
                "futu_corporate_actions", [[{
                    "symbol": symbol, "stock_id": identity["stock_id"], "stock_type": identity["stock_type"],
                    "listing_date": listing, "coverage_start": listing, "coverage_end": action_coverage_end.isoformat(),
                    "coverage_status": coverage, "coverage_basis": "provider_reported_since_listing",
                    "events_json": json.dumps(actions.rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "event_count": len(actions.rows), "raw_ref": action_part["raw_ref"],
                    "raw_sha256": action_part["raw_sha256"], "collected_at": action_part["observed_at"],
                    "observed_at": observed, "available_at": observed,
                }]], source="futu:corporate-actions:rehab", observed_at=observed,
                raw_ref=action_part["raw_ref"], details={"symbol": symbol, "source_parts": action_parts,
                    "security_identity": identity, "coverage_basis": "provider_reported_since_listing"},
            )
            result = market.ingest(
                "futu_raw_eod_daily", [rows], source="futu:history-kline:unadjusted",
                observed_at=observed,
                details={
                    "symbol": symbol, "start": begin.isoformat(), "end": stop.isoformat(),
                    "source_parts": source_parts, "security_identity": identity,
                    "directory_batch_id": directory["batch_id"],
                    "corporate_actions_batch_id": action_result["batch_id"],
                    "ohlc_adjustment": "unadjusted", "total_return_available": False,
                    "ohlc_inconsistent_rows": inconsistent_count,
                },
            )
            return dict(result, status="partial" if inconsistent_count else "ready", symbol=symbol, rows=len(rows),
                        start=begin.isoformat(), end=stop.isoformat(),
                        first_price_date=min(incoming_dates), last_price_date=max(incoming_dates),
                        ohlc_inconsistent_rows=inconsistent_count,
                        directory_batch_id=directory["batch_id"], corporate_actions_batch_id=action_result["batch_id"],
                        corporate_actions_coverage=coverage, total_return_available=False)
    except FutuQuoteError as error:
        _archive(settings, getattr(error, "captures", ()))
        raise
    finally:
        if client is None and quotes is not None:
            quotes.close()
        if oauth is not None:
            oauth.close()
        if store is None:
            market.close()
