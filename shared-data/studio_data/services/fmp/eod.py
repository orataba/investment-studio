from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from studio_market.calendars import latest_closed_market_session, closed_market_sessions
from investment_studio_instrument_core.listing_contract import session_calendar_name
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import closed_symbol_date
from studio_data.services.fmp.client import FmpApiError
from studio_data.services.fmp.eod_capture import FmpHistoryIncomplete, ensure_fmp_security_history
from studio_data.services.fmp.price_projection import publish_price_history
from studio_data.services.instrument_store import (
    get_instrument,
    update_refresh_status,
)


def _decimal_value(row: dict[str, object], *keys: str) -> Decimal:
    for key in keys:
        raw = row.get(key)
        if raw is not None and str(raw).strip():
            return Decimal(str(raw))
    raise ValueError(f"FMP EOD row is missing {keys[0]}.")


def _price_multiplier(instrument: dict[str, object]) -> Decimal:
    source_settings = instrument.get("source_settings")
    raw_multiplier = (
        source_settings.get("source_price_multiplier", "1")
        if isinstance(source_settings, dict)
        else "1"
    )
    try:
        multiplier = Decimal(str(raw_multiplier).strip())
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("FMP source_price_multiplier is invalid.") from error
    if not multiplier.is_finite() or multiplier <= 0:
        raise ValueError("FMP source_price_multiplier is invalid.")
    return multiplier


