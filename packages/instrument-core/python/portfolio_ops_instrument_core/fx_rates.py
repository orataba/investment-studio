from __future__ import annotations

from datetime import date
from decimal import Decimal

from portfolio_ops_instrument_core.instrument_store import (
    SessionFactory,
    get_instrument,
    upsert_market_data,
)


SUPPORTED_FX_CURRENCIES: tuple[str, ...] = ("USD", "HKD", "CNY")
PIVOT_CURRENCY = "USD"
MAINTAINED_FX_INSTRUMENTS: dict[tuple[str, str], str] = {
    ("USD", "HKD"): "fx-usd-hkd",
    ("USD", "CNY"): "fx-usd-cny",
}


def supported_fx_currencies() -> list[str]:
    return list(SUPPORTED_FX_CURRENCIES)


def maintained_fx_pairs() -> list[str]:
    return [f"{base}/{quote}" for base, quote in MAINTAINED_FX_INSTRUMENTS]


def _latest_spot_point(session_factory: SessionFactory, instrument_id: str) -> dict[str, object] | None:
    instrument = get_instrument(session_factory, instrument_id)
    if instrument is None:
        return None

    market_data = instrument.get("market_data", [])
    if not isinstance(market_data, list):
        return None

    points = [
        item
        for item in market_data
        if str(item.get("metric_family") or "") == "fx"
        and str(item.get("quote_basis") or "") == "spot"
    ]
    if not points:
        return None

    return max(points, key=lambda item: str(item.get("as_of_date") or ""))


def _direct_rate_record(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
) -> dict[str, object] | None:
    instrument_id = MAINTAINED_FX_INSTRUMENTS.get((base_currency, quote_currency))
    if instrument_id is None:
        return None

    point = _latest_spot_point(session_factory, instrument_id)
    if point is None:
        return None

    return {
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "rate": Decimal(str(point.get("value") or "0")),
        "as_of_date": date.fromisoformat(str(point.get("as_of_date") or date.today().isoformat())),
        "source_kind": "direct",
        "instrument_id": instrument_id,
        "source_instrument_ids": [instrument_id],
        "provider": point.get("provider"),
        "status": str(point.get("status") or "complete"),
    }


def _inverse_rate_record(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
) -> dict[str, object] | None:
    direct_record = _direct_rate_record(session_factory, quote_currency, base_currency)
    if direct_record is None:
        return None

    rate = Decimal("1") / Decimal(str(direct_record["rate"]))
    return {
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "rate": rate,
        "as_of_date": direct_record["as_of_date"],
        "source_kind": "inverse",
        "instrument_id": direct_record["instrument_id"],
        "source_instrument_ids": list(direct_record["source_instrument_ids"]),
        "provider": direct_record["provider"],
        "status": direct_record["status"],
    }


def _cross_rate_record(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
) -> dict[str, object] | None:
    if base_currency == PIVOT_CURRENCY or quote_currency == PIVOT_CURRENCY:
        return None

    usd_to_base = _direct_rate_record(session_factory, PIVOT_CURRENCY, base_currency)
    usd_to_quote = _direct_rate_record(session_factory, PIVOT_CURRENCY, quote_currency)
    if usd_to_base is None or usd_to_quote is None:
        return None

    rate = Decimal(str(usd_to_quote["rate"])) / Decimal(str(usd_to_base["rate"]))
    status = "partial" if "partial" in {usd_to_base["status"], usd_to_quote["status"]} else "complete"
    as_of_date = min(usd_to_base["as_of_date"], usd_to_quote["as_of_date"])
    provider_parts = [part for part in [usd_to_base.get("provider"), usd_to_quote.get("provider")] if part]
    provider = " + ".join(dict.fromkeys(provider_parts)) or None
    source_instrument_ids = list(dict.fromkeys([*usd_to_base["source_instrument_ids"], *usd_to_quote["source_instrument_ids"]]))
    return {
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "rate": rate,
        "as_of_date": as_of_date,
        "source_kind": "cross",
        "instrument_id": None,
        "source_instrument_ids": source_instrument_ids,
        "provider": provider,
        "status": status,
    }


def get_fx_rate(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
) -> dict[str, object] | None:
    normalized_base = base_currency.strip().upper()
    normalized_quote = quote_currency.strip().upper()
    if normalized_base not in SUPPORTED_FX_CURRENCIES or normalized_quote not in SUPPORTED_FX_CURRENCIES:
        return None
    if normalized_base == normalized_quote:
        return {
            "base_currency": normalized_base,
            "quote_currency": normalized_quote,
            "rate": Decimal("1"),
            "as_of_date": date.today(),
            "source_kind": "direct",
            "instrument_id": None,
            "source_instrument_ids": [],
            "provider": None,
            "status": "complete",
        }

    direct_record = _direct_rate_record(session_factory, normalized_base, normalized_quote)
    if direct_record is not None:
        return direct_record

    inverse_record = _inverse_rate_record(session_factory, normalized_base, normalized_quote)
    if inverse_record is not None:
        return inverse_record

    return _cross_rate_record(session_factory, normalized_base, normalized_quote)


def list_fx_rates(session_factory: SessionFactory) -> list[dict[str, object]]:
    rates: list[dict[str, object]] = []
    for base_currency in SUPPORTED_FX_CURRENCIES:
        for quote_currency in SUPPORTED_FX_CURRENCIES:
            if base_currency == quote_currency:
                continue
            record = get_fx_rate(session_factory, base_currency, quote_currency)
            if record is not None:
                rates.append(record)
    rates.sort(key=lambda item: (str(item.get("base_currency") or ""), str(item.get("quote_currency") or "")))
    return rates


def get_fx_payload(session_factory: SessionFactory) -> dict[str, object]:
    return {
        "supported_currencies": supported_fx_currencies(),
        "maintained_pairs": maintained_fx_pairs(),
        "rates": list_fx_rates(session_factory),
    }


def upsert_fx_rate(
    session_factory: SessionFactory,
    *,
    base_currency: str,
    quote_currency: str,
    rate: Decimal,
    as_of_date: date,
    provider: str | None,
    status: str,
) -> dict[str, object]:
    normalized_base = base_currency.strip().upper()
    normalized_quote = quote_currency.strip().upper()
    instrument_id = MAINTAINED_FX_INSTRUMENTS.get((normalized_base, normalized_quote))
    if instrument_id is None:
        raise ValueError("Only USD/HKD and USD/CNY are maintained directly in this MVP.")

    record = upsert_market_data(
        session_factory,
        instrument_id=instrument_id,
        metric_family="fx",
        quote_basis="spot",
        as_of_date=as_of_date,
        value=str(rate),
        currency=normalized_quote,
        provider=provider,
        status=status,
    )
    if record is None:
        raise ValueError("FX instrument not found in shared instrument registry.")

    refreshed = _direct_rate_record(session_factory, normalized_base, normalized_quote)
    if refreshed is None:
        raise ValueError("Failed to refresh FX rate after update.")
    return refreshed
