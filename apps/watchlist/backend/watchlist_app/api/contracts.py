from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from watchlist_app.services.quote_resolution_summary import (
    CanonicalQuoteSeriesResolutionSummary,
)


class WatchlistCreateRequest(BaseModel):
    name: str
    description: str | None = None


class WatchlistItemsCreateRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)


class WatchlistItemsDeleteRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)


class WatchlistItemsTransferRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)
    target_watchlist_id: str


class WatchlistItemsCopyRequest(WatchlistItemsTransferRequest):
    pass


class WatchlistItemsMoveRequest(WatchlistItemsTransferRequest):
    pass


class WatchlistReorderRequest(BaseModel):
    watchlist_ids: list[str] = Field(default_factory=list)


class ManualFundCreateRequest(BaseModel):
    ticker: str
    name: str
    product_type: str = "instrument"
    fund_type: str = "generic"


class AdvancedFilterRuleInput(BaseModel):
    type: Literal["rule"] = "rule"
    field: str
    operator: Literal[
        "eq",
        "neq",
        "in",
        "not_in",
        "contains",
        "gte",
        "lte",
        "gt",
        "lt",
        "exists",
    ]
    value: Any = None


class AdvancedFilterGroupInput(BaseModel):
    type: Literal["group"] = "group"
    logic: Literal["and", "or"] = "and"
    conditions: list[AdvancedFilterRuleInput | AdvancedFilterGroupInput] = Field(
        default_factory=list
    )


AdvancedFilterGroupInput.model_rebuild()


class SortRule(BaseModel):
    field: str
    direction: str = "asc"


class WatchlistViewColumnInput(BaseModel):
    field_key: str
    display_order: int
    width: int | None = None
    is_visible: bool = True


class WatchlistViewCreateRequest(BaseModel):
    name: str
    description: str | None = None
    default_group_by: str | None = "none"
    default_sort: list[SortRule] = Field(default_factory=list)
    default_filters: dict[str, Any] = Field(default_factory=dict)
    default_advanced_filters: AdvancedFilterGroupInput | None = None
    columns: list[WatchlistViewColumnInput] = Field(default_factory=list)


class PaginationInput(BaseModel):
    page: int = 1
    page_size: int = 50


class ScreenerQueryRequest(BaseModel):
    watchlist_id: str
    view_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    filters: dict[str, list[Any]] = Field(default_factory=dict)
    advanced_filters: AdvancedFilterGroupInput | None = None
    sort: list[SortRule] = Field(default_factory=list)
    group_by: str | None = "none"
    pagination: PaginationInput = Field(default_factory=PaginationInput)


class InstrumentAttributeDefinitionCreateRequest(BaseModel):
    attribute_key: str
    label: str
    description: str | None = None
    data_type: Literal[
        "single_select",
        "multi_select",
        "boolean",
        "number",
        "text",
        "date",
    ]
    domain_code: Literal["overview", "research", "monitoring"]
    group_code: str
    display_order: int = 999
    options: list[str] = Field(default_factory=list)
    instrument_scope_json: list[str] = Field(default_factory=lambda: ["fund"])
    applicability_json: dict[str, list[str]] = Field(default_factory=dict)
    rubric_json: dict[str, Any] = Field(default_factory=dict)
    is_groupable: bool = True
    is_filterable: bool = True
    is_view_column: bool = True
    default_visible: bool = False
    required_for_monitoring: bool = False


class FundAttributeValueInput(BaseModel):
    attribute_key: str
    value: Any


class FundAttributesUpsertRequest(BaseModel):
    values: list[FundAttributeValueInput] = Field(default_factory=list)


class TaxonomyAssignmentUpsertRequest(BaseModel):
    node_id: str | None = None
    updated_by: str | None = None


class RecalcExecuteRequest(BaseModel):
    job_type: Literal["performance", "all"] = "all"
    trigger_type: str = "manual_api"
    trigger_ref_type: str | None = "api_request"
    trigger_ref_id: str | None = None


class RecalcBulkRequest(BaseModel):
    instrument_ids: list[str] = Field(min_length=1, max_length=2000)
    job_type: Literal["performance", "all"] = "all"
    trigger_type: str = "market_data_refresh"
    trigger_ref_type: str | None = "shared_market_data"
    trigger_ref_id: str | None = None


class ManualProfileUpsertRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    updated_by: str | None = None


class ResearchOverviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    research_view: str = Field(default="", max_length=4000)
    dd_status: str = Field(default="", max_length=200)
    odd_status: str = Field(default="", max_length=200)
    ic_status: str = Field(default="", max_length=200)
    decision: str = Field(default="", max_length=1000)
    next_review_date: date | None = None
    primary_analyst: str = Field(default="", max_length=200)


class ResearchProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: ResearchOverviewRequest = Field(default_factory=ResearchOverviewRequest)
    timeline_notes: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


class ResearchProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: ResearchProfilePayload = Field(default_factory=ResearchProfilePayload)
    updated_by: str | None = Field(default=None, max_length=200)


class ResearchRatingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: Annotated[int, Field(strict=True, ge=1, le=5)] | None
    confidence: Literal["low", "medium", "high"]
    rationale: str = Field(min_length=1, max_length=4000)
    as_of_date: date
    next_review_date: date | None = None
    author: str = Field(min_length=1, max_length=200)
    expected_current_revision_id: str | None

    @model_validator(mode="after")
    def validate_review_date(self) -> "ResearchRatingUpdateRequest":
        if self.as_of_date > date.today():
            raise ValueError("as_of_date cannot be in the future")
        if self.next_review_date is not None and self.next_review_date < self.as_of_date:
            raise ValueError("next_review_date cannot be before as_of_date")
        return self


class NavSettingsUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_benchmark_instrument_id: str | None = None
    peer_baseline_instrument_ids: list[str] | None = None
    updated_by: str | None = None


class InvestmentAnalyticsMetricQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "qualified", "unavailable", "not_requested"]
    reason: str | None
    observation_count: int = Field(ge=0)
    excluded_observation_count: int = Field(ge=0)
    used_window_count: int = Field(default=0, ge=0)
    excluded_window_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reason_contract(self) -> "InvestmentAnalyticsMetricQualityResponse":
        if self.status == "available" and self.reason is not None:
            raise ValueError("available quality must not include a reason")
        if self.status != "available" and not (self.reason or "").strip():
            raise ValueError("non-available quality must include a stable reason")
        return self


class InvestmentAnalyticsPerformanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_return: FiniteFloat | None
    annualized_return: FiniteFloat | None
    annualized_volatility: FiniteFloat | None
    annualized_downside_deviation: FiniteFloat | None
    sharpe_ratio: FiniteFloat | None
    sortino_ratio: FiniteFloat | None
    calmar_ratio: FiniteFloat | None
    max_drawdown: FiniteFloat | None
    recovery_days: int | None = Field(default=None, ge=0)
    recovery_open: bool


class InvestmentAnalyticsRelativeSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excess_return: FiniteFloat | None
    information_ratio: FiniteFloat | None
    tracking_error: FiniteFloat | None
    beta: FiniteFloat | None
    upside_capture: FiniteFloat | None
    downside_capture: FiniteFloat | None


class InvestmentAnalyticsPerformanceQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: InvestmentAnalyticsMetricQualityResponse
    annualized_return: InvestmentAnalyticsMetricQualityResponse
    annualized_volatility: InvestmentAnalyticsMetricQualityResponse
    sharpe_ratio: InvestmentAnalyticsMetricQualityResponse
    downside_deviation: InvestmentAnalyticsMetricQualityResponse
    sortino_ratio: InvestmentAnalyticsMetricQualityResponse
    calmar_ratio: InvestmentAnalyticsMetricQualityResponse


class InvestmentAnalyticsRelativeQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alignment: InvestmentAnalyticsMetricQualityResponse
    excess_return: InvestmentAnalyticsMetricQualityResponse
    tracking_error: InvestmentAnalyticsMetricQualityResponse
    information_ratio: InvestmentAnalyticsMetricQualityResponse
    beta: InvestmentAnalyticsMetricQualityResponse
    upside_capture: InvestmentAnalyticsMetricQualityResponse
    downside_capture: InvestmentAnalyticsMetricQualityResponse


class InvestmentAnalyticsPeriodQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund: InvestmentAnalyticsPerformanceQualityResponse
    benchmark: InvestmentAnalyticsPerformanceQualityResponse | None
    relative: InvestmentAnalyticsRelativeQualityResponse


def _validate_metric_availability(
    *,
    value: float | None,
    quality: InvestmentAnalyticsMetricQualityResponse,
    metric_name: str,
) -> None:
    value_expected = quality.status in {"available", "qualified"}
    if value_expected and value is None:
        raise ValueError(f"{metric_name} is null despite available quality")
    if not value_expected and value is not None:
        raise ValueError(f"{metric_name} has a value despite unavailable quality")


class InvestmentAnalyticsPeriodResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["1W", "MTD", "YTD", "1Y", "2Y", "3Y", "5Y", "SI"]
    fund: InvestmentAnalyticsPerformanceSnapshotResponse
    benchmark: InvestmentAnalyticsPerformanceSnapshotResponse | None
    relative: InvestmentAnalyticsRelativeSnapshotResponse | None
    quality: InvestmentAnalyticsPeriodQualityResponse

    @model_validator(mode="after")
    def validate_metric_quality_contract(self) -> "InvestmentAnalyticsPeriodResponse":
        fund_quality = self.quality.fund
        self._validate_performance_snapshot("fund", self.fund, fund_quality)

        benchmark_quality = self.quality.benchmark
        if self.benchmark is not None:
            if not isinstance(
                benchmark_quality,
                InvestmentAnalyticsPerformanceQualityResponse,
            ):
                raise ValueError("benchmark quality is required when benchmark metrics exist")
            self._validate_performance_snapshot(
                "benchmark",
                self.benchmark,
                benchmark_quality,
            )

        relative_quality = self.quality.relative
        relative_values = self.relative or InvestmentAnalyticsRelativeSnapshotResponse(
            excess_return=None,
            information_ratio=None,
            tracking_error=None,
            beta=None,
            upside_capture=None,
            downside_capture=None,
        )
        for metric_name in (
            "excess_return",
            "information_ratio",
            "tracking_error",
            "beta",
            "upside_capture",
            "downside_capture",
        ):
            _validate_metric_availability(
                value=getattr(relative_values, metric_name),
                quality=getattr(relative_quality, metric_name),
                metric_name=f"relative.{metric_name}",
            )
        return self

    @staticmethod
    def _validate_performance_snapshot(
        label: str,
        snapshot: InvestmentAnalyticsPerformanceSnapshotResponse,
        quality: InvestmentAnalyticsPerformanceQualityResponse,
    ) -> None:
        for value_field, quality_field in (
            ("annualized_return", "annualized_return"),
            ("annualized_volatility", "annualized_volatility"),
            ("sharpe_ratio", "sharpe_ratio"),
            ("annualized_downside_deviation", "downside_deviation"),
            ("sortino_ratio", "sortino_ratio"),
            ("calmar_ratio", "calmar_ratio"),
        ):
            _validate_metric_availability(
                value=getattr(snapshot, value_field),
                quality=getattr(quality, quality_field),
                metric_name=f"{label}.{value_field}",
            )
        window_available = quality.window.status in {"available", "qualified"}
        if window_available != (snapshot.period_return is not None):
            raise ValueError(f"{label}.period_return conflicts with window quality")


class InvestmentAnalyticsChartPointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    value: FiniteFloat


class InvestmentAnalyticsSeriesSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest: FiniteFloat | None
    median: FiniteFloat | None
    percentile: FiniteFloat | None
    maximum: FiniteFloat | None
    minimum: FiniteFloat | None


class InvestmentAnalyticsMethodologyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annualized_return: Literal["geometric_minimum_365_calendar_days"]
    calmar_ratio: Literal[
        "annualized_return_over_max_drawdown_minimum_1096_calendar_days"
    ]
    annualized_risk: Literal["minimum_12_same_frequency_returns"]
    sharpe_ratio: Literal[
        "arithmetic_mean_excess_return_risk_free_rate_zero"
    ]
    downside_deviation: Literal[
        "lower_partial_moment_mar_zero_all_observations"
    ]
    sortino_ratio: Literal["arithmetic_mean_excess_return_mar_zero"]
    relative_alignment: Literal["exact_fund_observation_boundaries"]
    monthly_volatility_annualization: Literal[
        "fixed_same_frequency_annualization_252_52_12"
    ]
    rolling_beta_frequency: Literal["exact_contiguous_calendar_months"]
    capture_ratio: Literal[
        "minimum_3_exact_same_frequency_returns_per_regime"
    ]


class InvestmentAnalyticsSeriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drawdown: list[InvestmentAnalyticsChartPointResponse]
    benchmark_drawdown: list[InvestmentAnalyticsChartPointResponse]
    monthly_drawdown: list[InvestmentAnalyticsChartPointResponse]
    monthly_annualized_volatility: list[InvestmentAnalyticsChartPointResponse]
    rolling_annualized_volatility: list[InvestmentAnalyticsChartPointResponse]
    benchmark_rolling_annualized_volatility: list[
        InvestmentAnalyticsChartPointResponse
    ]
    rolling_sharpe_ratio: list[InvestmentAnalyticsChartPointResponse]
    benchmark_rolling_sharpe_ratio: list[InvestmentAnalyticsChartPointResponse]
    rolling_beta: list[InvestmentAnalyticsChartPointResponse]


class InvestmentAnalyticsStatisticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_drawdown: FiniteFloat | None
    monthly_return: InvestmentAnalyticsSeriesSummaryResponse
    monthly_drawdown: InvestmentAnalyticsSeriesSummaryResponse
    rolling_annualized_volatility: InvestmentAnalyticsSeriesSummaryResponse
    rolling_beta: InvestmentAnalyticsSeriesSummaryResponse
    trailing_negative_month_count: int | None = Field(default=None, ge=0)


class InvestmentAnalyticsMatrixRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: str
    months: list[FiniteFloat | None] = Field(min_length=12, max_length=12)
    ytd: FiniteFloat | None


class InvestmentAnalyticsQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_status: Literal["available", "unavailable"]
    fund_reason: str | None
    benchmark_status: Literal["available", "unavailable", "not_requested"]
    benchmark_reason: str | None
    series: "InvestmentAnalyticsSeriesQualityResponse"


class InvestmentAnalyticsSeriesQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_annualized_volatility: InvestmentAnalyticsMetricQualityResponse
    rolling_annualized_volatility: InvestmentAnalyticsMetricQualityResponse
    benchmark_rolling_annualized_volatility: InvestmentAnalyticsMetricQualityResponse
    rolling_sharpe_ratio: InvestmentAnalyticsMetricQualityResponse
    benchmark_rolling_sharpe_ratio: InvestmentAnalyticsMetricQualityResponse
    rolling_beta: InvestmentAnalyticsMetricQualityResponse


InvestmentAnalyticsQualityResponse.model_rebuild()


class InvestmentAnalyticsQuoteResolutionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund: CanonicalQuoteSeriesResolutionSummary
    benchmark: CanonicalQuoteSeriesResolutionSummary | None


class InvestmentAnalyticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodology_version: Literal["canonical-investment-analytics/v2"]
    methodology: InvestmentAnalyticsMethodologyResponse
    valuation_date: date
    quote_resolutions: InvestmentAnalyticsQuoteResolutionsResponse
    calculation_states: dict[str, dict[str, Any] | None] = Field(
        default_factory=dict
    )
    as_of_date: date | None
    benchmark_instrument_id: str | None
    rolling_window_months: Literal[1, 3, 6, 12]
    periods: list[InvestmentAnalyticsPeriodResponse]
    monthly_return_matrix: list[InvestmentAnalyticsMatrixRowResponse]
    series: InvestmentAnalyticsSeriesResponse
    statistics: InvestmentAnalyticsStatisticsResponse
    source_observation_count: int = Field(ge=0)
    benchmark_observation_count: int = Field(ge=0)
    source_input_observation_count: int = Field(ge=0)
    benchmark_input_observation_count: int = Field(ge=0)
    quality: InvestmentAnalyticsQualityResponse

    @model_validator(mode="after")
    def validate_series_quality_contract(self) -> "InvestmentAnalyticsResponse":
        for series_name in (
            "monthly_annualized_volatility",
            "rolling_annualized_volatility",
            "benchmark_rolling_annualized_volatility",
            "rolling_sharpe_ratio",
            "benchmark_rolling_sharpe_ratio",
            "rolling_beta",
        ):
            points = getattr(self.series, series_name)
            quality = getattr(self.quality.series, series_name)
            values_expected = quality.status in {"available", "qualified"}
            if values_expected != bool(points):
                raise ValueError(f"{series_name} conflicts with series quality")
        return self


class FundSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    quote_resolution: CanonicalQuoteSeriesResolutionSummary | None = None


class FundChartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    resolution: CanonicalQuoteSeriesResolutionSummary | None = None


class FundPerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    analytics: InvestmentAnalyticsResponse
    quote_resolution: CanonicalQuoteSeriesResolutionSummary | None = None
    historical_quote_resolution: CanonicalQuoteSeriesResolutionSummary | None = None


class FundRiskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    quote_resolution: CanonicalQuoteSeriesResolutionSummary | None = None
    historical_quote_resolution: CanonicalQuoteSeriesResolutionSummary | None = None


class CopilotMessageInput(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class WatchlistCopilotChatRequest(BaseModel):
    question: str
    view_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    filters: dict[str, list[Any]] = Field(default_factory=dict)
    advanced_filters: AdvancedFilterGroupInput | None = None
    sort: list[SortRule] = Field(default_factory=list)
    group_by: str | None = "none"
    history: list[CopilotMessageInput] = Field(default_factory=list)


class FundCopilotChatRequest(BaseModel):
    question: str
    active_tab: str | None = None
    history: list[CopilotMessageInput] = Field(default_factory=list)
