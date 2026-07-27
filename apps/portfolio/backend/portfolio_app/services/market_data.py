from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Iterable

from portfolio_ops_instrument_core import (
    FUND_TOTAL_RETURN_QUOTE_BASES,
    QUOTE_BASIS_METRIC_FAMILY,
    canonical_price_contract,
    confirmed_total_return_quote_bases,
)


USABLE_MARKET_DATA_STATUS = "complete"

SUPPORTED_PRIMARY_QUOTE_BASES = frozenset(
    set(QUOTE_BASIS_METRIC_FAMILY) - {"accrued_interest"}
)


@dataclass(frozen=True)
class QuoteSeriesResolution:
    points: tuple[dict[str, object], ...] = ()
    metric_family: str | None = None
    quote_basis: str | None = None
    source_quote_basis: str | None = None
    currency: str | None = None
    price_unit: str | None = None
    price_scale: float | None = None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.points) and self.unavailable_reason is None


@dataclass(frozen=True)
class QuotePointResolution:
    point: dict[str, object] | None = None
    unavailable_reason: str | None = None


def _normalized_text(value: object) -> str:
    return str(value or "").strip().lower()


def _normalized_currency(value: object) -> str:
    return str(value or "").strip().upper()


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _finite_float(value: object, *, allow_zero: bool = False) -> float | None:
    try:
        resolved = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if resolved is None or not isfinite(resolved):
        return None
    if resolved < 0 or (resolved == 0 and not allow_zero):
        return None
    return resolved


def _market_data_points(detail: dict[str, object]) -> list[dict[str, object]]:
    market_data = detail.get("market_data")
    if isinstance(market_data, list) and market_data:
        return [point for point in market_data if isinstance(point, dict)]
    latest_market_data = detail.get("latest_market_data")
    if isinstance(latest_market_data, list):
        return [point for point in latest_market_data if isinstance(point, dict)]
    return []


def quote_policy_bases(
    detail: dict[str, object],
    roles: str | Iterable[str],
) -> list[str]:
    policy = detail.get("quote_selection_policy")
    if not isinstance(policy, dict):
        return []
    resolved_roles = (roles,) if isinstance(roles, str) else tuple(roles)
    bases: list[str] = []
    for role in resolved_roles:
        raw_bases = policy.get(role)
        if not isinstance(raw_bases, list):
            continue
        for raw_basis in raw_bases:
            quote_basis = _normalized_text(raw_basis)
            if quote_basis and quote_basis not in bases:
                bases.append(quote_basis)
    return bases


def analytical_return_quote_bases(detail: dict[str, object]) -> list[str]:
    """Return the ordered quote bases eligible for analytical return series.

    A fund's unit NAV is a valuation fact, not a total-return substitute. Fund
    analytics therefore consume only the canonical dividend-reinvested NAV and
    remain unavailable when it is absent. Listed instruments retain their
    explicit policy order, including adjusted close and raw-price fallbacks.
    """

    if _normalized_text(detail.get("instrument_type")) == "fund":
        return [
            quote_basis
            for quote_basis in quote_policy_bases(detail, ("total_return", "chart"))
            if quote_basis in FUND_TOTAL_RETURN_QUOTE_BASES
        ]
    return quote_policy_bases(
        detail,
        ("total_return", "chart", "valuation", "reference"),
    )


def benchmark_total_return_quote_bases(detail: dict[str, object]) -> list[str]:
    """Return only quote identities proven comparable with portfolio TWR."""

    source_settings = detail.get("source_settings")
    confirmed_bases = confirmed_total_return_quote_bases(
        instrument_type=detail.get("instrument_type"),
        quote_bases=quote_policy_bases(
            detail,
            ("total_return", "chart", "valuation", "reference"),
        ),
        source_settings=source_settings if isinstance(source_settings, dict) else None,
    )
    available_bases = set(available_quote_bases(detail))
    return [basis for basis in confirmed_bases if basis in available_bases]


def available_quote_bases(detail: dict[str, object]) -> list[str]:
    bases: list[str] = []
    for point in _market_data_points(detail):
        if not is_usable_market_data_point(point):
            continue
        quote_basis = _normalized_text(point.get("quote_basis"))
        if quote_basis and quote_basis != "accrued_interest" and quote_basis not in bases:
            bases.append(quote_basis)
    return bases


def _price_contract(
    points: list[dict[str, object]],
) -> tuple[str | None, float | None, str | None]:
    contracts: set[tuple[str | None, float | None]] = set()
    for point in points:
        price_unit = _normalized_text(point.get("price_unit")) or None
        raw_scale = point.get("price_scale")
        price_scale = _finite_float(raw_scale) if raw_scale is not None else None
        if (price_unit is None) != (price_scale is None):
            return None, None, "quote_price_contract_incomplete"
        contracts.add((price_unit, price_scale))
    if len(contracts) > 1:
        return None, None, "ambiguous_quote_price_contract"
    return next(iter(contracts), (None, None)) + (None,)


