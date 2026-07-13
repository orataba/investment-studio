from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from portfolio_ops_instrument_core.db_models import (
    Instrument,
    QuoteObservation,
    QuoteObservationRevision,
    QuoteSeries,
)
from portfolio_ops_instrument_core.models import (
    CASH_CUMULATIVE_NAV_BASES,
    VALUATION_PROHIBITED_TOTAL_RETURN_BASES,
    CanonicalQuoteCalculationDependency,
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesCalculationDependency,
    CanonicalQuoteSeriesObservation,
    CanonicalQuoteSeriesPoint,
    CanonicalQuoteSeriesResolution,
    QuoteFreshnessPolicy,
    TOTAL_RETURN_QUOTE_BASES,
)
from portfolio_ops_instrument_core.quote_revisions import (
    USABLE_CURRENT_STATUS,
    VALID_METRIC_FAMILIES,
    VALID_QUOTE_BASES,
)


CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION = "canonical_quote_resolver.v1"
CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION = "canonical_quote_freshness.v1"
QUOTE_SELECTION_POLICY_VERSION = "quote_selection_policy.v1"
QUOTE_POLICY_ROLES = ("trading", "valuation", "total_return", "chart", "reference")
MAX_CALENDAR_CARRY_FORWARD_DAYS = 366

SessionFactory = Callable[[], Session]


