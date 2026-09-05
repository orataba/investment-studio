from __future__ import annotations

from studio_data.services.equities.catalog import (
    get_catalog_equity,
    search_equity_catalog,
)
from studio_data.services.fmp import FmpClient, refresh_fmp_eod
from studio_data.services.fmp.exchanges import exchange_by_code
from studio_data.services.fmp.profile import listing_quote_contract
from studio_data.services.instrument_store import (
    create_instrument,
    ensure_secondary_identifier,
    find_instrument_by_identifier,
    get_instrument,
    get_price_bar_coverage,
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
        "instrument_type": "equity",
        "symbol": exchange_ticker,
        "catalog_provider": "fmp",
        "catalog_symbol": symbol,
        "name": str(catalog_record["company_name"]),
        "exchange_code": exchange.exchange_code,
        "exchange_label": exchange.label,
        "market": exchange.market,
        "currency": str(catalog_record["currency"]),
        "currency_verified": not exchange.requires_profile_currency,
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


def _source_settings(
    instrument_id: str,
    exchange_code: str,
    *,
    provider_currency: str | None = None,
    price_multiplier: object = 1,
) -> None:
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
        source_provider_currency=provider_currency,
        source_price_multiplier=price_multiplier,
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
    provider_currency: str | None = None
    price_multiplier: object = 1
    if exchange.requires_profile_currency:
        try:
            quote_contract = listing_quote_contract(
                client=fmp,
                symbol=symbol,
                exchange=exchange,
                instrument_type="equity",
            )
        except ValueError as error:
            raise EquityNotSupportedError(str(error)) from error
        currency = quote_contract.currency
        provider_currency = quote_contract.provider_currency
        price_multiplier = quote_contract.price_multiplier
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
    else:
        if str(existing.get("exchange_code") or "") != exchange.exchange_code:
            raise EquityNotSupportedError(
                "Registry exchange identity conflicts with the local FMP catalog."
            )
        if str(existing.get("currency") or "").strip().upper() != currency:
            raise EquityNotSupportedError(
                "Registry currency conflicts with the local FMP equity catalog."
            )

    instrument_id = str(existing["instrument_id"])
    ensured = ensure_secondary_identifier(
        instrument_id=instrument_id,
        identifier_type="provider_symbol",
        identifier_value=f"fmp:{symbol}",
    )
    if ensured is None:
        raise RuntimeError(f"Registry equity disappeared during materialization: {instrument_id}")
    _source_settings(
        instrument_id,
        exchange.exchange_code,
        provider_currency=provider_currency,
        price_multiplier=price_multiplier,
    )
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


def refresh_equity_eod(
    instrument_id: str,
    *,
    full_history: bool = False,
    client: FmpClient | None = None,
) -> dict[str, object]:
    return refresh_fmp_eod(
        instrument_id=instrument_id,
        instrument_type="equity",
        full_history=full_history,
        client=client,
    )
