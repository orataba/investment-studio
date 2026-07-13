from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core import (
    CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesResolution,
    QuoteFreshnessPolicy,
    QuoteResolverError,
    resolve_quote_series_observation_at,
    resolve_role_quote_in_session,
    resolve_role_quote_series_in_session,
)
from portfolio_ops_instrument_core.db_models import Instrument

from portfolio_app.core.settings import get_settings


QuoteRole = Literal["valuation", "trading", "total_return"]
PORTFOLIO_QUOTE_CONSUMER_POLICY_VERSION = "portfolio_quote_consumer.v2"


@dataclass(frozen=True)
class ConsumerFreshnessProfile:
    policy_type: str
    policy_version: str
    canonical_instrument_type: str
    resolver_policy: QuoteFreshnessPolicy


def valuation_freshness_profile(instrument_type: str) -> ConsumerFreshnessProfile:
    """Select Portfolio's explicit freshness class from canonical type only.

    Official fund NAVs can legitimately publish at a monthly cadence.  Listed
    and other daily-market instruments must fail closed much sooner.  This is
    deliberately based on the canonical ``instrument_type``; Portfolio never
    infers type from identifiers, taxonomy, or quote shape.
    """

    normalized_type = str(instrument_type or "").strip().lower()
    settings = get_settings()
    if normalized_type == "fund":
        policy_type = "periodic_fund_nav"
        max_age_days = settings.fund_valuation_quote_max_age_days
    else:
        policy_type = "daily_market"
        max_age_days = settings.daily_market_valuation_quote_max_age_days
    return ConsumerFreshnessProfile(
        policy_type=policy_type,
        policy_version=PORTFOLIO_QUOTE_CONSUMER_POLICY_VERSION,
        canonical_instrument_type=normalized_type,
        resolver_policy=QuoteFreshnessPolicy(
            policy_version=CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
            mode="calendar_day_carry_forward",
            max_age_days=max_age_days,
        ),
    )


def valuation_freshness_policy(instrument_type: str) -> QuoteFreshnessPolicy:
    return valuation_freshness_profile(instrument_type).resolver_policy


def maximum_valuation_quote_age_days() -> int:
    settings = get_settings()
    return max(
        settings.daily_market_valuation_quote_max_age_days,
        settings.fund_valuation_quote_max_age_days,
    )


def trading_freshness_policy() -> QuoteFreshnessPolicy:
    return QuoteFreshnessPolicy(
        policy_version=CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION,
        mode="exact_only",
        max_age_days=0,
    )


def _normalized_ids(instrument_ids: Iterable[str]) -> list[str]:
    return sorted(
        {
            normalized
            for instrument_id in instrument_ids
            if (normalized := str(instrument_id or "").strip())
        }
    )


def _canonical_instruments(
    session: Session,
    instrument_ids: Iterable[str],
) -> dict[str, Instrument]:
    """Load canonical quote identity and keep strong ORM references while resolving."""

    normalized_ids = _normalized_ids(instrument_ids)
    if not normalized_ids:
        return {}
    instruments = session.scalars(
        select(Instrument).where(Instrument.instrument_id.in_(normalized_ids))
    ).all()
    return {
        str(instrument.instrument_id): instrument
        for instrument in instruments
        if str(instrument.instrument_id or "").strip()
        and str(instrument.currency or "").strip()
    }


def _consumer_profile(
    *,
    role: QuoteRole,
    instrument_type: str,
    explicit_policy: QuoteFreshnessPolicy | None,
) -> ConsumerFreshnessProfile:
    normalized_type = str(instrument_type or "").strip().lower()
    if explicit_policy is not None:
        return ConsumerFreshnessProfile(
            policy_type="explicit_override",
            policy_version=PORTFOLIO_QUOTE_CONSUMER_POLICY_VERSION,
            canonical_instrument_type=normalized_type,
            resolver_policy=explicit_policy,
        )
    if role in {"valuation", "total_return"}:
        return valuation_freshness_profile(normalized_type)
    return ConsumerFreshnessProfile(
        policy_type="same_day_trading",
        policy_version=PORTFOLIO_QUOTE_CONSUMER_POLICY_VERSION,
        canonical_instrument_type=normalized_type,
        resolver_policy=trading_freshness_policy(),
    )


def _source_status_at(
    window: CanonicalQuoteSeriesResolution,
    requested_as_of_date: date,
) -> str | None:
    candidates = [
        observation
        for observation in [window.start_boundary_observation, *window.observations]
        if observation is not None
        and observation.observation_date <= requested_as_of_date
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.observation_date, item.revision_number),
    ).status


def _coverage_status(
    resolution: CanonicalQuoteResolution | CanonicalQuoteSeriesResolution,
    *,
    source_status: str | None,
) -> str:
    if resolution.resolution_status == "resolved":
        return "complete"
    if source_status == "partial" or "partial_series" in resolution.reason_codes:
        return "partial"
    return "unavailable"