class QuoteResolverError(ValueError):
    """Fail-closed resolver configuration or canonical-data invariant error."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class QuoteSeriesDescriptor:
    quote_series_id: str
    instrument_id: str
    metric_family: str
    quote_basis: str
    currency: str


@dataclass(frozen=True)
class QuoteRevisionCandidate:
    quote_series_id: str
    observation_id: str
    revision_id: str
    revision_number: int
    payload_hash: str
    value: Decimal | None
    status: str
    observation_date: date
    source_ref: str | None
    source_published_at: datetime | None
    ingested_at: datetime | None


def _stable_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_resolver_strategy_version(version: object) -> str:
    normalized = str(version or "").strip()
    if normalized != CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION:
        raise QuoteResolverError(
            "unknown_resolver_strategy",
            f'Unsupported canonical quote resolver strategy "{normalized}".',
        )
    return normalized


def validate_freshness_policy(
    policy: QuoteFreshnessPolicy | Mapping[str, object],
) -> QuoteFreshnessPolicy:
    raw_policy = (
        policy.model_dump() if isinstance(policy, QuoteFreshnessPolicy) else dict(policy)
    )
    raw_version = str(raw_policy.get("policy_version") or "").strip()
    if raw_version != CANONICAL_QUOTE_FRESHNESS_POLICY_VERSION:
        raise QuoteResolverError(
            "unknown_freshness_policy",
            f'Unsupported freshness policy version "{raw_version}".',
        )
    try:
        resolved = (
            policy
            if isinstance(policy, QuoteFreshnessPolicy)
            else QuoteFreshnessPolicy.model_validate(raw_policy)
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise QuoteResolverError(
            "invalid_freshness_policy",
            "Freshness policy does not match the canonical contract.",
        ) from error
    if resolved.mode == "exact_only" and resolved.max_age_days != 0:
        raise QuoteResolverError(
            "invalid_freshness_policy",
            "exact_only requires max_age_days=0.",
        )
    if resolved.mode == "calendar_day_carry_forward" and not (
        1 <= resolved.max_age_days <= MAX_CALENDAR_CARRY_FORWARD_DAYS
    ):
        raise QuoteResolverError(
            "invalid_freshness_policy",
            "calendar_day_carry_forward requires max_age_days between 1 and 366.",
        )
    return resolved


def _normalize_explicit_identity(
    *,
    instrument_id: object,
    metric_family: object,
    quote_basis: object,
    currency: object,
) -> tuple[str, str, str, str]:
    normalized_instrument_id = str(instrument_id or "").strip()
    normalized_metric_family = str(metric_family or "").strip().lower()
    normalized_quote_basis = str(quote_basis or "").strip().lower()
    normalized_currency = str(currency or "").strip().upper()
    if (
        not normalized_instrument_id
        or normalized_metric_family not in VALID_METRIC_FAMILIES
        or normalized_quote_basis not in VALID_QUOTE_BASES
        or VALID_QUOTE_BASES.get(normalized_quote_basis) != normalized_metric_family
        or not normalized_currency
        or len(normalized_currency) > 8
    ):
        raise QuoteResolverError(
            "invalid_quote_identity",
            "Canonical quote identity requires a valid instrument, metric, basis, and currency.",
        )
    return (
        normalized_instrument_id,
        normalized_metric_family,
        normalized_quote_basis,
        normalized_currency,
    )


def canonicalize_quote_selection_policy(
    policy: Mapping[str, object],
) -> dict[str, list[str]]:
    if not isinstance(policy, Mapping):
        raise QuoteResolverError(
            "missing_quote_policy",
            "Quote selection policy must be an object.",
        )
    normalized: dict[str, list[str]] = {}
    for role in QUOTE_POLICY_ROLES:
        if role not in policy:
            raise QuoteResolverError(
                "invalid_quote_selection_policy",
                f'quote_selection_policy must explicitly define role "{role}".',
            )
        raw_values = policy.get(role)
        if not isinstance(raw_values, list):
            raise QuoteResolverError(
                "invalid_quote_selection_policy",
                f"quote_selection_policy.{role} must be a list.",
            )
        bases: list[str] = []
        for raw_value in raw_values:
            basis = str(raw_value or "").strip().lower()
            if basis not in VALID_QUOTE_BASES:
                raise QuoteResolverError(
                    "invalid_quote_selection_policy",
                    f'Unsupported quote basis "{basis}" in role "{role}".',
                )
            if basis in bases:
                raise QuoteResolverError(
                    "invalid_quote_selection_policy",
                    f'Duplicate quote basis "{basis}" in role "{role}".',
                )
            bases.append(basis)
        normalized[role] = bases

    prohibited_valuation = sorted(
        set(normalized["valuation"]).intersection(
            VALUATION_PROHIBITED_TOTAL_RETURN_BASES
        )
    )
    if prohibited_valuation:
        raise QuoteResolverError(
            "invalid_quote_selection_policy",
            "Valuation policy contains prohibited total-return bases: "
            + ", ".join(prohibited_valuation),
        )
    for role in ("total_return", "chart"):
        invalid_cumulative = sorted(
            set(normalized[role]).intersection(CASH_CUMULATIVE_NAV_BASES)
        )
        if invalid_cumulative:
            raise QuoteResolverError(
                "invalid_quote_selection_policy",
                f"{role} policy contains cash-cumulative NAV bases: "
                + ", ".join(invalid_cumulative),
            )
    invalid_total_return = sorted(
        set(normalized["total_return"]).difference(TOTAL_RETURN_QUOTE_BASES)
    )
    if invalid_total_return:
        raise QuoteResolverError(
            "invalid_quote_selection_policy",
            "total_return role contains non-total-return quote bases: "
            + ", ".join(invalid_total_return),
        )
    return normalized


def canonical_quote_selection_policy_revision(
    policy: Mapping[str, object],
) -> str:
    normalized = canonicalize_quote_selection_policy(policy)
    return _stable_hash(
        {
            "policy_version": QUOTE_SELECTION_POLICY_VERSION,
            "roles": normalized,
        }
    )


def validate_quote_selection_policy_version(version: object) -> str:
    normalized = str(version or "").strip()
    if normalized != QUOTE_SELECTION_POLICY_VERSION:
        raise QuoteResolverError(
            "unknown_quote_selection_policy",
            f'Unsupported quote selection policy version "{normalized}".',
        )
    return normalized


def _dependency(
    *,
    resolution_kind: str,
    resolver_strategy_version: str,
    freshness_policy: QuoteFreshnessPolicy,
    instrument_id: str,
    role: str | None,
    metric_family: str | None,
    quote_basis: str | None,
    currency: str,
    requested_as_of_date: date,
    quote_selection_policy_version: str | None,
    quote_selection_policy_revision: str | None,
    series: QuoteSeriesDescriptor | None,
    candidate: QuoteRevisionCandidate | None,
    reason_codes: Sequence[str],
) -> CanonicalQuoteCalculationDependency:
    payload = {
        "resolution_kind": resolution_kind,
        "resolver_strategy_version": resolver_strategy_version,
        "freshness_policy": freshness_policy.model_dump(mode="json"),
        "instrument_id": instrument_id,
        "role": role,
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "currency": currency,
        "requested_as_of_date": requested_as_of_date.isoformat(),
        "quote_selection_policy_version": quote_selection_policy_version,
        "quote_selection_policy_revision": quote_selection_policy_revision,
        "quote_series_id": series.quote_series_id if series else None,
        "observation_id": candidate.observation_id if candidate else None,
        "revision_id": candidate.revision_id if candidate else None,
        "payload_hash": candidate.payload_hash if candidate else None,
        "reason_codes": list(reason_codes),
    }
    return CanonicalQuoteCalculationDependency(
        resolver_strategy_version=resolver_strategy_version,
        freshness_policy_version=freshness_policy.policy_version,
        freshness_mode=freshness_policy.mode,
        max_age_days=freshness_policy.max_age_days,
        quote_selection_policy_version=quote_selection_policy_version,
        quote_selection_policy_revision=quote_selection_policy_revision,
        quote_series_id=series.quote_series_id if series else None,
        observation_id=candidate.observation_id if candidate else None,
        revision_id=candidate.revision_id if candidate else None,
        payload_hash=candidate.payload_hash if candidate else None,
        fingerprint=_stable_hash(payload),
    )


def resolve_explicit_quote_candidate(
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    metric_family: object,
    quote_basis: object,
    currency: object,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    series: QuoteSeriesDescriptor | None,
    candidate: QuoteRevisionCandidate | None,
    missing_series_reason: str = "missing_quote_series",
    resolution_kind: str = "explicit_series",
    role: str | None = None,
    quote_selection_policy_version: str | None = None,
    quote_selection_policy_revision: str | None = None,
) -> CanonicalQuoteResolution:
    strategy_version = validate_resolver_strategy_version(resolver_strategy_version)
    resolved_freshness = validate_freshness_policy(freshness_policy)
    (
        resolved_instrument_id,
        resolved_metric_family,
        resolved_quote_basis,
        resolved_currency,
    ) = _normalize_explicit_identity(
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        currency=currency,
    )
    if not isinstance(requested_as_of_date, date):
        raise QuoteResolverError(
            "invalid_as_of_date",
            "requested_as_of_date must be a date.",
        )
    if series is not None and (
        series.instrument_id != resolved_instrument_id
        or series.metric_family != resolved_metric_family
        or series.quote_basis != resolved_quote_basis
        or series.currency != resolved_currency
    ):
        raise QuoteResolverError(
            "invalid_quote_identity",
            "Resolved series does not match the complete requested identity.",
        )

    reason_codes: list[str]
    resolution_status: str
    freshness_status: str
    reliability_status: str
    carry_forward = False
    age_days: int | None = None
    value: Decimal | None = None
    if series is None:
        candidate = None
        resolution_status = "unavailable"
        freshness_status = "missing"
        reliability_status = "unavailable"
        reason_codes = [missing_series_reason]
    elif candidate is None:
        resolution_status = "unavailable"
        freshness_status = "missing"
        reliability_status = "unavailable"
        reason_codes = ["missing_observation"]
    else:
        if candidate.quote_series_id != series.quote_series_id:
            raise QuoteResolverError(
                "invalid_quote_identity",
                "Observation candidate belongs to a different quote series.",
            )
        age_days = (requested_as_of_date - candidate.observation_date).days
        if age_days < 0:
            raise QuoteResolverError(
                "invalid_as_of_date",
                "Resolver candidate cannot be after requested_as_of_date.",
            )
        if candidate.status != USABLE_CURRENT_STATUS or candidate.value is None:
            resolution_status = "unavailable"
            freshness_status = "current" if age_days == 0 else "late"
            reliability_status = "unavailable"
            status_reason = {
                "partial": "partial_series",
                "rejected": "rejected_observation",
                "withdrawn": "withdrawn_observation",
            }.get(candidate.status, "missing_observation")
            reason_codes = [status_reason]
            if age_days > 0:
                reason_codes.insert(0, "late_observation")
            if candidate.ingested_at is None:
                reason_codes.append("unknown_ingestion_time")
        elif age_days == 0:
            resolution_status = "resolved"
            freshness_status = "current"
            reliability_status = "reliable"
            reason_codes = (
                [] if candidate.ingested_at is not None else ["unknown_ingestion_time"]
            )
            value = candidate.value
        elif (
            resolved_freshness.mode == "calendar_day_carry_forward"
            and age_days <= resolved_freshness.max_age_days
        ):
            resolution_status = "resolved"
            freshness_status = "current"
            reliability_status = "qualified"
            carry_forward = True
            reason_codes = ["carried_forward_observation"]
            if candidate.ingested_at is None:
                reason_codes.append("unknown_ingestion_time")
            value = candidate.value
        else:
            resolution_status = "unavailable"
            freshness_status = "late"
            reliability_status = "unavailable"
            reason_codes = ["late_observation", "freshness_limit_exceeded"]
            if candidate.ingested_at is None:
                reason_codes.append("unknown_ingestion_time")

    dependency = _dependency(
        resolution_kind=resolution_kind,
        resolver_strategy_version=strategy_version,
        freshness_policy=resolved_freshness,
        instrument_id=resolved_instrument_id,
        role=role,
        metric_family=resolved_metric_family,
        quote_basis=resolved_quote_basis,
        currency=resolved_currency,
        requested_as_of_date=requested_as_of_date,
        quote_selection_policy_version=quote_selection_policy_version,
        quote_selection_policy_revision=quote_selection_policy_revision,
        series=series,
        candidate=candidate,
        reason_codes=reason_codes,
    )
    return CanonicalQuoteResolution(
        resolution_kind=resolution_kind,
        resolution_status=resolution_status,
        resolver_strategy_version=strategy_version,
        instrument_id=resolved_instrument_id,
        role=role,
        metric_family=resolved_metric_family,
        quote_basis=resolved_quote_basis,
        currency=resolved_currency,
        requested_as_of_date=requested_as_of_date,
        quote_selection_policy_version=quote_selection_policy_version,
        quote_selection_policy_revision=quote_selection_policy_revision,
        freshness_policy=resolved_freshness,
        quote_series_id=series.quote_series_id if series else None,
        observation_id=candidate.observation_id if candidate else None,
        revision_id=candidate.revision_id if candidate else None,
        revision_number=candidate.revision_number if candidate else None,
        payload_hash=candidate.payload_hash if candidate else None,
        value=value,
        observation_date=candidate.observation_date if candidate else None,
        source_ref=candidate.source_ref if candidate else None,
        source_published_at=candidate.source_published_at if candidate else None,
        ingested_at=candidate.ingested_at if candidate else None,
        carry_forward=carry_forward,
        age_days=age_days,
        freshness_status=freshness_status,
        ingestion_status=(
            "current"
            if candidate is not None and candidate.ingested_at is not None
            else "unknown"
        ),
        reliability_status=reliability_status,
        reason_codes=reason_codes,
        calculation_dependency=dependency,
    )


def _series_descriptor(row: QuoteSeries) -> QuoteSeriesDescriptor:
    return QuoteSeriesDescriptor(
        quote_series_id=str(row.quote_series_id),
        instrument_id=str(row.instrument_id),
        metric_family=str(row.metric_family),
        quote_basis=str(row.quote_basis),
        currency=str(row.currency),
    )


def _canonical_target_for_alias(instrument: Instrument) -> str | None:
    lifecycle = instrument.lifecycle_state_json or {}
    if not isinstance(lifecycle, Mapping):
        return None
    canonical_id = str(lifecycle.get("canonical_instrument_id") or "").strip()
    if (
        str(lifecycle.get("status") or "active").strip().lower() == "archived"
        and canonical_id
        and canonical_id != instrument.instrument_id
    ):
        return canonical_id
    return None


def _latest_current_candidate(
    session: Session,
    *,
    quote_series_id: str,
    requested_as_of_date: date,
) -> QuoteRevisionCandidate | None:
    row = session.execute(
        select(
            QuoteObservation.observation_id,
            QuoteObservation.as_of_date,
            QuoteObservationRevision.revision_id,
            QuoteObservationRevision.revision_number,
            QuoteObservationRevision.value,
            QuoteObservationRevision.source_ref,
            QuoteObservationRevision.status,
            QuoteObservationRevision.source_published_at,
            QuoteObservationRevision.ingested_at,
            QuoteObservationRevision.payload_hash,
        )
        .join(
            QuoteObservationRevision,
            QuoteObservationRevision.observation_id == QuoteObservation.observation_id,
        )
        .where(
            QuoteObservation.quote_series_id == quote_series_id,
            QuoteObservation.as_of_date <= requested_as_of_date,
            QuoteObservationRevision.is_current.is_(True),
        )
        .order_by(QuoteObservation.as_of_date.desc())
        .limit(1)
    ).mappings().first()
    if row is None:
        return None
    return QuoteRevisionCandidate(
        quote_series_id=quote_series_id,
        observation_id=str(row["observation_id"]),
        revision_id=str(row["revision_id"]),
        revision_number=int(row["revision_number"]),
        payload_hash=str(row["payload_hash"]),
        value=(
            Decimal(str(row["value"])) if row["value"] is not None else None
        ),
        status=str(row["status"]),
        observation_date=row["as_of_date"],
        source_ref=row["source_ref"],
        source_published_at=row["source_published_at"],
        ingested_at=row["ingested_at"],
    )


def _select_explicit_series(
    *,
    all_series: Sequence[QuoteSeriesDescriptor],
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    currency: str,
) -> tuple[QuoteSeriesDescriptor | None, str]:
    same_basis = [
        series
        for series in all_series
        if series.instrument_id == instrument_id
        and series.metric_family == metric_family
        and series.quote_basis == quote_basis
    ]
    exact = [series for series in same_basis if series.currency == currency]
    if len(exact) > 1:
        raise QuoteResolverError(
            "ambiguous_quote_series",
            "More than one quote series matches the complete canonical identity.",
        )
    if exact:
        return exact[0], "missing_quote_series"
    return None, "currency_mismatch" if same_basis else "missing_quote_series"


def resolve_explicit_quote(
    session_factory: SessionFactory,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    metric_family: object,
    quote_basis: object,
    currency: object,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
) -> CanonicalQuoteResolution:
    strategy_version = validate_resolver_strategy_version(resolver_strategy_version)
    resolved_freshness = validate_freshness_policy(freshness_policy)
    identity = _normalize_explicit_identity(
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        currency=currency,
    )
    with session_factory() as session:
        instrument = session.get(Instrument, identity[0])
        if instrument is None:
            return resolve_explicit_quote_candidate(
                resolver_strategy_version=strategy_version,
                instrument_id=identity[0],
                metric_family=identity[1],
                quote_basis=identity[2],
                currency=identity[3],
                requested_as_of_date=requested_as_of_date,
                freshness_policy=resolved_freshness,
                series=None,
                candidate=None,
                missing_series_reason="instrument_not_found",
            )
        if _canonical_target_for_alias(instrument) is not None:
            return resolve_explicit_quote_candidate(
                resolver_strategy_version=strategy_version,
                instrument_id=identity[0],
                metric_family=identity[1],
                quote_basis=identity[2],
                currency=identity[3],
                requested_as_of_date=requested_as_of_date,
                freshness_policy=resolved_freshness,
                series=None,
                candidate=None,
                missing_series_reason="non_canonical_instrument_id",
            )
        series_rows = session.scalars(
            select(QuoteSeries).where(
                QuoteSeries.instrument_id == identity[0],
                QuoteSeries.quote_basis == identity[2],
            )
        ).all()
        descriptors = [_series_descriptor(series) for series in series_rows]
        series, missing_reason = _select_explicit_series(
            all_series=descriptors,
            instrument_id=identity[0],
            metric_family=identity[1],
            quote_basis=identity[2],
            currency=identity[3],
        )
        candidate = (
            _latest_current_candidate(
                session,
                quote_series_id=series.quote_series_id,
                requested_as_of_date=requested_as_of_date,
            )
            if series is not None
            else None
        )
    return resolve_explicit_quote_candidate(
        resolver_strategy_version=strategy_version,
        instrument_id=identity[0],
        metric_family=identity[1],
        quote_basis=identity[2],
        currency=identity[3],
        requested_as_of_date=requested_as_of_date,
        freshness_policy=resolved_freshness,
        series=series,
        candidate=candidate,
        missing_series_reason=missing_reason,
    )


def resolve_explicit_quote_in_session(
    session: Session,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    metric_family: object,
    quote_basis: object,
    currency: object,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
) -> CanonicalQuoteResolution:
    """Resolve inside the caller's UoW without opening or closing a transaction."""
    return resolve_explicit_quote(
        lambda: nullcontext(session),  # type: ignore[arg-type]
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=freshness_policy,
    )


