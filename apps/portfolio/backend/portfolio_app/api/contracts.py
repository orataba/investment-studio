from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime, time
import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from portfolio_ops_instrument_core.models import (
    InstrumentCore as InstrumentCoreContract,
)
from portfolio_ops_instrument_core.models import (
    DataStatus as CoverageState,
    MetricFamily,
    ObservationFreshnessStatus,
    QuoteBasis,
    QuoteReliabilityStatus,
    QuoteResolutionStatus,
    QuoteSeriesCoverageStatus,
)
from portfolio_app.calculations.numeric import (
    RATIO_SCALE,
    canonical_decimal,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_divide,
    method_decimal_subtract,
    quantize_decimal,
    require_decimal,
    require_method_decimal,
)
from portfolio_app.core.operating_profiles import (
    PortfolioOperatingProfile as PortfolioOperatingProfile,
)

AccountScopedInstrumentType = Literal["fund", "etf", "bond", "equity", "other"]
AccountType = Literal["deposit_account", "securities_account"]
CostBasisMethod = Literal["moving_average", "fifo"]
SupportedCurrency = Literal["USD", "HKD", "CNY"]
TaxonomyAssignmentScope = Literal["instrument", "account", "cash_bucket"]
TargetMemberType = Literal["taxonomy_node", "instrument", "account", "cash_bucket"]
DefaultTargetDimension = Literal["weight", "risk_budget"]
TransactionType = Literal[
    "buy",
    "sell",
    "dividend",
    "dividend_reinvestment",
    "coupon",
    "interest",
    "return_of_capital",
    "maturity_redemption",
    "fee",
    "tax",
    "deposit",
    "withdrawal",
    "fx_conversion",
    "transfer_in",
    "transfer_out",
    "opening_balance",
]
FlowScope = Literal["external_cash_flow", "internal_portfolio", "bootstrap"]
TransferScope = Literal["external_portfolio_boundary", "internal_portfolio"]
TransferObjectType = Literal["cash", "position"]
AllocationResearchRunStatus = Literal["running", "completed", "failed"]
AllocationResearchRunReliabilityState = Literal["current", "stale", "unassessed", "not_completed"]
AllocationResearchExecutionStatus = Literal["ready", "manual_review_required"]
AllocationResearchArtifactPreviewKind = Literal["text", "html", "binary"]
AllocationResearchAsOfMode = Literal["dynamic", "pinned"]
AllocationResearchTargetDimension = Literal["scope_default", "weight", "risk_budget"]
AllocationResearchCapitalMode = Literal[
    "unit_notional", "fixed_gross", "target_volatility", "volatility_cap"
]
AllocationResearchCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]
AllocationResearchMissingReturnPolicy = Literal["strict", "complete_case_drop"]
PolicyReplayRebalanceFrequency = Literal["1w", "1m", "3m"]
PortfolioCalculationFrequency = Literal["daily", "weekly", "monthly"]
PortfolioRiskCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]
PortfolioRiskMissingReturnPolicy = Literal["strict", "complete_case_drop"]
PortfolioRiskCovarianceModel = Literal[
    "ewma_vol_shrinkage_corr_covariance", "ewma_covariance", "sample_covariance"
]
PortfolioRiskContributionMode = Literal["signed", "abs"]
TargetSetType = Literal["saa", "taa"]
TwrState = Literal["linked", "carry_forward", "broken", "reanchor", "no_anchor"]
TwrReliabilityStatus = Literal["reliable", "qualified", "unavailable"]
TwrReliabilityReason = Literal[
    "stale_valuation_on_external_flow",
    "incomplete_valuation_on_external_flow",
    "awaiting_fresh_valuation_anchor",
    "fresh_valuation_reanchor",
    "invalid_return_denominator",
    "crosses_broken_twr_boundary",
    "stale_valuation_without_external_flow",
    "carried_forward_valuation_without_external_flow",
    "carried_forward_valuation_on_external_flow",
]
AnnualizedReturnEligibilityReason = Literal[
    "performance_history_window_unavailable",
    "annualized_return_history_below_minimum",
]

SUPPORTED_PORTFOLIO_CURRENCIES: tuple[SupportedCurrency, ...] = ("USD", "HKD", "CNY")
SUPPORTED_RISK_WINDOW_DAYS = {30, 90, 180, 366, 730}
QUANTITY_STORAGE_QUANTUM = Decimal("0.000000000001")
AMOUNT_STORAGE_QUANTUM = Decimal("0.00000001")
PRICE_STORAGE_QUANTUM = Decimal("0.000000000001")
FX_RATE_STORAGE_QUANTUM = Decimal("0.000000000000000001")
AMOUNT_DISPLAY_QUANTUM = Decimal("0.01")
TRANSACTION_DECIMAL_PATTERN = r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$"


def _serialize_transaction_decimal(value: Decimal) -> str:
    return canonical_decimal(value, field_name="transaction decimal")


TransactionDecimal = Annotated[
    Decimal,
    PlainSerializer(_serialize_transaction_decimal, return_type=str, when_used="json"),
]


def _validate_canonical_output_decimal(value: object) -> Decimal:
    return require_decimal(value, field_name="canonical API decimal")


CanonicalOutputDecimal = Annotated[
    Decimal,
    BeforeValidator(_validate_canonical_output_decimal),
    PlainSerializer(
        canonical_decimal,
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^-?(?:0|[1-9]\d*)(?:\.\d*[1-9])?$",
        },
        mode="serialization",
    ),
]


