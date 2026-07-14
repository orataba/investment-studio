"""Freeze canonical per-day valuation quote decisions and exact revisions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.capture_common import (
    CommonCaptureResult,
    ManifestCaptureError,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    QUOTE_FRESHNESS_POLICY_VERSION,
    QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    VALUATION_QUOTE_CONSUMER_POLICY_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_quote_candidate,
    portfolio_daily_quote_window,
)
from portfolio_app.calculations.portfolio_daily.identifiers import (
    portfolio_daily_uuid,
)
from portfolio_ops_instrument_core.models import (
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesObservation,
    QuoteFreshnessPolicy,
)
from portfolio_ops_instrument_core.quote_resolver import (
    QuoteResolverError,
    resolve_quote_series_observation_at,
    resolve_role_quote_series_in_session,
)


def _calendar_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    if start_date > end_date:
        raise ManifestCaptureError("quote capture date range is inverted")
    return tuple(
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    )


def quote_window_id(
    *,
    portfolio_id: str,
    instrument_id: str,
    role: str,
    valuation_date: date,
) -> UUID:
    return portfolio_daily_uuid(
        "quote-window",
        portfolio_id,
        instrument_id,
        role,
        valuation_date.isoformat(),
    )


def _raw_observation_by_revision(
    observations: tuple[CanonicalQuoteSeriesObservation, ...],
) -> dict[str, CanonicalQuoteSeriesObservation]:
    return {observation.revision_id: observation for observation in observations}


def _reason_codes(values: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(set(values))


def _uuid(value: object, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ManifestCaptureError(
            f"canonical quote {field_name} is not a UUID: {value!r}"
        ) from error


def capture_quote_dependencies(
    session: Session,
    *,
    common: CommonCaptureResult,
) -> dict[str, list[dict[str, object]]]:
    window_rows: list[dict[str, object]] = []
    candidate_rows: list[dict[str, object]] = []
    days = _calendar_dates(common.range_start, common.range_end)
    zone = ZoneInfo(common.valuation_timezone)

    for instrument_id in common.instrument_ids_for_valuation:
        freshness = common.instrument_freshness[instrument_id]
        freshness_policy = QuoteFreshnessPolicy(
            policy_version=QUOTE_FRESHNESS_POLICY_VERSION,
            mode=freshness.mode,
            max_age_days=freshness.max_age_days,
        )
        series_window = resolve_role_quote_series_in_session(
            session,
            resolver_strategy_version=QUOTE_RESOLVER_STRATEGY_VERSION,
            instrument_id=instrument_id,
            role="valuation",
            currency=next(
                row["currency"]
                for row in common.rows_by_table["portfolio_daily_instrument_input"]
                if row["instrument_id"] == instrument_id
            ),
            range_mode="bounded",
            start_date=common.range_start,
            end_date=common.range_end,
            freshness_policy=freshness_policy,
            quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        )
        policy_revision = series_window.quote_selection_policy_revision
        if not policy_revision:
            raise ManifestCaptureError(
                f"valuation quote policy revision unavailable for {instrument_id}"
            )
        observations = tuple(
            observation
            for observation in (
                series_window.start_boundary_observation,
                *series_window.observations,
            )
            if observation is not None
        )
        raw_by_revision = _raw_observation_by_revision(observations)

        for valuation_date in days:
            resolution: CanonicalQuoteResolution | None = None
            if series_window.quote_series_id is not None:
                try:
                    resolution = resolve_quote_series_observation_at(
                        series_window,
                        requested_as_of_date=valuation_date,
                    )
                except QuoteResolverError as error:
                    raise ManifestCaptureError(
                        f"quote window replay failed for {instrument_id} "
                        f"on {valuation_date}: {error.reason_code}"
                    ) from error

            selected = (
                resolution is not None
                and resolution.resolution_status == "resolved"
                and resolution.revision_id is not None
            )
            reasons = _reason_codes(
                resolution.reason_codes
                if resolution is not None
                else series_window.reason_codes
            )
            candidate: CanonicalQuoteSeriesObservation | None = None
            if resolution is not None and resolution.revision_id is not None:
                candidate = raw_by_revision.get(resolution.revision_id)
                if candidate is None:
                    raise ManifestCaptureError(
                        "quote resolution revision is absent from its locked window: "
                        + resolution.revision_id
                    )
            resolved_window_id = quote_window_id(
                portfolio_id=common.portfolio_id,
                instrument_id=instrument_id,
                role="valuation",
                valuation_date=valuation_date,
            )
            window_start = datetime.combine(
                valuation_date - timedelta(days=freshness.max_age_days),
                time.min,
                tzinfo=zone,
            ).astimezone(UTC)
            window_end = datetime.combine(
                valuation_date,
                time(23, 59, 59, 999999),
                tzinfo=zone,
            ).astimezone(UTC)
            window_rows.append(
                {
                    "portfolio_id": common.portfolio_id,
                    "quote_window_id": resolved_window_id,
                    "instrument_id": instrument_id,
                    "quote_role": "valuation",
                    "valuation_date": valuation_date,
                    "quote_currency": series_window.currency,
                    "window_start_at": window_start,
                    "window_end_at": window_end,
                    "selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
                    "selection_policy_revision": policy_revision,
                    "consumer_policy_version": (
                        VALUATION_QUOTE_CONSUMER_POLICY_VERSION
                    ),
                    "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
                    "freshness_mode": freshness.mode,
                    "freshness_max_age_days": freshness.max_age_days,
                    "resolver_strategy_version": QUOTE_RESOLVER_STRATEGY_VERSION,
                    "freshness_limit_seconds": freshness.max_age_days * 86_400,
                    "candidate_count": 1 if candidate is not None else 0,
                    "adopted_count": 1 if selected else 0,
                    "selection_status": "selected" if selected else "unavailable",
                    "coverage_state": "complete" if selected else "unavailable",
                    "reason_codes": [] if selected else reasons,
                }
            )
            if candidate is None:
                continue
            if candidate.ingested_at is None:
                raise ManifestCaptureError(
                    "canonical quote revision has unknown ingestion time: "
                    + candidate.revision_id
                )
            if candidate.ingestion_time_state not in {
                "observed",
                "legacy_series_upper_bound",
                "legacy_instrument_upper_bound",
                "legacy_migration_upper_bound",
            }:
                raise ManifestCaptureError(
                    "canonical quote revision has unknown ingestion evidence state: "
                    + candidate.revision_id
                )
            if candidate.ingested_at > common.knowledge_cutoff_at:
                raise ManifestCaptureError(
                    "canonical quote revision is after the manifest knowledge cutoff: "
                    + candidate.revision_id
                )
            decision_reason = (
                "adopted_carry_forward"
                if selected and resolution is not None and resolution.carry_forward
                else "adopted_exact"
                if selected
                else reasons[0]
                if reasons
                else "not_adopted"
            )
            candidate_rows.append(
                {
                    "portfolio_id": common.portfolio_id,
                    "quote_window_id": resolved_window_id,
                    "candidate_rank": 1,
                    "quote_series_id": _uuid(
                        candidate.quote_series_id,
                        field_name="quote_series_id",
                    ),
                    "observation_id": _uuid(
                        candidate.observation_id,
                        field_name="observation_id",
                    ),
                    "revision_id": _uuid(
                        candidate.revision_id,
                        field_name="revision_id",
                    ),
                    "revision_number": candidate.revision_number,
                    "observation_date": candidate.observation_date,
                    "quote_value": candidate.value,
                    "quote_status": candidate.status,
                    "source_published_at": candidate.source_published_at,
                    "ingested_at": candidate.ingested_at,
                    "ingestion_time_state": candidate.ingestion_time_state,
                    "payload_hash": candidate.payload_hash,
                    "decision": "adopted" if selected else "excluded",
                    "decision_reason_code": decision_reason,
                }
            )
    return {
        portfolio_daily_quote_window.name: window_rows,
        portfolio_daily_quote_candidate.name: candidate_rows,
    }


__all__ = ["capture_quote_dependencies", "quote_window_id"]