def _normalize_role(role: object) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in QUOTE_POLICY_ROLES:
        raise QuoteResolverError(
            "unknown_quote_role",
            f'Unsupported quote role "{normalized}".',
        )
    return normalized


def select_role_quote_series(
    *,
    quote_selection_policy: Mapping[str, object],
    quote_selection_policy_version: object,
    role: object,
    instrument_id: str,
    currency: str,
    available_series: Sequence[QuoteSeriesDescriptor],
) -> tuple[QuoteSeriesDescriptor | None, str, dict[str, list[str]], str]:
    validate_quote_selection_policy_version(quote_selection_policy_version)
    normalized_role = _normalize_role(role)
    normalized_policy = canonicalize_quote_selection_policy(quote_selection_policy)
    bases = normalized_policy[normalized_role]
    policy_revision = canonical_quote_selection_policy_revision(normalized_policy)
    if not bases:
        return (
            None,
            "missing_quote_policy",
            normalized_policy,
            policy_revision,
        )
    saw_other_currency = False
    for basis in bases:
        expected_metric = VALID_QUOTE_BASES[basis]
        basis_series = [
            series
            for series in available_series
            if series.instrument_id == instrument_id and series.quote_basis == basis
        ]
        if any(series.metric_family != expected_metric for series in basis_series):
            raise QuoteResolverError(
                "invalid_quote_identity",
                f'Canonical series for basis "{basis}" has the wrong metric family.',
            )
        exact = [series for series in basis_series if series.currency == currency]
        if len(exact) > 1:
            raise QuoteResolverError(
                "ambiguous_quote_series",
                f'More than one canonical series matches role basis "{basis}".',
            )
        if exact:
            # The series identity is selected before any observation lookup.
            # Missing tail observations must not switch the role to another basis.
            return exact[0], "missing_quote_series", normalized_policy, policy_revision
        saw_other_currency = saw_other_currency or bool(basis_series)
    return (
        None,
        "currency_mismatch" if saw_other_currency else "missing_quote_series",
        normalized_policy,
        policy_revision,
    )