def _matching_accrued_interest(
    all_points: list[dict[str, object]],
    *,
    point_date: date,
    currency: str,
    price_unit: str,
    price_scale: float,
) -> tuple[float | None, str | None]:
    same_date_components: list[dict[str, object]] = []
    for point in all_points:
        if not is_usable_market_data_point(point):
            continue
        if _normalized_text(point.get("quote_basis")) != "accrued_interest":
            continue
        if _parse_iso_date(point.get("as_of_date")) != point_date:
            continue
        same_date_components.append(point)

    if not same_date_components:
        return None, "clean_price_requires_matching_accrued_interest"
    if len(same_date_components) != 1:
        return None, "duplicate_accrued_interest_component"
    component = same_date_components[0]
    component_scale = _finite_float(component.get("price_scale"))
    if _normalized_text(component.get("metric_family")) != "price":
        return None, "accrued_interest_metric_family_mismatch"
    if _normalized_currency(component.get("currency")) != currency:
        return None, "accrued_interest_currency_mismatch"
    if (
        _normalized_text(component.get("price_unit")) != price_unit
        or component_scale is None
        or abs(component_scale - price_scale) > 1e-12
    ):
        return None, "accrued_interest_price_contract_mismatch"
    value = _finite_float(component.get("value"), allow_zero=True)
    if value is None:
        return None, "invalid_accrued_interest_component"
    return value, None


def resolve_quote_series(
    detail: dict[str, object],
    *,
    candidate_bases: Iterable[str],
    end_date: date | None = None,
) -> QuoteSeriesResolution:
    """Resolve exactly one complete quote-series identity.

    A policy basis is a preference, not a series identity.  Portfolio therefore
    locks the selected history to metric family, basis, and currency and rejects
    duplicate dates instead of merging provider rows.  Explicit unit/scale and
    same-date accrued-interest components come from the canonical shared market
    data contract and are validated again here at the Portfolio boundary.
    """

    points = _market_data_points(detail)
    expected_currency = _normalized_currency(detail.get("currency"))
    instrument_type = _normalized_text(detail.get("instrument_type"))
    normalized_bases: list[str] = []
    for raw_basis in candidate_bases:
        quote_basis = _normalized_text(raw_basis)
        if quote_basis and quote_basis not in normalized_bases:
            normalized_bases.append(quote_basis)

    if not normalized_bases:
        return QuoteSeriesResolution(unavailable_reason="quote_policy_unavailable")
    if any(quote_basis not in SUPPORTED_PRIMARY_QUOTE_BASES for quote_basis in normalized_bases):
        return QuoteSeriesResolution(unavailable_reason="unsupported_quote_basis")
    if not expected_currency:
        return QuoteSeriesResolution(unavailable_reason="instrument_currency_unavailable")

    fallback_unavailable_reason: str | None = None
    for quote_basis in normalized_bases:
        matching_raw: list[dict[str, object]] = []
        invalid_observation = False
        for point in points:
            if not is_usable_market_data_point(point):
                continue
            if _normalized_text(point.get("quote_basis")) != quote_basis:
                continue
            point_date = _parse_iso_date(point.get("as_of_date"))
            if point_date is None:
                invalid_observation = True
                continue
            if end_date is not None and point_date > end_date:
                continue
            if _finite_float(point.get("value")) is None:
                invalid_observation = True
                continue
            matching_raw.append(point)

        if invalid_observation:
            return QuoteSeriesResolution(unavailable_reason="invalid_quote_observation")
        if not matching_raw:
            continue

        identities = {
            (
                _normalized_text(point.get("metric_family")),
                quote_basis,
                _normalized_currency(point.get("currency")),
            )
            for point in matching_raw
        }
        if len(identities) != 1:
            return QuoteSeriesResolution(unavailable_reason="ambiguous_quote_series_identity")
        metric_family, _, currency = next(iter(identities))
        if not metric_family or not currency:
            return QuoteSeriesResolution(unavailable_reason="incomplete_quote_series_identity")
        if currency != expected_currency:
            return QuoteSeriesResolution(unavailable_reason="quote_currency_mismatch")

        dates = [_parse_iso_date(point.get("as_of_date")) for point in matching_raw]
        if len(dates) != len(set(dates)):
            return QuoteSeriesResolution(unavailable_reason="duplicate_quote_observation")

        price_unit, price_scale, contract_error = _price_contract(matching_raw)
        if contract_error is not None:
            return QuoteSeriesResolution(unavailable_reason=contract_error)
        if instrument_type == "bond":
            if quote_basis not in {"dirty_price", "clean_price"}:
                return QuoteSeriesResolution(unavailable_reason="bond_quote_basis_unavailable")
        try:
            canonical_unit, canonical_scale = canonical_price_contract(
                instrument_type=instrument_type,
                metric_family=metric_family,
                quote_basis=quote_basis,
            )
        except ValueError:
            return QuoteSeriesResolution(unavailable_reason="quote_metric_family_mismatch")
        if (
            price_unit != canonical_unit
            or price_scale is None
            or abs(price_scale - float(canonical_scale)) > 1e-12
        ):
            if instrument_type == "bond":
                reason = "bond_price_contract_unavailable"
            elif canonical_unit == "rate":
                reason = "fx_price_contract_unsupported"
            else:
                reason = "non_bond_price_contract_unsupported"
            return QuoteSeriesResolution(unavailable_reason=reason)

        normalized_points: list[dict[str, object]] = []
        candidate_unavailable_reason: str | None = None
        for raw_point in matching_raw:
            point_date = _parse_iso_date(raw_point.get("as_of_date"))
            value = _finite_float(raw_point.get("value"))
            if point_date is None or value is None:
                return QuoteSeriesResolution(unavailable_reason="invalid_quote_observation")
            normalized_point = dict(raw_point)
            normalized_point.pop("source_quote_basis", None)
            normalized_point.pop("clean_value", None)
            normalized_point.pop("accrued_interest", None)
            normalized_point.update(
                {
                    "as_of_date": point_date,
                    "value": value,
                    "metric_family": metric_family,
                    "quote_basis": quote_basis,
                    "currency": currency,
                    "status": market_data_status(raw_point),
                    "price_unit": price_unit,
                    "price_scale": price_scale,
                }
            )
            if instrument_type == "bond" and quote_basis == "clean_price":
                assert price_unit is not None and price_scale is not None
                accrued_interest, accrued_error = _matching_accrued_interest(
                    points,
                    point_date=point_date,
                    currency=currency,
                    price_unit=price_unit,
                    price_scale=price_scale,
                )
                if accrued_interest is None:
                    if accrued_error != "clean_price_requires_matching_accrued_interest":
                        return QuoteSeriesResolution(unavailable_reason=accrued_error)
                    candidate_unavailable_reason = accrued_error
                    break
                normalized_point["clean_value"] = value
                normalized_point["accrued_interest"] = accrued_interest
                normalized_point["value"] = value + accrued_interest
                normalized_point["source_quote_basis"] = "clean_price"
                normalized_point["quote_basis"] = "dirty_price"
            normalized_points.append(normalized_point)

        if candidate_unavailable_reason is not None:
            fallback_unavailable_reason = candidate_unavailable_reason
            continue

        normalized_points.sort(key=lambda point: point["as_of_date"])
        canonical_quote_basis = "dirty_price" if quote_basis == "clean_price" else quote_basis
        return QuoteSeriesResolution(
            points=tuple(normalized_points),
            metric_family=metric_family,
            quote_basis=canonical_quote_basis,
            source_quote_basis=(quote_basis if quote_basis != canonical_quote_basis else None),
            currency=currency,
            price_unit=price_unit,
            price_scale=price_scale,
        )

    return QuoteSeriesResolution(
        unavailable_reason=fallback_unavailable_reason or "quote_series_unavailable"
    )


