from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from math import isfinite
from typing import Callable, Iterable

import exchange_calendars
from exchange_calendars.errors import CalendarError

from investment_studio_instrument_core import (
    FUND_INSTRUMENT_TYPES,
    FUND_TOTAL_RETURN_QUOTE_BASES,
    QUOTE_BASIS_METRIC_FAMILY,
    canonical_price_contract,
    confirmed_total_return_quote_bases,
)
from portfolio_app.services.asset_deliveries import expand_asset_deliveries
from portfolio_app.services.transaction_dates import transaction_position_effective_date


USABLE_MARKET_DATA_STATUS = "complete"

SUPPORTED_PRIMARY_QUOTE_BASES = frozenset(QUOTE_BASIS_METRIC_FAMILY)


@lru_cache(maxsize=256)
def market_calendar_sessions(
    calendar_name: str, start_date: date, end_date: date,
) -> tuple[date, ...] | None:
    try:
        # The registry uses Shenzhen's MIC. exchange_calendars publishes the
        # common mainland exchange session schedule under XSHG only.
        if calendar_name == "XSHE":
            calendar_name = "XSHG"
        calendar = exchange_calendars.get_calendar(calendar_name)
        return tuple(session.date() for session in calendar.sessions_in_range(
            start_date.isoformat(), end_date.isoformat(),
        ))
    except (CalendarError, ValueError):
        return None


def quote_is_stale(
    detail: dict[str, object], *, point_date: date, as_of_date: date,
) -> bool:
    """Carry a close only across confirmed non-session days, never missing prices."""
    # Point readers use date.max to request the latest available observation,
    # without a valuation date against which freshness could be assessed.
    if as_of_date == date.max or point_date >= as_of_date:
        return False
    settings = detail.get("source_settings")
    calendar_name = str(
        (settings.get("market_calendar") if isinstance(settings, dict) else None)
        or detail.get("exchange_code") or ""
    ).strip()
    if not calendar_name:
        return True
    sessions = market_calendar_sessions(calendar_name, point_date + timedelta(days=1), as_of_date)
    return sessions is None or bool(sessions)


def bind_initial_purchase_valuations(
    transactions: list[dict[str, object]],
    detail_cache: dict[str, dict[str, object] | None],
    *,
    instrument_detail_loader: Callable[[str], dict[str, object] | None],
) -> None:
    """Bind portfolio transaction evidence to request-local valuation details.

    This never adds a quote to market_data. Only the instrument's first
    position-effective date can establish this basis; later additions cannot
    price an already-held position. Multiple initial buys use gross/quantity,
    excluding charges, and share one price across accounts and cost lots.
    """
    entries: dict[str, list[tuple[date, dict[str, object]]]] = {}
    for transaction in expand_asset_deliveries(transactions):
        instrument_id = str(transaction.get("instrument_id") or "")
        effective_date = transaction_position_effective_date(transaction)
        if not instrument_id or effective_date is None:
            continue
        entries.setdefault(instrument_id, []).append((effective_date, transaction))
    for instrument_id, facts in entries.items():
        first_date = min(day for day, _ in facts)
        initial_facts = [fact for day, fact in facts if day == first_date]
        if any(
            fact.get("transaction_type") != "buy"
            or fact.get("_short_source_type") or fact.get("noncash_delivery")
            for fact in initial_facts
        ):
            continue
        quantity = sum(float(fact.get("quantity") or 0) for fact in initial_facts)
        gross = sum(float(fact.get("gross_amount") or 0) for fact in initial_facts)
        if quantity <= 0 or gross <= 0:
            continue
        if instrument_id not in detail_cache:
            detail_cache[instrument_id] = instrument_detail_loader(instrument_id)
        detail = detail_cache[instrument_id]
        if not isinstance(detail, dict):
            continue
        purchase_point = {
            "_portfolio_id": str(initial_facts[0].get("portfolio_id") or ""),
            "as_of_date": first_date,
            "value": gross / quantity,
            "currency": initial_facts[0].get("currency"),
            "price_unit": "per_unit",
            "price_scale": 1.0,
            "status": "transaction-price",
            "provider": "portfolio_transactions",
            "valuation_source_transaction_ids": sorted(
                str(fact["transaction_id"]) for fact in initial_facts
            ),
            "stale": False,
        }
        if detail.get("_initial_purchase_valuation") != purchase_point:
            detail_cache[instrument_id] = {
                **detail, "_initial_purchase_valuation": purchase_point,
            }


