from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from studio_data.services.fmp.client import FmpClient
from studio_data.services.instrument_store import (
    get_instrument,
    get_price_bar_coverage,
    update_refresh_status,
    upsert_market_data_points,
    upsert_price_bars,
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
    client: FmpClient | None = None,
) -> dict[str, object]:
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
    symbol = provider_identifier.removeprefix("fmp:")
    coverage = get_price_bar_coverage(instrument_id=instrument_id)
    latest_date = str(coverage.get("latest_date") or "")
    start_date = date(1900, 1, 1)
    if not full_history and latest_date:
        start_date = date.fromisoformat(latest_date) - timedelta(days=7)
    end_date = date.today()
    fmp = client or FmpClient()
    raw_rows = fmp.historical_eod(
        symbol,
        adjusted=False,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    adjusted_rows = fmp.historical_eod(
        symbol,
        adjusted=True,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    adjusted_by_date = {
        str(row.get("date") or ""): row
        for row in adjusted_rows
        if str(row.get("date") or "")
    }
    currency = str(instrument.get("currency") or "").strip().upper()
    multiplier = _price_multiplier(instrument)
    price_bars: list[dict[str, object]] = []
    market_data: list[dict[str, object]] = []
    for row in sorted(raw_rows, key=lambda item: str(item.get("date") or "")):
        as_of_date = str(row.get("date") or "").strip()
        if not as_of_date:
            continue
        close = _decimal_value(row, "close", "adjClose") * multiplier
        adjusted = adjusted_by_date.get(as_of_date)
        adjustment_factor: Decimal | None = None
        if adjusted is not None:
            adjusted_close = _decimal_value(adjusted, "adjClose", "close") * multiplier
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
    if not price_bars:
        record = update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"FMP returned no EOD rows for {symbol}.",
            updated_by=updated_by,
            mode="api",
        )
        if record is None:
            raise RuntimeError(
                f"Registry {instrument_type} disappeared during refresh: {instrument_id}"
            )
        return record

    upsert_price_bars(instrument_id=instrument_id, rows=price_bars)
    upsert_market_data_points(instrument_id=instrument_id, rows=market_data)
    record = update_refresh_status(
        instrument_id=instrument_id,
        status="refreshed",
        message=f"Stored {len(price_bars)} FMP EOD rows for {symbol}.",
        updated_by=updated_by,
        mode="api",
    )
    if record is None:
        raise RuntimeError(
            f"Registry {instrument_type} disappeared during refresh: {instrument_id}"
        )
    return record