def _missing_role_resolution(
    *,
    resolver_strategy_version: str,
    instrument_id: str,
    role: str,
    currency: str,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy,
    policy_revision: str | None,
    reason_codes: list[str],
) -> CanonicalQuoteResolution:
    dependency = _dependency(
        resolution_kind="quote_role",
        resolver_strategy_version=resolver_strategy_version,
        freshness_policy=freshness_policy,
        instrument_id=instrument_id,
        role=role,
        metric_family=None,
        quote_basis=None,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
        series=None,
        candidate=None,
        reason_codes=reason_codes,
    )
    return CanonicalQuoteResolution(
        resolution_kind="quote_role",
        resolution_status="unavailable",
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        role=role,
        metric_family=None,
        quote_basis=None,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
        freshness_policy=freshness_policy,
        carry_forward=False,
        freshness_status="missing",
        ingestion_status="unknown",
        reliability_status="unavailable",
        reason_codes=reason_codes,
        calculation_dependency=dependency,
    )


def resolve_role_quote(
    session_factory: SessionFactory,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    role: object,
    currency: object,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    quote_selection_policy_version: object,
) -> CanonicalQuoteResolution:
    strategy_version = validate_resolver_strategy_version(resolver_strategy_version)
    resolved_freshness = validate_freshness_policy(freshness_policy)
    validate_quote_selection_policy_version(quote_selection_policy_version)
    normalized_role = _normalize_role(role)
    normalized_instrument_id = str(instrument_id or "").strip()
    normalized_currency = str(currency or "").strip().upper()
    if not normalized_instrument_id or not normalized_currency or len(normalized_currency) > 8:
        raise QuoteResolverError(
            "invalid_quote_identity",
            "Role resolution requires an instrument and currency.",
        )
    with session_factory() as session:
        instrument = session.get(Instrument, normalized_instrument_id)
        if instrument is None:
            return _missing_role_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                requested_as_of_date=requested_as_of_date,
                freshness_policy=resolved_freshness,
                policy_revision=None,
                reason_codes=["instrument_not_found"],
            )
        if _canonical_target_for_alias(instrument) is not None:
            return _missing_role_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                requested_as_of_date=requested_as_of_date,
                freshness_policy=resolved_freshness,
                policy_revision=None,
                reason_codes=["non_canonical_instrument_id"],
            )
        normalized_policy = canonicalize_quote_selection_policy(
            instrument.quote_selection_policy_json or {}
        )
        role_bases = normalized_policy[normalized_role]
        rows = session.scalars(
            select(QuoteSeries).where(
                QuoteSeries.instrument_id == normalized_instrument_id,
                QuoteSeries.quote_basis.in_(role_bases),
            )
        ).all()
        descriptors = [_series_descriptor(series) for series in rows]
        selected, missing_reason, _, policy_revision = select_role_quote_series(
            quote_selection_policy=normalized_policy,
            quote_selection_policy_version=quote_selection_policy_version,
            role=normalized_role,
            instrument_id=normalized_instrument_id,
            currency=normalized_currency,
            available_series=descriptors,
        )
        if selected is None:
            return _missing_role_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                requested_as_of_date=requested_as_of_date,
                freshness_policy=resolved_freshness,
                policy_revision=policy_revision,
                reason_codes=[missing_reason],
            )
        candidate = _latest_current_candidate(
            session,
            quote_series_id=selected.quote_series_id,
            requested_as_of_date=requested_as_of_date,
        )
    return resolve_explicit_quote_candidate(
        resolver_strategy_version=strategy_version,
        instrument_id=selected.instrument_id,
        metric_family=selected.metric_family,
        quote_basis=selected.quote_basis,
        currency=selected.currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=resolved_freshness,
        series=selected,
        candidate=candidate,
        resolution_kind="quote_role",
        role=normalized_role,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
    )


