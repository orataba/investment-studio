from __future__ import annotations

from datetime import date
from math import isfinite

from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import is_usable_market_data_point, market_data_status


# Execution and position valuation must use an observable, unadjusted quote.
# Keep this as an allow-list so newly introduced adjusted or cumulative bases
# cannot silently become eligible without an explicit accounting decision.
UNADJUSTED_EXECUTION_QUOTE_BASES = frozenset(
    {
        "last",
        "close",
        "official_nav",
        "spot",
        "clean_price",
        "dirty_price",
        "par",
    }
)
EXECUTION_QUOTE_POLICY_ROLES = ("trading", "valuation")


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return date.fromisoformat(normalized[:10])
            except ValueError:
                return None
    return None


def _positive_float(value: object) -> float | None:
    try:
        resolved = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if resolved is None or not isfinite(resolved) or resolved <= 0:
        return None
    return resolved


def _execution_quote_candidates(detail: dict[str, object]) -> list[tuple[str, str]]:
    policy = detail.get("quote_selection_policy")
    if not isinstance(policy, dict):
        return []

    candidates: list[tuple[str, str]] = []
    seen_bases: set[str] = set()
    for role in EXECUTION_QUOTE_POLICY_ROLES:
        raw_bases = policy.get(role)
        if not isinstance(raw_bases, list):
            continue
        for raw_basis in raw_bases:
            quote_basis = str(raw_basis or "").strip().lower()
            if (
                not quote_basis
                or quote_basis in seen_bases
                or quote_basis not in UNADJUSTED_EXECUTION_QUOTE_BASES
            ):
                continue
            seen_bases.add(quote_basis)
            candidates.append((role, quote_basis))
    return candidates


def build_execution_quote_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
) -> dict[str, object]:
    candidates = _execution_quote_candidates(detail)
    candidate_bases = {quote_basis for _, quote_basis in candidates}
    latest_by_basis: dict[str, tuple[date, dict[str, object], float]] = {}
    market_data = detail.get("market_data")

    if isinstance(market_data, list) and candidate_bases:
        for point in market_data:
            if not is_usable_market_data_point(point):
                continue
            quote_basis = str(point.get("quote_basis") or "").strip().lower()
            if quote_basis not in candidate_bases:
                continue
            quote_date = _parse_iso_date(point.get("as_of_date"))
            value = _positive_float(point.get("value"))
            if quote_date is None or quote_date > as_of_date or value is None:
                continue
            current = latest_by_basis.get(quote_basis)
            if current is None or quote_date >= current[0]:
                latest_by_basis[quote_basis] = (quote_date, point, value)

    for selection_role, quote_basis in candidates:
        selected = latest_by_basis.get(quote_basis)
        if selected is None:
            continue
        quote_date, point, value = selected
        return {
            "instrument_id": str(detail.get("instrument_id") or instrument_id),
            "requested_as_of_date": as_of_date,
            "selection_role": selection_role,
            "value": value,
            "quote_date": quote_date,
            "quote_basis": quote_basis,
            "metric_family": str(point.get("metric_family") or "").strip() or None,
            "currency": str(point.get("currency") or detail.get("currency") or "USD"),
            "provider": str(point.get("provider")) if point.get("provider") is not None else None,
            "status": market_data_status(point),
            "stale": quote_date < as_of_date,
        }

    return {
        "instrument_id": str(detail.get("instrument_id") or instrument_id),
        "requested_as_of_date": as_of_date,
        "selection_role": None,
        "value": None,
        "quote_date": None,
        "quote_basis": None,
        "metric_family": None,
        "currency": str(detail.get("currency") or "USD"),
        "provider": None,
        "status": "unavailable",
        "stale": False,
    }


def get_execution_quote_on_or_before(
    instrument_id: str,
    *,
    as_of_date: date,
) -> dict[str, object] | None:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return None
    return build_execution_quote_from_detail(
        detail,
        instrument_id=instrument_id,
        as_of_date=as_of_date,
    )
