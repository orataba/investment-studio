"""Project independent raw prices and evidence-backed cash-reinvestment returns."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

import exchange_calendars
from sqlalchemy import select

from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData, InstrumentPriceBar
from studio_market.config import MarketSettings
from studio_market.calendars import closed_market_sessions, confirmed_market_closures
from studio_market.futu.collect import FutuHistoryError
from studio_market.numeric import NumericStore
from studio_market.numeric.schema import batches
from studio_market.numeric.store import instant
from studio_market.futu.returns import ACTION_SOURCE, PROVIDER as RETURN_PROVIDER, derive_returns
from studio_data.db.session import get_session_factory
from studio_data.services.instrument_store import get_instrument, get_price_bars, update_refresh_status

PROVIDER = "futu:history-kline:unadjusted"
CLOSE_POLICY = {role: ["adjusted_close"] if role == "total_return" else ["close"]
                for role in ("trading", "valuation", "total_return", "chart", "reference")}
US_EXCHANGES = {"XNAS", "XNYS", "XASE", "ARCX", "BATS"}


class FutuRefreshError(ValueError):
    """A source or identity contract prevents publishing Futu prices."""


def _configuration(instrument: dict) -> tuple[str, date, str, int]:
    source = dict(instrument.get("source_settings") or {})
    if source.get("source_mode") != "api" or str(source.get("source_api_profile") or "").lower() != "futu":
        raise FutuRefreshError("Futu refresh requires the instrument's explicitly configured Futu source.")
    if instrument.get("instrument_type") not in {"etf", "equity"}:
        raise FutuRefreshError("Futu daily prices support registered ETFs and equities only.")
    if dict(instrument.get("lifecycle_state") or {}).get("status", "active") != "active":
        raise FutuRefreshError("Futu refresh requires an active registered instrument.")
    symbols = {str(row.get("identifier_value") or "")[5:] for row in instrument.get("identifiers", [])
               if row.get("identifier_type") == "provider_symbol"
               and str(row.get("identifier_value") or "").startswith("futu:")}
    if len(symbols) != 1:
        raise FutuRefreshError("Futu refresh requires exactly one futu: provider_symbol identity.")
    symbol = symbols.pop()
    if not re.fullmatch(r"HK\.[0-9]{5}|US\.[A-Z0-9][A-Z0-9.-]*", symbol):
        raise FutuRefreshError("Futu provider_symbol must use the exact HK.00000 or US.SYMBOL format.")
    provider_security_id = str(source.get("source_provider_security_id") or "")
    if not re.fullmatch(r"[1-9][0-9]*", provider_security_id):
        raise FutuRefreshError("Futu requires the catalog's stock_id bound as source_provider_security_id.")
    exchange = str(instrument.get("exchange_code") or "")
    if (symbol.startswith("HK.") and exchange != "XHKG") or (symbol.startswith("US.") and exchange not in US_EXCHANGES):
        raise FutuRefreshError("Futu symbol market conflicts with the registered listing exchange.")
    if source.get("market_calendar") != exchange:
        raise FutuRefreshError("Futu prices require the registered listing's explicit market_calendar.")
    currency = str(instrument.get("currency") or "").upper()
    if str(source.get("source_provider_currency") or "").upper() != currency or not currency:
        raise FutuRefreshError("Futu requires an explicitly verified source_provider_currency matching the instrument currency.")
    try:
        multiplier = Decimal(str(source.get("source_price_multiplier", 1)))
    except InvalidOperation as error:
        raise FutuRefreshError("Futu source_price_multiplier is invalid.") from error
    if not multiplier.is_finite() or multiplier != 1:
        raise FutuRefreshError("Futu raw prices require source_price_multiplier=1; other quote units need a separate verified contract.")
    if source.get("return_semantics") != "price_return":
        raise FutuRefreshError("Futu unadjusted daily prices require return_semantics=price_return.")
    try:
        start = date.fromisoformat(str(source["history_start_date"]))
    except (KeyError, TypeError, ValueError) as error:
        raise FutuRefreshError("Set an explicit ISO history_start_date before requesting Futu history.") from error
    return symbol, start, currency, int(provider_security_id)


def _owned_history(instrument: dict, bars: list[dict]) -> None:
    for point in instrument.get("market_data", []):
        if (point.get("provider"), point.get("quote_basis")) not in {
                (PROVIDER, "close"), (RETURN_PROVIDER, "adjusted_close")}:
            raise FutuRefreshError("Existing non-Futu or adjusted price history requires a separate reviewed source migration.")
    if any(bar.get("provider") != PROVIDER for bar in bars):
        raise FutuRefreshError("Existing non-Futu or adjusted OHLC history requires a separate reviewed source migration.")


def validate_futu_configuration(instrument: dict) -> tuple[str, date, str, int]:
    contract = _configuration(instrument)
    _owned_history(instrument, get_price_bars(instrument_id=str(instrument["instrument_id"])))
    return contract


def _publish(instrument: dict, points: list[dict], bars: list[dict], *, omitted_bar_dates: set[date], metadata: dict) -> tuple[int, int]:
    """Recheck ownership under the same row lock as the atomic publication."""
    instrument_id = str(instrument["instrument_id"])
    with get_session_factory()() as session:
        target = session.scalar(select(Instrument).where(Instrument.instrument_id == instrument_id).with_for_update())
        if target is None:
            raise FutuRefreshError("Registered Futu instrument disappeared during refresh.")
        current = shared_store._instrument_to_store_dict(target)
        if (_configuration(current) != _configuration(instrument)
                or current["instrument_type"] != instrument["instrument_type"]
                or shared_store._normalized_source_settings(current) != shared_store._normalized_source_settings(instrument)):
            raise FutuRefreshError("Instrument source configuration changed during Futu acquisition; refresh again.")
        existing_bars = [{"provider": row.provider, "adjustment_factor": row.adjustment_factor, "as_of_date": row.as_of_date}
                         for row in session.scalars(select(InstrumentPriceBar).where(InstrumentPriceBar.instrument_id == instrument_id))]
        _owned_history(current, existing_bars)
        conflicting_dates = omitted_bar_dates.intersection(row["as_of_date"] for row in existing_bars)
        if conflicting_dates:
            raise FutuRefreshError(
                f"Inconsistent Futu OHLC conflicts with {len(conflicting_dates)} previously published bars "
                f"from {min(conflicting_dates)} to {max(conflicting_dates)}; the entire projection was aborted for review.")
        if target.quote_selection_policy_json != CLOSE_POLICY:
            target.quote_selection_policy_json = {role: list(bases) for role, bases in CLOSE_POLICY.items()}
            target.calculation_inputs_updated_at = shared_store._next_calculation_input_watermark(session)
        source_settings = dict(target.source_settings_json or {})
        source_settings.update(metadata)
        if source_settings != target.source_settings_json:
            target.source_settings_json = source_settings
            target.calculation_inputs_updated_at = shared_store._next_calculation_input_watermark(session)
        # Rebuilds can withdraw formerly trusted returns. Delete only our own
        # derived series, preserving raw facts; update/delete shares this lock,
        # transaction and watermark with all factor and coverage changes.
        desired_dates = {point["as_of_date"] for point in points if point["quote_basis"] == "adjusted_close"}
        stale = list(session.scalars(select(InstrumentMarketData).where(
            InstrumentMarketData.instrument_id == instrument_id,
            InstrumentMarketData.quote_basis == "adjusted_close",
            InstrumentMarketData.provider == RETURN_PROVIDER)))
        withdrawn = 0
        for point in stale:
            if point.as_of_date not in desired_dates:
                session.delete(point)
                withdrawn += 1
        desired_bar_dates = {bar["as_of_date"] for bar in bars}
        for bar in session.scalars(select(InstrumentPriceBar).where(
                InstrumentPriceBar.instrument_id == instrument_id)):
            if bar.as_of_date not in desired_bar_dates and bar.adjustment_factor is not None:
                bar.adjustment_factor = None
                withdrawn += 1
        if withdrawn:
            target.market_data_updated_at = shared_store._next_market_data_watermark(session)
        point_count = shared_store._upsert_market_data_points(session, instrument_id=instrument_id, rows=points)
        bar_count = shared_store._upsert_price_bars(session, instrument_id=instrument_id, rows=bars)
        session.commit()
    return int(point_count or 0) + withdrawn, int(bar_count or 0)


def _expected_session(instrument: dict, now: datetime) -> date:
    is_hk = instrument["exchange_code"] == "XHKG"
    local_day = now.astimezone(ZoneInfo("Asia/Hong_Kong" if is_hk else "America/New_York")).date()
    sessions = closed_market_sessions("XHKG" if is_hk else "XNYS", local_day - timedelta(days=370), local_day, now)
    if not sessions:
        raise FutuRefreshError("The listing's completed market sessions could not be verified.")
    return sessions[-1]


def _first_requested_session(instrument: dict, start: date, end: date) -> date:
    name = "XHKG" if instrument["exchange_code"] == "XHKG" else "XNYS"
    calendar = exchange_calendars.get_calendar(name, start=str(start - timedelta(days=7)),
                                               end=str(end + timedelta(days=7)))
    session = calendar.date_to_session(start.isoformat(), direction="next")
    while session.date() in confirmed_market_closures(name):
        session = calendar.next_session(session)
    return session.date()


def _captured_actions(market, symbol: str, rows: list[dict]) -> dict | None:
    """Read the action snapshot bound by a successfully published price capture.

    A collector can fail after archiving actions but before publishing prices.
    Such an unbound snapshot must not revise already retained total returns.
    """
    if not rows or any(not row.get("batch_id") or not row.get("observed_at") for row in rows):
        return None
    newest = max(rows, key=lambda row: (instant(row["observed_at"]), row["batch_id"]))
    with market.engine.connect() as connection:
        details = connection.scalar(select(batches.c.details).where(
            batches.c.id == newest["batch_id"], batches.c.dataset == "futu_raw_eod_daily",
            batches.c.source == PROVIDER, batches.c.status == "ready"))
    action_batch = (details or {}).get("corporate_actions_batch_id")
    if not isinstance(action_batch, str) or not action_batch:
        return None
    captured = market.query("futu_corporate_actions", symbols=[symbol], batch_id=action_batch, limit=2)
    snapshots = captured["rows"]
    if captured["total"] != 1 or len(snapshots) != 1:
        raise FutuRefreshError("The committed Futu price capture lacks its bound corporate-action snapshot.")
    return snapshots[0]


def refresh_futu_eod(instrument_id: str, *, full_history: bool = False, store=None,
                     now: datetime | None = None) -> dict:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise FutuRefreshError("Registered Futu instrument not found.")
    symbol, start, currency, stock_id = validate_futu_configuration(instrument)
    now = now or datetime.now(UTC)
    owned = store is None
    market = store
    try:
        expected = _expected_session(instrument, now)
        if start > expected:
            raise FutuRefreshError("Futu history_start_date is later than the latest completed market session.")
        if market is None:
            market = NumericStore(MarketSettings.from_environment())
        if owned:
            from studio_market.futu.collect import collect_history

            existing_dates = [date.fromisoformat(str(point["as_of_date"]))
                              for point in instrument.get("market_data", []) if point.get("quote_basis") == "close"]
            fetch_start = start if full_history or not existing_dates else max(start, max(existing_dates) - timedelta(days=7))
            collect_history(market.settings, symbol, fetch_start, expected, store=market, now=now)
        directory = market.latest("futu_security_directory", symbols=[symbol], limit=2)["rows"]
        if len(directory) != 1 or directory[0].get("symbol") != symbol:
            raise FutuRefreshError("Futu security identity is unavailable or ambiguous.")
        identity = directory[0]
        if type(identity.get("stock_id")) is not int or identity["stock_id"] <= 0:
            raise FutuRefreshError("Futu security identity lacks a stable stock_id.")
        if identity["stock_id"] != stock_id:
            raise FutuRefreshError("Futu stock_id differs from the registered catalog identity; no canonical prices were published.")
        if identity.get("stock_type") != {"etf": "ETF", "equity": "STOCK"}[str(instrument["instrument_type"])]:
            raise FutuRefreshError("Futu security type conflicts with the registered instrument type.")
        listing_date = date.fromisoformat(str(identity["listing_date"]))
        history = market.query("futu_raw_eod_daily", symbols=[symbol], start=max(start, listing_date).isoformat(),
                               end=expected.isoformat(), limit=100000)
        rows = history["rows"]
        if len(rows) != history["total"]:
            raise FutuRefreshError("Futu retained history is truncated; no canonical prices were published.")
        points, bars, seen, omitted_bar_dates = [], [], set(), set()
        for row in sorted(rows, key=lambda item: str(item.get("date"))):
            day = date.fromisoformat(str(row["date"]))
            if (row.get("symbol") != symbol or row.get("stock_id") != identity.get("stock_id")
                    or row.get("ohlc_adjustment") != "unadjusted" or row.get("adjusted_close") is not None
                    or row.get("adjusted_close_adjustment") != "unavailable"
                    or (row.get("volume") is not None and row.get("volume_unit") != "shares")
                    or row.get("source") != PROVIDER or day in seen
                    or not max(start, listing_date) <= day <= expected):
                raise FutuRefreshError("Futu retained history violates its raw-price, identity or date contract.")
            seen.add(day)
            prices = {key: Decimal(str(row[key])) for key in ("open", "high", "low", "close")}
            if any(not value.is_finite() or value <= 0 for value in prices.values()):
                raise FutuRefreshError("Futu retained history contains an invalid price.")
            if prices["low"] > prices["high"]:
                raise FutuRefreshError("Futu retained history has low above high; no canonical prices were published.")
            if not prices["low"] <= prices["close"] <= prices["high"]:
                raise FutuRefreshError("Futu retained close conflicts with its high-low range; no canonical prices were published.")
            points.append({"metric_family": "price", "quote_basis": "close", "as_of_date": day,
                           "value": prices["close"], "currency": currency, "provider": PROVIDER, "status": "complete"})
            # Retain the observed close without inventing a repaired candle.
            # Recompute quality here rather than trusting a provider flag.
            if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
                omitted_bar_dates.add(day)
                continue
            bars.append({"as_of_date": day, **prices, "volume": row.get("volume"),
                         "volume_unit": row.get("volume_unit") if row.get("volume") is not None else None,
                         "adjustment_factor": None, "currency": currency, "provider": PROVIDER, "status": "complete"})
        snapshot = _captured_actions(market, symbol, rows)
        if snapshot and (snapshot.get("symbol") != symbol or snapshot.get("stock_id") != stock_id
                         or snapshot.get("stock_type") != identity["stock_type"]
                         or snapshot.get("listing_date") != identity["listing_date"]
                         or snapshot.get("source") != ACTION_SOURCE):
            raise FutuRefreshError("Futu corporate-action evidence conflicts with the registered security identity.")
        derived = derive_returns(rows, snapshot, currency=currency,
                                 calendar_name="XHKG" if instrument["exchange_code"] == "XHKG" else "XNYS")
        factors = {}
        for row in derived.rows:
            if row["adjusted_close"] is not None:
                day = date.fromisoformat(row["date"])
                points.append({"metric_family": "price", "quote_basis": "adjusted_close", "as_of_date": day,
                               "value": Decimal(row["adjusted_close"]), "currency": currency,
                               "provider": RETURN_PROVIDER, "status": "complete"})
                factors[day] = Decimal(row["adjustment_factor"])
        for bar in bars:
            bar["adjustment_factor"] = factors.get(bar["as_of_date"])
        if derived.rows:
            market.ingest("futu_return_daily", [derived.rows], source=RETURN_PROVIDER,
                          details={"symbol": symbol, **derived.metadata})
        if not rows:
            changes, status = (0, 0), "partial"
            message = f"Futu has no retained raw daily prices for {symbol} from {max(start, listing_date)} through {expected}."
        else:
            latest = max(seen)
            first_expected = _first_requested_session(instrument, max(start, listing_date), expected)
            leading_missing = min(seen) > first_expected
            changes = _publish(instrument, points, bars, omitted_bar_dates=omitted_bar_dates, metadata=derived.metadata)
            status = "partial" if leading_missing or latest < expected or omitted_bar_dates or derived.metadata["total_return_unavailable_intervals"] else "refreshed" if any(changes) else "no_new_data"
            message = (f"Projected {len(rows)} Futu raw closing prices and {len(bars)} verified OHLCV bars for {symbol}; actual retained range {min(seen)} to {latest}. "
                       f"Derived {len(factors)} cash-reinvestment adjusted closes across {len(derived.metadata['total_return_segments'])} covered segments.")
            if derived.metadata["total_return_unavailable_intervals"]:
                reasons = sorted({interval["reason"] for interval in derived.metadata["total_return_unavailable_intervals"]})
                message += f" Total-return links are unavailable for {len(derived.metadata['total_return_unavailable_intervals'])} intervals: {', '.join(reasons)}."
            if omitted_bar_dates:
                message += (f" Omitted {len(omitted_bar_dates)} OHLCV bars with opens outside their high-low ranges "
                            f"from {min(omitted_bar_dates)} to {max(omitted_bar_dates)}; only closes verified within those ranges are retained.")
            if latest < expected:
                message += f" Latest completed market session {expected} has no confirmed price."
            if leading_missing:
                message += f" Requested history starts on market session {first_expected}, before the first retained price {min(seen)}."
        record = update_refresh_status(instrument_id=instrument_id, status=status, message=message,
                                       updated_by="futu_price_sync", mode="api")
        if record is None:
            raise FutuRefreshError("Registered Futu instrument disappeared during refresh.")
        return record
    except Exception as error:
        # The transport retains diagnostics separately. Do not expose credentials
        # or arbitrary upstream response bodies through the instrument UI.
        failure = error if isinstance(error, FutuRefreshError) else FutuRefreshError(
            str(error) if isinstance(error, FutuHistoryError) else
            f"Futu daily-price refresh failed ({type(error).__name__}); inspect the quote-source status.")
        update_refresh_status(instrument_id=instrument_id, status="failed", message=str(failure),
                              updated_by="futu_price_sync", mode="api")
        if failure is error:
            raise
        raise failure from error
    finally:
        if owned and market is not None:
            market.close()