def resolve_role_quote_in_session(
    session: Session,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    role: object,
    currency: object,
    requested_as_of_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    quote_selection_policy_version: object,
) -> CanonicalQuoteResolution:
    """Resolve a role against uncommitted state already present in ``session``."""
    return resolve_role_quote(
        lambda: nullcontext(session),  # type: ignore[arg-type]
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        role=role,
        currency=currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=freshness_policy,
        quote_selection_policy_version=quote_selection_policy_version,
    )


def _current_candidates_for_window(
    session: Session,
    *,
    quote_series_id: str,
    start_date: date | None,
    end_date: date,
) -> list[QuoteRevisionCandidate]:
    statement = (
        select(
            QuoteObservation.observation_id,
            QuoteObservation.as_of_date,
            QuoteObservationRevision.revision_id,
            QuoteObservationRevision.revision_number,
            QuoteObservationRevision.value,
            QuoteObservationRevision.source_ref,
            QuoteObservationRevision.status,
            QuoteObservationRevision.source_published_at,
            QuoteObservationRevision.ingested_at,
            QuoteObservationRevision.payload_hash,
        )
        .join(
            QuoteObservationRevision,
            QuoteObservationRevision.observation_id == QuoteObservation.observation_id,
        )
        .where(
            QuoteObservation.quote_series_id == quote_series_id,
            QuoteObservation.as_of_date <= end_date,
            QuoteObservationRevision.is_current.is_(True),
        )
        .order_by(QuoteObservation.as_of_date)
    )
    if start_date is not None:
        anchor_observation = aliased(QuoteObservation)
        anchor_revision = aliased(QuoteObservationRevision)
        anchor_date = (
            select(func.max(anchor_observation.as_of_date))
            .join(
                anchor_revision,
                anchor_revision.observation_id
                == anchor_observation.observation_id,
            )
            .where(
                anchor_observation.quote_series_id == quote_series_id,
                anchor_observation.as_of_date <= start_date,
                anchor_revision.is_current.is_(True),
            )
            .scalar_subquery()
        )
        statement = statement.where(
            or_(
                QuoteObservation.as_of_date >= start_date,
                QuoteObservation.as_of_date == anchor_date,
            )
        )
    rows = session.execute(statement).mappings()
    return [
        QuoteRevisionCandidate(
            quote_series_id=quote_series_id,
            observation_id=str(row["observation_id"]),
            revision_id=str(row["revision_id"]),
            revision_number=int(row["revision_number"]),
            payload_hash=str(row["payload_hash"]),
            value=(
                Decimal(str(row["value"]))
                if row["value"] is not None
                else None
            ),
            status=str(row["status"]),
            observation_date=row["as_of_date"],
            source_ref=row["source_ref"],
            source_published_at=row["source_published_at"],
            ingested_at=row["ingested_at"],
        )
        for row in rows
    ]


