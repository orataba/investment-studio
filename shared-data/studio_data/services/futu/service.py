"""Register an explicitly selected security from the retained Futu catalog."""
from __future__ import annotations

from datetime import date
from sqlalchemy.exc import IntegrityError

from studio_data.services.instrument_store import (
    create_instrument, find_instrument_by_identifier, get_instrument,
)
from .eod import CLOSE_POLICY, FutuRefreshError, refresh_futu_eod, validate_futu_configuration


def materialize_futu_security(
    symbol: str, *, catalog_record: dict, refresh_eod: bool = True, store=None,
) -> dict:
    """Consume a trusted local catalog record; never take identity from UI input."""
    symbol = symbol.strip().upper()
    if catalog_record.get("symbol") != symbol:
        raise FutuRefreshError("The selected symbol does not match the retained Futu catalog identity.")
    stock_type = catalog_record.get("stock_type")
    if stock_type not in {"ETF", "STOCK"}:
        raise FutuRefreshError("Only ETF and stock listings can be registered from Futu.")
    instrument_type = "etf" if stock_type == "ETF" else "equity"
    currency = str(catalog_record.get("currency") or "").strip().upper()
    if catalog_record.get("currency_verified") is not True or not currency:
        raise FutuRefreshError("The Futu listing's trading currency has not been verified; registration is unavailable.")
    exchange = str(catalog_record.get("exchange_code") or "").strip().upper()
    name = str(catalog_record.get("name") or "").strip()
    if not name or type(catalog_record.get("stock_id")) is not int or catalog_record["stock_id"] <= 0:
        raise FutuRefreshError("The retained Futu catalog lacks a confirmed name or stock identity.")
    try:
        listing_date = date.fromisoformat(str(catalog_record["listing_date"]))
        ticker = f"{int(symbol[3:]):04d}.HK" if symbol.startswith("HK.") else symbol.removeprefix("US.")
    except (KeyError, TypeError, ValueError) as error:
        raise FutuRefreshError("The Futu listing date or listing symbol is invalid.") from error
    identifiers = [
        {"identifier_type": "exchange_ticker", "identifier_value": ticker, "is_primary": True},
        {"identifier_type": "provider_symbol", "identifier_value": f"futu:{symbol}", "is_primary": False},
    ]
    source_settings = {
        "source_mode": "api", "source_api_profile": "futu", "source_location": "Futu quote API",
        "expected_frequency": "daily", "market_calendar": exchange, "release_lag_days": 0,
        "return_semantics": "price_return", "source_provider_currency": currency,
        "source_provider_security_id": str(catalog_record["stock_id"]),
        "source_price_multiplier": "1", "history_start_date": listing_date.isoformat(),
    }
    existing = None
    for identifier in reversed(identifiers):
        match = find_instrument_by_identifier(identifier_type=identifier["identifier_type"],
            identifier_value=identifier["identifier_value"], include_inactive=True)
        if match is not None:
            if existing is not None and existing["instrument_id"] != match["instrument_id"]:
                raise FutuRefreshError("The Futu provider and exchange identities resolve to different instruments.")
            existing = match
    if existing is not None:
        if (existing.get("instrument_type"), existing.get("currency"), existing.get("exchange_code")) != (instrument_type, currency, exchange):
            raise FutuRefreshError("The selected Futu listing conflicts with the registered type, currency or exchange.")
        previous_source = dict(existing.get("source_settings") or {})
        if str(previous_source.get("source_api_profile") or "").lower() != "futu":
            raise FutuRefreshError("The registered source requires a separate reviewed source migration; selecting a catalog listing cannot change it.")
        if previous_source.get("source_provider_security_id") != source_settings["source_provider_security_id"]:
            raise FutuRefreshError("The selected catalog stock_id differs from the registered Futu identity; a separate reviewed migration is required.")
        # Re-selecting an existing listing is read-only registration. Its source,
        # identity and history boundary were committed together when created;
        # do not overwrite a subsequent configuration in separate transactions.
        validate_futu_configuration(existing)
    else:
        # Validate the complete listing contract before creating any registry row.
        from .eod import _configuration

        _configuration({"instrument_type": instrument_type, "currency": currency,
                        "exchange_code": exchange, "identifiers": identifiers, "source_settings": source_settings})
        try:
            existing = create_instrument(instrument_name=name, instrument_type=instrument_type,
                currency=currency, exchange_code=exchange, identifiers=identifiers,
                quote_selection_policy=CLOSE_POLICY, source_settings=source_settings)
        except (ValueError, IntegrityError):
            # Re-resolve a simultaneous explicit selection, preserving identity checks.
            resolved = find_instrument_by_identifier(identifier_type="provider_symbol",
                identifier_value=f"futu:{symbol}", include_inactive=True)
            if resolved is None:
                raise
            return materialize_futu_security(symbol, catalog_record=catalog_record,
                                            refresh_eod=refresh_eod, store=store)
    instrument_id = str(existing["instrument_id"])
    if refresh_eod:
        refresh_futu_eod(instrument_id, full_history=True, store=store)
    result = get_instrument(instrument_id)
    if result is None:
        raise FutuRefreshError("Registered Futu instrument disappeared during registration.")
    return result
