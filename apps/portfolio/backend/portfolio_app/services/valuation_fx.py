"""Valuation and FX primitives with instrument-detail loading injected by callers.

This module intentionally owns no portfolio return-chain, period, attribution,
or holdings-profile policy. Callers supply the instrument-detail loader so the
runtime boundary remains explicit and testable.
"""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Callable

from portfolio_ops_instrument_core.fx_contract import fx_instrument_identity

from portfolio_app.services.market_data import (
    market_data_status,
    resolve_quote_point,
    resolve_quote_series,
)


InstrumentDetail = dict[str, object]
InstrumentDetailCache = dict[str, InstrumentDetail | None]
InstrumentDetailLoader = Callable[[str], InstrumentDetail | None]
FxInstrumentMap = dict[tuple[str, str], str]


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def normalized_currency(value: object) -> str:
    """Normalize a supplied currency without inventing one when it is absent."""

    return str(value or "").strip().upper()


def required_currency(value: object, *, field_name: str = "currency") -> str:
    normalized = normalized_currency(value)
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    return normalized


def position_market_value(
    *,
    quantity: float,
    last_price: float | None,
    price_scale: float | None = None,
) -> float | None:
    if last_price is None:
        return None
    resolved_scale = price_scale if price_scale is not None else 1.0
    if not isfinite(resolved_scale) or resolved_scale <= 0:
        return None
    return quantity * last_price * resolved_scale


def instrument_detail_cache_get(
    instrument_id: str,
    cache: InstrumentDetailCache,
    *,
    instrument_detail_loader: InstrumentDetailLoader,
) -> InstrumentDetail | None:
    if instrument_id not in cache:
        cache[instrument_id] = instrument_detail_loader(instrument_id)
    return cache[instrument_id]


def fx_direct_instrument_map(fx_payload: dict[str, object]) -> FxInstrumentMap:
    direct_instruments: FxInstrumentMap = {}
    for item in fx_payload.get("rates", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("source_kind") or "") != "direct":
            continue
        base_currency = normalized_currency(item.get("base_currency"))
        quote_currency = normalized_currency(item.get("quote_currency"))
        instrument_id = str(item.get("instrument_id") or "").strip()
        identity = fx_instrument_identity(instrument_id)
        if identity is None:
            continue
        if (
            base_currency != identity.base_currency
            or quote_currency != identity.quote_currency
        ):
            continue
        direct_instruments[
            (identity.base_currency, identity.quote_currency)
        ] = identity.instrument_id
    return direct_instruments