def _require_transaction_decimal_string(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("Transaction decimal values must be JSON strings.")
    normalized = value.strip()
    if not re.fullmatch(TRANSACTION_DECIMAL_PATTERN, normalized):
        raise ValueError("Transaction decimal values must use plain decimal notation.")
    return normalized


TransactionInputDecimal = Annotated[
    Decimal,
    BeforeValidator(_require_transaction_decimal_string),
    PlainSerializer(_serialize_transaction_decimal, return_type=str, when_used="json"),
    WithJsonSchema(
        {"type": "string", "pattern": TRANSACTION_DECIMAL_PATTERN},
        mode="validation",
    ),
]


def _validate_numeric_input_scale(value: object, *, quantum: Decimal) -> object:
    """Validate declared source scale without rounding or Decimal context use."""

    if value is None:
        return None
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        return None
    max_scale = -quantum.as_tuple().exponent
    declared_scale = len(normalized.rsplit(".", 1)[1]) if "." in normalized else 0
    if declared_scale > max_scale:
        raise ValueError(
            f"Value declares more than {max_scale} decimal places; rounding is forbidden."
        )
    return normalized


def _normalize_required_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_optional_text(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return value


def _normalize_optional_text_list(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        seen: set[str] = set()
        resolved: list[str] = []
        for item in value:
            normalized = _normalize_optional_text(item)
            if not isinstance(normalized, str):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)
        return resolved
    return value


def _validate_risk_window_days(value: int) -> int:
    resolved = int(value)
    if resolved not in SUPPORTED_RISK_WINDOW_DAYS:
        raise ValueError("risk window must be one of 1M, 3M, 6M, 12M, or 24M.")
    return resolved


class InstrumentOption(BaseModel):
    instrument_core: InstrumentCoreContract
    coverage_state: CoverageState
    latest_market_data: list[dict[str, object]] = Field(default_factory=list)
    quote_selection_policy: dict[str, list[str]] = Field(default_factory=dict)


class SharedInstrumentListResponse(BaseModel):
    portfolio_id: str
    instruments: list[InstrumentOption]


class SharedFxRateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: CanonicalOutputDecimal
    as_of_date: date
    source_kind: str
    instrument_id: str | None = None
    source_instrument_ids: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    status: CoverageState = "complete"


class SharedFxRatesResponse(BaseModel):
    portfolio_id: str
    supported_currencies: list[SupportedCurrency]
    maintained_pairs: list[str]
    rates: list[SharedFxRateRecord]


class AccountRecord(BaseModel):
    account_id: str
    portfolio_id: str
    account_name: str
    account_type: AccountType
    currency: str
    institution: str | None = None
    default_settlement_cash_account_id: str | None = None
    cost_basis_method: CostBasisMethod | None = None
    allowed_instrument_types: list[AccountScopedInstrumentType] | None = None
    opened_at: date | None = None
    closed_at: date | None = None
    status: str = "active"


class AccountListResponse(BaseModel):
    portfolio_id: str
    accounts: list[AccountRecord]


class AccountCreateRequest(BaseModel):
    account_name: str = Field(min_length=1)
    account_type: AccountType
    currency: str = Field(min_length=1, max_length=8)
    institution: str | None = None
    default_settlement_cash_account_id: str | None = None
    cost_basis_method: CostBasisMethod | None = None
    allowed_instrument_types: list[AccountScopedInstrumentType] | None = None
    opened_at: date | None = None
    closed_at: date | None = None
    status: str = "active"

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized not in SUPPORTED_PORTFOLIO_CURRENCIES:
                raise ValueError("Account currency must be one of USD, HKD, or CNY.")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_account_contract(self) -> "AccountCreateRequest":
        if self.allowed_instrument_types == []:
            self.allowed_instrument_types = None
        if self.account_type == "deposit_account":
            if self.default_settlement_cash_account_id is not None:
                raise ValueError(
                    "deposit_account must not carry default_settlement_cash_account_id."
                )
            if self.cost_basis_method is not None:
                raise ValueError("deposit_account must not carry cost_basis_method.")
            if self.allowed_instrument_types is not None:
                raise ValueError(
                    "deposit_account must not carry allowed_instrument_types."
                )
        if self.account_type == "securities_account" and self.cost_basis_method is None:
            self.cost_basis_method = "fifo"
        if self.allowed_instrument_types:
            normalized: list[AccountScopedInstrumentType] = []
            for raw_value in self.allowed_instrument_types:
                value = str(raw_value).strip().lower()
                if value and value not in normalized:
                    normalized.append(value)  # type: ignore[arg-type]
            self.allowed_instrument_types = normalized or None
        if self.opened_at and self.closed_at and self.closed_at < self.opened_at:
            raise ValueError("closed_at must not be earlier than opened_at.")
        return self


class AccountUpdateRequest(BaseModel):
    account_name: str | None = Field(default=None, min_length=1)
    institution: str | None = None
    default_settlement_cash_account_id: str | None = None
    cost_basis_method: CostBasisMethod | None = None
    allowed_instrument_types: list[AccountScopedInstrumentType] | None = None
    opened_at: date | None = None
    closed_at: date | None = None
    status: str | None = None

    @model_validator(mode="after")
    def validate_account_update_contract(self) -> "AccountUpdateRequest":
        if self.allowed_instrument_types == []:
            self.allowed_instrument_types = None
        if self.allowed_instrument_types:
            normalized: list[AccountScopedInstrumentType] = []
            for raw_value in self.allowed_instrument_types:
                value = str(raw_value).strip().lower()
                if value and value not in normalized:
                    normalized.append(value)  # type: ignore[arg-type]
            self.allowed_instrument_types = normalized or None
        if self.opened_at and self.closed_at and self.closed_at < self.opened_at:
            raise ValueError("closed_at must not be earlier than opened_at.")
        return self


TransactionActorType = Literal["user", "service", "migration"]
TransactionActorSource = Literal[
    "client_asserted",
    "authenticated_principal",
    "trusted_service",
    "migration",
]
TransactionLifecycleStatus = Literal["active", "deleted"]
TransactionRevisionOperation = Literal["baseline", "create", "amend", "delete"]
TransactionConsiderationBasis = Literal[
    "exact_quantity_price",
    "source_reported",
]
TransactionNumericScaleState = Literal["declared", "legacy_inferred"]


class _TransactionActorBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)


class TransactionActorInput(_TransactionActorBase):
    actor_type: Literal["user"] = "user"
    actor_source: Literal["client_asserted"] = "client_asserted"


class TransactionActorRecord(_TransactionActorBase):
    actor_type: TransactionActorType
    actor_source: TransactionActorSource


class TransactionRecord(BaseModel):
    transaction_id: str
    portfolio_id: str
    transaction_type: TransactionType
    flow_scope: FlowScope
    trade_date: date
    trade_time: str
    trade_at: str
    trade_timezone: str
    trade_time_is_estimated: bool = False
    settlement_date: date
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account: AccountRecord
    settlement_cash_account: AccountRecord | None = None
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    quantity: TransactionDecimal | None = None
    price: TransactionDecimal | None = None
    gross_amount: TransactionDecimal
    counter_amount: TransactionDecimal | None = None
    quoted_fx_rate: TransactionDecimal | None = None
    fees: TransactionDecimal = Decimal("0.00")
    taxes: TransactionDecimal = Decimal("0.00")
    consideration_basis: TransactionConsiderationBasis | None = None
    numeric_scale_state: TransactionNumericScaleState
    quantity_input_scale: int | None = Field(default=None, ge=0, le=12)
    price_input_scale: int | None = Field(default=None, ge=0, le=12)
    gross_amount_input_scale: int = Field(ge=0, le=8)
    counter_amount_input_scale: int | None = Field(default=None, ge=0, le=8)
    quoted_fx_rate_input_scale: int | None = Field(default=None, ge=0, le=18)
    fees_input_scale: int = Field(ge=0, le=8)
    taxes_input_scale: int = Field(ge=0, le=8)
    currency: str
    transfer_scope: TransferScope | None = None
    transfer_object_type: TransferObjectType | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    net_cash_effect: TransactionDecimal | None = None
    note: str | None = None
    created_at: str | None = None
    revision_id: str
    revision_number: int = Field(ge=1)
    lifecycle_status: TransactionLifecycleStatus
    last_mutation_id: str
    last_changed_at: datetime
    last_actor: TransactionActorRecord
    last_change_reason: str | None = None


class TransactionListSummary(BaseModel):
    total_transactions: int
    instrument_transactions: int
    external_cash_flows: int
    opening_balance_records: int


class TransactionListResponse(BaseModel):
    portfolio_id: str
    summary: TransactionListSummary
    transactions: list[TransactionRecord]


class InstrumentPriceChartPoint(BaseModel):
    date: date
    value: float


class InstrumentPriceChartSummary(BaseModel):
    point_count: int
    change_value: float | None = None
    change_pct: float | None = None
    high: float | None = None
    low: float | None = None


class InstrumentPriceChartResponse(BaseModel):
    portfolio_id: str
    instrument_core: InstrumentCoreContract
    as_of_date: date
    range_key: str
    chart_basis: str | None = None
    metric_family: str | None = None
    currency: str
    points: list[InstrumentPriceChartPoint] = Field(default_factory=list)
    summary: InstrumentPriceChartSummary


class TransactionExecutionQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    instrument_id: str
    requested_as_of_date: date
    selection_role: Literal["trading", "valuation"] | None = None
    value: CanonicalOutputDecimal | None = None
    suggested_transaction_price: CanonicalOutputDecimal | None = None
    suggested_transaction_price_scale: Literal[12] = 12
    suggested_transaction_price_rounding: Literal["ROUND_HALF_EVEN"] = (
        "ROUND_HALF_EVEN"
    )
    suggested_transaction_price_was_rounded: bool = False
    quote_date: date | None = None
    quote_basis: QuoteBasis | None = None
    metric_family: MetricFamily | None = None
    currency: str
    source_ref: str | None = None
    source_status: Literal["complete", "partial", "rejected", "withdrawn"] | None = None
    status: CoverageState
    resolution_status: Literal["resolved", "unavailable"]
    freshness_status: Literal["current", "late", "missing"]
    ingestion_status: Literal["current", "bounded", "unknown"]
    reliability_status: Literal["reliable", "qualified", "unavailable"]
    reason_codes: list[str] = Field(default_factory=list)
    stale: bool = False
    carry_forward: bool = False
    age_days: int | None = None
    quote_selection_policy_version: str | None = None
    quote_selection_policy_revision: str | None = None
    quote_series_id: str | None = None
    observation_id: str | None = None
    revision_id: str | None = None
    revision_number: int | None = None
    payload_hash: str | None = None
    source_published_at: datetime | None = None
    ingested_at: datetime | None = None
    ingestion_time_state: Literal[
        "observed",
        "legacy_series_upper_bound",
        "legacy_instrument_upper_bound",
        "legacy_migration_upper_bound",
    ] | None = None
    calculation_dependency: dict[str, object]


class TransactionWorkspaceResponse(BaseModel):
    portfolio_id: str
    summary: TransactionListSummary
    selected_transaction_id: str | None = None
    transactions: list[TransactionRecord]
    selected_transaction: TransactionRecord | None = None


class DailySnapshotRecord(BaseModel):
    as_of_date: date
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    total_position_count: int = 0
    priced_position_count: int = 0
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    valuation_quote_quality: dict[str, dict[str, object]] = Field(default_factory=dict)
    fx_dependency_manifest: dict[str, object]
    cash_balance: float | None = None
    pending_settlement: float | None = None
    position_market_value: float | None = None
    nav: float | None = None
    open_cost_basis: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    return_of_capital_amount: float | None = None
    total_pnl: float | None = None
    external_cash_in: float = 0.0
    external_cash_out: float = 0.0
    net_external_inflow: float = 0.0
    beginning_nav: float | None = None
    ending_nav: float | None = None
    absolute_change: float | None = None
    delta: float | None = None
    daily_twr: float | None = None
    cumulative_twr: float | None = None
    drawdown: float | None = None


class DailySnapshotListSummary(BaseModel):
    snapshot_count: int
    complete_count: int
    partial_count: int
    unavailable_count: int
    latest_complete_as_of_date: date | None = None


class DailySnapshotListResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: DailySnapshotListSummary
    snapshots: list[DailySnapshotRecord]


class DailySnapshotRefreshRequest(BaseModel):
    portfolio_ids: list[str] = Field(default_factory=list)
    instrument_ids: list[str] = Field(default_factory=list)
    dirty_from: date | None = None
    refresh_all: bool = False


class DailySnapshotRefreshResult(BaseModel):
    portfolio_id: str
    snapshot_count: int = 0
    refreshed_from: date | None = None
    refreshed_to: date | None = None
    refreshed_at: str | None = None
    source_market_data_updated_at: str | None = None
    recalculated_from: date | None = None


class DailySnapshotRefreshResponse(BaseModel):
    portfolio_ids: list[str]
    refreshed: list[DailySnapshotRefreshResult]


class DailyPerformancePoint(BaseModel):
    as_of_date: date
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    beginning_nav: float | None = None
    ending_nav: float | None = None
    pending_settlement: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    return_of_capital_amount: float | None = None
    total_pnl: float | None = None
    external_cash_in: float = 0.0
    external_cash_out: float = 0.0
    net_external_inflow: float = 0.0
    absolute_change: float | None = None
    delta: float | None = None
    daily_twr: float | None = None
    cumulative_twr: float | None = None
    drawdown: float | None = None


class PerformanceHistoryReliability(BaseModel):
    start_date: date | None
    end_date: date | None
    elapsed_days: int | None = Field(ge=0)
    calendar_span_days: int | None = Field(ge=1)
    minimum_history_days: Literal[365]
    annualized_return_eligible: bool
    annualized_return_reason_codes: list[AnnualizedReturnEligibilityReason]
    sample_label: str = Field(min_length=1)
    annualization_message: str | None

    @model_validator(mode="after")
    def validate_history_policy(self) -> "PerformanceHistoryReliability":
        if self.start_date is None or self.end_date is None:
            expected_elapsed_days = None
            expected_calendar_span_days = None
            expected_eligible = False
            expected_reasons = ["performance_history_window_unavailable"]
        else:
            expected_elapsed_days = (self.end_date - self.start_date).days
            if expected_elapsed_days < 0:
                raise ValueError(
                    "Performance history end_date must not precede start_date."
                )
            expected_calendar_span_days = expected_elapsed_days + 1
            expected_eligible = expected_elapsed_days >= self.minimum_history_days
            expected_reasons = (
                [] if expected_eligible else ["annualized_return_history_below_minimum"]
            )
        if self.elapsed_days != expected_elapsed_days:
            raise ValueError(
                "Performance history elapsed_days is inconsistent with its boundaries."
            )
        if self.calendar_span_days != expected_calendar_span_days:
            raise ValueError(
                "Performance history calendar_span_days is inconsistent with elapsed_days."
            )
        if self.annualized_return_eligible != expected_eligible:
            raise ValueError(
                "Annualized-return eligibility is inconsistent with performance history."
            )
        if self.annualized_return_reason_codes != expected_reasons:
            raise ValueError(
                "Annualized-return reason codes are inconsistent with performance history."
            )
        if expected_eligible != (self.annualization_message is None):
            raise ValueError(
                "Annualization message is inconsistent with performance history eligibility."
            )
        return self


class PerformanceSummary(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    history_reliability: PerformanceHistoryReliability
    snapshot_count: int
    return_observation_count: int
    risk_return_observation_count: int = 0
    risk_annualization_periods_per_year: float | None = None
    latest_complete_as_of_date: date | None = None
    start_nav: float | None = None
    end_nav: float | None = None
    external_cash_in: float = 0.0
    external_cash_out: float = 0.0
    net_external_inflow: float = 0.0
    cumulative_twr: float | None = None
    annualized_twr: float | None = None
    irr: float | None = None
    mwror: float | None = None
    absolute_change: float | None = None
    delta: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    return_of_capital_amount: float | None = None
    total_pnl: float | None = None
    mean_daily_return: float | None = None
    annualized_return_from_daily_mean: float | None = None
    annualized_volatility: float | None = None
    annualized_downside_volatility: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None
    current_drawdown: float | None = None
    max_drawdown: float | None = None
    max_drawdown_days: int | None = None
    drawdown_duration_days: int | None = None
    quality_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_history_eligibility(self) -> "PerformanceSummary":
        history = self.history_reliability
        if history.start_date != self.start_date or history.end_date != self.end_date:
            raise ValueError(
                "Performance history boundaries must match summary boundaries."
            )
        if not history.annualized_return_eligible and any(
            value is not None
            for value in (
                self.annualized_twr,
                self.irr,
                self.mwror,
                self.calmar_ratio,
            )
        ):
            raise ValueError(
                "Ineligible performance history requires null annualized TWR, "
                "IRR / MWRR, and Calmar Ratio."
            )
        return self


class PerformanceResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PerformanceSummary
    daily_series: list[DailyPerformancePoint]


class PerformanceComparisonMetrics(BaseModel):
    period_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    annualized_downside_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    current_drawdown: float | None
    max_drawdown: float | None
    calmar_ratio: float | None


class PerformanceRelativeMetrics(BaseModel):
    excess_return: float | None
    tracking_error: float | None
    information_ratio: float | None
    beta: float | None
    correlation: float | None
    upside_capture: float | None
    downside_capture: float | None
    capture_ratio: float | None


class PerformanceComparisonCoverage(BaseModel):
    required_observation_count: int = Field(ge=0)
    aligned_observation_count: int = Field(ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    comparison_start_boundary_date: date
    benchmark_start_anchor_date: date | None = None
    benchmark_start_anchor_gap_days: int | None = Field(default=None, ge=0)
    calculation_frequency: PortfolioCalculationFrequency | None = None
    first_aligned_date: date | None = None
    last_aligned_date: date | None = None
    benchmark_currency: str | None = None
    benchmark_quote_basis: QuoteBasis | None = None
    benchmark_metric_family: MetricFamily | None = None
    benchmark_resolution_status: QuoteResolutionStatus | None = None
    benchmark_coverage_status: QuoteSeriesCoverageStatus | None = None
    benchmark_freshness_status: ObservationFreshnessStatus | None = None
    benchmark_reliability_status: QuoteReliabilityStatus | None = None
    benchmark_reason_codes: list[str] = Field(default_factory=list)
    portfolio_twr_reliability_status: TwrReliabilityStatus | None = None
    portfolio_twr_reliability_reasons: list[TwrReliabilityReason] = Field(
        default_factory=list
    )


class PerformanceComparisonPoint(BaseModel):
    date: date
    portfolio_index: float
    benchmark_index: float
    difference: float


class PerformanceComparisonLineage(BaseModel):
    method_version: str = Field(min_length=1)
    portfolio: dict[str, object]
    benchmark: dict[str, object] | None
    fingerprint: str = Field(min_length=1)


class PerformanceComparisonResponse(BaseModel):
    portfolio_id: str
    benchmark_instrument_id: str
    benchmark_name: str | None = None
    base_currency: SupportedCurrency
    benchmark_currency: str | None = None
    market_data_role: Literal["total_return"]
    requested_start_date: date
    requested_end_date: date
    as_of_date: date
    status: Literal["ready", "unavailable"]
    unavailable_reasons: list[str] = Field(default_factory=list)
    coverage: PerformanceComparisonCoverage
    history_reliability: PerformanceHistoryReliability
    portfolio_metrics: PerformanceComparisonMetrics
    benchmark_metrics: PerformanceComparisonMetrics
    relative_metrics: PerformanceRelativeMetrics
    differences: PerformanceComparisonMetrics
    points: list[PerformanceComparisonPoint] = Field(default_factory=list)
    lineage: PerformanceComparisonLineage

    @model_validator(mode="after")
    def validate_history_eligibility(self) -> "PerformanceComparisonResponse":
        history = self.history_reliability
        if history.start_date != self.coverage.comparison_start_boundary_date:
            raise ValueError(
                "Comparison history start_date must match the calculation boundary."
            )
        if history.end_date != self.coverage.last_aligned_date:
            raise ValueError(
                "Comparison history end_date must match the last aligned observation."
            )

        if history.start_date is None or history.end_date is None:
            expected_elapsed_days = None
            expected_calendar_span_days = None
            expected_eligibility = False
        else:
            expected_elapsed_days = (history.end_date - history.start_date).days
            if expected_elapsed_days < 0:
                raise ValueError(
                    "Comparison history end_date must not precede start_date."
                )
            expected_calendar_span_days = expected_elapsed_days + 1
            expected_eligibility = expected_elapsed_days >= history.minimum_history_days
        if history.elapsed_days != expected_elapsed_days:
            raise ValueError(
                "Comparison history elapsed_days is inconsistent with its boundaries."
            )
        if history.calendar_span_days != expected_calendar_span_days:
            raise ValueError(
                "Comparison history calendar_span_days is inconsistent with elapsed_days."
            )
        if history.annualized_return_eligible != expected_eligibility:
            raise ValueError(
                "Comparison annualized-return eligibility is inconsistent with its history span."
            )
        if history.annualized_return_eligible:
            if (
                history.annualized_return_reason_codes
                or history.annualization_message is not None
            ):
                raise ValueError(
                    "Eligible comparison history cannot carry annualization failure reasons."
                )
        elif (
            not history.annualized_return_reason_codes
            or history.annualization_message is None
        ):
            raise ValueError(
                "Ineligible comparison history requires reason codes and a message."
            )

        if not history.annualized_return_eligible:
            for label, metrics in (
                ("portfolio_metrics", self.portfolio_metrics),
                ("benchmark_metrics", self.benchmark_metrics),
                ("differences", self.differences),
            ):
                if (
                    metrics.annualized_return is not None
                    or metrics.calmar_ratio is not None
                ):
                    raise ValueError(
                        "Ineligible comparison history requires null "
                        f"{label} annualized_return and calmar_ratio."
                    )
        return self


class PeriodCalculationLine(BaseModel):
    key: str
    label: str
    amount: float | None = None
    line_kind: Literal["boundary", "performance", "external", "detail"]
    parent_key: str | None = None
    sort_order: int


class PeriodCalculationSummary(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    initial_value: float | None = None
    final_value: float | None = None
    delta: float | None = None
    capital_gains: float | None = None
    realized_capital_gains: float | None = None
    unrealized_capital_gains: float | None = None
    earnings: float | None = None
    fees: float | None = None
    taxes: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    deposits: float = 0.0
    withdrawals: float = 0.0
    net_external_inflow: float = 0.0


class PeriodCalculationResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationSummary
    lines: list[PeriodCalculationLine]


class PeriodBoundaryHoldingRecord(BaseModel):
    position_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract
    quantity: float
    cost_basis: float | None = None
    cost_basis_base: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_base: float | None = None
    unrealized_return: float | None = None
    last_price: float | None = None
    market_value: float | None = None
    market_value_base: float | None = None
    currency: str
    portfolio_weight: float | None = None
    account_ids: list[str] = Field(default_factory=list)
    account_count: int = 0
    open_position_lot_count: int = 0


class PeriodBoundaryHoldingsSummary(BaseModel):
    axis: ContributionAxis | None = None
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    start_date: date | None = None
    start_boundary_date: date | None = None
    end_date: date | None = None
    start_position_count: int = 0
    end_position_count: int = 0
    start_total_market_value_base: float | None = None
    end_total_market_value_base: float | None = None


class PeriodBoundaryHoldingsResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodBoundaryHoldingsSummary
    start_positions: list[PeriodBoundaryHoldingRecord]
    end_positions: list[PeriodBoundaryHoldingRecord]


class ReturnCalendarBucket(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    start_date: date
    end_date: date
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    observation_count: int
    start_nav: float | None = None
    end_nav: float | None = None
    external_cash_in: float = 0.0
    external_cash_out: float = 0.0
    net_external_inflow: float = 0.0
    absolute_change: float | None = None
    delta: float | None = None
    cumulative_twr: float | None = None


class ReturnCalendarSummary(BaseModel):
    frequency: Literal["monthly", "weekly"]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    bucket_count: int
    complete_bucket_count: int
    partial_bucket_count: int
    unavailable_bucket_count: int
    start_date: date | None = None
    end_date: date | None = None


class ReturnCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ReturnCalendarSummary
    buckets: list[ReturnCalendarBucket]


class TaxonomyRecord(BaseModel):
    taxonomy_id: str
    portfolio_id: str
    name: str
    taxonomy_type: str
    purpose: str | None = None
    primary_assignment_scope: TaxonomyAssignmentScope
    planning_enabled: bool = False
    budgeting_level: str | None = None
    root_default_target_dimension: DefaultTargetDimension = "weight"
    status: str = "active"
    source_template_ref: str | None = None


class TaxonomyNodeRecord(BaseModel):
    taxonomy_node_id: str
    taxonomy_id: str
    parent_taxonomy_node_id: str | None = None
    node_name: str
    node_code: str | None = None
    sort_order: int = 0
    is_terminal: bool = True
    default_target_dimension: DefaultTargetDimension = "weight"
    status: str = "active"


class TaxonomyAssignmentRecord(BaseModel):
    assignment_id: str
    taxonomy_id: str
    target_scope: TaxonomyAssignmentScope
    target_entity_id: str
    taxonomy_node_id: str
    status: str = "active"


class PortfolioInstrumentUniverseRecord(BaseModel):
    portfolio_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract | None = None
    source: str
    holding_state: str
    first_transaction_date: date | None = None
    last_transaction_date: date | None = None
    transaction_count: int = 0
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None
    instrument_trend_basis: str | None = None
    instrument_risk_frequency: PortfolioCalculationFrequency | None = None
    instrument_return_series_all: dict[str, object] | None = None


class PortfolioInstrumentUniverseCreateRequest(BaseModel):
    instrument_id: str = Field(min_length=1)

    @field_validator("instrument_id", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)


class TargetSetRecord(BaseModel):
    target_set_id: str
    taxonomy_id: str
    comparator_taxonomy_node_id: str | None = None
    target_set_type: TargetSetType
    name: str
    weight_enabled: bool = False
    risk_budget_enabled: bool = False
    status: str = "active"
    notes: str | None = None


class TargetSetLineRecord(BaseModel):
    target_line_id: str
    target_set_id: str
    target_member_type: TargetMemberType
    target_member_id: str
    taxonomy_node_id: str | None = None
    target_weight: float | None = None
    target_risk_share: float | None = None
    notes: str | None = None


class TargetSetIntegrityIssueRecord(BaseModel):
    taxonomy_id: str
    comparator_taxonomy_node_id: str | None = None
    scope_label: str
    target_set_id: str
    target_set_type: TargetSetType
    target_set_name: str
    issue_code: Literal["invalid_active_target_set"]
    message: str


class TaxonomyCatalogResponse(BaseModel):
    portfolio_id: str
    default_planning_taxonomy_id: str | None = None
    risk_basis: dict[str, object] | None = None
    taxonomies: list[TaxonomyRecord]
    taxonomy_nodes: list[TaxonomyNodeRecord]
    taxonomy_assignments: list[TaxonomyAssignmentRecord]
    instrument_universe: list[PortfolioInstrumentUniverseRecord] = Field(
        default_factory=list
    )
    target_sets: list[TargetSetRecord] = Field(default_factory=list)
    target_set_lines: list[TargetSetLineRecord] = Field(default_factory=list)
    target_set_integrity_issues: list[TargetSetIntegrityIssueRecord] = Field(
        default_factory=list
    )


class AllocationResearchPlanningTaxonomyOption(BaseModel):
    taxonomy_id: str
    name: str
    taxonomy_type: str
    budgeting_level: str | None = None


class AllocationResearchPlanningScopeOption(BaseModel):
    taxonomy_node_id: str | None = None
    label: str
    path: str
    depth: int = 0
    default_target_dimension: DefaultTargetDimension = "weight"
    has_children: bool = False


class AllocationResearchCalculationFrequencyOption(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    label: str
    available: bool
    reason: str | None = None


class AllocationResearchCalculationFrequencyProfile(BaseModel):
    requested_frequency: AllocationResearchCalculationFrequency = "auto"
    resolved_frequency: Literal["daily", "weekly", "monthly"] = "daily"
    default_frequency: Literal["daily", "weekly", "monthly"] = "daily"
    source_frequency_counts: dict[str, int] = Field(default_factory=dict)
    options: list[AllocationResearchCalculationFrequencyOption] = Field(default_factory=list)
    status_label: str


class AllocationResearchTopSleeveWeightBoundRecord(BaseModel):
    taxonomy_node_id: str = Field(min_length=1)
    min_weight: float | None = Field(default=None, ge=0, le=1)
    max_weight: float | None = Field(default=None, ge=0, le=1)

    @field_validator("taxonomy_node_id", mode="before")
    @classmethod
    def validate_taxonomy_node_id(cls, value: object) -> object:
        return _normalize_optional_text(value) or ""

    @model_validator(mode="after")
    def validate_bounds(self) -> "AllocationResearchTopSleeveWeightBoundRecord":
        if self.min_weight is None and self.max_weight is None:
            raise ValueError("top sleeve bound must set min_weight or max_weight.")
        if (
            self.min_weight is not None
            and self.max_weight is not None
            and self.min_weight > self.max_weight
        ):
            raise ValueError("top sleeve min_weight cannot exceed max_weight.")
        return self


class AllocationResearchSettingsRecord(BaseModel):
    portfolio_id: str
    planning_taxonomy_id: str | None = None
    planning_taxonomy_name: str | None = None
    comparator_taxonomy_node_id: str | None = None
    comparator_taxonomy_node_name: str | None = None
    as_of_mode: AllocationResearchAsOfMode = "dynamic"
    as_of_date: date | None = None
    pinned_as_of_date: date | None = None
    lookback_days: int = Field(default=90)
    calculation_frequency: AllocationResearchCalculationFrequency = "auto"
    missing_return_policy: AllocationResearchMissingReturnPolicy = "strict"
    target_dimension: AllocationResearchTargetDimension = "scope_default"
    capital_mode: AllocationResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] = Field(default_factory=list)
    top_sleeve_weight_bounds: list[AllocationResearchTopSleeveWeightBoundRecord] = Field(
        default_factory=list
    )
    policy_replay_rebalance_frequency: PolicyReplayRebalanceFrequency = "1m"
    policy_replay_benchmark_instrument_id: str | None = None
    notes: str | None = None
    updated_at: str | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class AllocationResearchSettingsUpdateRequest(BaseModel):
    planning_taxonomy_id: str | None = None
    comparator_taxonomy_node_id: str | None = None
    as_of_mode: AllocationResearchAsOfMode = "dynamic"
    as_of_date: date | None = None
    lookback_days: int = Field(default=90)
    calculation_frequency: AllocationResearchCalculationFrequency = "auto"
    missing_return_policy: AllocationResearchMissingReturnPolicy = "strict"
    covariance_model_id: PortfolioRiskCovarianceModel = (
        "ewma_vol_shrinkage_corr_covariance"
    )
    contribution_mode: PortfolioRiskContributionMode = "signed"
    target_dimension: AllocationResearchTargetDimension = "scope_default"
    capital_mode: AllocationResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] | None = None
    top_sleeve_weight_bounds: list[AllocationResearchTopSleeveWeightBoundRecord] | None = None
    policy_replay_rebalance_frequency: PolicyReplayRebalanceFrequency = "1m"
    policy_replay_benchmark_instrument_id: str | None = None
    notes: str | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)

    @field_validator(
        "planning_taxonomy_id",
        "comparator_taxonomy_node_id",
        "policy_replay_benchmark_instrument_id",
        "notes",
        mode="before",
    )
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @field_validator("frozen_taxonomy_node_ids", mode="before")
    @classmethod
    def validate_frozen_taxonomy_node_ids(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_optional_text_list(value)

    @model_validator(mode="after")
    def validate_allocation_research_settings(self) -> "AllocationResearchSettingsUpdateRequest":
        if self.as_of_mode == "pinned" and self.as_of_date is None:
            raise ValueError("pinned allocation research mode requires as_of_date.")
        if self.capital_mode == "unit_notional":
            if (
                self.gross_exposure is not None
                or self.target_volatility is not None
                or self.max_gross_exposure is not None
            ):
                raise ValueError(
                    "unit_notional capital mode must not set gross_exposure, target_volatility, or max_gross_exposure."
                )
        elif self.capital_mode == "fixed_gross":
            if self.gross_exposure is None:
                raise ValueError("fixed_gross capital mode requires gross_exposure.")
            if self.target_volatility is not None:
                raise ValueError(
                    "fixed_gross capital mode must not set target_volatility."
                )
            if self.max_gross_exposure is not None:
                raise ValueError(
                    "fixed_gross capital mode must not set max_gross_exposure."
                )
        elif self.capital_mode in {"target_volatility", "volatility_cap"}:
            if self.target_volatility is None:
                raise ValueError(
                    f"{self.capital_mode} capital mode requires target_volatility."
                )
            if self.gross_exposure is not None:
                raise ValueError(
                    f"{self.capital_mode} capital mode must not set gross_exposure."
                )
            if (
                self.capital_mode == "volatility_cap"
                and self.max_gross_exposure is not None
            ):
                raise ValueError(
                    "volatility_cap capital mode must not set max_gross_exposure."
                )
        if (
            self.max_gross_exposure is not None
            and self.gross_exposure is not None
            and self.max_gross_exposure + 1e-12 < self.gross_exposure
        ):
            raise ValueError(
                "max_gross_exposure cannot be smaller than gross_exposure."
            )
        return self


class PortfolioRiskPolicyRecord(BaseModel):
    model_name: str = "Production Risk Model"
    model_role: str = "production"
    covariance_model_id: PortfolioRiskCovarianceModel = (
        "ewma_vol_shrinkage_corr_covariance"
    )
    lookback_days: int = Field(default=90)
    calculation_frequency: PortfolioRiskCalculationFrequency = "auto"
    resolved_calculation_frequency: PortfolioCalculationFrequency = "daily"
    missing_return_policy: PortfolioRiskMissingReturnPolicy = "strict"
    contribution_mode: PortfolioRiskContributionMode = "signed"
    parameters: dict[str, object] = Field(default_factory=dict)
    parameters_by_frequency: dict[str, dict[str, object]] = Field(default_factory=dict)

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class PortfolioRiskPolicyUpdateRequest(BaseModel):
    covariance_model_id: PortfolioRiskCovarianceModel = (
        "ewma_vol_shrinkage_corr_covariance"
    )
    lookback_days: int = Field(default=90)
    calculation_frequency: PortfolioRiskCalculationFrequency = "auto"
    missing_return_policy: PortfolioRiskMissingReturnPolicy = "strict"
    contribution_mode: PortfolioRiskContributionMode = "signed"

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


RiskWorkspaceStatus = Literal["ready", "partial", "unavailable"]


class RiskWorkspaceErrorRecord(BaseModel):
    message: str
    reason_codes: list[str] = Field(default_factory=list)
    dependency: dict[str, object] | None = None


class RiskWorkspacePointRecord(BaseModel):
    date: date
    value: float


class RiskWorkspaceRollingRecord(BaseModel):
    status: Literal["ready", "unavailable"]
    errors: list[RiskWorkspaceErrorRecord] = Field(default_factory=list)
    lookback_days: int
    model_id: PortfolioRiskCovarianceModel
    portfolio_volatility_points: list[RiskWorkspacePointRecord] = Field(
        default_factory=list
    )
    portfolio_sharpe_points: list[RiskWorkspacePointRecord] = Field(
        default_factory=list
    )
    benchmark_volatility_points: list[RiskWorkspacePointRecord] = Field(
        default_factory=list
    )
    benchmark_sharpe_points: list[RiskWorkspacePointRecord] = Field(
        default_factory=list
    )


class RiskWorkspaceMatrixGroupRecord(BaseModel):
    key: str
    label: str
    observation_count: int = 0
    weight: float | None = None


class RiskWorkspaceMatrixCellRecord(BaseModel):
    value: float | None = None
    observation_count: int = 0


class RiskWorkspaceMatrixRecord(BaseModel):
    status: Literal["ready", "unavailable"]
    errors: list[RiskWorkspaceErrorRecord] = Field(default_factory=list)
    scope: str
    as_of_date: date | None = None
    available_as_of_dates: list[date] = Field(default_factory=list)
    groups: list[RiskWorkspaceMatrixGroupRecord] = Field(default_factory=list)
    cells: list[list[RiskWorkspaceMatrixCellRecord]] = Field(default_factory=list)
    max_abs: float = 0.0
    coverage: dict[str, object] | None = None


class RiskWorkspaceContributionRowRecord(BaseModel):
    group_key: str
    group_label: str
    weight: float
    annualized_volatility: float
    risk_share: float
    contribution_to_variance: float
    observation_count: int


class RiskWorkspaceContributionRecord(BaseModel):
    status: Literal["ready", "unavailable"]
    errors: list[RiskWorkspaceErrorRecord] = Field(default_factory=list)
    rows: list[RiskWorkspaceContributionRowRecord] = Field(default_factory=list)
    portfolio_variance: float | None = None
    portfolio_volatility: float | None = None
    observation_count: int | None = None


class RiskWorkspaceTargetGapRowRecord(BaseModel):
    id: str
    label: str
    current: float | None = None
    saa_target: float | None = None
    taa_target: float | None = None
    saa_gap: float | None = None
    taa_gap: float | None = None
    current_value_base: float | None = None


class RiskWorkspaceAllocationPolicyDriftRecord(BaseModel):
    status: Literal["ready", "unavailable", "not_applicable"]
    errors: list[RiskWorkspaceErrorRecord] = Field(default_factory=list)
    weight_rows: list[RiskWorkspaceTargetGapRowRecord] = Field(default_factory=list)
    risk_rows: list[RiskWorkspaceTargetGapRowRecord] = Field(default_factory=list)


class RiskWorkspaceInstrumentCoverageRecord(BaseModel):
    instrument_id: str
    label: str
    scopes: list[str] = Field(default_factory=list)
    status: Literal["ready", "unavailable"]
    observation_count: int = 0
    first_observation_date: date | None = None
    last_observation_date: date | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[RiskWorkspaceErrorRecord] = Field(default_factory=list)


class RiskWorkspaceCoverageRecord(BaseModel):
    market_data_role: Literal["total_return"] = "total_return"
    instrument_count: int
    ready_instrument_count: int
    instruments: list[RiskWorkspaceInstrumentCoverageRecord] = Field(
        default_factory=list
    )


class RiskWorkspaceScopeOptionRecord(BaseModel):
    value: str
    label: str
    kind: Literal["instrument", "taxonomy"]


class RiskWorkspaceTaxonomyRecord(BaseModel):
    taxonomy_id: str
    name: str


class RiskWorkspaceResponse(BaseModel):
    portfolio_id: str
    portfolio_name: str
    base_currency: SupportedCurrency
    operating_profile: PortfolioOperatingProfile
    as_of_date: date
    status: RiskWorkspaceStatus
    planning_taxonomy: RiskWorkspaceTaxonomyRecord | None = None
    risk_policy: PortfolioRiskPolicyRecord
    frequency_profile: dict[str, object]
    matrix_scope_options: list[RiskWorkspaceScopeOptionRecord] = Field(
        default_factory=list
    )
    rolling: RiskWorkspaceRollingRecord
    matrix: RiskWorkspaceMatrixRecord
    risk_contribution: RiskWorkspaceContributionRecord
    allocation_policy_drift: RiskWorkspaceAllocationPolicyDriftRecord
    coverage: RiskWorkspaceCoverageRecord
    calculation_lineage: dict[str, object]
    data_lineage: dict[str, object]


class AllocationResearchContextSignalRecord(BaseModel):
    label: str
    value: str
    tone: str = "neutral"


class AllocationResearchHoldingSnapshotRecord(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: str | None = None
    allocation: float | None = None
    market_value_base: float | None = None
    cost_basis_base: float | None = None
    base_currency: str
    price: float | None = None


class AllocationResearchPlanningGroupSnapshotRecord(BaseModel):
    group_key: str
    group_label: str
    start_allocation: float | None = None
    end_allocation: float | None = None
    allocation_change: float | None = None
    start_value_base: float | None = None
    end_value_base: float | None = None
    period_contribution: float | None = None
    total_pnl: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    position_count: int = 0


class AllocationResearchFindingRecord(BaseModel):
    title: str
    detail: str


class AllocationResearchCurrentContextSummary(BaseModel):
    period_return: float | None = None
    annualized_volatility: float | None = None
    current_drawdown: float | None = None
    max_drawdown: float | None = None
    start_nav: float | None = None
    end_nav: float | None = None


class AllocationResearchPlanningTargetSummary(BaseModel):
    root_saa_configured: bool = False
    root_taa_configured: bool = False
    scoped_target_set_count: int = 0


class AllocationResearchCurrentContextRecord(BaseModel):
    portfolio_id: str
    portfolio_name: str
    base_currency: str
    as_of_date: date
    lookback_start: date
    lookback_end: date
    nav: float | None = None
    holdings_count: int = 0
    planning_group_count: int = 0
    summary: AllocationResearchCurrentContextSummary
    planning_target_summary: AllocationResearchPlanningTargetSummary | None = None
    quality_warnings: list[str] = Field(default_factory=list)
    top_holdings: list[AllocationResearchHoldingSnapshotRecord] = Field(default_factory=list)
    planning_groups: list[AllocationResearchPlanningGroupSnapshotRecord] = Field(
        default_factory=list
    )


class AllocationResearchScopeSelectionRecord(BaseModel):
    taxonomy_node_id: str | None = None
    label: str
    path: str
    depth: int = 0
    default_target_dimension: DefaultTargetDimension = "weight"
    member_source: str = "child_sleeves"


class AllocationResearchMemberTargetRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    scope_path: str | None = None
    member_path: str | None = None
    default_target_dimension: DefaultTargetDimension | None = None
    selected_target_dimension: AllocationResearchTargetDimension | None = None
    source_target_set_type: TargetSetType | None = None
    current_weight: float | None = None
    current_risk_share: float | None = None
    target_weight: float | None = None
    weight_change: float | None = None
    configured_weight: float | None = None
    configured_risk_share: float | None = None
    selected_target_value: float | None = None


class AllocationResearchSolvedResultRowRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    solved_weight: float | None = None
    target_risk_share: float | None = None
    forward_risk_contribution: float | None = None


class AllocationResearchSolvedResultGroupRecord(BaseModel):
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    solved_weight: float | None = None
    target_risk_share: float | None = None
    forward_risk_contribution: float | None = None
    min_weight: float | None = None
    max_weight: float | None = None
    bound_status: str | None = None
    rows: list[AllocationResearchSolvedResultRowRecord] = Field(default_factory=list)


class AllocationResearchSolveEventRecord(BaseModel):
    as_of_date: str
    scope_node_id: str | None = None
    scope_label: str
    scope_path: str | None = None
    scope_depth: int | None = None
    requested_target_dimension: str | None = None
    taxonomy_default_target_dimension: DefaultTargetDimension | None = None
    target_dimension: AllocationResearchTargetDimension | None = None
    solver_kind: str | None = None
    solver_detail: str | None = None
    solver_message: str | None = None
    covariance_model: str | None = None
    covariance_observations: int | None = None
    risk_contribution_mode: str | None = None
    missing_return_policy: AllocationResearchMissingReturnPolicy | None = None
    return_rows_before_policy: int | None = None
    return_rows_after_policy: int | None = None
    missing_return_row_count: int | None = None
    missing_return_row_fraction: float | None = None
    dropped_return_rows: list[dict[str, object]] = Field(default_factory=list)
    latest_complete_return_date: str | None = None
    trailing_complete_return_staleness_days: int | None = None
    calculation_frequency: Literal["daily", "weekly", "monthly"] | None = None
    gap_turnover: float | None = None
    current_weight_total: float | None = None
    target_weight_total: float | None = None
    max_weight_gap: float | None = None
    max_risk_share_gap: float | None = None
    estimated_risk_sleeve_volatility: float | None = None
    target_volatility: float | None = None
    gross_exposure: float | None = None
    risky_allocation_scaling_factor: float | None = None
    member_count: int = 0
    scope_solve_count: int | None = None


class AllocationResearchTargetWeightGapRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    current_weight: float | None = None
    target_weight: float | None = None
    gap: float | None = None
    current_value_base: float | None = None
    base_currency: str
    action: str
    execution_status: AllocationResearchExecutionStatus = "ready"
    execution_note: str | None = None


class AllocationResearchTargetRowRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    current_weight: float | None = None
    current_value_base: float | None = None
    default_target_dimension: DefaultTargetDimension | None = None
    selected_target_dimension: AllocationResearchTargetDimension | None = None
    source_target_set_type: TargetSetType | None = None
    source_target_set_id: str | None = None
    source_label: str | None = None
    selected_target_value: float | None = None
    target_weight: float | None = None
    target_risk_share: float | None = None
    implementation_weight: float | None = None
    gap_to_implementation: float | None = None
    action: str | None = None
    execution_status: AllocationResearchExecutionStatus = "ready"
    execution_note: str | None = None


class PolicyReplayPointRecord(BaseModel):
    date: str
    value: float | None = None


class PolicyReplaySleeveValueRecord(BaseModel):
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    value: float | None = None


class PolicyReplaySleevePointRecord(BaseModel):
    date: str
    sleeves: list[PolicyReplaySleeveValueRecord] = Field(default_factory=list)


class PolicyReplayMetricsRecord(BaseModel):
    method_version: Literal[
        "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
    ]
    history_reliability: PerformanceHistoryReliability
    start_date: str | None = None
    end_date: str | None = None
    period_return: float | None = None
    ytd_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    max_drawdown_start_date: str | None = None
    max_drawdown_end_date: str | None = None
    max_drawdown_days: int | None = None
    max_drawdown_recovery_date: str | None = None
    max_drawdown_recovery_days: int | None = None
    current_drawdown: float | None = None
    calmar_ratio: float | None = None

    @model_validator(mode="after")
    def validate_history_eligibility(self) -> "PolicyReplayMetricsRecord":
        history = self.history_reliability
        start_date = date.fromisoformat(self.start_date) if self.start_date else None
        end_date = date.fromisoformat(self.end_date) if self.end_date else None
        if history.start_date != start_date or history.end_date != end_date:
            raise ValueError(
                "Allocation Research metric history boundaries must match metric boundaries."
            )
        if not history.annualized_return_eligible and (
            self.annualized_return is not None or self.calmar_ratio is not None
        ):
            raise ValueError(
                "Ineligible Allocation Research history requires null annualized return "
                "and Calmar Ratio."
            )
        return self


class PolicyReplayRecord(BaseModel):
    rebalance_frequency: PolicyReplayRebalanceFrequency = "1m"
    common_history_start_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    lookback_days: int = 90
    points: list[PolicyReplayPointRecord] = Field(default_factory=list)
    metrics: PolicyReplayMetricsRecord | None = None
    top_sleeve_weight_points: list[PolicyReplaySleevePointRecord] = Field(
        default_factory=list
    )
    top_sleeve_contribution_points: list[PolicyReplaySleevePointRecord] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class PolicyReplayBenchmarkRecord(BaseModel):
    instrument_id: str | None = None
    label: str | None = None
    points: list[PolicyReplayPointRecord] = Field(default_factory=list)
    metrics: PolicyReplayMetricsRecord | None = None
    warnings: list[str] = Field(default_factory=list)


class PolicyReplayRelativeMetricsRecord(PolicyReplayMetricsRecord):
    excess_return: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None


class PolicyReplayBenchmarkComparisonResponse(BaseModel):
    policy_replay_benchmark: PolicyReplayBenchmarkRecord | None = None
    policy_replay_relative_metrics: PolicyReplayRelativeMetricsRecord | None = None


class AllocationResearchRunDetailRecord(BaseModel):
    headline: str | None = None
    coverage_note: str | None = None
    signals: list[AllocationResearchContextSignalRecord] = Field(default_factory=list)
    findings: list[AllocationResearchFindingRecord] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    top_holdings: list[AllocationResearchHoldingSnapshotRecord] = Field(default_factory=list)
    planning_groups: list[AllocationResearchPlanningGroupSnapshotRecord] = Field(
        default_factory=list
    )
    selected_scope: AllocationResearchScopeSelectionRecord | None = None
    target_assumptions: list[str] = Field(default_factory=list)
    target_rows: list[AllocationResearchTargetRowRecord] = Field(default_factory=list)
    member_targets: list[AllocationResearchMemberTargetRecord] = Field(default_factory=list)
    leaf_targets: list[AllocationResearchMemberTargetRecord] = Field(default_factory=list)
    solved_result_groups: list[AllocationResearchSolvedResultGroupRecord] = Field(
        default_factory=list
    )
    solve_event: AllocationResearchSolveEventRecord | None = None
    scope_solve_events: list[AllocationResearchSolveEventRecord] = Field(default_factory=list)
    target_weight_gaps: list[AllocationResearchTargetWeightGapRecord] = Field(
        default_factory=list
    )
    policy_replay: PolicyReplayRecord | None = None
    policy_replay_benchmark: PolicyReplayBenchmarkRecord | None = None
    policy_replay_relative_metrics: PolicyReplayRelativeMetricsRecord | None = None
    warnings: list[str] = Field(default_factory=list)


class AllocationResearchArtifactRecord(BaseModel):
    artifact_id: str
    label: str
    path: str
    media_type: str
    preview_kind: AllocationResearchArtifactPreviewKind


class AllocationResearchRunRecord(BaseModel):
    allocation_research_run_id: str
    portfolio_id: str
    job_type: str
    status: AllocationResearchRunStatus
    requested_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    as_of_date: date | None = None
    planning_taxonomy_id: str | None = None
    planning_taxonomy_name: str | None = None
    lookback_days: int = 90
    requested_by: str | None = None
    headline: str | None = None
    error_message: str | None = None
    reliability_state: AllocationResearchRunReliabilityState = "unassessed"
    is_current: bool = False
    reliability_reasons: list[str] = Field(default_factory=list)
    artifact_count: int = 0
    artifacts: list[AllocationResearchArtifactRecord] = Field(default_factory=list)
    detail: AllocationResearchRunDetailRecord | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class AllocationResearchRunCreateRequest(BaseModel):
    requested_by: str | None = None

    @field_validator("requested_by", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class AllocationResearchWorkbenchResponse(BaseModel):
    portfolio_id: str
    portfolio_name: str
    base_currency: str
    as_of_date: date
    default_planning_taxonomy_id: str | None = None
    planning_taxonomy_options: list[AllocationResearchPlanningTaxonomyOption] = Field(
        default_factory=list
    )
    planning_scope_options: list[AllocationResearchPlanningScopeOption] = Field(
        default_factory=list
    )
    calculation_frequency: AllocationResearchCalculationFrequencyProfile
    settings: AllocationResearchSettingsRecord
    risk_policy: PortfolioRiskPolicyRecord
    current_context: AllocationResearchCurrentContextRecord
    runs: list[AllocationResearchRunRecord] = Field(default_factory=list)
    selected_run: AllocationResearchRunRecord | None = None


class AllocationResearchArtifactContentResponse(BaseModel):
    filename: str
    path: str
    media_type: str
    encoding: Literal["text"]
    preview_kind: AllocationResearchArtifactPreviewKind
    content: str


class DefaultPlanningTaxonomyUpdateRequest(BaseModel):
    taxonomy_id: str | None = None

    @field_validator("taxonomy_id", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class DefaultPlanningTaxonomyResponse(BaseModel):
    portfolio_id: str
    default_planning_taxonomy_id: str | None = None


class TaxonomyCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    taxonomy_type: str = "custom"
    purpose: str | None = None
    primary_assignment_scope: TaxonomyAssignmentScope = "instrument"
    planning_enabled: bool = False
    budgeting_level: str | None = None
    root_default_target_dimension: DefaultTargetDimension = "weight"
    status: str = "active"
    source_template_ref: str | None = None

    @field_validator(
        "name",
        "taxonomy_type",
        "status",
        "root_default_target_dimension",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @field_validator("purpose", "budgeting_level", "source_template_ref", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_taxonomy_contract(self) -> "TaxonomyCreateRequest":
        if self.budgeting_level and not self.planning_enabled:
            raise ValueError("budgeting_level requires planning_enabled.")
        if self.planning_enabled and self.primary_assignment_scope != "instrument":
            raise ValueError(
                "planning_enabled taxonomies must use instrument assignment scope."
            )
        return self


class TaxonomyNodeCreateRequest(BaseModel):
    node_name: str = Field(min_length=1)
    node_code: str | None = None
    parent_taxonomy_node_id: str | None = None
    sort_order: int | None = None
    is_terminal: bool = True
    default_target_dimension: DefaultTargetDimension = "weight"
    status: str = "active"

    @field_validator("node_name", "status", "default_target_dimension", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @field_validator("node_code", "parent_taxonomy_node_id", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class TaxonomyUpdateRequest(BaseModel):
    name: str | None = None
    taxonomy_type: str | None = None
    purpose: str | None = None
    planning_enabled: bool | None = None
    budgeting_level: str | None = None
    root_default_target_dimension: DefaultTargetDimension | None = None
    status: str | None = None

    @field_validator(
        "name",
        "taxonomy_type",
        "status",
        "root_default_target_dimension",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("purpose", "budgeting_level", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_taxonomy_contract(self) -> "TaxonomyUpdateRequest":
        if self.budgeting_level and self.planning_enabled is False:
            raise ValueError("budgeting_level requires planning_enabled.")
        return self


class TaxonomyNodeUpdateRequest(BaseModel):
    node_name: str | None = None
    node_code: str | None = None
    parent_taxonomy_node_id: str | None = None
    sort_order: int | None = None
    default_target_dimension: DefaultTargetDimension | None = None
    status: str | None = None

    @field_validator("node_name", "status", "default_target_dimension", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("node_code", "parent_taxonomy_node_id", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class TaxonomyAssignmentCreateRequest(BaseModel):
    target_scope: TaxonomyAssignmentScope
    target_entity_id: str = Field(min_length=1)
    taxonomy_node_id: str = Field(min_length=1)
    status: str = "active"

    @field_validator("target_entity_id", "taxonomy_node_id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)


class TaxonomyAssignmentUpdateRequest(BaseModel):
    taxonomy_node_id: str | None = None
    status: str | None = None

    @field_validator("taxonomy_node_id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)


class TargetSetLineInput(BaseModel):
    target_member_type: TargetMemberType | None = None
    target_member_id: str | None = None
    taxonomy_node_id: str | None = None
    target_weight: float | None = None
    target_risk_share: float | None = None
    notes: str | None = None

    @field_validator("target_member_type", mode="before")
    @classmethod
    def validate_target_member_type(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("target_member_id", "taxonomy_node_id", mode="before")
    @classmethod
    def validate_optional_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("notes", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_target_member_contract(self) -> "TargetSetLineInput":
        if self.taxonomy_node_id:
            if self.target_member_type and self.target_member_type != "taxonomy_node":
                raise ValueError(
                    "taxonomy_node_id cannot be combined with a non-node target_member_type."
                )
            if self.target_member_id and self.target_member_id != self.taxonomy_node_id:
                raise ValueError(
                    "taxonomy_node_id must match target_member_id when both are provided."
                )
            self.target_member_type = "taxonomy_node"
            self.target_member_id = self.taxonomy_node_id

        if not self.target_member_type or not self.target_member_id:
            raise ValueError("Target set lines require a target member reference.")

        if self.target_member_type == "taxonomy_node":
            self.taxonomy_node_id = self.target_member_id
        else:
            self.taxonomy_node_id = None
        return self


class TargetSetCreateRequest(BaseModel):
    comparator_taxonomy_node_id: str | None = None
    target_set_type: TargetSetType
    name: str = Field(min_length=1)
    weight_enabled: bool = False
    risk_budget_enabled: bool = False
    status: str = "active"
    notes: str | None = None
    lines: list[TargetSetLineInput] = Field(default_factory=list)

    @field_validator("comparator_taxonomy_node_id", "notes", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @field_validator("name", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @model_validator(mode="after")
    def validate_target_set_contract(self) -> "TargetSetCreateRequest":
        if not self.weight_enabled and not self.risk_budget_enabled:
            raise ValueError("At least one target dimension must be enabled.")
        if not self.lines:
            raise ValueError("Target set lines are required.")
        return self


class TargetSetUpdateRequest(BaseModel):
    name: str | None = None
    weight_enabled: bool | None = None
    risk_budget_enabled: bool | None = None
    status: str | None = None
    notes: str | None = None
    lines: list[TargetSetLineInput] | None = None

    @field_validator("name", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("notes", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_target_set_contract(self) -> "TargetSetUpdateRequest":
        if self.weight_enabled is False and self.risk_budget_enabled is False:
            raise ValueError("At least one target dimension must be enabled.")
        return self


ContributionAxis = Literal[
    "instrument", "account", "instrument_type", "currency", "taxonomy"
]
CalculationBucket = Literal[
    "initial_value",
    "final_value",
    "beginning_weight",
    "capital_gains",
    "realized_capital_gains",
    "unrealized_capital_gains",
    "earnings",
    "fees",
    "taxes",
    "cash_currency_gains",
    "instrument_currency_gains",
    "total_pnl",
    "period_contribution",
    "residual_delta",
]
CalculationEntryBucket = Literal[
    "deposits",
    "withdrawals",
    "earnings",
    "fees",
    "taxes",
    "realized_capital_gains",
]


class DailyContributionSliceRecord(BaseModel):
    as_of_date: date
    axis: ContributionAxis
    group_key: str
    group_label: str
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    beginning_value_base: float | None = None
    ending_value_base: float | None = None
    beginning_weight: float | None = None
    ending_weight: float | None = None
    cash_balance_base: float | None = None
    position_market_value_base: float | None = None
    open_cost_basis_base: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_change: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    fee_amount: float | None = None
    tax_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    daily_return: float | None = None
    daily_contribution: float | None = None


class ContributionLineRecord(BaseModel):
    axis: ContributionAxis
    group_key: str
    group_label: str
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    start_value_base: float | None = None
    end_value_base: float | None = None
    beginning_weight: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl_change: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    fee_amount: float | None = None
    tax_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    period_contribution: float | None = None


class ContributionReportSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    slice_count: int = 0
    group_count: int = 0
    observation_count: int = 0
    start_nav: float | None = None
    end_nav: float | None = None
    portfolio_arithmetic_return: float | None = None
    portfolio_cumulative_twr: float | None = None
    total_period_contribution: float | None = None
    contribution_residual: float | None = None


class ContributionReportResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionReportSummary
    lines: list[ContributionLineRecord]
    daily_slices: list[DailyContributionSliceRecord]


class ContributionCalendarBucketRecord(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    start_date: date
    end_date: date
    axis: ContributionAxis
    group_key: str
    group_label: str
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    observation_count: int = 0
    beginning_value_base: float | None = None
    ending_value_base: float | None = None
    beginning_weight: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl_change: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    fee_amount: float | None = None
    tax_amount: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    bucket_contribution: float | None = None


class ContributionCalendarSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    frequency: Literal["monthly", "weekly"]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    start_date: date | None = None
    end_date: date | None = None
    bucket_count: int = 0
    group_count: int = 0
    observation_count: int = 0
    total_bucket_contribution: float | None = None
    contribution_residual: float | None = None


class ContributionCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionCalendarSummary
    buckets: list[ContributionCalendarBucketRecord]


ContributionBucket = Literal[
    "start_value",
    "end_value",
    "beginning_weight",
    "average_weight",
    "ending_weight",
    "realized_pnl",
    "unrealized_pnl_change",
    "income_cash_amount",
    "expense_cash_amount",
    "fee_amount",
    "tax_amount",
    "cash_currency_gains",
    "instrument_currency_gains",
    "total_pnl",
    "contribution",
]


class ContributionBucketGroupRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    bucket: ContributionBucket
    group_key: str
    group_label: str
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    amount: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    total_pnl: float | None = None
    contribution: float | None = None


class ContributionBucketSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: ContributionBucket
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    start_date: date | None = None
    end_date: date | None = None
    group_count: int = 0
    total_amount: float | None = None


class ContributionBucketResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionBucketSummary
    groups: list[ContributionBucketGroupRecord]


class ContributionBucketCalendarBucketRecord(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    axis: ContributionAxis
    taxonomy_id: str | None = None
    bucket: ContributionBucket
    group_key: str
    group_label: str
    start_date: date
    end_date: date
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    observation_count: int = 0
    amount: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    total_pnl: float | None = None
    contribution: float | None = None


class ContributionBucketCalendarSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: ContributionBucket
    frequency: Literal["monthly", "weekly"]
    twr_state: TwrState
    twr_reliability_status: TwrReliabilityStatus
    twr_reliability_reasons: list[TwrReliabilityReason]
    start_date: date | None = None
    end_date: date | None = None
    bucket_count: int = 0
    group_count: int = 0
    observation_count: int = 0
    total_amount: float | None = None


class ContributionBucketCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionBucketCalendarSummary
    buckets: list[ContributionBucketCalendarBucketRecord]


ContributionEntryBucket = Literal[
    "realized_pnl",
    "income_cash_amount",
    "expense_cash_amount",
    "fee_amount",
    "tax_amount",
]


class ContributionEntryRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    bucket: ContributionEntryBucket
    entry_kind: Literal["transaction", "realization"]
    component_kind: str
    transaction_id: str
    transaction_type: str
    trade_date: date | None = None
    settlement_date: date | None = None
    group_key: str
    group_label: str
    account_id: str | None = None
    account_name: str | None = None
    instrument_id: str | None = None
    instrument_name: str | None = None
    currency: str
    local_amount: float | None = None
    base_amount: float | None = None
    note: str | None = None
    stale_fx_flag: bool = False


class ContributionEntriesSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: ContributionEntryBucket
    start_date: date | None = None
    end_date: date | None = None
    entry_count: int = 0
    total_amount: float | None = None


class ContributionEntriesResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionEntriesSummary
    entries: list[ContributionEntryRecord]


class ContributionEntryCalendarBucketRecord(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    axis: ContributionAxis
    taxonomy_id: str | None = None
    contribution_bucket: ContributionEntryBucket
    group_key: str
    group_label: str
    start_date: date
    end_date: date
    entry_count: int = 0
    total_amount: float | None = None


class ContributionEntryCalendarSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: ContributionEntryBucket
    frequency: Literal["monthly", "weekly"]
    start_date: date | None = None
    end_date: date | None = None
    bucket_count: int = 0
    group_count: int = 0
    entry_count: int = 0
    total_amount: float | None = None


class ContributionEntryCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: ContributionEntryCalendarSummary
    buckets: list[ContributionEntryCalendarBucketRecord]


class BoundaryGroupRecord(BaseModel):
    axis: Literal["taxonomy"]
    taxonomy_id: str
    group_key: str
    group_label: str
    position_count: int = 0
    instrument_count: int = 0
    cost_basis_base: float | None = None
    market_value_base: float | None = None
    unrealized_pnl: float | None = None
    portfolio_weight: float | None = None
    open_position_lot_count: int = 0


class BoundaryGroupsSummary(BaseModel):
    axis: Literal["taxonomy"]
    taxonomy_id: str
    start_date: date | None = None
    end_date: date | None = None
    start_group_count: int = 0
    end_group_count: int = 0
    start_total_market_value_base: float | None = None
    end_total_market_value_base: float | None = None


class BoundaryGroupsResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: BoundaryGroupsSummary
    start_groups: list[BoundaryGroupRecord]
    end_groups: list[BoundaryGroupRecord]


class PeriodCalculationGroupChildRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    parent_group_key: str
    parent_group_label: str
    item_key: str
    item_label: str
    item_kind: Literal["instrument", "cash"]
    beginning_weight: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    period_return: float | None = None
    initial_value: float | None = None
    final_value: float | None = None
    delta: float | None = None
    residual_delta: float | None = None
    capital_gains: float | None = None
    realized_capital_gains: float | None = None
    unrealized_pnl_change: float | None = None
    earnings: float | None = None
    expense_cash_amount: float | None = None
    fees: float | None = None
    taxes: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    period_contribution: float | None = None
    risk_calculation_frequency: PortfolioCalculationFrequency
    risk_return_observation_count: int
    risk_annualization_periods_per_year: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    correlation_to_portfolio: float | None
    beta_to_portfolio: float | None
    realized_risk_contribution: float | None


class PeriodCalculationGroupRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str
    group_label: str
    beginning_weight: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    period_return: float | None = None
    initial_value: float | None = None
    final_value: float | None = None
    delta: float | None = None
    residual_delta: float | None = None
    capital_gains: float | None = None
    realized_capital_gains: float | None = None
    unrealized_pnl_change: float | None = None
    earnings: float | None = None
    expense_cash_amount: float | None = None
    fees: float | None = None
    taxes: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    period_contribution: float | None = None
    risk_calculation_frequency: PortfolioCalculationFrequency
    risk_return_observation_count: int
    risk_annualization_periods_per_year: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    correlation_to_portfolio: float | None
    beta_to_portfolio: float | None
    realized_risk_contribution: float | None
    children: list[PeriodCalculationGroupChildRecord] = Field(default_factory=list)


class PeriodCalculationGroupsSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    group_count: int = 0
    total_initial_value: float | None = None
    total_final_value: float | None = None
    total_delta: float | None = None
    total_residual_delta: float | None = None
    total_pnl: float | None = None
    total_period_contribution: float | None = None
    contribution_residual: float | None = None
    risk_calculation_frequency: PortfolioCalculationFrequency
    risk_frequency_status_label: str | None
    risk_return_observation_count: int
    risk_annualization_periods_per_year: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None


class PeriodCalculationGroupsResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationGroupsSummary
    groups: list[PeriodCalculationGroupRecord]


class PeriodCalculationGroupCalendarBucketRecord(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    start_date: date
    end_date: date
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str
    group_label: str
    nav_coverage_state: CoverageState
    nav_coverage_reason_codes: list[str]
    book_pnl_coverage_state: CoverageState
    book_pnl_coverage_reason_codes: list[str]
    observation_count: int = 0
    beginning_weight: float | None = None
    average_weight: float | None = None
    ending_weight: float | None = None
    initial_value: float | None = None
    final_value: float | None = None
    delta: float | None = None
    residual_delta: float | None = None
    capital_gains: float | None = None
    realized_capital_gains: float | None = None
    unrealized_pnl_change: float | None = None
    earnings: float | None = None
    expense_cash_amount: float | None = None
    fees: float | None = None
    taxes: float | None = None
    cash_currency_gains: float | None = None
    instrument_currency_gains: float | None = None
    total_pnl: float | None = None
    bucket_contribution: float | None = None


class PeriodCalculationGroupsCalendarSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    frequency: Literal["monthly", "weekly"]
    start_date: date | None = None
    end_date: date | None = None
    bucket_count: int = 0
    group_count: int = 0
    observation_count: int = 0
    total_delta: float | None = None
    total_residual_delta: float | None = None
    total_pnl: float | None = None
    total_bucket_contribution: float | None = None
    contribution_residual: float | None = None


class PeriodCalculationGroupsCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationGroupsCalendarSummary
    buckets: list[PeriodCalculationGroupCalendarBucketRecord]


class PeriodCalculationBucketGroupRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    bucket: CalculationBucket
    group_key: str
    group_label: str
    amount: float | None = None
    initial_value: float | None = None
    final_value: float | None = None
    total_pnl: float | None = None
    period_contribution: float | None = None


class PeriodCalculationBucketSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: CalculationBucket
    start_date: date | None = None
    end_date: date | None = None
    group_count: int = 0
    total_amount: float | None = None


class PeriodCalculationBucketResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationBucketSummary
    groups: list[PeriodCalculationBucketGroupRecord]


class PeriodCalculationEntryRecord(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    bucket: CalculationEntryBucket
    entry_kind: Literal["transaction", "realization"]
    component_kind: str
    transaction_id: str
    transaction_type: str
    trade_date: date | None = None
    settlement_date: date | None = None
    effective_date: date | None = None
    group_key: str
    group_label: str
    account_id: str | None = None
    account_name: str | None = None
    instrument_id: str | None = None
    instrument_name: str | None = None
    currency: str
    local_amount: float | None = None
    base_amount: float | None = None
    note: str | None = None
    stale_fx_flag: bool = False


class PeriodCalculationEntriesSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: CalculationEntryBucket
    start_date: date | None = None
    end_date: date | None = None
    entry_count: int = 0
    total_amount: float | None = None


class PeriodCalculationEntriesResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationEntriesSummary
    entries: list[PeriodCalculationEntryRecord]


class PeriodCalculationEntryCalendarBucketRecord(BaseModel):
    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    axis: ContributionAxis
    taxonomy_id: str | None = None
    calculation_bucket: CalculationEntryBucket
    group_key: str
    group_label: str
    start_date: date
    end_date: date
    entry_count: int = 0
    total_amount: float | None = None


class PeriodCalculationEntryCalendarSummary(BaseModel):
    axis: ContributionAxis
    taxonomy_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    bucket: CalculationEntryBucket
    frequency: Literal["monthly", "weekly"]
    start_date: date | None = None
    end_date: date | None = None
    bucket_count: int = 0
    group_count: int = 0
    entry_count: int = 0
    total_amount: float | None = None


class PeriodCalculationEntryCalendarResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PeriodCalculationEntryCalendarSummary
    buckets: list[PeriodCalculationEntryCalendarBucketRecord]


class _TransactionFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType
    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account_id: str
    settlement_cash_account_id: str | None = None
    instrument_id: str | None = None
    quantity: TransactionInputDecimal | None = Field(default=None, ge=0)
    price: TransactionInputDecimal | None = Field(default=None, ge=0)
    gross_amount: TransactionInputDecimal = Field(ge=0)
    counter_amount: TransactionInputDecimal | None = Field(default=None, ge=0)
    quoted_fx_rate: TransactionInputDecimal | None = Field(default=None, gt=0)
    consideration_basis: TransactionConsiderationBasis | None = None
    fees: TransactionInputDecimal = Field(default=Decimal("0.00"), ge=0)
    taxes: TransactionInputDecimal = Field(default=Decimal("0.00"), ge=0)
    currency: str = Field(min_length=1, max_length=8)
    transfer_scope: TransferScope | None = None
    transfer_object_type: TransferObjectType | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    note: str | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized not in SUPPORTED_PORTFOLIO_CURRENCIES:
                raise ValueError(
                    "Transaction currency must be one of USD, HKD, or CNY."
                )
            return normalized
        return value

    @field_validator("trade_time", mode="before")
    @classmethod
    def validate_trade_time(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                parsed = time.fromisoformat(normalized)
            except ValueError as exc:
                raise ValueError("trade_time must use HH:MM format.") from exc
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def normalize_quantity_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=QUANTITY_STORAGE_QUANTUM)

    @field_validator("price", mode="before")
    @classmethod
    def normalize_price_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=PRICE_STORAGE_QUANTUM)

    @field_validator("gross_amount", "counter_amount", "fees", "taxes", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=AMOUNT_STORAGE_QUANTUM)

    @field_validator("quoted_fx_rate", mode="before")
    @classmethod
    def normalize_quoted_fx_rate_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=FX_RATE_STORAGE_QUANTUM)

    @model_validator(mode="after")
    def validate_amount_contract(self) -> Self:
        if (
            self.transaction_type in {"deposit", "withdrawal"}
            and self.settlement_date is None
        ):
            raise ValueError(
                "deposit and withdrawal require an explicit settlement_date (cash value date)."
            )
        if self.settlement_date is None:
            self.settlement_date = self.trade_date
        if self.settlement_date < self.trade_date:
            raise ValueError("settlement_date must not be earlier than trade_date.")
        if (
            self.entitlement_date is not None
            and self.entitlement_date > self.trade_date
        ):
            raise ValueError("entitlement_date must not be later than trade_date.")
        if (
            self.acquisition_date is not None
            and self.acquisition_date > self.trade_date
        ):
            raise ValueError("acquisition_date must not be later than trade_date.")

        if self.transaction_type in {"buy", "sell"}:
            if not self.instrument_id:
                raise ValueError("Security transactions require instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Security transactions require positive quantity.")
            if self.price is not None and self.price <= 0:
                raise ValueError("Security transaction price must be positive when provided.")
            if self.consideration_basis is None:
                self.consideration_basis = "source_reported"
            if self.consideration_basis == "exact_quantity_price" and (
                self.price is None or self.price <= 0
            ):
                raise ValueError(
                    "exact_quantity_price security transactions require positive price."
                )

        if self.transaction_type in {"dividend", "coupon"}:
            if not self.instrument_id:
                raise ValueError("Income transactions require instrument_id.")
            if self.quantity is not None or self.price is not None:
                raise ValueError(
                    "Dividend and coupon must not carry quantity or price."
                )

        if self.transaction_type == "interest":
            if self.instrument_id is not None:
                raise ValueError("Interest must not carry instrument_id.")
            if self.quantity is not None or self.price is not None:
                raise ValueError("Interest must not carry quantity or price.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Interest must not carry settlement_cash_account_id.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Interest must not carry fees or taxes.")

        if self.transaction_type == "return_of_capital":
            if not self.instrument_id:
                raise ValueError("Return of capital requires instrument_id.")
            if self.quantity is not None:
                raise ValueError("Return of capital must not carry quantity.")
            if self.price is not None:
                raise ValueError("Return of capital must not carry price.")
            if self.entitlement_date is not None:
                raise ValueError(
                    "Return of capital does not yet support entitlement_date."
                )

        if self.transaction_type == "dividend_reinvestment":
            if not self.instrument_id:
                raise ValueError("Dividend reinvestment requires instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Dividend reinvestment requires positive quantity.")
            if self.consideration_basis is None:
                self.consideration_basis = "source_reported"
            if self.consideration_basis == "exact_quantity_price" and (
                self.price is None or self.price <= 0
            ):
                raise ValueError(
                    "exact_quantity_price dividend reinvestment requires positive price."
                )
            if self.entitlement_date is not None:
                raise ValueError(
                    "Dividend reinvestment does not yet support entitlement_date."
                )
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "Dividend reinvestment must not carry settlement_cash_account_id."
                )
            if self.price is not None:
                if self.price <= 0:
                    raise ValueError(
                        "Dividend reinvestment price must be positive when provided."
                    )
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Dividend reinvestment must not carry fees or taxes.")

        if self.transaction_type == "maturity_redemption":
            if not self.instrument_id:
                raise ValueError("Maturity redemption requires instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Maturity redemption requires positive quantity.")
            if self.price is not None:
                raise ValueError("Maturity redemption must not carry price.")

        if self.transaction_type in {"deposit", "withdrawal"}:
            if self.instrument_id is not None:
                raise ValueError("Cash-flow transactions must not carry instrument_id.")
            if self.quantity is not None or self.price is not None:
                raise ValueError(
                    "Cash-flow transactions must not carry quantity or price."
                )
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "Cash-flow transactions must not carry settlement_cash_account_id."
                )
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Cash-flow transactions must not carry fees or taxes.")

        if self.transaction_type == "fx_conversion":
            if self.instrument_id is not None:
                raise ValueError("FX conversion must not carry instrument_id.")
            if self.quantity is not None or self.price is not None:
                raise ValueError("FX conversion must not carry quantity or price.")
            if self.gross_amount <= 0:
                raise ValueError("FX conversion requires positive gross_amount.")
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "FX conversion must not carry settlement_cash_account_id."
                )
            if not self.counterparty_account_id:
                raise ValueError("FX conversion requires counterparty_account_id.")
            if self.counter_amount is None or self.counter_amount <= 0:
                raise ValueError("FX conversion requires positive counter_amount.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("FX conversion must not carry fees or taxes.")

        if self.transaction_type != "fx_conversion" and (
            self.counter_amount is not None or self.quoted_fx_rate is not None
        ):
            raise ValueError(
                "counter_amount and quoted_fx_rate are only allowed for fx_conversion."
            )

        if (
            self.transaction_type
            not in {"fx_conversion", "transfer_in", "transfer_out"}
            and self.counterparty_account_id is not None
        ):
            raise ValueError(
                "counterparty_account_id is only allowed for FX conversion and internal transfer."
            )

        if self.transaction_type in {"fee", "tax"}:
            if self.quantity is not None:
                raise ValueError("Fee and tax transactions must not carry quantity.")
            if self.price is not None:
                raise ValueError("Fee and tax transactions must not carry price.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError(
                    "Fee and tax transactions must not carry nested fees or taxes."
                )
            if self.entitlement_date is not None and not self.instrument_id:
                raise ValueError(
                    "entitlement_date on fee and tax requires instrument_id."
                )

        if self.entitlement_date is not None and self.transaction_type not in {
            "dividend",
            "coupon",
            "fee",
            "tax",
        }:
            raise ValueError(
                "entitlement_date is only allowed for dividend, coupon, fee, and tax."
            )

        if self.transaction_type in {"transfer_in", "transfer_out"}:
            if self.transfer_scope != "internal_portfolio":
                raise ValueError(
                    "Transfer transactions require transfer_scope=internal_portfolio."
                )
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "Transfer transactions must not carry settlement_cash_account_id."
                )
            if self.transfer_object_type is None:
                raise ValueError("Transfer transactions require transfer_object_type.")
            if not self.transfer_group_id:
                raise ValueError("Transfer transactions require transfer_group_id.")
            if not self.counterparty_account_id:
                raise ValueError(
                    "Transfer transactions require counterparty_account_id."
                )
            if self.transfer_object_type == "cash":
                if (
                    self.instrument_id is not None
                    or self.quantity is not None
                    or self.price is not None
                ):
                    raise ValueError(
                        "Cash transfers must not carry instrument, quantity, or price."
                    )
            if self.transfer_object_type == "position":
                if not self.instrument_id:
                    raise ValueError("Position transfers require instrument_id.")
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError("Position transfers require positive quantity.")
                if self.price is not None:
                    raise ValueError("Position transfers must not carry price.")
                if self.gross_amount < 0:
                    raise ValueError(
                        "Position transfers require non-negative transferred cost basis."
                    )
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Transfer transactions must not carry fees or taxes.")

        if self.transaction_type not in {"transfer_in", "transfer_out"} and (
            self.transfer_scope is not None
            or self.transfer_object_type is not None
            or self.transfer_group_id is not None
        ):
            raise ValueError(
                "Transfer fields are only allowed for transfer transactions."
            )

        if self.transaction_type == "opening_balance":
            if self.instrument_id:
                if self.acquisition_date is None:
                    self.acquisition_date = self.trade_date
            elif self.acquisition_date is not None:
                raise ValueError(
                    "Cash opening balance must not carry acquisition_date."
                )
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "Opening balance must not carry settlement_cash_account_id."
                )
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Opening balance must not carry fees or taxes.")
            if self.instrument_id:
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError(
                        "Security opening balance requires positive quantity."
                    )
                if self.consideration_basis is None:
                    self.consideration_basis = "source_reported"
                if self.consideration_basis == "exact_quantity_price" and (
                    self.price is None or self.price <= 0
                ):
                    raise ValueError(
                        "exact_quantity_price opening balance requires positive price."
                    )
                if self.price is not None:
                    if self.price <= 0:
                        raise ValueError(
                            "Security opening balance price must be positive when provided."
                        )
            elif self.quantity is not None or self.price is not None:
                raise ValueError(
                    "Cash opening balance must not carry quantity or price."
                )
        elif self.acquisition_date is not None:
            raise ValueError(
                "acquisition_date is only allowed for security opening balance."
            )

        uses_consideration_basis = (
            self.transaction_type in {"buy", "sell", "dividend_reinvestment"}
            or (
                self.transaction_type == "opening_balance"
                and self.instrument_id is not None
            )
        )
        if not uses_consideration_basis and self.consideration_basis is not None:
            raise ValueError(
                "consideration_basis is only allowed for security price/amount transactions."
            )

        return self


class TransactionCreateRequest(_TransactionFactRequest):
    actor: TransactionActorInput
    change_reason: str | None = Field(default=None, min_length=3, max_length=500)

    @field_validator("change_reason", mode="before")
    @classmethod
    def normalize_optional_change_reason(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)


class TransactionUpdateRequest(_TransactionFactRequest):
    expected_revision_id: str = Field(min_length=1, max_length=128)
    expected_revision_number: int = Field(ge=1)
    actor: TransactionActorInput
    change_reason: str = Field(min_length=3, max_length=500)

    @field_validator("expected_revision_id", "change_reason", mode="before")
    @classmethod
    def normalize_revision_metadata(cls, value: object) -> object:
        return _normalize_required_text(value)


class InternalTransferCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    transfer_object_type: TransferObjectType
    from_account_id: str
    to_account_id: str
    instrument_id: str | None = None
    quantity: TransactionInputDecimal | None = Field(default=None, ge=0)
    gross_amount: TransactionInputDecimal | None = Field(ge=0)
    note: str | None = None
    transfer_group_id: str | None = None
    actor: TransactionActorInput
    change_reason: str | None = Field(default=None, min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_internal_transfer(self) -> Self:
        settlement_date = self.settlement_date or self.trade_date
        if settlement_date < self.trade_date:
            raise ValueError("settlement_date must not be earlier than trade_date.")
        if self.from_account_id == self.to_account_id:
            raise ValueError(
                "Internal transfer requires distinct source and destination accounts."
            )
        if self.transfer_object_type == "cash":
            if self.gross_amount is None or self.gross_amount <= 0:
                raise ValueError("Cash transfer requires positive amount.")
            if self.instrument_id is not None or self.quantity is not None:
                raise ValueError(
                    "Cash transfer must not carry instrument_id or quantity."
                )
        if self.transfer_object_type == "position":
            if self.gross_amount is None:
                raise ValueError(
                    "Position transfer requires exact transferred local cost basis."
                )
            if not self.instrument_id:
                raise ValueError("Position transfer requires instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Position transfer requires positive quantity.")
        return self

    @field_validator("trade_time", mode="before")
    @classmethod
    def validate_trade_time(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                parsed = time.fromisoformat(normalized)
            except ValueError as exc:
                raise ValueError("trade_time must use HH:MM format.") from exc
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def normalize_quantity_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=QUANTITY_STORAGE_QUANTUM)

    @field_validator("gross_amount", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _validate_numeric_input_scale(value, quantum=AMOUNT_STORAGE_QUANTUM)

    @field_validator("change_reason", mode="before")
    @classmethod
    def normalize_optional_change_reason(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)


class TransactionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision_id: str = Field(min_length=1, max_length=128)
    expected_revision_number: int = Field(ge=1)
    actor: TransactionActorInput
    change_reason: str = Field(min_length=3, max_length=500)

    @field_validator("expected_revision_id", "change_reason", mode="before")
    @classmethod
    def normalize_revision_metadata(cls, value: object) -> object:
        return _normalize_required_text(value)


class TransactionRevisionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType
    trade_date: date
    trade_time: str
    trade_at: str
    trade_timezone: str
    trade_time_is_estimated: bool = False
    settlement_date: date
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account_id: str
    settlement_cash_account_id: str | None = None
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    quantity: TransactionDecimal | None = None
    price: TransactionDecimal | None = None
    gross_amount: TransactionDecimal
    counter_amount: TransactionDecimal | None = None
    quoted_fx_rate: TransactionDecimal | None = None
    fees: TransactionDecimal = Decimal("0.00")
    taxes: TransactionDecimal = Decimal("0.00")
    consideration_basis: TransactionConsiderationBasis | None = None
    numeric_scale_state: TransactionNumericScaleState
    quantity_input_scale: int | None = Field(default=None, ge=0, le=12)
    price_input_scale: int | None = Field(default=None, ge=0, le=12)
    gross_amount_input_scale: int = Field(ge=0, le=8)
    counter_amount_input_scale: int | None = Field(default=None, ge=0, le=8)
    quoted_fx_rate_input_scale: int | None = Field(default=None, ge=0, le=18)
    fees_input_scale: int = Field(ge=0, le=8)
    taxes_input_scale: int = Field(ge=0, le=8)
    currency: SupportedCurrency
    transfer_scope: TransferScope | None = None
    transfer_object_type: TransferObjectType | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    note: str | None = None
    created_at: str | None = None


class TransactionRevisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    transaction_id: str
    portfolio_id: str
    revision_number: int = Field(ge=1)
    previous_revision_id: str | None = None
    mutation_id: str
    operation: TransactionRevisionOperation
    lifecycle_status: TransactionLifecycleStatus
    recorded_at: datetime
    actor: TransactionActorRecord
    change_reason: str | None = None
    changed_fields: list[str]
    snapshot: TransactionRevisionSnapshot | None


class TransactionRevisionHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    transaction_id: str
    current_revision_id: str
    current_revision_number: int = Field(ge=1)
    lifecycle_status: TransactionLifecycleStatus
    revisions: list[TransactionRevisionRecord]


class TransactionBatchResponse(BaseModel):
    portfolio_id: str
    mutation_id: str
    created_count: int
    transfer_group_id: str | None = None
    transactions: list[TransactionRecord]


class TransactionDeleteResponse(BaseModel):
    portfolio_id: str
    mutation_id: str
    deleted_count: int
    deleted_transaction_ids: list[str]
    transfer_group_id: str | None = None
    revisions: list[TransactionRevisionRecord]


CalculationRunApiStatus = Literal[
    "capturing",
    "queued",
    "running",
    "succeeded",
    "published",
    "superseded",
    "failed",
]


class PortfolioDailyCalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str = Field(min_length=1, max_length=255)
    as_of_date: date

    @field_validator("portfolio_id")
    @classmethod
    def normalize_portfolio_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("portfolio_id must not be blank")
        return normalized


class PortfolioDailyCalculationAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    manifest_id: str | None
    portfolio_id: str
    status: CalculationRunApiStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    captured_generation: int = Field(ge=0)
    deduplicated: bool
    canonical_manifest_hash: str | None


class CalculationRunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    portfolio_id: str
    calculation_kind: str
    status: CalculationRunApiStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    captured_generation: int = Field(ge=0)
    manifest_id: str | None
    publication_id: str | None
    published_output_hash: str | None
    published_fencing_token: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# Portfolio Daily v1 published-read contracts use the shared strict output
# decimal: Decimal in-process, canonical plain string on the JSON wire.
PortfolioDailyDecimal = CanonicalOutputDecimal


class PortfolioDailyPublicationMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: str
    run_id: str
    manifest_id: str
    published_fencing_token: int = Field(gt=0)
    published_at: datetime
    calculated_at: datetime
    requested_as_of: date
    effective_as_of: date
    output_range_start: date
    output_range_end: date
    methodology_version: str
    output_schema_version: str
    canonical_output_hash: str
    captured_generation: int = Field(ge=0)
    current_generation: int = Field(ge=0)
    stale: bool
    pending: bool
    pending_generation: int | None = Field(default=None, ge=0)
    pending_run_id: str | None = None
    pending_run_status: CalculationRunApiStatus | None = None
    pending_intent_id: str | None = None
    pending_intent_status: Literal["pending", "materialized"] | None = None
    coverage_state: Literal["complete", "partial", "unavailable"]
    reason_codes: list[str]


class PortfolioWorkspaceSummarySectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    href: str
    status: Literal["published", "separate-calculation", "facts"]


class PortfolioWorkspaceSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    portfolio_name: str
    base_currency: SupportedCurrency
    operating_profile: PortfolioOperatingProfile
    valuation_timezone: str
    as_of_date: date
    measured_nav: bool
    nav: PortfolioDailyDecimal | None
    economic_pnl: PortfolioDailyDecimal | None
    subperiod_twr_method50: PortfolioDailyDecimal | None
    subperiod_twr_published: PortfolioDailyDecimal | None
    return_period_start_date: date | None
    return_period_end_date: date | None
    return_period_day_count: int | None = Field(default=None, ge=0)
    instrument_count: int = Field(ge=0)
    nav_coverage_state: Literal["complete", "partial", "unavailable"]
    nav_reason_codes: list[str]
    book_pnl_coverage_state: Literal["complete", "partial", "unavailable"]
    book_pnl_reason_codes: list[str]
    return_coverage_state: Literal["complete", "partial", "unavailable"]
    return_reason_codes: list[str]
    valuation_endpoint_status: Literal["fresh", "carry_forward", "stale", "unavailable"]
    valuation_reason_codes: list[str]
    default_planning_taxonomy_id: str | None
    publication: PortfolioDailyPublicationMetadataResponse
    toolbar_label: str
    sections: list[PortfolioWorkspaceSummarySectionRecord]


class PortfolioDailyPublishedAccountPositionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    account_id: str
    instrument_id: str
    instrument_name: str
    instrument_type: str
    currency: str
    quantity_exact: PortfolioDailyDecimal
    measured_price: bool
    adopted_price_exact: PortfolioDailyDecimal | None
    measured_market_value: bool
    market_value_local_exact: PortfolioDailyDecimal | None
    market_value_base_exact: PortfolioDailyDecimal | None
    measured_base_cost: bool
    cost_basis_local_exact: PortfolioDailyDecimal
    cost_basis_base_exact: PortfolioDailyDecimal | None
    open_lot_count: int = Field(ge=0)
    valuation_coverage_state: Literal["complete", "partial", "unavailable"]
    valuation_reason_codes: list[str]
    base_cost_coverage_state: Literal["complete", "unavailable"]
    base_cost_reason_codes: list[str]


class PortfolioDailyPublishedAccountBalanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    account_id: str
    component_type: Literal[
        "settled_cash",
        "pending_receivable",
        "pending_payable",
        "income_accrual",
        "fee_accrual",
        "tax_accrual",
        "other_accrual",
    ]
    component_key: str
    currency: str
    local_amount: PortfolioDailyDecimal
    measured_base_amount: bool
    base_amount_exact: PortfolioDailyDecimal | None
    coverage_state: Literal["complete", "partial", "unavailable"]
    reason_codes: list[str]


class AccountWorkspaceAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: AccountRecord
    default_settlement_cash_account_name: str | None = None
    linked_transaction_count: int = Field(ge=0)
    balance_component_count: int = Field(ge=0)
    position_line_count: int = Field(ge=0)
    open_lot_count: int = Field(ge=0)
    settled_cash_local: PortfolioDailyDecimal | None
    settled_cash_base_exact: PortfolioDailyDecimal | None
    pending_receivable_local: PortfolioDailyDecimal | None
    pending_receivable_base_exact: PortfolioDailyDecimal | None
    pending_payable_local: PortfolioDailyDecimal | None
    pending_payable_base_exact: PortfolioDailyDecimal | None
    accrual_receivable_local: PortfolioDailyDecimal | None
    accrual_receivable_base_exact: PortfolioDailyDecimal | None
    accrual_payable_local: PortfolioDailyDecimal | None
    accrual_payable_base_exact: PortfolioDailyDecimal | None
    position_market_value_local_exact: PortfolioDailyDecimal | None
    position_market_value_base_exact: PortfolioDailyDecimal | None
    cost_basis_local_exact: PortfolioDailyDecimal | None
    cost_basis_base_exact: PortfolioDailyDecimal | None
    account_value_base_exact: PortfolioDailyDecimal | None
    valuation_coverage_state: Literal["complete", "partial", "unavailable"]
    valuation_reason_codes: list[str]
    cost_basis_coverage_state: Literal["complete", "unavailable"]
    cost_basis_reason_codes: list[str]


class AccountsWorkspaceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_count: int = Field(ge=0)
    deposit_account_count: int = Field(ge=0)
    securities_account_count: int = Field(ge=0)
    balance_component_count: int = Field(ge=0)
    position_line_count: int = Field(ge=0)
    open_lot_count: int = Field(ge=0)
    valuation_coverage_state: Literal["complete", "partial", "unavailable"]


class AccountsWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    base_currency: str
    as_of_date: date
    publication: PortfolioDailyPublicationMetadataResponse
    summary: AccountsWorkspaceSummary
    selected_account_id: str | None = None
    accounts: list[AccountWorkspaceAccount]
    balances: list[PortfolioDailyPublishedAccountBalanceRecord]
    positions: list[PortfolioDailyPublishedAccountPositionRecord]
    linked_transactions_summary: TransactionListSummary | None = None
    linked_transactions: list[TransactionRecord] = Field(default_factory=list)


class PortfolioDailyPublishedPositionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    account_id: str
    instrument_id: str
    currency: str
    quantity_exact: PortfolioDailyDecimal
    quantity: PortfolioDailyDecimal
    measured_price: bool
    measured_market_value: bool
    measured_book_pnl: bool
    adopted_price_exact: PortfolioDailyDecimal | None
    price: PortfolioDailyDecimal | None
    contract_multiplier_exact: PortfolioDailyDecimal | None
    price_factor_exact: PortfolioDailyDecimal | None
    adopted_fx_rate_exact: PortfolioDailyDecimal | None
    market_value_local_exact: PortfolioDailyDecimal | None
    market_value_base_exact: PortfolioDailyDecimal | None
    cost_basis_local: PortfolioDailyDecimal | None
    cost_basis_base: PortfolioDailyDecimal | None
    economic_pnl_daily_base: PortfolioDailyDecimal | None
    portfolio_weight: PortfolioDailyDecimal | None
    return_contribution: PortfolioDailyDecimal | None
    valuation_coverage_state: Literal["complete", "partial", "unavailable"]
    valuation_coverage_reason_codes: list[str]
    book_pnl_coverage_state: Literal["complete", "partial", "unavailable"]
    book_pnl_reason_codes: list[str]
    valuation_endpoint_status: Literal["fresh", "carry_forward", "stale", "unavailable"]
    valuation_reason_codes: list[str]


class PortfolioDailyPublishedPositionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    position_count: int = Field(ge=0)
    priced_position_count: int = Field(ge=0)
    account_count: int = Field(ge=0)
    measured_market_value: bool
    market_value_base_exact: PortfolioDailyDecimal | None


class PortfolioDailyPublishedPositionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    publication: PortfolioDailyPublicationMetadataResponse
    summary: PortfolioDailyPublishedPositionSummary
    positions: list[PortfolioDailyPublishedPositionRecord]


class PortfolioDailyPublishedLotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    account_id: str
    instrument_id: str
    lot_id: str
    source_transaction_id: str
    source_revision_id: str
    source_revision_number: int = Field(ge=1)
    custody_transaction_id: str
    custody_revision_id: str
    custody_revision_number: int = Field(ge=1)
    acquisition_date: date
    currency: str
    open_quantity_exact: PortfolioDailyDecimal
    open_quantity: PortfolioDailyDecimal
    measured_base_cost: bool
    acquisition_fx_rate_exact: PortfolioDailyDecimal | None
    cost_basis_local_exact: PortfolioDailyDecimal
    cost_basis_local: PortfolioDailyDecimal
    unit_cost_local: PortfolioDailyDecimal
    unit_cost_local_rounding_residual_exact: PortfolioDailyDecimal
    cost_basis_base_exact: PortfolioDailyDecimal | None
    cost_basis_base: PortfolioDailyDecimal | None
    unit_cost_base: PortfolioDailyDecimal | None
    unit_cost_base_rounding_residual_exact: PortfolioDailyDecimal | None
    base_cost_coverage_state: Literal["complete", "unavailable"]
    base_cost_reason_codes: list[str]


class PortfolioDailyPublishedLotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    lot_count: int = Field(ge=0)
    account_count: int = Field(ge=0)
    instrument_count: int = Field(ge=0)
    measured_base_cost: bool
    open_quantity_exact: PortfolioDailyDecimal
    cost_basis_base_exact: PortfolioDailyDecimal | None


class PortfolioDailyPublishedLotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    publication: PortfolioDailyPublicationMetadataResponse
    summary: PortfolioDailyPublishedLotSummary
    position_lots: list[PortfolioDailyPublishedLotRecord]


class PortfolioDailyPublishedSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    base_currency: str
    measured_nav: bool
    measured_position_market_value: bool
    measured_book_pnl: bool
    measured_return: bool
    measured_external_flows: bool
    unavailable_component_count: int = Field(ge=0)
    opening_nav: PortfolioDailyDecimal | None
    closing_nav: PortfolioDailyDecimal | None
    position_market_value: PortfolioDailyDecimal | None
    settled_cash: PortfolioDailyDecimal | None
    pending_receivable: PortfolioDailyDecimal | None
    pending_payable: PortfolioDailyDecimal | None
    accrual_receivable: PortfolioDailyDecimal | None
    accrual_payable: PortfolioDailyDecimal | None
    external_flow_in: PortfolioDailyDecimal | None
    external_flow_out: PortfolioDailyDecimal | None
    economic_pnl: PortfolioDailyDecimal | None
    realized_pnl_daily: PortfolioDailyDecimal | None
    unrealized_pnl_beginning: PortfolioDailyDecimal | None
    unrealized_pnl_ending: PortfolioDailyDecimal | None
    unrealized_pnl_change: PortfolioDailyDecimal | None
    gross_income_daily: PortfolioDailyDecimal | None
    return_of_capital_daily: PortfolioDailyDecimal | None
    capitalized_fee_daily: PortfolioDailyDecimal | None
    capitalized_tax_daily: PortfolioDailyDecimal | None
    expensed_fee_daily: PortfolioDailyDecimal | None
    expensed_tax_daily: PortfolioDailyDecimal | None
    local_price_effect_daily: PortfolioDailyDecimal | None
    position_fx_effect_daily: PortfolioDailyDecimal | None
    cash_fx_effect_daily: PortfolioDailyDecimal | None
    pending_fx_effect_daily: PortfolioDailyDecimal | None
    accrual_fx_effect_daily: PortfolioDailyDecimal | None
    fx_conversion_effect_daily: PortfolioDailyDecimal | None
    subperiod_twr_method50: PortfolioDailyDecimal | None
    subperiod_twr_published: PortfolioDailyDecimal | None
    cumulative_twr_method50: PortfolioDailyDecimal | None
    cumulative_twr_published: PortfolioDailyDecimal | None
    wealth_index_method50: PortfolioDailyDecimal | None
    wealth_index_published: PortfolioDailyDecimal | None
    peak_wealth_index_method50: PortfolioDailyDecimal | None
    peak_wealth_index_published: PortfolioDailyDecimal | None
    drawdown_method50: PortfolioDailyDecimal | None
    drawdown_published: PortfolioDailyDecimal | None
    wealth_chain_rounding_adjustment_exact: PortfolioDailyDecimal | None
    reliable_anchor_date: date | None
    return_period_start_date: date | None
    return_period_end_date: date | None
    return_period_day_count: int | None
    calculation_status: str
    return_chain_status: str
    nav_coverage_state: Literal["complete", "partial", "unavailable"]
    nav_reason_codes: list[str]
    book_pnl_coverage_state: Literal["complete", "partial", "unavailable"]
    book_pnl_reason_codes: list[str]
    return_coverage_state: Literal["complete", "partial", "unavailable"]
    return_reason_codes: list[str]
    flow_coverage_state: Literal["complete", "partial", "unavailable"]
    flow_reason_codes: list[str]
    valuation_endpoint_status: Literal["fresh", "carry_forward", "stale", "unavailable"]
    valuation_reason_codes: list[str]

    @model_validator(mode="after")
    def validate_method50_return_evidence(
        self,
    ) -> "PortfolioDailyPublishedSnapshotRecord":
        pairs = (
            (self.subperiod_twr_method50, self.subperiod_twr_published),
            (self.cumulative_twr_method50, self.cumulative_twr_published),
            (self.wealth_index_method50, self.wealth_index_published),
            (self.peak_wealth_index_method50, self.peak_wealth_index_published),
            (self.drawdown_method50, self.drawdown_published),
        )
        for method50, published in pairs:
            if (method50 is None) != (published is None):
                raise ValueError(
                    "method50/published return nullability is inconsistent"
                )
            if method50 is not None and published != quantize_decimal(
                method50,
                scale=RATIO_SCALE,
                field_name="snapshot published return",
            ):
                raise ValueError("snapshot published return is not method50 rounded18")
            if method50 is not None:
                require_method_decimal(
                    method50,
                    field_name="snapshot method50 return",
                )
        chain = (
            self.cumulative_twr_method50,
            self.wealth_index_method50,
            self.peak_wealth_index_method50,
            self.drawdown_method50,
        )
        if any(value is not None for value in chain):
            if any(value is None for value in chain):
                raise ValueError("snapshot method50 wealth chain is partial")
            cumulative, wealth, peak, drawdown = chain
            assert cumulative is not None and wealth is not None
            assert peak is not None and drawdown is not None
            if wealth < 0 or peak <= 0 or peak < wealth:
                raise ValueError("snapshot method50 wealth/peak ordering is invalid")
            if cumulative != method_decimal_subtract(wealth, Decimal("1")):
                raise ValueError("snapshot cumulative return does not close to wealth")
            if drawdown != method_decimal_divide(
                method_decimal_subtract(wealth, peak),
                peak,
            ):
                raise ValueError("snapshot drawdown does not close to wealth/peak")
        return self


class PortfolioDailyPublishedSnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_count: int = Field(ge=0)
    measured_nav_count: int = Field(ge=0)
    measured_return_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    latest_as_of_date: date | None


class PortfolioDailyPublishedSnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    publication: PortfolioDailyPublicationMetadataResponse
    base_currency: str
    valuation_timezone: str
    summary: PortfolioDailyPublishedSnapshotSummary
    snapshots: list[PortfolioDailyPublishedSnapshotRecord]


class PortfolioDailyPublishedDecimalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method50: PortfolioDailyDecimal
    published: PortfolioDailyDecimal
    rounding_adjustment_exact: PortfolioDailyDecimal

    @model_validator(mode="after")
    def validate_rounding_evidence(self) -> "PortfolioDailyPublishedDecimalMetric":
        require_method_decimal(
            self.method50,
            field_name="published reporting metric method50",
        )
        expected_published = quantize_decimal(
            self.method50,
            scale=RATIO_SCALE,
            field_name="published reporting metric",
        )
        if self.published != expected_published:
            raise ValueError("published metric does not match HALF_EVEN scale 18")
        if self.rounding_adjustment_exact != exact_decimal_subtract(
            self.published,
            self.method50,
        ):
            raise ValueError("published metric rounding evidence does not close")
        return self


class PortfolioDailyPublishedPerformanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    start_date: date | None
    end_date: date | None
    effective_return_start_date: date | None
    effective_return_end_date: date | None
    elapsed_days: int | None = Field(default=None, ge=0)
    snapshot_count: int = Field(ge=0)
    measured_nav_count: int = Field(ge=0)
    measured_return_count: int = Field(ge=0)
    cumulative_twr: PortfolioDailyPublishedDecimalMetric | None
    annualized_twr: PortfolioDailyPublishedDecimalMetric | None
    current_drawdown: PortfolioDailyPublishedDecimalMetric | None
    max_drawdown: PortfolioDailyPublishedDecimalMetric | None
    return_chain_status: str | None
    coverage_state: Literal["complete", "partial", "unavailable"]
    reason_codes: list[str]

    @model_validator(mode="after")
    def validate_performance_state(self) -> "PortfolioDailyPublishedPerformanceSummary":
        return_values = (
            self.cumulative_twr,
            self.current_drawdown,
            self.max_drawdown,
        )
        if self.status == "ready":
            if (
                self.effective_return_start_date is None
                or self.effective_return_end_date is None
                or self.elapsed_days is None
                or self.measured_return_count <= 0
                or any(value is None for value in return_values)
                or self.return_chain_status != "linked"
            ):
                raise ValueError(
                    "ready performance summary lacks method50 return evidence"
                )
        elif any(value is not None for value in return_values):
            raise ValueError("unavailable performance summary cannot publish returns")
        return self


class PortfolioDailyPublishedPerformanceStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    method_version: Literal[
        "calendar-period.v1.monthly.ppy-12.vol-sample-n-minus-1."
        "downside-target-zero-n.decimal50",
        "calendar-period.v1.weekly.ppy-52.vol-sample-n-minus-1."
        "downside-target-zero-n.decimal50",
    ]
    reason_codes: list[str]
    frequency: Literal["monthly", "weekly"]
    observation_count: int = Field(ge=0)
    excluded_partial_bucket_count: int = Field(ge=0)
    excluded_unavailable_bucket_count: int = Field(ge=0)
    periods_per_year: PortfolioDailyPublishedDecimalMetric | None
    mean_period_return: PortfolioDailyPublishedDecimalMetric | None
    annualized_arithmetic_mean: PortfolioDailyPublishedDecimalMetric | None
    annualized_volatility: PortfolioDailyPublishedDecimalMetric | None
    annualized_downside_deviation: PortfolioDailyPublishedDecimalMetric | None

    @model_validator(mode="after")
    def validate_statistics_state(
        self,
    ) -> "PortfolioDailyPublishedPerformanceStatistics":
        values = (
            self.periods_per_year,
            self.mean_period_return,
            self.annualized_arithmetic_mean,
            self.annualized_volatility,
            self.annualized_downside_deviation,
        )
        if self.status == "ready":
            if (
                self.observation_count < 2
                or any(value is None for value in values)
                or self.reason_codes
            ):
                raise ValueError(
                    "ready statistics lack calendar-period Decimal evidence"
                )
        elif any(value is not None for value in values) or not self.reason_codes:
            raise ValueError("unavailable statistics require reasons and null metrics")
        return self


class PortfolioDailyPublishedXirrResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    method_version: Literal["xirr.v1.actual-365.decimal50.unique-sign-change"]
    reason_codes: list[str]
    cash_flow_count: int = Field(ge=0)
    annualized_headline_eligible: bool
    rate: PortfolioDailyPublishedDecimalMetric | None
    xnpv_residual_exact: PortfolioDailyDecimal | None

    @model_validator(mode="after")
    def validate_xirr_state(self) -> "PortfolioDailyPublishedXirrResult":
        if self.status == "ready":
            expected_reasons = (
                []
                if self.annualized_headline_eligible
                else ["xirr_annualized_headline_requires_at_least_365_elapsed_days"]
            )
            if (
                self.rate is None
                or self.xnpv_residual_exact is None
                or self.reason_codes != expected_reasons
            ):
                raise ValueError("ready XIRR lacks Decimal rate/residual evidence")
        elif (
            self.rate is not None
            or self.xnpv_residual_exact is not None
            or self.annualized_headline_eligible
            or not self.reason_codes
        ):
            raise ValueError("unavailable XIRR requires reasons and null rate evidence")
        return self


class PortfolioDailyPublishedRebasedWealthPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    wealth_index_method50: PortfolioDailyDecimal
    peak_wealth_index_method50: PortfolioDailyDecimal
    drawdown_method50: PortfolioDailyDecimal
    wealth_chain_rounding_adjustment_exact: PortfolioDailyDecimal | None
    return_chain_status: str


class PortfolioDailyPublishedReportingUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    publication: PortfolioDailyPublicationMetadataResponse
    surface: str
    status: Literal["unavailable"]
    requested_start_date: date | None
    requested_end_date: date | None
    reason_codes: list[str] = Field(min_length=1)


class PortfolioDailyPublishedAttributionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    axis: Literal["portfolio", "account", "instrument", "currency", "taxonomy"]
    group_key: str
    group_label: str
    opening_nav_exact: PortfolioDailyDecimal
    closing_nav_exact: PortfolioDailyDecimal
    external_flow_in_exact: PortfolioDailyDecimal
    external_flow_out_exact: PortfolioDailyDecimal
    internal_flow_in_exact: PortfolioDailyDecimal
    internal_flow_out_exact: PortfolioDailyDecimal
    economic_pnl_exact: PortfolioDailyDecimal
    linked_contribution_method50: PortfolioDailyDecimal
    linking_adjustment_exact: PortfolioDailyDecimal
    linked_contribution_effective: PortfolioDailyDecimal
    closure_residual_exact: PortfolioDailyDecimal

    @model_validator(mode="after")
    def validate_linking_adjustment(
        self,
    ) -> "PortfolioDailyPublishedAttributionGroup":
        require_method_decimal(
            self.linked_contribution_method50,
            field_name="linked contribution method50",
        )
        if self.linked_contribution_effective != exact_decimal_sum(
            (
                self.linked_contribution_method50,
                self.linking_adjustment_exact,
            )
        ):
            raise ValueError("linked contribution adjustment evidence does not close")
        if self.closure_residual_exact != Decimal("0"):
            raise ValueError("attribution monetary bridge does not close exactly")
        return self


class PortfolioDailyPublishedAttributionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    method_version: Literal[
        "frongello-forward.v2.decimal50-deterministic-group-balance"
    ]
    axis: Literal["portfolio", "account", "instrument", "currency", "taxonomy"]
    effective_start_date: date | None
    effective_end_date: date | None
    observation_count: int = Field(ge=0)
    cumulative_twr_method50: PortfolioDailyDecimal | None
    total_linked_contribution_effective: PortfolioDailyDecimal | None
    total_linking_adjustment_exact: PortfolioDailyDecimal | None
    closure_residual_exact: PortfolioDailyDecimal | None
    reason_codes: list[str]

    @model_validator(mode="after")
    def validate_attribution_state(self) -> "PortfolioDailyPublishedAttributionSummary":
        if self.cumulative_twr_method50 is not None:
            require_method_decimal(
                self.cumulative_twr_method50,
                field_name="attribution cumulative TWR method50",
            )
        if self.status == "ready":
            if (
                self.cumulative_twr_method50 is None
                or self.total_linked_contribution_effective is None
                or self.total_linking_adjustment_exact is None
                or self.closure_residual_exact != Decimal("0")
                or self.total_linked_contribution_effective
                != self.cumulative_twr_method50
                or self.reason_codes
            ):
                raise ValueError("ready attribution does not close exactly to TWR")
        elif (
            self.total_linked_contribution_effective is not None
            or self.total_linking_adjustment_exact is not None
            or self.closure_residual_exact is not None
            or not self.reason_codes
        ):
            raise ValueError("unavailable attribution requires reasons and null totals")
        return self


class PortfolioDailyPublishedAttributionCalendarBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    calendar_start_date: date
    calendar_end_date: date
    coverage_state: Literal["complete", "partial", "unavailable"]
    coverage_reason_codes: list[str]
    summary: PortfolioDailyPublishedAttributionSummary
    groups: list[PortfolioDailyPublishedAttributionGroup]

    @model_validator(mode="after")
    def validate_coverage_state(
        self,
    ) -> "PortfolioDailyPublishedAttributionCalendarBucket":
        if self.coverage_state == "complete" and self.coverage_reason_codes:
            raise ValueError("complete calendar coverage cannot carry coverage reasons")
        if self.coverage_state != "complete" and not self.coverage_reason_codes:
            raise ValueError("non-complete calendar coverage requires coverage reasons")
        return self


class PortfolioDailyPublishedReturnCalendarBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_key: str
    frequency: Literal["monthly", "weekly"]
    calendar_start_date: date
    calendar_end_date: date
    coverage_state: Literal["complete", "partial", "unavailable"]
    coverage_reason_codes: list[str]
    status: Literal["ready", "unavailable"]
    effective_return_start_date: date | None
    effective_return_end_date: date | None
    observation_count: int = Field(ge=0)
    cumulative_twr: PortfolioDailyPublishedDecimalMetric | None
    current_drawdown: PortfolioDailyPublishedDecimalMetric | None
    max_drawdown: PortfolioDailyPublishedDecimalMetric | None
    reason_codes: list[str]

    @model_validator(mode="after")
    def validate_bucket_state(self) -> "PortfolioDailyPublishedReturnCalendarBucket":
        values = (
            self.cumulative_twr,
            self.current_drawdown,
            self.max_drawdown,
        )
        if self.status == "ready" and any(value is None for value in values):
            raise ValueError("ready calendar bucket lacks method50 return evidence")
        if self.status == "unavailable" and any(value is not None for value in values):
            raise ValueError("unavailable calendar bucket cannot publish returns")
        if self.coverage_state == "complete" and self.coverage_reason_codes:
            raise ValueError("complete calendar coverage cannot carry coverage reasons")
        if self.coverage_state != "complete" and not self.coverage_reason_codes:
            raise ValueError("non-complete calendar coverage requires coverage reasons")
        if self.coverage_state == "unavailable" and self.status != "unavailable":
            raise ValueError(
                "unavailable calendar coverage requires unavailable return"
            )
        if self.coverage_state != "unavailable" and self.status != "ready":
            raise ValueError("ready/partial calendar coverage requires a ready return")
        return self


class PortfolioDailyPublishedPerformanceReportResponse(BaseModel):
    """One coherent Performance page payload from one fenced publication."""

    model_config = ConfigDict(extra="forbid")

    portfolio_id: str
    publication: PortfolioDailyPublicationMetadataResponse
    base_currency: str
    valuation_timezone: str
    axis: Literal["portfolio", "account", "instrument", "currency", "taxonomy"]
    selected_group_key: str | None
    frequency: Literal["monthly", "weekly"]
    performance: PortfolioDailyPublishedPerformanceSummary
    statistics: PortfolioDailyPublishedPerformanceStatistics
    xirr: PortfolioDailyPublishedXirrResult
    portfolio_bridge: PortfolioDailyPublishedAttributionGroup | None
    rebased_wealth_series: list[PortfolioDailyPublishedRebasedWealthPoint]
    daily_series: list[PortfolioDailyPublishedSnapshotRecord]
    attribution: PortfolioDailyPublishedAttributionSummary
    attribution_groups: list[PortfolioDailyPublishedAttributionGroup]
    return_calendar: list[PortfolioDailyPublishedReturnCalendarBucket]
    attribution_calendar: list[PortfolioDailyPublishedAttributionCalendarBucket]

    @model_validator(mode="after")
    def validate_attribution_balancing(
        self,
    ) -> "PortfolioDailyPublishedPerformanceReportResponse":
        if self.attribution.status == "unavailable":
            if self.attribution_groups:
                raise ValueError("unavailable attribution cannot publish groups")
            return self
        if self.selected_group_key is None:
            if (
                exact_decimal_sum(
                    tuple(
                        group.linked_contribution_effective
                        for group in self.attribution_groups
                    )
                )
                != self.attribution.total_linked_contribution_effective
            ):
                raise ValueError("attribution group effective values do not close")
            if (
                exact_decimal_sum(
                    tuple(
                        group.linking_adjustment_exact
                        for group in self.attribution_groups
                    )
                )
                != self.attribution.total_linking_adjustment_exact
            ):
                raise ValueError("attribution group linking adjustments do not close")
            for bucket in self.attribution_calendar:
                if bucket.summary.status != "ready":
                    continue
                if (
                    exact_decimal_sum(
                        tuple(
                            group.linked_contribution_effective
                            for group in bucket.groups
                        )
                    )
                    != bucket.summary.total_linked_contribution_effective
                ):
                    raise ValueError(
                        "calendar attribution effective values do not close"
                    )
                if (
                    exact_decimal_sum(
                        tuple(group.linking_adjustment_exact for group in bucket.groups)
                    )
                    != bucket.summary.total_linking_adjustment_exact
                ):
                    raise ValueError(
                        "calendar attribution linking adjustments do not close"
                    )
        return self
