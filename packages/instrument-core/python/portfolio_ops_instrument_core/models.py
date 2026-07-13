from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


InstrumentType = Literal["fund", "etf", "index", "bond", "equity", "cash", "fx", "other"]
IdentifierType = Literal[
    "ticker",
    "exchange_ticker",
    "ts_code",
    "isin",
    "cusip",
    "sedol",
    "internal",
    "fund_name",
    "cash_currency",
    "other",
]
MetricFamily = Literal["price", "nav", "fx"]
QuoteBasis = Literal[
    "last",
    "close",
    "adjusted_close",
    "official_nav",
    "total_return_nav",
    "cumulative_nav",
    "accumulated_nav",
    "cum_nav",
    "dividend_adjusted_nav",
    "reinvested_nav",
    "spot",
    "clean_price",
    "dirty_price",
    "par",
]
QuoteRole = Literal["trading", "valuation", "total_return", "chart", "reference"]
DataStatus = Literal["complete", "partial", "unavailable"]
SourceObservationStatus = Literal["complete", "partial", "rejected"]
QuoteRevisionStatus = Literal["complete", "partial", "rejected", "withdrawn"]
QuoteFreshnessMode = Literal["exact_only", "calendar_day_carry_forward"]
ObservationFreshnessStatus = Literal["current", "late", "missing"]
QuoteIngestionStatus = Literal["current", "unknown"]
QuoteReliabilityStatus = Literal["reliable", "qualified", "unavailable"]
QuoteResolutionStatus = Literal["resolved", "unavailable"]
QuoteSeriesCoverageStatus = Literal["complete", "partial", "unavailable"]
QuoteSeriesRangeMode = Literal["bounded", "since_inception"]
CanonicalQuoteResolverStrategyVersion = Literal["canonical_quote_resolver.v1"]
QuoteFreshnessPolicyVersion = Literal["canonical_quote_freshness.v1"]
QuoteSelectionPolicyVersion = Literal["quote_selection_policy.v1"]
QuoteResolverReasonCode = Literal[
    "instrument_not_found",
    "non_canonical_instrument_id",
    "missing_quote_policy",
    "missing_quote_series",
    "missing_observation",
    "currency_mismatch",
    "partial_series",
    "rejected_observation",
    "withdrawn_observation",
    "ambiguous_quote_series",
    "late_observation",
    "carried_forward_observation",
    "freshness_limit_exceeded",
    "unknown_ingestion_time",
    "missing_anchor",
    "insufficient_history",
]
CorporateActionType = Literal["share_split"]
CorporateActionStatus = Literal["detected", "confirmed", "cancelled"]
QuantityRounding = Literal["exact", "truncate", "round_half_up", "cash_in_lieu"]
VALUATION_PROHIBITED_TOTAL_RETURN_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)
CASH_CUMULATIVE_NAV_BASES = frozenset(
    {"cumulative_nav", "accumulated_nav", "cum_nav"}
)
TOTAL_RETURN_QUOTE_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)


def _normalize_currency_code(value: object) -> object:
    if isinstance(value, str):
        return value.strip().upper()
    return value


CanonicalCurrencyCode = Annotated[
    str,
    BeforeValidator(_normalize_currency_code),
    Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]


