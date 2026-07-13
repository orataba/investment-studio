from __future__ import annotations

from portfolio_ops_instrument_core.models import (
    CanonicalQuoteResolution,
    QuoteFreshnessPolicy,
)
from portfolio_ops_instrument_core.quote_resolver import (
    resolve_explicit_quote as resolve_shared_explicit_quote,
    resolve_role_quote as resolve_shared_role_quote,
)

from platform_app.db.session import get_session_factory


def resolve_explicit_quote(
    *,
    resolver_strategy_version: str,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    currency: str,
    requested_as_of_date,
    freshness_policy: QuoteFreshnessPolicy,
) -> CanonicalQuoteResolution:
    return resolve_shared_explicit_quote(
        get_session_factory(),
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=freshness_policy,
    )


def resolve_role_quote(
    *,
    resolver_strategy_version: str,
    quote_selection_policy_version: str,
    instrument_id: str,
    role: str,
    currency: str,
    requested_as_of_date,
    freshness_policy: QuoteFreshnessPolicy,
) -> CanonicalQuoteResolution:
    return resolve_shared_role_quote(
        get_session_factory(),
        resolver_strategy_version=resolver_strategy_version,
        quote_selection_policy_version=quote_selection_policy_version,
        instrument_id=instrument_id,
        role=role,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=freshness_policy,
    )