def refresh_fmp_eod(
    instrument_id: str,
    *,
    instrument_type: str,
    full_history: bool = False,
    store: NumericStore | None = None,
    acquire_history: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    # The shared collector can revise retained adjusted prices after a split or
    # dividend. Projection is a local replay, so include those older revisions
    # on every run instead of mixing adjustment bases across a seven-day edge.
    del full_history
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    actual_type = str(instrument.get("instrument_type") or "")
    if actual_type != instrument_type:
        raise ValueError(
            f"FMP EOD refresh expected {instrument_type}, not {actual_type or 'unknown'}."
        )
    provider_identifier = next(
        (
            str(item.get("identifier_value") or "")
            for item in list(instrument.get("identifiers") or [])
            if isinstance(item, dict)
            and item.get("identifier_type") == "provider_symbol"
            and str(item.get("identifier_value") or "").startswith("fmp:")
        ),
        "",
    )
    if not provider_identifier:
        raise ValueError(f"{instrument_type.upper()} has no FMP provider_symbol identifier.")
    def publish(**kwargs):
        try:
            return publish_price_history(instrument, **kwargs)
        except FmpApiError as error:
            update_refresh_status(instrument_id=instrument_id, status="failed", message=str(error),
                                  updated_by=f"fmp_{instrument_type}_sync", mode="api")
            raise
    symbol = provider_identifier.removeprefix("fmp:")
    clock = now or datetime.now(UTC)
    exchange_code = str(instrument.get("exchange_code") or "")
    if actual_type == "index":
        # Indices have no listing MIC. Their publication calendar belongs to
        # the source contract, independent of the provider symbol's spelling.
        calendar_name = str(dict(instrument.get("source_settings") or {}).get("market_calendar") or "").strip()
        if not calendar_name:
            raise FmpApiError("FMP index projection requires a verified source_settings.market_calendar.")
        try:
            end_date = latest_closed_market_session(calendar_name, clock)
        except ValueError as error:
            raise FmpApiError("FMP index projection has no verified source market calendar or closing time.") from error
        if acquire_history:
            raise FmpApiError("FMP index history must be acquired by its shared source collector.")
    else:
        end_date = closed_symbol_date(symbol, clock, exchange_code=exchange_code)
    # Equity/ETF registration and scheduled refresh share this acquisition
    # boundary. An explicitly supplied store always stays an offline replay.
    no_trade_evidence = []
    coverage_error = None
    acquisition_error = None
    if acquire_history and store is None:
        try:
            no_trade_evidence = ensure_fmp_security_history(
                instrument_id,
                symbol=symbol,
                exchange_code=exchange_code,
                currency=str(instrument.get("currency") or ""),
                now=clock,
            ) or []
        except FmpHistoryIncomplete as error:
            # Preserve real observations and notify downstream via their
            # canonical watermarks, without turning missing sessions into a
            # successful refresh. Transport/revision errors still abort.
            coverage_error = str(error)
            no_trade_evidence = error.no_trade_evidence
        except FmpApiError as error:
            # A failed acquisition can reflect a newly quarantined identity.
            # Read its exact public eligibility below before deciding whether
            # this is a transient collection error or canonical invalidation.
            acquisition_error = error
    start_date = date(1900, 1, 1)
    market = store or NumericStore(MarketSettings.from_environment())
    try:
        # One query fixes the source file view across this instrument's history.
        # Daily observations since 1900 fit below the shared query's row limit.
        history = market.query("raw_eod_daily", symbols=[symbol], start=start_date.isoformat(),
                               end=end_date.isoformat(), limit=100000)
        raw_rows = history["rows"]
        if len(raw_rows) != history["total"]:
            raise ValueError(
                f"Shared FMP EOD returned an incomplete history: {len(raw_rows)} of {history['total']} rows."
            )
    finally:
        if store is None:
            market.close()
    provenance = history.get("provenance") or {}
    generation = (provenance.get("price_series_generations") or {}).get(symbol)
    unavailable = next((item for item in provenance.get("price_series_unavailable", [])
                        if item.get("symbol") == symbol), None)
    bound_generation_missing = not generation and bool(dict(instrument.get("source_settings") or {}).get("fmp_price_series"))
    if unavailable or bound_generation_missing:
        publish(market_data_rows=[], price_bar_rows=[], generation=generation,
            unavailable_reason=f"FMP price series unavailable for {symbol}: "
            + (str(unavailable.get('reason') or 'identity not verified') if unavailable
               else 'registered identity generation is missing'))
    if acquisition_error:
        previous = dict(dict(instrument.get("source_settings") or {}).get("fmp_price_series") or {})
        if generation and any(generation.get(key) != previous.get(key)
                              for key in ("identity_source_id", "base_capture_id")):
            # A tail transport failure must not keep the prior security or
            # adjustment generation usable. Only an unchanged qualified
            # generation may survive an ordinary acquisition failure.
            publish(market_data_rows=[], price_bar_rows=[], generation=generation,
                    unavailable_reason="Current FMP identity generation is not yet projected; acquisition failed.")
        raise acquisition_error
    # Raw provider captures remain immutable evidence. Only actual completed
    # sessions of the registered listing can enter its canonical daily series.
    excluded_dates = []
    if raw_rows:
        calendar = calendar_name if actual_type == "index" else session_calendar_name(exchange_code)
        sessions = closed_market_sessions(calendar, min(date.fromisoformat(str(row["date"])) for row in raw_rows),
                                          end_date, clock)
        if sessions is None:
            raise FmpApiError("FMP history has no verified session calendar over its full date range.")
        session_dates = {day.isoformat() for day in sessions}
        excluded_dates = sorted({str(row["date"]) for row in raw_rows if str(row["date"]) not in session_dates})
        raw_rows = [row for row in raw_rows if str(row["date"]) in session_dates]
    currency = str(instrument.get("currency") or "").strip().upper()
    multiplier = _price_multiplier(instrument)
    price_bars: list[dict[str, object]] = []
    market_data: list[dict[str, object]] = []
    for row in sorted(raw_rows, key=lambda item: str(item.get("date") or "")):
        as_of_date = str(row.get("date") or "").strip()
        if not as_of_date:
            continue
        close = _decimal_value(row, "close", "adjClose") * multiplier
        adjusted = row.get("adjusted_close")
        adjustment_factor: Decimal | None = None
        if adjusted is not None:
            adjusted_close = Decimal(str(adjusted)) * multiplier
            adjustment_factor = adjusted_close / close
        price_bars.append(
            {
                "as_of_date": as_of_date,
                "open": _decimal_value(row, "open", "adjOpen") * multiplier,
                "high": _decimal_value(row, "high", "adjHigh") * multiplier,
                "low": _decimal_value(row, "low", "adjLow") * multiplier,
                "close": close,
                "volume": row.get("volume"),
                "volume_unit": "shares",
                "adjustment_factor": adjustment_factor,
                "currency": currency,
                "provider": (
                    "fmp:historical-price-eod:non-split-adjusted+dividend-adjustment-factor"
                    if adjustment_factor is not None
                    else "fmp:historical-price-eod:non-split-adjusted"
                ),
                "status": "complete",
            }
        )
        market_data.append(
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": close,
                "currency": currency,
                "provider": "fmp:historical-price-eod:non-split-adjusted",
                "status": "complete",
            }
        )
        if adjusted is not None:
            market_data.append(
                {
                    "metric_family": "price",
                    "quote_basis": "adjusted_close",
                    "as_of_date": as_of_date,
                    "value": adjusted_close,
                    "currency": currency,
                    "provider": "fmp:historical-price-eod:dividend-adjusted",
                    "status": "complete",
                }
            )

    updated_by = f"fmp_{instrument_type}_sync"
    if not price_bars and not generation and not excluded_dates:
        record = update_refresh_status(
            instrument_id=instrument_id,
            status="failed" if coverage_error else "no_new_data",
            message=coverage_error or f"Shared market data has no raw EOD rows for {symbol} in the requested interval.",
            updated_by=updated_by,
            mode="api",
        )
        if record is None:
            raise RuntimeError(
                f"Registry {instrument_type} disappeared during refresh: {instrument_id}"
            )
        record["projection_complete"] = False
        return record

    changes = publish(
        market_data_rows=market_data, price_bar_rows=price_bars, generation=generation)
    if changes is None:
        raise RuntimeError(f"Registry {instrument_type} disappeared during refresh: {instrument_id}")
    record = update_refresh_status(
        instrument_id=instrument_id,
        status="failed" if coverage_error else "refreshed" if any(changes) else "no_new_data",
        message=(f"Projected {len(price_bars)} shared FMP EOD rows for {symbol}."
                 + (f" Excluded {len(excluded_dates)} off-session observation(s); raw source evidence is retained."
                    if excluded_dates else "")
                 + (f" HKEX confirms {len(no_trade_evidence)} no-trade session(s) through "
                    f"{max(str(row['date']) for row in no_trade_evidence)}; no prices were synthesized for those sessions."
                    if no_trade_evidence else "")
                 + (f" {coverage_error}" if coverage_error else "")),
        updated_by=updated_by,
        mode="api",
    )
    if record is None:
        raise RuntimeError(
            f"Registry {instrument_type} disappeared during refresh: {instrument_id}"
        )
    # Invocation receipt only; do not add a persistent field to the instrument.
    # Empty unbound source data and an unchanged complete replay both have the
    # historical no_new_data refresh status, but differ for release acceptance.
    record["projection_complete"] = True
    return record