class InstrumentIdentifier(BaseModel):
    identifier_type: IdentifierType
    identifier_value: str = Field(min_length=1)
    is_primary: bool = False

    @field_validator("identifier_value", mode="before")
    @classmethod
    def normalize_identifier_value(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("identifier_value must not be blank.")
            return normalized
        return value


class InstrumentCore(BaseModel):
    instrument_id: str = Field(min_length=1)
    instrument_name: str = Field(min_length=1)
    instrument_type: InstrumentType
    currency: CanonicalCurrencyCode
    identifiers: list[InstrumentIdentifier] = Field(default_factory=list)


class QuoteSelectionPolicy(BaseModel):
    trading: list[QuoteBasis]
    valuation: list[QuoteBasis]
    total_return: list[QuoteBasis]
    chart: list[QuoteBasis]
    reference: list[QuoteBasis]

    @model_validator(mode="after")
    def prohibit_total_return_valuation_bases(self) -> "QuoteSelectionPolicy":
        for role in ("trading", "valuation", "total_return", "chart", "reference"):
            values = getattr(self, role)
            if len(values) != len(set(values)):
                raise ValueError(f"{role} quote bases must not contain duplicates")
        invalid = sorted(set(self.valuation).intersection(VALUATION_PROHIBITED_TOTAL_RETURN_BASES))
        if invalid:
            raise ValueError(
                "valuation cannot use total-return quote bases: " + ", ".join(invalid)
            )
        for role in ("total_return", "chart"):
            invalid_cumulative = sorted(
                set(getattr(self, role)).intersection(CASH_CUMULATIVE_NAV_BASES)
            )
            if invalid_cumulative:
                raise ValueError(
                    f"{role} cannot use cash-cumulative NAV as total return: "
                    + ", ".join(invalid_cumulative)
                )
        invalid_total_return = sorted(
            set(self.total_return).difference(TOTAL_RETURN_QUOTE_BASES)
        )
        if invalid_total_return:
            raise ValueError(
                "total_return requires a true total-return quote basis: "
                + ", ".join(invalid_total_return)
            )
        return self


class MarketDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_series_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    instrument_id: str = Field(min_length=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: CanonicalCurrencyCode
    source_ref: str | None = None
    status: Literal["complete"] = "complete"
    source_published_at: datetime | None = None
    ingested_at: datetime | None = None
    payload_hash: str = Field(min_length=1)


class QuoteFreshnessPolicy(BaseModel):
    policy_version: QuoteFreshnessPolicyVersion
    mode: QuoteFreshnessMode
    max_age_days: int = Field(ge=0)


class CanonicalQuoteCalculationDependency(BaseModel):
    resolver_strategy_version: CanonicalQuoteResolverStrategyVersion
    freshness_policy_version: QuoteFreshnessPolicyVersion
    freshness_mode: QuoteFreshnessMode
    max_age_days: int = Field(ge=0)
    quote_selection_policy_version: QuoteSelectionPolicyVersion | None = None
    quote_selection_policy_revision: str | None = None
    quote_series_id: str | None = None
    observation_id: str | None = None
    revision_id: str | None = None
    payload_hash: str | None = None
    fingerprint: str = Field(min_length=1)


class CanonicalQuoteResolution(BaseModel):
    resolution_kind: Literal["explicit_series", "quote_role"]
    resolution_status: QuoteResolutionStatus
    resolver_strategy_version: CanonicalQuoteResolverStrategyVersion
    instrument_id: str = Field(min_length=1)
    role: QuoteRole | None = None
    metric_family: MetricFamily | None = None
    quote_basis: QuoteBasis | None = None
    currency: CanonicalCurrencyCode
    requested_as_of_date: date
    quote_selection_policy_version: QuoteSelectionPolicyVersion | None = None
    quote_selection_policy_revision: str | None = None
    freshness_policy: QuoteFreshnessPolicy
    quote_series_id: str | None = None
    observation_id: str | None = None
    revision_id: str | None = None
    revision_number: int | None = Field(default=None, ge=1)
    payload_hash: str | None = None
    value: Decimal | None = None
    observation_date: date | None = None
    source_ref: str | None = None
    source_published_at: datetime | None = None
    ingested_at: datetime | None = None
    carry_forward: bool
    age_days: int | None = Field(default=None, ge=0)
    freshness_status: ObservationFreshnessStatus
    ingestion_status: QuoteIngestionStatus
    reliability_status: QuoteReliabilityStatus
    reason_codes: list[QuoteResolverReasonCode] = Field(default_factory=list)
    calculation_dependency: CanonicalQuoteCalculationDependency

    @model_validator(mode="after")
    def validate_resolution_state(self) -> "CanonicalQuoteResolution":
        if self.resolution_kind == "quote_role":
            if (
                self.role is None
                or self.quote_selection_policy_version is None
            ):
                raise ValueError(
                    "quote_role resolution requires role and policy version"
                )
            if (
                not self.quote_selection_policy_revision
                and not {
                    "instrument_not_found",
                    "non_canonical_instrument_id",
                    "missing_quote_policy",
                }.intersection(self.reason_codes)
            ):
                raise ValueError("quote_role resolution requires policy revision")
        elif self.metric_family is None or self.quote_basis is None:
            raise ValueError(
                "explicit_series resolution requires metric_family and quote_basis"
            )

        identity_fields = (
            self.quote_series_id,
            self.observation_id,
            self.revision_id,
            self.revision_number,
            self.payload_hash,
        )
        if self.resolution_status == "resolved":
            if (
                any(value is None for value in identity_fields)
                or self.value is None
                or self.observation_date is None
            ):
                raise ValueError(
                    "resolved quote requires complete series, observation, revision, value, and date"
                )
            unavailable_reasons = {
                "instrument_not_found",
                "non_canonical_instrument_id",
                "missing_quote_policy",
                "missing_quote_series",
                "missing_observation",
                "currency_mismatch",
                "partial_series",
                "rejected_observation",
                "withdrawn_observation",
                "ambiguous_quote_series",
                "freshness_limit_exceeded",
            }
            if unavailable_reasons.intersection(self.reason_codes):
                raise ValueError("resolved quote cannot contain an unavailable reason")
        else:
            if self.value is not None:
                raise ValueError("unavailable quote must not expose an adopted value")
            if not self.reason_codes:
                raise ValueError("unavailable quote requires at least one reason code")
            missing_series_reasons = {
                "instrument_not_found",
                "non_canonical_instrument_id",
                "missing_quote_policy",
                "missing_quote_series",
                "currency_mismatch",
                "ambiguous_quote_series",
            }
            if missing_series_reasons.intersection(self.reason_codes):
                if any(value is not None for value in identity_fields):
                    raise ValueError("missing series must not expose quote identity ids")
            elif "missing_observation" in self.reason_codes:
                if self.quote_series_id is None:
                    raise ValueError("missing observation must retain selected series id")
                if any(
                    value is not None
                    for value in (
                        self.observation_id,
                        self.revision_id,
                        self.revision_number,
                        self.payload_hash,
                    )
                ):
                    raise ValueError("missing observation must not expose revision ids")
            elif any(value is None for value in identity_fields):
                raise ValueError(
                    "late or non-complete observation must retain full revision lineage"
                )
        return self


class CanonicalQuoteSeriesPoint(BaseModel):
    quote_series_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    payload_hash: str = Field(min_length=1)
    observation_date: date
    value: Decimal = Field(gt=0)
    source_ref: str | None = None
    source_published_at: datetime | None = None
    ingested_at: datetime | None = None


class CanonicalQuoteSeriesObservation(BaseModel):
    quote_series_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    payload_hash: str = Field(min_length=1)
    observation_date: date
    value: Decimal | None = None
    status: QuoteRevisionStatus
    source_ref: str | None = None
    source_published_at: datetime | None = None
    ingested_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status_value(self) -> "CanonicalQuoteSeriesObservation":
        if self.status == "withdrawn":
            if self.value is not None:
                raise ValueError("withdrawn observation must have a null value")
        elif self.value is None or self.value <= 0:
            raise ValueError("non-withdrawn observation requires a positive value")
        return self


class CanonicalQuoteSeriesCalculationDependency(BaseModel):
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
    revision_ids: list[str] = Field(default_factory=list)
    payload_hashes: list[str] = Field(default_factory=list)
    excluded_revision_ids: list[str] = Field(default_factory=list)
    excluded_payload_hashes: list[str] = Field(default_factory=list)
    fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revision_lineage(self) -> "CanonicalQuoteSeriesCalculationDependency":
        if self.range_mode == "bounded" and self.start_date is None:
            raise ValueError("bounded dependency requires start_date")
        if self.range_mode == "since_inception" and self.start_date is not None:
            raise ValueError("since_inception dependency must not use start_date")
        if self.start_date is not None and self.start_date > self.end_date:
            raise ValueError("dependency start_date cannot be after end_date")
        if len(self.revision_ids) != len(self.payload_hashes):
            raise ValueError("series dependency revisions and payload hashes must align")
        if len(self.revision_ids) != len(set(self.revision_ids)):
            raise ValueError("series dependency revision ids must be unique")
        if len(self.excluded_revision_ids) != len(self.excluded_payload_hashes):
            raise ValueError("excluded revisions and payload hashes must align")
        if not set(self.excluded_revision_ids).issubset(self.revision_ids):
            raise ValueError("excluded revisions must be part of dependency lineage")
        return self


class CanonicalQuoteSeriesResolution(BaseModel):
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
    observations: list[CanonicalQuoteSeriesObservation] = Field(default_factory=list)
    points: list[CanonicalQuoteSeriesPoint] = Field(default_factory=list)
    observation_count: int = Field(ge=0)
    adopted_point_count: int = Field(ge=0)
    first_observation_date: date | None = None
    last_observation_date: date | None = None
    coverage_status: QuoteSeriesCoverageStatus
    freshness_status: ObservationFreshnessStatus
    ingestion_status: QuoteIngestionStatus
    reliability_status: QuoteReliabilityStatus
    reason_codes: list[QuoteResolverReasonCode] = Field(default_factory=list)
    calculation_dependency: CanonicalQuoteSeriesCalculationDependency

    @model_validator(mode="after")
    def validate_series_resolution(self) -> "CanonicalQuoteSeriesResolution":
        if self.range_mode == "bounded" and self.start_date is None:
            raise ValueError("bounded series resolution requires start_date")
        if self.range_mode == "since_inception" and self.start_date is not None:
            raise ValueError("since_inception must not use an artificial start_date")
        if self.start_date is not None and self.start_date > self.end_date:
            raise ValueError("series resolution start_date cannot be after end_date")
        if (
            not self.quote_selection_policy_revision
            and not {
                "instrument_not_found",
                "non_canonical_instrument_id",
            }.intersection(self.reason_codes)
        ):
            raise ValueError("series resolution requires policy revision")
        if self.quote_series_id is None:
            if self.metric_family is not None or self.quote_basis is not None:
                raise ValueError("missing series cannot expose selected identity")
            if (
                self.observations
                or self.points
                or self.start_anchor is not None
                or self.start_boundary_observation is not None
            ):
                raise ValueError("missing series cannot expose observations")
        else:
            if self.metric_family is None or self.quote_basis is None:
                raise ValueError("selected series requires metric family and basis")
            if any(point.quote_series_id != self.quote_series_id for point in self.points):
                raise ValueError("series window cannot contain points from another series")
            if any(
                observation.quote_series_id != self.quote_series_id
                for observation in self.observations
            ):
                raise ValueError(
                    "series window cannot contain observations from another series"
                )
            if (
                self.start_boundary_observation is not None
                and self.start_boundary_observation.quote_series_id
                != self.quote_series_id
            ):
                raise ValueError("series boundary must belong to selected series")
            if (
                self.start_anchor is not None
                and self.start_anchor.quote_series_id != self.quote_series_id
            ):
                raise ValueError("series anchor must belong to selected series")
            if self.range_mode == "since_inception" and self.start_anchor is not None:
                raise ValueError("since_inception must not expose a start anchor")
            if (
                self.range_mode == "since_inception"
                and self.start_boundary_observation is not None
            ):
                raise ValueError(
                    "since_inception must not expose a start boundary observation"
                )
            if (
                self.start_boundary_observation is not None
                and self.start_date is not None
                and self.start_boundary_observation.observation_date > self.start_date
            ):
                raise ValueError("series boundary cannot be after start_date")
            if (
                self.start_anchor is not None
                and self.start_date is not None
                and self.start_anchor.observation_date > self.start_date
            ):
                raise ValueError("series anchor cannot be after start_date")
        if self.observation_count != len(self.observations):
            raise ValueError("observation_count must equal observations length")
        if self.adopted_point_count != len(self.points):
            raise ValueError("adopted_point_count must equal points length")
        if self.observations:
            dates = [observation.observation_date for observation in self.observations]
            if dates != sorted(dates) or any(
                (self.start_date is not None and point_date < self.start_date)
                or point_date > self.end_date
                for point_date in dates
            ):
                raise ValueError(
                    "series observations must be ordered and inside the window"
                )
            if self.first_observation_date != dates[0] or self.last_observation_date != dates[-1]:
                raise ValueError(
                    "series coverage boundary dates do not match observations"
                )
        elif self.first_observation_date is not None or self.last_observation_date is not None:
            raise ValueError("empty series window cannot expose boundary dates")
        complete_revision_ids = {
            observation.revision_id
            for observation in self.observations
            if observation.status == "complete"
        }
        if {point.revision_id for point in self.points} != complete_revision_ids:
            raise ValueError(
                "adopted points must exactly match complete current observations"
            )
        if self.resolution_status == "resolved":
            if not self.points or self.coverage_status == "unavailable":
                raise ValueError("resolved series requires available in-window observations")
        elif not self.reason_codes or self.coverage_status == "complete":
            raise ValueError(
                "unavailable series requires reasons and cannot claim complete coverage"
            )
        if not self.points and self.coverage_status != "unavailable":
            raise ValueError("empty series window must have unavailable coverage")
        return self


class CorporateActionEvent(BaseModel):
    corporate_action_event_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    action_type: CorporateActionType = "share_split"
    announcement_date: date | None = None
    record_date: date | None = None
    effective_date: date
    payable_date: date | None = None
    new_units: Decimal = Field(gt=0)
    old_units: Decimal = Field(gt=0)
    quantity_rounding: QuantityRounding = "exact"
    quantity_precision: int = Field(default=0, ge=0, le=12)
    cost_basis_treatment: Literal["carry"] = "carry"
    source: str = Field(min_length=1)
    external_event_id: str | None = None
    status: CorporateActionStatus = "confirmed"
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_event_dates_and_ratio(self) -> "CorporateActionEvent":
        if self.record_date is not None and self.record_date > self.effective_date:
            raise ValueError("record_date cannot be after effective_date.")
        if self.new_units == self.old_units:
            raise ValueError("A share split ratio must change the number of units.")
        return self
