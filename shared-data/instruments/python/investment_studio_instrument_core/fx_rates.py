from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData
from investment_studio_instrument_core.fx_contract import (
    FX_INSTRUMENT_IDENTITIES,
    PIVOT_CURRENCY,
    SUPPORTED_FX_CURRENCIES,
    fx_instrument_identity,
    fx_instrument_identity_for_pair,
    normalize_fx_currency,
    parse_positive_fx_rate,
    validate_fx_market_data_contract,
)
from investment_studio_instrument_core.instrument_store import (
    SessionFactory,
    get_instrument,
    upsert_market_data,
)


def supported_fx_currencies() -> list[str]:
    return list(SUPPORTED_FX_CURRENCIES)


def maintained_fx_pairs() -> list[str]:
    return [
        f"{identity.base_currency}/{identity.quote_currency}"
        for identity in FX_INSTRUMENT_IDENTITIES
    ]


def _positive_decimal(value: object) -> Decimal | None:
    try:
        return parse_positive_fx_rate(value)
    except ValueError:
        return None


def _latest_spot_point_from_instrument(
    instrument: dict[str, object],
    market_data: object,
) -> dict[str, object] | None:
    if not isinstance(market_data, list):
        return None

    raw_points = [
        item
        for item in market_data
        if isinstance(item, dict)
        if str(item.get("metric_family") or "") == "fx"
        and str(item.get("quote_basis") or "") == "spot"
    ]
    if not raw_points:
        return None

    points: list[dict[str, object]] = []
    for raw_point in raw_points:
        try:
            point_date = date.fromisoformat(str(raw_point.get("as_of_date") or ""))
            validated = validate_fx_market_data_contract(
                instrument_id=instrument.get("instrument_id"),
                instrument_type=instrument.get("instrument_type"),
                instrument_currency=instrument.get("currency"),
                metric_family=raw_point.get("metric_family"),
                quote_basis=raw_point.get("quote_basis"),
                point_currency=raw_point.get("currency"),
                value=raw_point.get("value"),
                status=raw_point.get("status"),
            )
        except ValueError:
            return None
        if validated is None:
            return None
        point = dict(raw_point)
        point.update(
            {
                "as_of_date": point_date,
                "value": validated.rate,
                "currency": validated.identity.quote_currency,
                "status": validated.status,
            }
        )
        points.append(point)

    latest_date = max(point["as_of_date"] for point in points)
    latest_points = [point for point in points if point["as_of_date"] == latest_date]
    if len(latest_points) != 1:
        return None
    return latest_points[0]


def _latest_spot_point(session_factory: SessionFactory, instrument_id: str) -> dict[str, object] | None:
    identity = fx_instrument_identity(instrument_id)
    if identity is None:
        return None
    try:
        instrument = get_instrument(session_factory, identity.instrument_id)
    except ValueError:
        return None
    if instrument is None:
        return None
    return _latest_spot_point_from_instrument(
        instrument,
        instrument.get("market_data", []),
    )


def _load_latest_spot_points(
    session_factory: SessionFactory,
) -> dict[str, dict[str, object] | None]:
    instrument_ids = [identity.instrument_id for identity in FX_INSTRUMENT_IDENTITIES]
    with session_factory() as session:
        instruments = {
            str(row.instrument_id): {
                "instrument_id": str(row.instrument_id),
                "instrument_type": str(row.instrument_type),
                "currency": str(row.currency),
            }
            for row in session.execute(
                select(
                    Instrument.instrument_id,
                    Instrument.instrument_type,
                    Instrument.currency,
                ).where(Instrument.instrument_id.in_(instrument_ids))
            )
        }
        market_data_by_instrument: dict[str, list[dict[str, object]]] = {
            instrument_id: [] for instrument_id in instrument_ids
        }
        for row in session.execute(
            select(
                InstrumentMarketData.instrument_id,
                InstrumentMarketData.metric_family,
                InstrumentMarketData.quote_basis,
                InstrumentMarketData.as_of_date,
                InstrumentMarketData.value,
                InstrumentMarketData.currency,
                InstrumentMarketData.price_unit,
                InstrumentMarketData.price_scale,
                InstrumentMarketData.provider,
                InstrumentMarketData.status,
            ).where(
                InstrumentMarketData.instrument_id.in_(instrument_ids),
                InstrumentMarketData.metric_family == "fx",
                InstrumentMarketData.quote_basis == "spot",
            )
        ):
            market_data_by_instrument[str(row.instrument_id)].append(
                {
                    "metric_family": row.metric_family,
                    "quote_basis": row.quote_basis,
                    "as_of_date": row.as_of_date,
                    "value": row.value,
                    "currency": row.currency,
                    "price_unit": row.price_unit,
                    "price_scale": row.price_scale,
                    "provider": row.provider,
                    "status": row.status,
                }
            )

    return {
        instrument_id: (
            _latest_spot_point_from_instrument(
                instruments[instrument_id],
                market_data_by_instrument[instrument_id],
            )
            if instrument_id in instruments
            else None
        )
        for instrument_id in instrument_ids
    }