def _point_source_status(resolution: CanonicalQuoteResolution) -> str | None:
    """Recover the current revision state without losing late complete lineage."""

    if resolution.observation_id is None:
        return None
    reason_codes = set(resolution.reason_codes)
    if "partial_series" in reason_codes:
        return "partial"
    if "rejected_observation" in reason_codes:
        return "rejected"
    if "withdrawn_observation" in reason_codes:
        return "withdrawn"
    return "complete"


def quote_resolution_payload(
    resolution: CanonicalQuoteResolution | CanonicalQuoteSeriesResolution,
    *,
    requested_as_of_date: date,
    source_status: str | None = None,
    window: CanonicalQuoteSeriesResolution | None = None,
    consumer_profile: ConsumerFreshnessProfile | None = None,
) -> dict[str, object]:
    """Render the canonical state without inventing values or dropping lineage."""

    is_point = isinstance(resolution, CanonicalQuoteResolution)
    value = resolution.value if is_point else None
    observation_date = resolution.observation_date if is_point else None
    dependency = resolution.calculation_dependency.model_dump(mode="json")
    return {
        "instrument_id": resolution.instrument_id,
        "role": resolution.role,
        "requested_as_of_date": requested_as_of_date,
        "resolution_status": resolution.resolution_status,
        "value": float(value) if isinstance(value, Decimal) else None,
        "as_of_date": observation_date,
        "metric_family": resolution.metric_family,
        "quote_basis": resolution.quote_basis,
        "currency": resolution.currency,
        "source_ref": resolution.source_ref if is_point else None,
        "source_status": source_status,
        "status": _coverage_status(resolution, source_status=source_status),
        "freshness_status": resolution.freshness_status,
        "ingestion_status": resolution.ingestion_status,
        "reliability_status": resolution.reliability_status,
        "reason_codes": list(resolution.reason_codes),
        "canonical_instrument_type": (
            consumer_profile.canonical_instrument_type
            if consumer_profile is not None
            else None
        ),
        "consumer_freshness_policy_type": (
            consumer_profile.policy_type if consumer_profile is not None else None
        ),
        "consumer_freshness_policy_version": (
            consumer_profile.policy_version if consumer_profile is not None else None
        ),
        "carry_forward": bool(resolution.carry_forward) if is_point else False,
        "stale": resolution.freshness_status == "late",
        "age_days": resolution.age_days if is_point else None,
        "quote_selection_policy_version": resolution.quote_selection_policy_version,
        "quote_selection_policy_revision": resolution.quote_selection_policy_revision,
        "quote_series_id": resolution.quote_series_id,
        "observation_id": resolution.observation_id if is_point else None,
        "revision_id": resolution.revision_id if is_point else None,
        "revision_number": resolution.revision_number if is_point else None,
        "payload_hash": resolution.payload_hash if is_point else None,
        "source_published_at": resolution.source_published_at if is_point else None,
        "ingested_at": resolution.ingested_at if is_point else None,
        "calculation_dependency": dependency,
        "window_calculation_dependency": (
            window.calculation_dependency.model_dump(mode="json")
            if window is not None
            else None
        ),
        "window_coverage_status": window.coverage_status if window is not None else None,
        "window_reason_codes": list(window.reason_codes) if window is not None else [],
    }


@dataclass(frozen=True)
class CanonicalQuoteWindowBook:
    role: QuoteRole
    start_date: date
    end_date: date
    windows: dict[str, CanonicalQuoteSeriesResolution]
    consumer_profiles: dict[str, ConsumerFreshnessProfile]
    missing_instrument_ids: frozenset[str]

    def quote_at(self, instrument_id: str, as_of_date: date) -> dict[str, object] | None:
        normalized_id = str(instrument_id or "").strip()
        window = self.windows.get(normalized_id)
        consumer_profile = self.consumer_profiles.get(normalized_id)
        if window is None:
            return None
        if window.quote_series_id is None:
            return quote_resolution_payload(
                window,
                requested_as_of_date=as_of_date,
                window=window,
                consumer_profile=consumer_profile,
            )
        resolution = resolve_quote_series_observation_at(
            window,
            requested_as_of_date=as_of_date,
        )
        return quote_resolution_payload(
            resolution,
            requested_as_of_date=as_of_date,
            source_status=_source_status_at(window, as_of_date),
            window=window,
            consumer_profile=consumer_profile,
        )

    def previous_quote(self, instrument_id: str, selected: dict[str, object]) -> dict[str, object] | None:
        observation_date = selected.get("as_of_date")
        if not isinstance(observation_date, date):
            return None
        normalized_id = str(instrument_id or "").strip()
        window = self.windows.get(normalized_id)
        if window is None or window.quote_series_id is None:
            return None
        previous_points = [
            point
            for point in window.points
            if point.observation_date < observation_date
        ]
        previous_point = max(
            previous_points,
            key=lambda point: point.observation_date,
            default=None,
        )
        if previous_point is None:
            return None
        resolution = resolve_quote_series_observation_at(
            window,
            requested_as_of_date=previous_point.observation_date,
        )
        if resolution.resolution_status != "resolved":
            return None
        return quote_resolution_payload(
            resolution,
            requested_as_of_date=previous_point.observation_date,
            source_status="complete",
            window=window,
            consumer_profile=self.consumer_profiles.get(normalized_id),
        )