def initial_purchase_valuation_point(
    detail: dict[str, object],
    *,
    market_point: dict[str, object] | None,
    as_of_date: date,
    candidate_bases: Iterable[str],
) -> dict[str, object] | None:
    if market_point is not None and not market_point.get("stale"):
        return market_point
    purchase_point = detail.get("_initial_purchase_valuation")
    if not isinstance(purchase_point, dict) or as_of_date == date.max:
        return market_point
    purchase_date = purchase_point["as_of_date"]
    if as_of_date < purchase_date or quote_is_stale(
        detail, point_date=purchase_date, as_of_date=as_of_date,
    ):
        return market_point
    # Missing observations permit the transaction basis; malformed or
    # ambiguous official series must retain their explicit failure.
    if market_point is None and resolve_quote_series(
        detail, candidate_bases=candidate_bases, end_date=as_of_date,
    ).unavailable_reason != "quote_series_unavailable":
        return None
    # Physical option stock legs are persisted as buys at strike. Only their
    # explicit delivery links distinguish them from ordinary cash purchases.
    # Consult those links only when a transaction valuation would be used.
    from portfolio_app.services.portfolio_store import list_option_delivery_links

    purchase_ids = set(purchase_point["valuation_source_transaction_ids"])
    if any(
        str(link["stock_transaction_id"]) in purchase_ids
        for link in list_option_delivery_links(purchase_point["_portfolio_id"])
    ):
        return market_point
    return {key: value for key, value in purchase_point.items() if key != "_portfolio_id"}


@dataclass(frozen=True)
class QuoteSeriesResolution:
    points: tuple[dict[str, object], ...] = ()
    metric_family: str | None = None
    quote_basis: str | None = None
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

    if _normalized_text(detail.get("instrument_type")) in FUND_INSTRUMENT_TYPES:
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
        if quote_basis and quote_basis not in bases:
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


def resolve_quote_series(
    detail: dict[str, object],
    *,
    candidate_bases: Iterable[str],
    end_date: date | None = None,
) -> QuoteSeriesResolution:
    """Resolve exactly one complete quote-series identity.

    A policy basis is a preference, not a series identity.  Portfolio therefore
    locks the selected history to metric family, basis, and currency and rejects
    duplicate dates instead of merging provider rows. The canonical unit/scale
    contract is validated again at the Portfolio boundary.
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
            if canonical_unit == "rate":
                reason = "fx_price_contract_unsupported"
            else:
                reason = "price_contract_unsupported"
            return QuoteSeriesResolution(unavailable_reason=reason)

        normalized_points: list[dict[str, object]] = []
        for raw_point in matching_raw:
            point_date = _parse_iso_date(raw_point.get("as_of_date"))
            value = _finite_float(raw_point.get("value"))
            if point_date is None or value is None:
                return QuoteSeriesResolution(unavailable_reason="invalid_quote_observation")
            normalized_point = dict(raw_point)
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
            normalized_points.append(normalized_point)

        normalized_points.sort(key=lambda point: point["as_of_date"])
        return QuoteSeriesResolution(
            points=tuple(normalized_points),
            metric_family=metric_family,
            quote_basis=quote_basis,
            currency=currency,
            price_unit=price_unit,
            price_scale=price_scale,
        )

    return QuoteSeriesResolution(unavailable_reason="quote_series_unavailable")


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
    point["stale"] = point_date is not None and quote_is_stale(
        detail, point_date=point_date, as_of_date=as_of_date,
    )
    return QuotePointResolution(point=point)


def previous_quote_point(
    detail: dict[str, object],
    *,
    selected_point: dict[str, object] | None,
) -> QuotePointResolution:
    if not isinstance(selected_point, dict):
        return QuotePointResolution(unavailable_reason="selected_quote_unavailable")
    quote_basis = _normalized_text(selected_point.get("quote_basis"))
    selected_date = _parse_iso_date(selected_point.get("as_of_date"))
    selected_identity = (
        _normalized_text(selected_point.get("metric_family")),
        quote_basis,
        _normalized_currency(selected_point.get("currency")),
    )
    if not quote_basis or selected_date is None or not all(selected_identity):
        return QuotePointResolution(unavailable_reason="selected_quote_identity_incomplete")
    series = resolve_quote_series(
        detail,
        candidate_bases=[quote_basis],
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