def direct_fx_point_as_of(
    *,
    instrument_id: str,
    as_of_date: date,
    instrument_detail_cache: InstrumentDetailCache,
    instrument_detail_loader: InstrumentDetailLoader,
) -> dict[str, object] | None:
    detail = instrument_detail_cache_get(
        instrument_id,
        instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    if not isinstance(detail, dict):
        return None
    point = resolve_quote_point(
        detail,
        candidate_bases=["spot"],
        as_of_date=as_of_date,
    ).point
    if point is None:
        return None
    rate = _safe_float(point.get("value"))
    point_date = _parse_iso_date(point.get("as_of_date"))
    if rate is None or rate <= 0 or point_date is None:
        return None
    return {
        "rate": rate,
        "as_of_date": point_date,
        "status": market_data_status(point),
        "stale": point_date < as_of_date,
        "source_instrument_ids": [instrument_id],
    }


def direct_fx_point_before(
    *,
    instrument_id: str,
    before_date: date,
    instrument_detail_cache: InstrumentDetailCache,
    instrument_detail_loader: InstrumentDetailLoader,
) -> dict[str, object] | None:
    detail = instrument_detail_cache_get(
        instrument_id,
        instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    if not isinstance(detail, dict):
        return None
    resolution = resolve_quote_series(
        detail,
        candidate_bases=["spot"],
        end_date=before_date,
    )
    if not resolution.available:
        return None
    for point in reversed(resolution.points):
        point_date = _parse_iso_date(point.get("as_of_date"))
        rate = _safe_float(point.get("value"))
        if point_date is None or point_date >= before_date or rate is None or rate <= 0:
            continue
        return {
            "rate": rate,
            "as_of_date": point_date,
            "status": market_data_status(point),
            "stale": False,
            "source_instrument_ids": [instrument_id],
        }
    return None


def resolve_fx_rate_on(
    *,
    as_of_date: date,
    base_currency: str,
    quote_currency: str,
    direct_instruments: FxInstrumentMap,
    instrument_detail_cache: InstrumentDetailCache,
    instrument_detail_loader: InstrumentDetailLoader,
) -> dict[str, object] | None:
    normalized_base = normalized_currency(base_currency)
    normalized_quote = normalized_currency(quote_currency)
    if not normalized_base or not normalized_quote:
        return None
    if normalized_base == normalized_quote:
        return {
            "rate": 1.0,
            "as_of_date": as_of_date,
            "status": "complete",
            "stale": False,
            "source_instrument_ids": [],
        }

    direct_instrument_id = direct_instruments.get((normalized_base, normalized_quote))
    if direct_instrument_id:
        direct_point = direct_fx_point_as_of(
            instrument_id=direct_instrument_id,
            as_of_date=as_of_date,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=instrument_detail_loader,
        )
        if direct_point is not None:
            return direct_point

    inverse_instrument_id = direct_instruments.get((normalized_quote, normalized_base))
    if inverse_instrument_id:
        inverse_point = direct_fx_point_as_of(
            instrument_id=inverse_instrument_id,
            as_of_date=as_of_date,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=instrument_detail_loader,
        )
        if inverse_point is not None:
            return {
                "rate": 1.0 / float(inverse_point["rate"]),
                "as_of_date": inverse_point["as_of_date"],
                "status": inverse_point["status"],
                "stale": bool(inverse_point["stale"]),
                "source_instrument_ids": list(
                    inverse_point.get("source_instrument_ids") or []
                ),
            }

    pivot_currency = "USD"
    if normalized_base == pivot_currency or normalized_quote == pivot_currency:
        return None

    base_leg = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=pivot_currency,
        quote_currency=normalized_base,
        direct_instruments=direct_instruments,
        instrument_detail_cache=instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    quote_leg = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=pivot_currency,
        quote_currency=normalized_quote,
        direct_instruments=direct_instruments,
        instrument_detail_cache=instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    if base_leg is None or quote_leg is None:
        return None

    base_rate = _safe_float(base_leg.get("rate"))
    quote_rate = _safe_float(quote_leg.get("rate"))
    base_date = _parse_iso_date(base_leg.get("as_of_date"))
    quote_date = _parse_iso_date(quote_leg.get("as_of_date"))
    if (
        base_rate is None
        or quote_rate is None
        or base_rate <= 0
        or quote_rate <= 0
        or base_date is None
        or quote_date is None
    ):
        return None
    return {
        "rate": quote_rate / base_rate,
        "as_of_date": min(base_date, quote_date),
        "status": "complete",
        "stale": bool(base_leg.get("stale")) or bool(quote_leg.get("stale")),
        "source_instrument_ids": sorted(
            {
                str(instrument_id)
                for leg in (base_leg, quote_leg)
                for instrument_id in list(leg.get("source_instrument_ids") or [])
                if str(instrument_id).strip()
            }
        ),
    }


def resolve_previous_fx_rate_before(
    *,
    before_date: date,
    base_currency: str,
    quote_currency: str,
    direct_instruments: FxInstrumentMap,
    instrument_detail_cache: InstrumentDetailCache,
    instrument_detail_loader: InstrumentDetailLoader,
) -> dict[str, object] | None:
    normalized_base = normalized_currency(base_currency)
    normalized_quote = normalized_currency(quote_currency)
    if not normalized_base or not normalized_quote:
        return None
    if normalized_base == normalized_quote:
        return {
            "rate": 1.0,
            "as_of_date": before_date,
            "status": "complete",
            "stale": False,
            "source_instrument_ids": [],
        }

    direct_instrument_id = direct_instruments.get((normalized_base, normalized_quote))
    if direct_instrument_id:
        direct_point = direct_fx_point_before(
            instrument_id=direct_instrument_id,
            before_date=before_date,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=instrument_detail_loader,
        )
        if direct_point is not None:
            return direct_point

    inverse_instrument_id = direct_instruments.get((normalized_quote, normalized_base))
    if inverse_instrument_id:
        inverse_point = direct_fx_point_before(
            instrument_id=inverse_instrument_id,
            before_date=before_date,
            instrument_detail_cache=instrument_detail_cache,
            instrument_detail_loader=instrument_detail_loader,
        )
        if inverse_point is not None:
            return {
                "rate": 1.0 / float(inverse_point["rate"]),
                "as_of_date": inverse_point["as_of_date"],
                "status": inverse_point["status"],
                "stale": bool(inverse_point["stale"]),
                "source_instrument_ids": list(
                    inverse_point.get("source_instrument_ids") or []
                ),
            }

    pivot_currency = "USD"
    if normalized_base == pivot_currency or normalized_quote == pivot_currency:
        return None

    base_leg = resolve_previous_fx_rate_before(
        before_date=before_date,
        base_currency=pivot_currency,
        quote_currency=normalized_base,
        direct_instruments=direct_instruments,
        instrument_detail_cache=instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    quote_leg = resolve_previous_fx_rate_before(
        before_date=before_date,
        base_currency=pivot_currency,
        quote_currency=normalized_quote,
        direct_instruments=direct_instruments,
        instrument_detail_cache=instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    if base_leg is None or quote_leg is None:
        return None

    base_rate = _safe_float(base_leg.get("rate"))
    quote_rate = _safe_float(quote_leg.get("rate"))
    base_date = _parse_iso_date(base_leg.get("as_of_date"))
    quote_date = _parse_iso_date(quote_leg.get("as_of_date"))
    if (
        base_rate is None
        or quote_rate is None
        or base_rate <= 0
        or quote_rate <= 0
        or base_date is None
        or quote_date is None
    ):
        return None
    return {
        "rate": quote_rate / base_rate,
        "as_of_date": min(base_date, quote_date),
        "status": "complete",
        "stale": bool(base_leg.get("stale")) or bool(quote_leg.get("stale")),
        "source_instrument_ids": sorted(
            {
                str(instrument_id)
                for leg in (base_leg, quote_leg)
                for instrument_id in list(leg.get("source_instrument_ids") or [])
                if str(instrument_id).strip()
            }
        ),
    }


def convert_amount_on(
    amount: float | None,
    *,
    as_of_date: date,
    from_currency: str,
    to_currency: str,
    direct_fx_instruments: FxInstrumentMap,
    instrument_detail_cache: InstrumentDetailCache,
    instrument_detail_loader: InstrumentDetailLoader,
) -> tuple[float | None, bool]:
    if amount is None:
        return None, False
    resolved_fx = resolve_fx_rate_on(
        as_of_date=as_of_date,
        base_currency=from_currency,
        quote_currency=to_currency,
        direct_instruments=direct_fx_instruments,
        instrument_detail_cache=instrument_detail_cache,
        instrument_detail_loader=instrument_detail_loader,
    )
    if resolved_fx is None:
        return None, False
    rate = _safe_float(resolved_fx.get("rate"))
    if rate is None or rate <= 0:
        return None, False
    return float(amount) * rate, bool(resolved_fx.get("stale"))