def _series_point(candidate: QuoteRevisionCandidate) -> CanonicalQuoteSeriesPoint:
    if candidate.status != USABLE_CURRENT_STATUS or candidate.value is None:
        raise QuoteResolverError(
            "invalid_quote_identity",
            "Only current complete observations can enter a resolved series window.",
        )
    return CanonicalQuoteSeriesPoint(
        quote_series_id=candidate.quote_series_id,
        observation_id=candidate.observation_id,
        revision_id=candidate.revision_id,
        revision_number=candidate.revision_number,
        payload_hash=candidate.payload_hash,
        observation_date=candidate.observation_date,
        value=candidate.value,
        source_ref=candidate.source_ref,
        source_published_at=candidate.source_published_at,
        ingested_at=candidate.ingested_at,
    )


def _series_observation(
    candidate: QuoteRevisionCandidate,
) -> CanonicalQuoteSeriesObservation:
    return CanonicalQuoteSeriesObservation(
        quote_series_id=candidate.quote_series_id,
        observation_id=candidate.observation_id,
        revision_id=candidate.revision_id,
        revision_number=candidate.revision_number,
        payload_hash=candidate.payload_hash,
        observation_date=candidate.observation_date,
        value=candidate.value,
        status=candidate.status,
        source_ref=candidate.source_ref,
        source_published_at=candidate.source_published_at,
        ingested_at=candidate.ingested_at,
    )


def _series_dependency(
    *,
    resolver_strategy_version: str,
    freshness_policy: QuoteFreshnessPolicy,
    policy_revision: str | None,
    instrument_id: str,
    role: str,
    currency: str,
    range_mode: str,
    start_date: date | None,
    end_date: date,
    series: QuoteSeriesDescriptor | None,
    candidates: Sequence[QuoteRevisionCandidate],
    excluded_candidates: Sequence[QuoteRevisionCandidate],
    reason_codes: Sequence[str],
) -> CanonicalQuoteSeriesCalculationDependency:
    revision_ids: list[str] = []
    payload_hashes: list[str] = []
    for candidate in candidates:
        if candidate.revision_id in revision_ids:
            continue
        revision_ids.append(candidate.revision_id)
        payload_hashes.append(candidate.payload_hash)
    excluded_revision_ids = [
        candidate.revision_id for candidate in excluded_candidates
    ]
    excluded_payload_hashes = [
        candidate.payload_hash for candidate in excluded_candidates
    ]
    payload = {
        "resolver_strategy_version": resolver_strategy_version,
        "freshness_policy": freshness_policy.model_dump(mode="json"),
        "quote_selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
        "quote_selection_policy_revision": policy_revision,
        "instrument_id": instrument_id,
        "role": role,
        "currency": currency,
        "range_mode": range_mode,
        "start_date": start_date.isoformat() if start_date is not None else None,
        "end_date": end_date.isoformat(),
        "quote_series_id": series.quote_series_id if series else None,
        "revision_ids": revision_ids,
        "payload_hashes": payload_hashes,
        "excluded_revision_ids": excluded_revision_ids,
        "excluded_payload_hashes": excluded_payload_hashes,
        "reason_codes": list(reason_codes),
    }
    return CanonicalQuoteSeriesCalculationDependency(
        resolver_strategy_version=resolver_strategy_version,
        freshness_policy_version=freshness_policy.policy_version,
        freshness_mode=freshness_policy.mode,
        max_age_days=freshness_policy.max_age_days,
        range_mode=range_mode,
        start_date=start_date,
        end_date=end_date,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
        quote_series_id=series.quote_series_id if series else None,
        revision_ids=revision_ids,
        payload_hashes=payload_hashes,
        excluded_revision_ids=excluded_revision_ids,
        excluded_payload_hashes=excluded_payload_hashes,
        fingerprint=_stable_hash(payload),
    )


def _missing_role_series_resolution(
    *,
    resolver_strategy_version: str,
    instrument_id: str,
    role: str,
    currency: str,
    range_mode: str,
    start_date: date | None,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy,
    policy_revision: str | None,
    reason_codes: list[str],
) -> CanonicalQuoteSeriesResolution:
    dependency = _series_dependency(
        resolver_strategy_version=resolver_strategy_version,
        freshness_policy=freshness_policy,
        policy_revision=policy_revision,
        instrument_id=instrument_id,
        role=role,
        currency=currency,
        range_mode=range_mode,
        start_date=start_date,
        end_date=end_date,
        series=None,
        candidates=[],
        excluded_candidates=[],
        reason_codes=reason_codes,
    )
    return CanonicalQuoteSeriesResolution(
        resolution_status="unavailable",
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        role=role,
        currency=currency,
        range_mode=range_mode,
        start_date=start_date,
        end_date=end_date,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
        freshness_policy=freshness_policy,
        observation_count=0,
        adopted_point_count=0,
        coverage_status="unavailable",
        freshness_status="missing",
        ingestion_status="unknown",
        reliability_status="unavailable",
        reason_codes=reason_codes,
        calculation_dependency=dependency,
    )


