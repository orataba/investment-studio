from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from portfolio_ops_instrument_core.models import (
    CanonicalCurrencyCode,
    CanonicalQuoteResolverStrategyVersion,
    CanonicalQuoteSeriesCalculationDependency,
    CanonicalQuoteSeriesObservation,
    CanonicalQuoteSeriesPoint,
    CanonicalQuoteSeriesResolution,
    MetricFamily,
    ObservationFreshnessStatus,
    QuoteBasis,
    QuoteFreshnessMode,
    QuoteFreshnessPolicy,
    QuoteFreshnessPolicyVersion,
    QuoteIngestionStatus,
    QuoteReliabilityStatus,
    QuoteResolutionStatus,
    QuoteResolverReasonCode,
    QuoteRole,
    QuoteSelectionPolicyVersion,
    QuoteSeriesCoverageStatus,
    QuoteSeriesRangeMode,
)

from watchlist_app.services.quote_consumer_policy import (
    ConsumerFreshnessProfile,
    WATCHLIST_QUOTE_DEPENDENCY_KIND,
    WATCHLIST_QUOTE_DEPENDENCY_VERSION,
    quote_consumer_dependency,
)


QUOTE_RESOLUTION_SUMMARY_SCHEMA_VERSION = "watchlist_quote_resolution_summary.v1"


class ConsumerFreshnessProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    canonical_instrument_type: str = Field(min_length=1)
    resolver_policy: QuoteFreshnessPolicy


class QuoteConsumerDependencyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_kind: Literal[WATCHLIST_QUOTE_DEPENDENCY_KIND]
    dependency_version: Literal[WATCHLIST_QUOTE_DEPENDENCY_VERSION]
    canonical_dependency_fingerprint: str = Field(min_length=1)
    consumer_freshness_profile: ConsumerFreshnessProfilePayload
    fingerprint: str = Field(min_length=1)


class CanonicalQuoteSeriesCalculationDependencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolver_strategy_version: CanonicalQuoteResolverStrategyVersion
    freshness_policy_version: QuoteFreshnessPolicyVersion
    freshness_mode: QuoteFreshnessMode
    max_age_days: int = Field(ge=0)
    range_mode: QuoteSeriesRangeMode
    start_date: date | None = None
    end_date: date
    quote_selection_policy_version: QuoteSelectionPolicyVersion
    quote_selection_policy_revision: str | None = None
    quote_series_id: str | None = None
    revision_count: int = Field(ge=0)
    excluded_revision_count: int = Field(ge=0)
    fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> "CanonicalQuoteSeriesCalculationDependencySummary":
        if self.excluded_revision_count > self.revision_count:
            raise ValueError("excluded revision count cannot exceed revision count")
        return self

    @classmethod
    def from_dependency(
        cls,
        dependency: CanonicalQuoteSeriesCalculationDependency,
    ) -> "CanonicalQuoteSeriesCalculationDependencySummary":
        return cls(
            resolver_strategy_version=dependency.resolver_strategy_version,
            freshness_policy_version=dependency.freshness_policy_version,
            freshness_mode=dependency.freshness_mode,
            max_age_days=dependency.max_age_days,
            range_mode=dependency.range_mode,
            start_date=dependency.start_date,
            end_date=dependency.end_date,
            quote_selection_policy_version=(
                dependency.quote_selection_policy_version
            ),
            quote_selection_policy_revision=(
                dependency.quote_selection_policy_revision
            ),
            quote_series_id=dependency.quote_series_id,
            revision_count=len(dependency.revision_ids),
            excluded_revision_count=len(dependency.excluded_revision_ids),
            fingerprint=dependency.fingerprint,
        )


class CanonicalQuoteSeriesResolutionSummary(BaseModel):
    """Bounded, positively enumerated lineage for projections and APIs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[QUOTE_RESOLUTION_SUMMARY_SCHEMA_VERSION]
    resolution_status: QuoteResolutionStatus
    resolver_strategy_version: CanonicalQuoteResolverStrategyVersion
    instrument_id: str = Field(min_length=1)
    role: QuoteRole
    metric_family: MetricFamily | None = None
    quote_basis: QuoteBasis | None = None
    currency: CanonicalCurrencyCode
    range_mode: QuoteSeriesRangeMode
    start_date: date | None = None
    end_date: date
    quote_selection_policy_version: QuoteSelectionPolicyVersion
    quote_selection_policy_revision: str | None = None
    freshness_policy: QuoteFreshnessPolicy
    quote_series_id: str | None = None
    start_boundary_observation: CanonicalQuoteSeriesObservation | None = None
    start_anchor: CanonicalQuoteSeriesPoint | None = None
    observation_count: int = Field(ge=0)
    adopted_point_count: int = Field(ge=0)
    first_observation_date: date | None = None
    last_observation_date: date | None = None
    coverage_status: QuoteSeriesCoverageStatus
    freshness_status: ObservationFreshnessStatus
    ingestion_status: QuoteIngestionStatus
    reliability_status: QuoteReliabilityStatus
    reason_codes: list[QuoteResolverReasonCode] = Field(default_factory=list)
    calculation_dependency: CanonicalQuoteSeriesCalculationDependencySummary
    consumer_freshness_profile: ConsumerFreshnessProfilePayload
    consumer_dependency: QuoteConsumerDependencyPayload

    @classmethod
    def from_resolution(
        cls,
        window: CanonicalQuoteSeriesResolution,
        consumer_profile: ConsumerFreshnessProfile,
    ) -> "CanonicalQuoteSeriesResolutionSummary":
        canonical_dependency_fingerprint = (
            window.calculation_dependency.fingerprint
        )
        return cls(
            schema_version=QUOTE_RESOLUTION_SUMMARY_SCHEMA_VERSION,
            resolution_status=window.resolution_status,
            resolver_strategy_version=window.resolver_strategy_version,
            instrument_id=window.instrument_id,
            role=window.role,
            metric_family=window.metric_family,
            quote_basis=window.quote_basis,
            currency=window.currency,
            range_mode=window.range_mode,
            start_date=window.start_date,
            end_date=window.end_date,
            quote_selection_policy_version=(
                window.quote_selection_policy_version
            ),
            quote_selection_policy_revision=(
                window.quote_selection_policy_revision
            ),
            freshness_policy=window.freshness_policy,
            quote_series_id=window.quote_series_id,
            start_boundary_observation=window.start_boundary_observation,
            start_anchor=window.start_anchor,
            observation_count=window.observation_count,
            adopted_point_count=window.adopted_point_count,
            first_observation_date=window.first_observation_date,
            last_observation_date=window.last_observation_date,
            coverage_status=window.coverage_status,
            freshness_status=window.freshness_status,
            ingestion_status=window.ingestion_status,
            reliability_status=window.reliability_status,
            reason_codes=window.reason_codes,
            calculation_dependency=(
                CanonicalQuoteSeriesCalculationDependencySummary.from_dependency(
                    window.calculation_dependency
                )
            ),
            consumer_freshness_profile=consumer_profile.payload(),
            consumer_dependency=quote_consumer_dependency(
                canonical_dependency_fingerprint=(
                    canonical_dependency_fingerprint
                ),
                consumer_profile=consumer_profile,
            ),
        )
