from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from platform_app.services.equities.catalog import (
    get_catalog_equity,
    search_equity_catalog,
)
from platform_app.services.equities.exchange_catalog import (
    exchange_by_code,
)
from platform_app.services.equities.fmp_client import FmpClient
from platform_app.services.instrument_store import (
    create_instrument,
    find_instrument_by_identifier,
    get_instrument,
    get_price_bar_coverage,
    update_refresh_status,
    upsert_market_data_points,
    upsert_price_bars,
    upsert_source_settings,
)


class EquityNotSupportedError(ValueError):
    pass


def _existing_equity(
    *,
    fmp_symbol: str,
    exchange_ticker: str,
) -> dict[str, object] | None:
    candidates = [
        ("provider_symbol", f"fmp:{fmp_symbol}"),
        ("exchange_ticker", exchange_ticker),
    ]
    for identifier_type, identifier_value in candidates:
        existing = find_instrument_by_identifier(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
            include_inactive=True,
        )
        if existing is None:
            continue
        if str(existing.get("instrument_type") or "") != "equity":
            raise EquityNotSupportedError(
                f"Identifier {identifier_type}:{identifier_value} belongs to a non-equity instrument."
            )
        lifecycle = dict(existing.get("lifecycle_state") or {})
        if str(lifecycle.get("status") or "active") != "active":
            raise EquityNotSupportedError("The matching Registry equity is archived.")
        return existing
    return None


def _search_record(
    catalog_record: dict[str, object],
) -> dict[str, object]:
    symbol = str(catalog_record["fmp_symbol"])
    exchange_ticker = str(catalog_record["exchange_ticker"])
    exchange_code = str(catalog_record["exchange_code"])
    exchange = exchange_by_code(exchange_code)
    if exchange is None:
        raise EquityNotSupportedError(
            f"Local FMP catalog has unsupported exchange {exchange_code}."
        )
    existing = _existing_equity(
        fmp_symbol=symbol,
        exchange_ticker=exchange_ticker,
    )
    return {
        "symbol": exchange_ticker,
        "fmp_symbol": symbol,
        "name": str(catalog_record["company_name"]),
        "exchange_code": exchange.exchange_code,
        "exchange_label": exchange.label,
        "market": exchange.market,
        "currency": str(catalog_record["currency"]),
        "country": catalog_record.get("country"),
        "sector": catalog_record.get("sector"),
        "industry": catalog_record.get("industry"),
        "existing_instrument_id": existing.get("instrument_id") if existing else None,
    }


def search_equities(
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, object]]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Equity search query must not be blank.")
    return [
        _search_record(record)
        for record in search_equity_catalog(normalized_query, limit=limit)
    ]


def _source_settings(instrument_id: str, exchange_code: str) -> None:
    updated = upsert_source_settings(
        instrument_id=instrument_id,
        source_mode="api",
        source_email="",
        source_location="FMP API",
        source_api_profile="fmp",
        source_email_rules=[],
        expected_frequency="daily",
        market_calendar=exchange_code,
        release_lag_days=0,
        return_semantics="price_return",
    )
    if updated is None:
        raise RuntimeError(f"Registry equity disappeared during materialization: {instrument_id}")