def resolve_role_quote_series(
    session_factory: SessionFactory,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    role: object,
    currency: object,
    range_mode: object,
    start_date: date | None,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    quote_selection_policy_version: object,
) -> CanonicalQuoteSeriesResolution:
    strategy_version = validate_resolver_strategy_version(resolver_strategy_version)
    resolved_freshness = validate_freshness_policy(freshness_policy)
    validate_quote_selection_policy_version(quote_selection_policy_version)
    normalized_role = _normalize_role(role)
    normalized_range_mode = str(range_mode or "").strip().lower()
    normalized_instrument_id = str(instrument_id or "").strip()
    normalized_currency = str(currency or "").strip().upper()
    if (
        not normalized_instrument_id
        or not normalized_currency
        or len(normalized_currency) > 8
        or normalized_range_mode not in {"bounded", "since_inception"}
        or (
            normalized_range_mode == "bounded"
            and not isinstance(start_date, date)
        )
        or (normalized_range_mode == "since_inception" and start_date is not None)
        or not isinstance(end_date, date)
        or (start_date is not None and start_date > end_date)
    ):
        raise QuoteResolverError(
            "invalid_quote_identity",
            "Role series resolution requires a valid identity and date window.",
        )

    with session_factory() as session:
        instrument = session.get(Instrument, normalized_instrument_id)
        if instrument is None:
            return _missing_role_series_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                range_mode=normalized_range_mode,
                start_date=start_date,
                end_date=end_date,
                freshness_policy=resolved_freshness,
                policy_revision=None,
                reason_codes=["instrument_not_found"],
            )
        if _canonical_target_for_alias(instrument) is not None:
            return _missing_role_series_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                range_mode=normalized_range_mode,
                start_date=start_date,
                end_date=end_date,
                freshness_policy=resolved_freshness,
                policy_revision=None,
                reason_codes=["non_canonical_instrument_id"],
            )
        normalized_policy = canonicalize_quote_selection_policy(
            instrument.quote_selection_policy_json or {}
        )
        role_bases = normalized_policy[normalized_role]
        series_rows = session.scalars(
            select(QuoteSeries).where(
                QuoteSeries.instrument_id == normalized_instrument_id,
                QuoteSeries.quote_basis.in_(role_bases),
            )
        ).all()
        selected, missing_reason, _, policy_revision = select_role_quote_series(
            quote_selection_policy=normalized_policy,
            quote_selection_policy_version=quote_selection_policy_version,
            role=normalized_role,
            instrument_id=normalized_instrument_id,
            currency=normalized_currency,
            available_series=[_series_descriptor(series) for series in series_rows],
        )
        if selected is None:
            return _missing_role_series_resolution(
                resolver_strategy_version=strategy_version,
                instrument_id=normalized_instrument_id,
                role=normalized_role,
                currency=normalized_currency,
                range_mode=normalized_range_mode,
                start_date=start_date,
                end_date=end_date,
                freshness_policy=resolved_freshness,
                policy_revision=policy_revision,
                reason_codes=[missing_reason],
            )

        loaded_candidates = _current_candidates_for_window(
            session,
            quote_series_id=selected.quote_series_id,
            start_date=start_date,
            end_date=end_date,
        )
        current_candidates = [
            candidate
            for candidate in loaded_candidates
            if start_date is None or candidate.observation_date >= start_date
        ]
        raw_anchor_candidate = (
            max(
                (
                    candidate
                    for candidate in loaded_candidates
                    if candidate.observation_date <= start_date
                ),
                key=lambda candidate: candidate.observation_date,
                default=None,
            )
            if start_date is not None
            else None
        )
        endpoint_candidate = max(
            loaded_candidates,
            key=lambda candidate: candidate.observation_date,
            default=None,
        )

    anchor_resolution = (
        resolve_explicit_quote_candidate(
            resolver_strategy_version=strategy_version,
            instrument_id=selected.instrument_id,
            metric_family=selected.metric_family,
            quote_basis=selected.quote_basis,
            currency=selected.currency,
            requested_as_of_date=start_date,
            freshness_policy=resolved_freshness,
            series=selected,
            candidate=raw_anchor_candidate,
            resolution_kind="quote_role",
            role=normalized_role,
            quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
            quote_selection_policy_revision=policy_revision,
        )
        if start_date is not None
        else None
    )
    endpoint_resolution = resolve_explicit_quote_candidate(
        resolver_strategy_version=strategy_version,
        instrument_id=selected.instrument_id,
        metric_family=selected.metric_family,
        quote_basis=selected.quote_basis,
        currency=selected.currency,
        requested_as_of_date=end_date,
        freshness_policy=resolved_freshness,
        series=selected,
        candidate=endpoint_candidate,
        resolution_kind="quote_role",
        role=normalized_role,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
    )
    usable_candidates = [
        candidate
        for candidate in current_candidates
        if candidate.status == USABLE_CURRENT_STATUS and candidate.value is not None
    ]
    excluded_candidates = [
        candidate
        for candidate in current_candidates
        if candidate.status != USABLE_CURRENT_STATUS or candidate.value is None
    ]
    points = [_series_point(candidate) for candidate in usable_candidates]
    observations = [
        _series_observation(candidate) for candidate in current_candidates
    ]
    dependency_candidates = [
        candidate
        for candidate in [raw_anchor_candidate, *current_candidates, endpoint_candidate]
        if candidate is not None
    ]
    reason_codes: list[str] = []

    def append_reason(reason_code: str) -> None:
        if reason_code not in reason_codes:
            reason_codes.append(reason_code)

    for reason_code in endpoint_resolution.reason_codes:
        append_reason(reason_code)
    anchor_is_usable = (
        anchor_resolution is None
        or anchor_resolution.resolution_status == "resolved"
    )
    if anchor_resolution is None:
        pass
    elif anchor_is_usable:
        for reason_code in anchor_resolution.reason_codes:
            append_reason(reason_code)
    else:
        for reason_code in anchor_resolution.reason_codes:
            if reason_code != "missing_observation":
                append_reason(reason_code)
        append_reason("missing_anchor")
    exclusion_reason_by_status = {
        "partial": "partial_series",
        "rejected": "rejected_observation",
        "withdrawn": "withdrawn_observation",
    }
    for excluded_candidate in excluded_candidates:
        append_reason(
            exclusion_reason_by_status.get(
                excluded_candidate.status,
                "missing_observation",
            )
        )

    if not points:
        append_reason("insufficient_history")
        resolution_status = "unavailable"
        coverage_status = "unavailable"
        freshness_status = "missing"
        reliability_status = "unavailable"
    elif endpoint_resolution.resolution_status == "unavailable":
        resolution_status = "unavailable"
        coverage_status = "partial"
        freshness_status = endpoint_resolution.freshness_status
        reliability_status = "unavailable"
    else:
        resolution_status = "resolved"
        freshness_status = endpoint_resolution.freshness_status
        if not anchor_is_usable or excluded_candidates:
            coverage_status = "partial"
            reliability_status = "qualified"
        else:
            coverage_status = "complete"
            reliability_status = (
                "qualified"
                if "qualified"
                in {
                    (
                        anchor_resolution.reliability_status
                        if anchor_resolution is not None
                        else "reliable"
                    ),
                    endpoint_resolution.reliability_status,
                }
                else "reliable"
            )

    if any(candidate.ingested_at is None for candidate in dependency_candidates):
        ingestion_status = "unknown"
        append_reason("unknown_ingestion_time")
    else:
        ingestion_status = "current" if dependency_candidates else "unknown"
    dependency = _series_dependency(
        resolver_strategy_version=strategy_version,
        freshness_policy=resolved_freshness,
        policy_revision=policy_revision,
        instrument_id=normalized_instrument_id,
        role=normalized_role,
        currency=normalized_currency,
        range_mode=normalized_range_mode,
        start_date=start_date,
        end_date=end_date,
        series=selected,
        candidates=dependency_candidates,
        excluded_candidates=excluded_candidates,
        reason_codes=reason_codes,
    )
    return CanonicalQuoteSeriesResolution(
        resolution_status=resolution_status,
        resolver_strategy_version=strategy_version,
        instrument_id=normalized_instrument_id,
        role=normalized_role,
        metric_family=selected.metric_family,
        quote_basis=selected.quote_basis,
        currency=normalized_currency,
        range_mode=normalized_range_mode,
        start_date=start_date,
        end_date=end_date,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        quote_selection_policy_revision=policy_revision,
        freshness_policy=resolved_freshness,
        quote_series_id=selected.quote_series_id,
        start_boundary_observation=(
            _series_observation(raw_anchor_candidate)
            if raw_anchor_candidate is not None and start_date is not None
            else None
        ),
        start_anchor=(
            _series_point(raw_anchor_candidate)
            if anchor_is_usable and raw_anchor_candidate is not None
            else None
        ),
        observations=observations,
        points=points,
        observation_count=len(observations),
        adopted_point_count=len(points),
        first_observation_date=(
            observations[0].observation_date if observations else None
        ),
        last_observation_date=(
            observations[-1].observation_date if observations else None
        ),
        coverage_status=coverage_status,
        freshness_status=freshness_status,
        ingestion_status=ingestion_status,
        reliability_status=reliability_status,
        reason_codes=reason_codes,
        calculation_dependency=dependency,
    )


