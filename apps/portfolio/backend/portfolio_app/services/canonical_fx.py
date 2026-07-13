from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy.orm import Session

from portfolio_ops_instrument_core.canonical_fx import (
    CanonicalFxResolverError,
    CanonicalFxWindowBook,
    resolve_canonical_fx_window_book_in_session,
)
from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
)


PORTFOLIO_FX_CONSUMER_POLICY_VERSION = "portfolio_fx_consumer.v1"
PORTFOLIO_FX_MAX_AGE_CALENDAR_DAYS = 5


def portfolio_fx_freshness_policy() -> QuoteFreshnessPolicy:
    return QuoteFreshnessPolicy(
        policy_version=CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
        mode="calendar_day_carry_forward",
        max_age_days=PORTFOLIO_FX_MAX_AGE_CALENDAR_DAYS,
    )


def resolve_portfolio_fx_window_book_in_session(
    session: Session,
    *,
    currency_pairs: Iterable[tuple[str, str]],
    start_date: date,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy,
) -> CanonicalFxWindowBook:
    """Lock Portfolio valuation FX with an explicit five-calendar-day policy."""

    expected_policy = portfolio_fx_freshness_policy()
    if freshness_policy != expected_policy:
        raise CanonicalFxResolverError(
            "invalid_portfolio_fx_freshness_policy",
            "Portfolio valuation FX requires the explicit versioned five-calendar-day policy.",
        )
    return resolve_canonical_fx_window_book_in_session(
        session,
        currency_pairs=currency_pairs,
        start_date=start_date,
        end_date=end_date,
        freshness_policy=freshness_policy,
        consumer_policy_version=PORTFOLIO_FX_CONSUMER_POLICY_VERSION,
    )