def resolve_role_quote_window_book_in_session(
    session: Session,
    *,
    instrument_ids: Iterable[str],
    role: QuoteRole,
    start_date: date,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy | None = None,
) -> CanonicalQuoteWindowBook:
    return resolve_quote_window_books_in_session(
        session,
        instrument_ids=instrument_ids,
        roles=[role],
        start_date=start_date,
        end_date=end_date,
        freshness_policies={role: freshness_policy},
    )[role]


def resolve_quote_window_books_in_session(
    session: Session,
    *,
    instrument_ids: Iterable[str],
    roles: Iterable[QuoteRole],
    start_date: date,
    end_date: date,
    freshness_policies: Mapping[QuoteRole, QuoteFreshnessPolicy | None] | None = None,
) -> dict[QuoteRole, CanonicalQuoteWindowBook]:
    """Resolve multiple semantic roles while retaining one canonical identity map.

    Holdings need valuation and total-return windows together.  Loading their
    instrument identities once keeps ORM references alive for every resolver
    call and makes query growth depend on roles/instruments, never calendar
    days.
    """

    normalized_ids = _normalized_ids(instrument_ids)
    instruments = _canonical_instruments(session, normalized_ids)
    normalized_roles = list(dict.fromkeys(roles))
    policies = freshness_policies or {}
    books: dict[QuoteRole, CanonicalQuoteWindowBook] = {}
    for role in normalized_roles:
        profiles = {
            instrument_id: _consumer_profile(
                role=role,
                instrument_type=str(instrument.instrument_type),
                explicit_policy=policies.get(role),
            )
            for instrument_id, instrument in instruments.items()
        }
        windows = {
            instrument_id: resolve_role_quote_series_in_session(
                session,
                resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
                quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
                instrument_id=instrument_id,
                role=role,
                currency=str(instruments[instrument_id].currency).strip().upper(),
                range_mode="bounded",
                start_date=start_date,
                end_date=end_date,
                freshness_policy=profiles[instrument_id].resolver_policy,
            )
            for instrument_id in normalized_ids
            if instrument_id in instruments
        }
        books[role] = CanonicalQuoteWindowBook(
            role=role,
            start_date=start_date,
            end_date=end_date,
            windows=windows,
            consumer_profiles=profiles,
            missing_instrument_ids=frozenset(
                set(normalized_ids).difference(instruments)
            ),
        )
    return books


def resolve_role_quotes_on_date_in_session(
    session: Session,
    *,
    instrument_ids: Iterable[str],
    role: QuoteRole,
    as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | None = None,
) -> dict[str, dict[str, object]]:
    normalized_ids = _normalized_ids(instrument_ids)
    instruments = _canonical_instruments(session, normalized_ids)
    payloads: dict[str, dict[str, object]] = {}
    for instrument_id in normalized_ids:
        instrument = instruments.get(instrument_id)
        if instrument is None:
            continue
        profile = _consumer_profile(
            role=role,
            instrument_type=str(instrument.instrument_type),
            explicit_policy=freshness_policy,
        )
        resolution = resolve_role_quote_in_session(
            session,
            resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
            quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
            instrument_id=instrument_id,
            role=role,
            currency=str(instrument.currency).strip().upper(),
            requested_as_of_date=as_of_date,
            freshness_policy=profile.resolver_policy,
        )
        payloads[instrument_id] = quote_resolution_payload(
            resolution,
            requested_as_of_date=as_of_date,
            source_status=_point_source_status(resolution),
            consumer_profile=profile,
        )
    return payloads


def resolve_single_role_quote_in_session(
    session: Session,
    *,
    instrument_id: str,
    role: QuoteRole,
    as_of_date: date,
) -> dict[str, object] | None:
    return resolve_role_quotes_on_date_in_session(
        session,
        instrument_ids=[instrument_id],
        role=role,
        as_of_date=as_of_date,
    ).get(str(instrument_id or "").strip())


__all__ = [
    "CanonicalQuoteWindowBook",
    "ConsumerFreshnessProfile",
    "PORTFOLIO_QUOTE_CONSUMER_POLICY_VERSION",
    "QuoteResolverError",
    "maximum_valuation_quote_age_days",
    "quote_resolution_payload",
    "resolve_quote_window_books_in_session",
    "resolve_role_quote_window_book_in_session",
    "resolve_role_quotes_on_date_in_session",
    "resolve_single_role_quote_in_session",
    "trading_freshness_policy",
    "valuation_freshness_profile",
    "valuation_freshness_policy",
]