def materialize_equity(
    fmp_symbol: str,
    *,
    refresh_eod: bool = True,
    client: FmpClient | None = None,
) -> dict[str, object]:
    fmp = client or FmpClient()
    catalog_record = get_catalog_equity(fmp_symbol)
    if catalog_record is None:
        raise EquityNotSupportedError(
            "Equity is not present in the local FMP stock catalog."
        )
    symbol = str(catalog_record["fmp_symbol"])
    exchange_ticker = str(catalog_record["exchange_ticker"])
    exchange_code = str(catalog_record["exchange_code"])
    exchange = exchange_by_code(exchange_code)
    if exchange is None:
        raise EquityNotSupportedError(
            f"Local FMP catalog has unsupported exchange {exchange_code}."
        )
    currency = str(catalog_record["currency"])
    existing = _existing_equity(
        fmp_symbol=symbol,
        exchange_ticker=exchange_ticker,
    )
    if existing is None:
        existing = create_instrument(
            instrument_name=str(catalog_record["company_name"]),
            instrument_type="equity",
            currency=currency,
            exchange_code=exchange.exchange_code,
            identifiers=[
                {
                    "identifier_type": "exchange_ticker",
                    "identifier_value": exchange_ticker,
                    "is_primary": True,
                },
                {
                    "identifier_type": "provider_symbol",
                    "identifier_value": f"fmp:{symbol}",
                    "is_primary": False,
                },
            ],
        )
    elif str(existing.get("exchange_code") or "") != exchange.exchange_code:
        raise EquityNotSupportedError(
            "Registry exchange identity conflicts with the local FMP catalog."
        )

    instrument_id = str(existing["instrument_id"])
    _source_settings(instrument_id, exchange.exchange_code)
    if refresh_eod:
        coverage = get_price_bar_coverage(instrument_id=instrument_id)
        refresh_equity_eod(
            instrument_id,
            full_history=not bool(coverage.get("latest_date")),
            client=fmp,
        )
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise RuntimeError(f"Materialized equity is missing from Registry: {instrument_id}")
    return instrument


def _decimal_value(row: dict[str, object], *keys: str) -> Decimal:
    for key in keys:
        raw = row.get(key)
        if raw is not None and str(raw).strip():
            return Decimal(str(raw))
    raise ValueError(f"FMP EOD row is missing {keys[0]}.")


def refresh_equity_eod(
    instrument_id: str,
    *,
    full_history: bool = False,
    client: FmpClient | None = None,
) -> dict[str, object]:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    if str(instrument.get("instrument_type") or "") != "equity":
        raise ValueError("FMP EOD refresh only supports equity instruments.")
    provider_identifier = next(
        (
            str(item.get("identifier_value") or "")
            for item in list(instrument.get("identifiers") or [])
            if isinstance(item, dict) and item.get("identifier_type") == "provider_symbol"
        ),
        "",
    )
    if not provider_identifier.startswith("fmp:"):
        raise ValueError("Equity has no FMP provider_symbol identifier.")
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
    price_bars: list[dict[str, object]] = []
    market_data: list[dict[str, object]] = []
    for row in sorted(raw_rows, key=lambda item: str(item.get("date") or "")):
        as_of_date = str(row.get("date") or "").strip()
        if not as_of_date:
            continue
        close = _decimal_value(row, "close", "adjClose")
        price_bars.append(
            {
                "as_of_date": as_of_date,
                "open": _decimal_value(row, "open", "adjOpen"),
                "high": _decimal_value(row, "high", "adjHigh"),
                "low": _decimal_value(row, "low", "adjLow"),
                "close": close,
                "volume": row.get("volume"),
                "volume_unit": "shares",
                "currency": currency,
                "provider": "fmp:historical-price-eod:non-split-adjusted",
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
        adjusted = adjusted_by_date.get(as_of_date)
        if adjusted is not None:
            market_data.append(
                {
                    "metric_family": "price",
                    "quote_basis": "adjusted_close",
                    "as_of_date": as_of_date,
                    "value": _decimal_value(adjusted, "adjClose", "close"),
                    "currency": currency,
                    "provider": "fmp:historical-price-eod:dividend-adjusted",
                    "status": "complete",
                }
            )

    if not price_bars:
        record = update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"FMP returned no EOD rows for {symbol}.",
            updated_by="fmp_equity_sync",
            mode="api",
        )
        if record is None:
            raise RuntimeError(f"Registry equity disappeared during refresh: {instrument_id}")
        return record

    upsert_price_bars(instrument_id=instrument_id, rows=price_bars)
    upsert_market_data_points(instrument_id=instrument_id, rows=market_data)
    record = update_refresh_status(
        instrument_id=instrument_id,
        status="refreshed",
        message=f"Stored {len(price_bars)} FMP EOD rows for {symbol}.",
        updated_by="fmp_equity_sync",
        mode="api",
    )
    if record is None:
        raise RuntimeError(f"Registry equity disappeared during refresh: {instrument_id}")
    return record
