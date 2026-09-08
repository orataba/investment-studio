"""Project the shared BTC/USD spot series into registered instrument prices."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore

from studio_data.services.instrument_store import (
    get_instrument, update_refresh_status, upsert_price_history,
)


def _price(row, key):
    try:
        value = Decimal(str(row[key]))
    except (KeyError, TypeError, InvalidOperation) as error:
        raise ValueError(f"BTC/USD observation has no valid {key}") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"BTC/USD observation has no valid {key}")
    return value


def refresh_fmp_crypto_eod(instrument_id: str, *, full_history: bool = False,
                           store=None, now: datetime | None = None) -> dict:
    # The complete retained series is small; replaying it also propagates source
    # revisions outside the latest week. Unchanged facts are idempotent in store.
    del full_history
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    identifiers = {(r.get("identifier_type"), r.get("identifier_value"))
                   for r in instrument.get("identifiers", [])}
    if (instrument.get("instrument_type") != "crypto" or instrument.get("currency") != "USD"
            or ("provider_symbol", "fmp:BTCUSD") not in identifiers):
        raise ValueError("The maintained crypto series requires a BTC/USD spot instrument in USD")
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("BTC/USD projection clock must include a timezone")
    stop = (clock.astimezone(UTC).date() - timedelta(days=1)).isoformat()
    market = store or NumericStore(MarketSettings.from_environment())
    try:
        # Read one complete file view so concurrent source revisions cannot
        # change the observations between pages of the same projection.
        history = market.query("market_series_daily", symbols=["BTCUSD"], end=stop, limit=100000)
        rows = history["rows"]
        if len(rows) != history["total"]:
            raise ValueError(
                f"Shared BTC/USD returned an incomplete history: {len(rows)} of {history['total']} rows."
            )
    finally:
        if store is None:
            market.close()
    points, bars = [], []
    provider = "fmp:market_series_daily:BTCUSD"
    for row in sorted(rows, key=lambda row: str(row["date"])):
        day = str(row["date"])
        if day > stop:
            continue
        if row.get("series_id", row.get("symbol")) != "BTCUSD":
            raise ValueError("Shared BTC/USD query returned a different series")
        close = _price(row, "close")
        points.append({"metric_family": "price", "quote_basis": "close", "as_of_date": day,
                       "value": close, "currency": "USD", "provider": provider, "status": "complete"})
        if all(row.get(key) is not None for key in ("open", "high", "low")):
            # Provider volume units are not documented in the retained series;
            # do not relabel aggregate dollar turnover as BTC quantity or shares.
            bars.append({"as_of_date": day, **{key: _price(row, key) for key in ("open", "high", "low", "close")},
                         "currency": "USD", "provider": provider, "status": "complete"})
    changes = upsert_price_history(instrument_id=instrument_id,
        market_data_rows=points, price_bar_rows=bars) if points else (0, 0)
    if changes is None:
        raise RuntimeError(f"Registry crypto disappeared during refresh: {instrument_id}")
    changed, bar_changed = changes
    if points:
        latest = points[-1]['as_of_date']
        status = "blocked" if latest < stop else "refreshed" if changed or bar_changed else "no_new_data"
        message = (f"Projected {len(points)} retained BTC/USD spot observations; "
                   f"latest observation {latest}, requested cutoff {stop} (UTC). "
                   "Source lineage is in market_series_daily for BTCUSD and each observation date.")
        if latest < stop:
            message += " The most recently completed UTC day is missing; source coverage remains limited."
    else:
        status = "blocked"
        message = (f"Shared market_series_daily has no completed BTC/USD spot observations "
                   f"through {stop} (UTC); collect or synchronize the shared series before projection.")
    result = update_refresh_status(instrument_id=instrument_id,
        status=status,
        message=message,
        updated_by="fmp_crypto_sync", mode="api")
    if result is None:
        raise RuntimeError(f"Registry crypto instrument disappeared during refresh: {instrument_id}")
    return result