def resolve_quote_point(
    detail: dict[str, object],
    *,
    candidate_bases: Iterable[str],
    as_of_date: date,
) -> QuotePointResolution:
    series = resolve_quote_series(
        detail,
        candidate_bases=candidate_bases,
        end_date=as_of_date,
    )
    if not series.available:
        return QuotePointResolution(unavailable_reason=series.unavailable_reason)
    point = dict(series.points[-1])
    point_date = _parse_iso_date(point.get("as_of_date"))
    point["stale"] = point_date is not None and point_date < as_of_date
    return QuotePointResolution(point=point)


def previous_quote_point(
    detail: dict[str, object],
    *,
    selected_point: dict[str, object] | None,
) -> QuotePointResolution:
    if not isinstance(selected_point, dict):
        return QuotePointResolution(unavailable_reason="selected_quote_unavailable")
    quote_basis = _normalized_text(selected_point.get("quote_basis"))
    source_quote_basis = (
        _normalized_text(selected_point.get("source_quote_basis")) or quote_basis
    )
    selected_date = _parse_iso_date(selected_point.get("as_of_date"))
    selected_identity = (
        _normalized_text(selected_point.get("metric_family")),
        quote_basis,
        _normalized_currency(selected_point.get("currency")),
    )
    if not source_quote_basis or selected_date is None or not all(selected_identity):
        return QuotePointResolution(unavailable_reason="selected_quote_identity_incomplete")
    series = resolve_quote_series(
        detail,
        candidate_bases=[source_quote_basis],
        end_date=selected_date,
    )
    if not series.available:
        return QuotePointResolution(unavailable_reason=series.unavailable_reason)
    if (series.metric_family, series.quote_basis, series.currency) != selected_identity:
        return QuotePointResolution(unavailable_reason="selected_quote_identity_mismatch")
    earlier = [
        point
        for point in series.points
        if isinstance(point.get("as_of_date"), date) and point["as_of_date"] < selected_date
    ]
    if not earlier:
        return QuotePointResolution(unavailable_reason="previous_quote_unavailable")
    point = dict(earlier[-1])
    point["stale"] = False
    return QuotePointResolution(point=point)


def market_data_status(point: dict[str, object]) -> str:
    return str(point.get("status") or "").strip().lower()


def is_usable_market_data_point(point: object) -> bool:
    return isinstance(point, dict) and market_data_status(point) == USABLE_MARKET_DATA_STATUS