def _direct_rate_record(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
    *,
    spot_points: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    identity = fx_instrument_identity_for_pair(base_currency, quote_currency)
    if identity is None:
        return None

    point = (
        spot_points.get(identity.instrument_id)
        if spot_points is not None
        else _latest_spot_point(session_factory, identity.instrument_id)
    )
    if point is None:
        return None

    return {
        "base_currency": identity.base_currency,
        "quote_currency": identity.quote_currency,
        "rate": point["value"],
        "as_of_date": point["as_of_date"],
        "source_kind": "direct",
        "instrument_id": identity.instrument_id,
        "source_instrument_ids": [identity.instrument_id],
        "provider": point.get("provider"),
        "status": point["status"],
    }


def _inverse_rate_record(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
    *,
    spot_points: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    direct_record = _direct_rate_record(
        session_factory,
        quote_currency,
        base_currency,
        spot_points=spot_points,
    )
    if direct_record is None:
        return None

    direct_rate = _positive_decimal(direct_record.get("rate"))
    if direct_rate is None:
        return None
    rate = Decimal("1") / direct_rate
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
    *,
    spot_points: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    if base_currency == PIVOT_CURRENCY or quote_currency == PIVOT_CURRENCY:
        return None

    usd_to_base = _direct_rate_record(
        session_factory,
        PIVOT_CURRENCY,
        base_currency,
        spot_points=spot_points,
    )
    usd_to_quote = _direct_rate_record(
        session_factory,
        PIVOT_CURRENCY,
        quote_currency,
        spot_points=spot_points,
    )
    if usd_to_base is None or usd_to_quote is None:
        return None

    base_rate = _positive_decimal(usd_to_base.get("rate"))
    quote_rate = _positive_decimal(usd_to_quote.get("rate"))
    if base_rate is None or quote_rate is None:
        return None
    rate = quote_rate / base_rate
    leg_statuses = {
        str(usd_to_base.get("status") or "").strip().lower(),
        str(usd_to_quote.get("status") or "").strip().lower(),
    }
    if leg_statuses == {"complete"}:
        status = "complete"
    elif leg_statuses.issubset({"complete", "partial"}):
        status = "partial"
    else:
        status = "unavailable"
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


def _get_fx_rate(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
    *,
    spot_points: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    normalized_base = normalize_fx_currency(base_currency)
    normalized_quote = normalize_fx_currency(quote_currency)
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

    direct_record = _direct_rate_record(
        session_factory,
        normalized_base,
        normalized_quote,
        spot_points=spot_points,
    )
    if direct_record is not None:
        return direct_record

    inverse_record = _inverse_rate_record(
        session_factory,
        normalized_base,
        normalized_quote,
        spot_points=spot_points,
    )
    if inverse_record is not None:
        return inverse_record

    return _cross_rate_record(
        session_factory,
        normalized_base,
        normalized_quote,
        spot_points=spot_points,
    )


def get_fx_rate(
    session_factory: SessionFactory,
    base_currency: str,
    quote_currency: str,
) -> dict[str, object] | None:
    return _get_fx_rate(session_factory, base_currency, quote_currency)


def list_fx_rates(session_factory: SessionFactory) -> list[dict[str, object]]:
    spot_points = _load_latest_spot_points(session_factory)
    rates: list[dict[str, object]] = []
    for base_currency in SUPPORTED_FX_CURRENCIES:
        for quote_currency in SUPPORTED_FX_CURRENCIES:
            if base_currency == quote_currency:
                continue
            record = _get_fx_rate(
                session_factory,
                base_currency,
                quote_currency,
                spot_points=spot_points,
            )
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
    identity = fx_instrument_identity_for_pair(base_currency, quote_currency)
    if identity is None:
        raise ValueError(
            "FX pair is not maintained directly; choose one of "
            + ", ".join(maintained_fx_pairs())
            + "."
        )
    validated = validate_fx_market_data_contract(
        instrument_id=identity.instrument_id,
        instrument_type="fx",
        instrument_currency=identity.quote_currency,
        metric_family="fx",
        quote_basis="spot",
        point_currency=identity.quote_currency,
        value=rate,
        status=status,
    )
    if validated is None:
        raise ValueError("FX rate did not resolve to a maintained spot contract.")

    record = upsert_market_data(
        session_factory,
        instrument_id=identity.instrument_id,
        metric_family="fx",
        quote_basis="spot",
        as_of_date=as_of_date,
        value=str(validated.rate),
        currency=identity.quote_currency,
        provider=provider,
        status=status,
    )
    if record is None:
        raise ValueError("FX instrument not found in shared instrument registry.")

    refreshed = _direct_rate_record(
        session_factory,
        identity.base_currency,
        identity.quote_currency,
    )
    if refreshed is None:
        raise ValueError("Failed to refresh FX rate after update.")
    return refreshed
