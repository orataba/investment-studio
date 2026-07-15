from __future__ import annotations

from datetime import date

from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import resolve_quote_point


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
    resolution = resolve_quote_point(
        detail,
        candidate_bases=[quote_basis for _, quote_basis in candidates],
        as_of_date=as_of_date,
    )
    point = resolution.point
    if point is not None:
        quote_basis = str(point.get("quote_basis") or "").strip().lower()
        selection_basis = str(
            point.get("source_quote_basis") or quote_basis
        ).strip().lower()
        selection_role = next(
            (role for role, candidate_basis in candidates if candidate_basis == selection_basis),
            None,
        )
        quote_date = _parse_iso_date(point.get("as_of_date"))
        return {
            "instrument_id": str(detail.get("instrument_id") or instrument_id),
            "requested_as_of_date": as_of_date,
            "selection_role": selection_role,
            "value": point.get("value"),
            "quote_date": quote_date,
            "quote_basis": quote_basis,
            "metric_family": str(point.get("metric_family") or "").strip() or None,
            "currency": str(point.get("currency") or detail.get("currency") or "USD").upper(),
            "provider": str(point.get("provider")) if point.get("provider") is not None else None,
            "status": str(point.get("status") or "complete"),
            "stale": quote_date is not None and quote_date < as_of_date,
            "price_unit": point.get("price_unit"),
            "price_scale": point.get("price_scale"),
            "unavailable_reason": None,
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
        "price_unit": None,
        "price_scale": None,
        "unavailable_reason": (
            resolution.unavailable_reason
            if candidates
            else "no_eligible_execution_quote"
        ),
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
