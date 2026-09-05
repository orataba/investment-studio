from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from investment_studio_instrument_core.fx_contract import fx_instrument_identity

from studio_data.services.fmp.client import FmpClient
from studio_data.services.instrument_store import (
    get_instrument,
    update_refresh_status,
    upsert_market_data_points,
)


def _positive_close(row: dict[str, object]) -> Decimal:
    raw_value = row.get("close")
    try:
        value = Decimal(str(raw_value).strip())
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("FMP FX row is missing a valid close.") from error
    if not value.is_finite() or value <= 0:
        raise ValueError("FMP FX row is missing a valid close.")
    return value


def refresh_fmp_fx_eod(
    instrument_id: str,
    *,
    full_history: bool = False,
    client: FmpClient | None = None,
) -> dict[str, object]:
    identity = fx_instrument_identity(instrument_id)
    if identity is None:
        raise ValueError(f"Registry instrument is not a maintained FX pair: {instrument_id}")
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    if str(instrument.get("instrument_type") or "") != "fx":
        raise ValueError(f"Maintained FX identity is not typed as FX: {instrument_id}")

    latest_date: date | None = None
    for point in instrument.get("market_data", []):
        if not isinstance(point, dict):
            continue
        if point.get("metric_family") != "fx" or point.get("quote_basis") != "spot":
            continue
        try:
            point_date = date.fromisoformat(str(point.get("as_of_date") or ""))
        except ValueError:
            continue
        latest_date = max(latest_date, point_date) if latest_date else point_date

    start_date = date(1900, 1, 1)
    if not full_history and latest_date is not None:
        start_date = latest_date - timedelta(days=7)
    end_date = date.today()
    symbol = identity.base_currency + identity.quote_currency
    fmp = client or FmpClient()
    rows = fmp.historical_fx(
        symbol,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    points = []
    for row in sorted(rows, key=lambda item: str(item.get("date") or "")):
        as_of_date = str(row.get("date") or "").strip()
        if not as_of_date:
            continue
        points.append(
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": as_of_date,
                "value": _positive_close(row),
                "currency": identity.quote_currency,
                "provider": "fmp:historical-price-eod:full",
                "status": "complete",
            }
        )

    if not points:
        record = update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"FMP returned no FX EOD rows for {symbol}.",
            updated_by="fmp_fx_sync",
            mode="api",
        )
    else:
        upsert_market_data_points(instrument_id=instrument_id, rows=points)
        record = update_refresh_status(
            instrument_id=instrument_id,
            status="refreshed",
            message=f"Stored {len(points)} FMP FX EOD rows for {symbol}.",
            updated_by="fmp_fx_sync",
            mode="api",
        )
    if record is None:
        raise RuntimeError(f"Registry FX instrument disappeared during refresh: {instrument_id}")
    return record