def resolve_quote_series_observation_at(
    window: CanonicalQuoteSeriesResolution,
    *,
    requested_as_of_date: date,
) -> CanonicalQuoteResolution:
    """Resolve one date from a preloaded window without issuing another SQL query."""
    if not isinstance(requested_as_of_date, date):
        raise QuoteResolverError(
            "invalid_as_of_date",
            "requested_as_of_date must be a date.",
        )
    if (
        requested_as_of_date > window.end_date
        or (
            window.range_mode == "bounded"
            and window.start_date is not None
            and requested_as_of_date < window.start_date
        )
    ):
        raise QuoteResolverError(
            "invalid_as_of_date",
            "requested_as_of_date must be inside the preloaded series window.",
        )
    if (
        window.quote_series_id is None
        or window.metric_family is None
        or window.quote_basis is None
        or window.quote_selection_policy_revision is None
    ):
        raise QuoteResolverError(
            "missing_quote_series",
            "Cannot materialize a date from a window without a selected series.",
        )

    candidates_by_revision: dict[str, QuoteRevisionCandidate] = {}
    raw_observations = [
        observation
        for observation in [window.start_boundary_observation, *window.observations]
        if observation is not None
        and observation.observation_date <= requested_as_of_date
    ]
    for observation in raw_observations:
        candidates_by_revision[observation.revision_id] = QuoteRevisionCandidate(
            quote_series_id=observation.quote_series_id,
            observation_id=observation.observation_id,
            revision_id=observation.revision_id,
            revision_number=observation.revision_number,
            payload_hash=observation.payload_hash,
            value=observation.value,
            status=observation.status,
            observation_date=observation.observation_date,
            source_ref=observation.source_ref,
            source_published_at=observation.source_published_at,
            ingested_at=observation.ingested_at,
        )
    candidate = max(
        candidates_by_revision.values(),
        key=lambda item: (item.observation_date, item.revision_number),
        default=None,
    )
    series = QuoteSeriesDescriptor(
        quote_series_id=window.quote_series_id,
        instrument_id=window.instrument_id,
        metric_family=window.metric_family,
        quote_basis=window.quote_basis,
        currency=window.currency,
    )
    return resolve_explicit_quote_candidate(
        resolver_strategy_version=window.resolver_strategy_version,
        instrument_id=window.instrument_id,
        metric_family=window.metric_family,
        quote_basis=window.quote_basis,
        currency=window.currency,
        requested_as_of_date=requested_as_of_date,
        freshness_policy=window.freshness_policy,
        series=series,
        candidate=candidate,
        resolution_kind="quote_role",
        role=window.role,
        quote_selection_policy_version=window.quote_selection_policy_version,
        quote_selection_policy_revision=window.quote_selection_policy_revision,
    )


def resolve_role_quote_series_in_session(
    session: Session,
    *,
    resolver_strategy_version: object,
    instrument_id: object,
    role: object,
    currency: object,
    range_mode: object,
    start_date: date | None,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    quote_selection_policy_version: object,
) -> CanonicalQuoteSeriesResolution:
    """Resolve one locked series window inside the caller's existing UoW."""
    return resolve_role_quote_series(
        lambda: nullcontext(session),  # type: ignore[arg-type]
        resolver_strategy_version=resolver_strategy_version,
        instrument_id=instrument_id,
        role=role,
        currency=currency,
        range_mode=range_mode,
        start_date=start_date,
        end_date=end_date,
        freshness_policy=freshness_policy,
        quote_selection_policy_version=quote_selection_policy_version,
    )
