from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from yungu_instrument_core.models import InstrumentCore as InstrumentCoreContract
from yungu_instrument_core.models import InstrumentIdentifier as InstrumentIdentifierContract
from yungu_instrument_core.models import InstrumentType, DataStatus as CoverageState, IdentifierType


AccountScopedInstrumentType = Literal["fund", "bond", "equity", "other"]
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
DerivationStage = Literal["not_started", "next_layer"]
PostingRole = Literal[
    "opening_cash",
    "opening_position",
    "external_cash_flow",
    "fx_conversion_source_cash",
    "fx_conversion_target_cash",
    "internal_cash_transfer",
    "internal_position_transfer",
    "security_settlement_cash",
    "security_position",
    "security_income_cash",
    "security_redemption_cash",
    "security_reinvestment_position",
    "account_income_cash",
    "account_expense_cash",
]
PositionLotStatus = Literal["open", "closed"]
PositionLotCloseReason = Literal["disposed", "transferred"]
ResearchRunStatus = Literal["running", "completed", "failed"]
ResearchArtifactPreviewKind = Literal["text", "html", "binary"]
ResearchTargetDimension = Literal["scope_default", "weight", "risk_budget"]
ResearchCapitalMode = Literal["unit_notional", "fixed_gross", "target_volatility"]
ResearchCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]
ResearchMissingReturnPolicy = Literal["strict", "complete_case_drop"]
PortfolioCalculationFrequency = Literal["daily", "weekly", "monthly"]
TargetSetType = Literal["saa", "taa"]

SUPPORTED_PORTFOLIO_CURRENCIES: tuple[SupportedCurrency, ...] = ("USD", "HKD", "CNY")
QUANTITY_DISPLAY_QUANTUM = Decimal("0.01")
AMOUNT_DISPLAY_QUANTUM = Decimal("0.01")
PRICE_DISPLAY_QUANTUM = Decimal("0.0001")
AMOUNT_CONTRACT_EPSILON = Decimal("0.000001")


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount_contract_matches_display_price(
    *,
    quantity: object,
    price: object,
    gross_amount: object,
) -> bool:
    resolved_quantity = _to_decimal(quantity)
    resolved_price = _to_decimal(price)
    resolved_gross_amount = _to_decimal(gross_amount)
    if (
        resolved_quantity is None
        or resolved_price is None
        or resolved_gross_amount is None
        or resolved_quantity <= 0
        or resolved_price <= 0
    ):
        return False

    expected_gross_amount = resolved_quantity * resolved_price
    if abs(resolved_gross_amount - expected_gross_amount) <= AMOUNT_CONTRACT_EPSILON:
        return True

    derived_display_price = (resolved_gross_amount / resolved_quantity).quantize(
        PRICE_DISPLAY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    normalized_price = resolved_price.quantize(
        PRICE_DISPLAY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return normalized_price == derived_display_price


def _quantize_numeric_input(value: object, *, quantum: Decimal) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
    resolved_value = _to_decimal(value)
    if resolved_value is None:
        return value
    return float(resolved_value.quantize(quantum, rounding=ROUND_HALF_UP))


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


class InstrumentOption(BaseModel):
    instrument_core: InstrumentCoreContract
    coverage_state: CoverageState
    latest_market_data: list[dict[str, object]] = Field(default_factory=list)
    quote_selection_policy: dict[str, list[str]] = Field(default_factory=dict)


class SharedInstrumentListResponse(BaseModel):
    portfolio_id: str
    instruments: list[InstrumentOption]


class SharedFxRateRecord(BaseModel):
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: float
    as_of_date: date
    source_kind: str
    instrument_id: str | None = None
    source_instrument_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
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
                raise ValueError("deposit_account must not carry default_settlement_cash_account_id.")
            if self.cost_basis_method is not None:
                raise ValueError("deposit_account must not carry cost_basis_method.")
            if self.allowed_instrument_types is not None:
                raise ValueError("deposit_account must not carry allowed_instrument_types.")
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
    quantity: float | None = None
    price: float | None = None
    gross_amount: float
    counter_amount: float | None = None
    fx_rate: float | None = None
    fees: float = 0.0
    taxes: float = 0.0
    currency: str
    transfer_scope: TransferScope | None = None
    transfer_object_type: TransferObjectType | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    net_cash_effect: float | None = None
    note: str | None = None
    created_at: str | None = None


class TransactionListSummary(BaseModel):
    total_transactions: int
    instrument_transactions: int
    external_cash_flows: int
    opening_balance_records: int


class DerivationBoundaryStatus(BaseModel):
    ledger_postings: DerivationStage = "next_layer"
    positions: DerivationStage = "not_started"
    position_lots: DerivationStage = "not_started"
    holdings: DerivationStage = "not_started"
    snapshot: DerivationStage = "not_started"


class TransactionListResponse(BaseModel):
    portfolio_id: str
    summary: TransactionListSummary
    derivation_boundary: DerivationBoundaryStatus
    transactions: list[TransactionRecord]


class LedgerPostingRecord(BaseModel):
    posting_id: str
    transaction_id: str
    portfolio_id: str
    account_id: str
    posting_role: PostingRole
    source_transaction_type: TransactionType
    trade_date: date
    settlement_date: date
    effective_date: date
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    cash_amount_delta: float | None = None
    quantity_delta: float | None = None
    cost_basis_delta: float | None = None
    currency: str
    transfer_group_id: str | None = None
    note: str | None = None


class AccountPositionRecord(BaseModel):
    position_id: str | None = None
    account_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract
    quantity: float
    cost_basis: float | None = None
    last_price: float | None = None
    market_value: float | None = None
    currency: str
    cost_basis_method: CostBasisMethod | None = None
    open_position_lot_count: int = 0


class PositionRecord(BaseModel):
    position_id: str
    portfolio_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract
    quantity: float
    cost_basis: float | None = None
    last_price: float | None = None
    market_value: float | None = None
    currency: str
    account_ids: list[str] = Field(default_factory=list)
    account_count: int = 0
    open_position_lot_count: int = 0


class PositionListSummary(BaseModel):
    position_count: int
    priced_position_count: int
    open_position_lot_count: int


class PositionListResponse(BaseModel):
    portfolio_id: str
    summary: PositionListSummary
    positions: list[PositionRecord]


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


class PositionLotRealizationRecord(BaseModel):
    realization_id: str
    transaction_id: str
    transaction_type: TransactionType
    trade_date: date
    quantity: float
    proceeds: float | None = None
    cost_basis_released: float
    realized_pnl: float | None = None
    price: float | None = None
    remaining_quantity_after: float
    remaining_cost_basis_after: float
    status_after: PositionLotStatus
    note: str | None = None


class PositionLotRecord(BaseModel):
    position_lot_id: str
    portfolio_id: str
    account_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract
    currency: str
    cost_basis_method: CostBasisMethod
    opened_by_transaction_id: str
    opening_transaction_type: TransactionType
    opened_at: date
    acquisition_date: date
    closed_at: date | None = None
    status: PositionLotStatus
    close_reason: PositionLotCloseReason | None = None
    source_position_lot_id: str | None = None
    entry_quantity: float
    remaining_quantity: float
    realized_quantity: float
    transferred_quantity: float = 0.0
    entry_gross_amount: float = 0.0
    entry_fee_amount: float = 0.0
    entry_tax_amount: float = 0.0
    entry_cost_basis: float
    entry_cost_per_unit: float | None = None
    remaining_cost_basis: float
    realized_cost_basis: float
    transferred_cost_basis: float = 0.0
    realized_proceeds: float = 0.0
    realized_pnl: float = 0.0
    income_cash_amount: float = 0.0
    expense_cash_amount: float = 0.0
    return_of_capital_amount: float = 0.0
    entry_price: float | None = None
    average_exit_price: float | None = None
    current_market_value: float | None = None
    unrealized_pnl: float | None = None
    holding_period_days: int | None = None
    linked_transaction_count: int = 0
    realization_count: int = 0
    realizations: list[PositionLotRealizationRecord] = Field(default_factory=list)


class PositionLotListSummary(BaseModel):
    position_lot_count: int
    open_position_lot_count: int
    closed_position_lot_count: int
    realized_pnl: float


class PositionLotListResponse(BaseModel):
    portfolio_id: str
    summary: PositionLotListSummary
    position_lots: list[PositionLotRecord]


class TransactionWorkspaceResponse(BaseModel):
    portfolio_id: str
    summary: TransactionListSummary
    derivation_boundary: DerivationBoundaryStatus
    selected_transaction_id: str | None = None
    transactions: list[TransactionRecord]
    selected_transaction: TransactionRecord | None = None
    ledger_summary: LedgerPostingListSummary
    ledger_postings: list[LedgerPostingRecord]
    related_position_lot_summary: PositionLotListSummary
    related_position_lots: list[PositionLotRecord]


class TransactionPositionPreviewResponse(BaseModel):
    portfolio_id: str
    account_id: str
    instrument_id: str
    as_of_date: date
    trade_at: str
    quantity: float


class DailySnapshotRecord(BaseModel):
    as_of_date: date
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    coverage_state: CoverageState
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    total_position_count: int = 0
    priced_position_count: int = 0
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    cash_balance: float | None = None
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


class DailySnapshotRefreshResponse(BaseModel):
    portfolio_ids: list[str]
    refreshed: list[DailySnapshotRefreshResult]


class DailyPerformancePoint(BaseModel):
    as_of_date: date
    coverage_state: CoverageState
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    beginning_nav: float | None = None
    ending_nav: float | None = None
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


class PerformanceSummary(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    coverage_state: CoverageState
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
    current_drawdown: float | None = None
    max_drawdown: float | None = None
    max_drawdown_days: int | None = None
    drawdown_duration_days: int | None = None


class PerformanceResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: PerformanceSummary
    daily_series: list[DailyPerformancePoint]


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
    coverage_state: CoverageState
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
    coverage_state: CoverageState
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
    effective_from: date | None = None
    effective_to: date | None = None
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
    effective_from: date | None = None
    effective_to: date | None = None
    status: str = "active"


class TargetSetRecord(BaseModel):
    target_set_id: str
    taxonomy_id: str
    comparator_taxonomy_node_id: str | None = None
    target_set_type: TargetSetType
    name: str
    effective_from: date | None = None
    effective_to: date | None = None
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


class TaxonomyCatalogResponse(BaseModel):
    portfolio_id: str
    default_planning_taxonomy_id: str | None = None
    taxonomies: list[TaxonomyRecord]
    taxonomy_nodes: list[TaxonomyNodeRecord]
    taxonomy_assignments: list[TaxonomyAssignmentRecord]
    target_sets: list[TargetSetRecord] = Field(default_factory=list)
    target_set_lines: list[TargetSetLineRecord] = Field(default_factory=list)


class ResearchPlanningTaxonomyOption(BaseModel):
    taxonomy_id: str
    name: str
    taxonomy_type: str
    budgeting_level: str | None = None


class ResearchPlanningScopeOption(BaseModel):
    taxonomy_node_id: str | None = None
    label: str
    path: str
    depth: int = 0
    default_target_dimension: DefaultTargetDimension = "weight"
    has_children: bool = False


class ResearchCalculationFrequencyOption(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    label: str
    available: bool
    reason: str | None = None


class ResearchCalculationFrequencyProfile(BaseModel):
    requested_frequency: ResearchCalculationFrequency = "auto"
    resolved_frequency: Literal["daily", "weekly", "monthly"] = "daily"
    default_frequency: Literal["daily", "weekly", "monthly"] = "daily"
    source_frequency_counts: dict[str, int] = Field(default_factory=dict)
    options: list[ResearchCalculationFrequencyOption] = Field(default_factory=list)
    status_label: str


class ResearchSettingsRecord(BaseModel):
    portfolio_id: str
    planning_taxonomy_id: str | None = None
    planning_taxonomy_name: str | None = None
    comparator_taxonomy_node_id: str | None = None
    comparator_taxonomy_node_name: str | None = None
    as_of_date: date | None = None
    lookback_days: int = Field(default=90, ge=7, le=366)
    calculation_frequency: ResearchCalculationFrequency = "auto"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    target_dimension: ResearchTargetDimension = "scope_default"
    capital_mode: ResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
    updated_at: str | None = None


class ResearchSettingsUpdateRequest(BaseModel):
    planning_taxonomy_id: str | None = None
    comparator_taxonomy_node_id: str | None = None
    as_of_date: date | None = None
    lookback_days: int = Field(default=90, ge=7, le=366)
    calculation_frequency: ResearchCalculationFrequency = "auto"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    target_dimension: ResearchTargetDimension = "scope_default"
    capital_mode: ResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] | None = None
    notes: str | None = None

    @field_validator("planning_taxonomy_id", "comparator_taxonomy_node_id", "notes", mode="before")
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
    def validate_research_settings(self) -> "ResearchSettingsUpdateRequest":
        if self.capital_mode == "unit_notional":
            if self.gross_exposure is not None or self.target_volatility is not None:
                raise ValueError("unit_notional capital mode must not set gross_exposure or target_volatility.")
        elif self.capital_mode == "fixed_gross":
            if self.gross_exposure is None:
                raise ValueError("fixed_gross capital mode requires gross_exposure.")
            if self.target_volatility is not None:
                raise ValueError("fixed_gross capital mode must not set target_volatility.")
        elif self.capital_mode == "target_volatility":
            if self.target_volatility is None:
                raise ValueError("target_volatility capital mode requires target_volatility.")
            if self.gross_exposure is not None:
                raise ValueError("target_volatility capital mode must not set gross_exposure.")
        if (
            self.max_gross_exposure is not None
            and self.gross_exposure is not None
            and self.max_gross_exposure + 1e-12 < self.gross_exposure
        ):
            raise ValueError("max_gross_exposure cannot be smaller than gross_exposure.")
        return self


class ResearchContextSignalRecord(BaseModel):
    label: str
    value: str
    tone: str = "neutral"


class ResearchHoldingSnapshotRecord(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: str | None = None
    allocation: float | None = None
    market_value_base: float | None = None
    cost_basis_base: float | None = None
    base_currency: str
    price: float | None = None


class ResearchPlanningGroupSnapshotRecord(BaseModel):
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


class ResearchFindingRecord(BaseModel):
    title: str
    detail: str


class ResearchContextPoint(BaseModel):
    date: str
    value: float | None = None


class ResearchCurrentContextSummary(BaseModel):
    period_return: float | None = None
    annualized_volatility: float | None = None
    current_drawdown: float | None = None
    max_drawdown: float | None = None
    start_nav: float | None = None
    end_nav: float | None = None


class ResearchPlanningTargetSummary(BaseModel):
    root_saa_configured: bool = False
    root_taa_configured: bool = False
    scoped_target_set_count: int = 0


class ResearchCurrentContextRecord(BaseModel):
    portfolio_id: str
    portfolio_name: str
    base_currency: str
    as_of_date: date
    lookback_start: date
    lookback_end: date
    nav: float | None = None
    holdings_count: int = 0
    planning_group_count: int = 0
    chart_label: str | None = None
    chart_note: str | None = None
    chart_currency: str | None = None
    summary: ResearchCurrentContextSummary
    planning_target_summary: ResearchPlanningTargetSummary | None = None
    chart_points: list[ResearchContextPoint] = Field(default_factory=list)
    top_holdings: list[ResearchHoldingSnapshotRecord] = Field(default_factory=list)
    planning_groups: list[ResearchPlanningGroupSnapshotRecord] = Field(default_factory=list)


class ResearchScopeSelectionRecord(BaseModel):
    taxonomy_node_id: str | None = None
    label: str
    path: str
    depth: int = 0
    default_target_dimension: DefaultTargetDimension = "weight"
    member_source: str = "child_sleeves"


class ResearchMemberTargetRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    scope_path: str | None = None
    member_path: str | None = None
    default_target_dimension: DefaultTargetDimension | None = None
    selected_target_dimension: ResearchTargetDimension | None = None
    source_target_set_type: TargetSetType | None = None
    current_weight: float | None = None
    current_risk_share: float | None = None
    target_weight: float | None = None
    weight_change: float | None = None
    configured_weight: float | None = None
    configured_risk_share: float | None = None
    selected_target_value: float | None = None


class ResearchSolveEventRecord(BaseModel):
    as_of_date: str
    scope_node_id: str | None = None
    scope_label: str
    scope_path: str | None = None
    scope_depth: int | None = None
    requested_target_dimension: str | None = None
    taxonomy_default_target_dimension: DefaultTargetDimension | None = None
    target_dimension: ResearchTargetDimension | None = None
    solver_kind: str | None = None
    solver_detail: str | None = None
    solver_message: str | None = None
    covariance_model: str | None = None
    covariance_observations: int | None = None
    risk_contribution_mode: str | None = None
    missing_return_policy: ResearchMissingReturnPolicy | None = None
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


class ResearchTargetWeightGapRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    current_weight: float | None = None
    target_weight: float | None = None
    gap: float | None = None
    current_value_base: float | None = None
    base_currency: str
    action: str


class ResearchTargetRowRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    current_weight: float | None = None
    current_value_base: float | None = None
    default_target_dimension: DefaultTargetDimension | None = None
    selected_target_dimension: ResearchTargetDimension | None = None
    source_target_set_type: TargetSetType | None = None
    source_target_set_id: str | None = None
    source_label: str | None = None
    selected_target_value: float | None = None
    target_weight: float | None = None
    target_risk_share: float | None = None
    implementation_weight: float | None = None
    gap_to_implementation: float | None = None
    action: str | None = None


class ResearchRunDetailRecord(BaseModel):
    headline: str | None = None
    coverage_note: str | None = None
    signals: list[ResearchContextSignalRecord] = Field(default_factory=list)
    findings: list[ResearchFindingRecord] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    top_holdings: list[ResearchHoldingSnapshotRecord] = Field(default_factory=list)
    planning_groups: list[ResearchPlanningGroupSnapshotRecord] = Field(default_factory=list)
    selected_scope: ResearchScopeSelectionRecord | None = None
    target_assumptions: list[str] = Field(default_factory=list)
    target_rows: list[ResearchTargetRowRecord] = Field(default_factory=list)
    member_targets: list[ResearchMemberTargetRecord] = Field(default_factory=list)
    leaf_targets: list[ResearchMemberTargetRecord] = Field(default_factory=list)
    solve_event: ResearchSolveEventRecord | None = None
    scope_solve_events: list[ResearchSolveEventRecord] = Field(default_factory=list)
    target_weight_gaps: list[ResearchTargetWeightGapRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchArtifactRecord(BaseModel):
    artifact_id: str
    label: str
    path: str
    media_type: str
    preview_kind: ResearchArtifactPreviewKind


class ResearchRunRecord(BaseModel):
    research_run_id: str
    portfolio_id: str
    job_type: str
    status: ResearchRunStatus
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
    artifact_count: int = 0
    artifacts: list[ResearchArtifactRecord] = Field(default_factory=list)
    detail: ResearchRunDetailRecord | None = None


class ResearchRunCreateRequest(BaseModel):
    requested_by: str | None = None

    @field_validator("requested_by", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class ResearchWorkbenchResponse(BaseModel):
    portfolio_id: str
    portfolio_name: str
    base_currency: str
    as_of_date: date
    default_planning_taxonomy_id: str | None = None
    planning_taxonomy_options: list[ResearchPlanningTaxonomyOption] = Field(default_factory=list)
    planning_scope_options: list[ResearchPlanningScopeOption] = Field(default_factory=list)
    calculation_frequency: ResearchCalculationFrequencyProfile
    settings: ResearchSettingsRecord
    current_context: ResearchCurrentContextRecord
    runs: list[ResearchRunRecord] = Field(default_factory=list)
    selected_run: ResearchRunRecord | None = None


class ResearchArtifactContentResponse(BaseModel):
    filename: str
    path: str
    media_type: str
    encoding: Literal["text"]
    preview_kind: ResearchArtifactPreviewKind
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
    effective_from: date | None = None
    effective_to: date | None = None
    status: str = "active"
    source_template_ref: str | None = None

    @field_validator("name", "taxonomy_type", "status", "root_default_target_dimension", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @field_validator("purpose", "budgeting_level", "source_template_ref", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_taxonomy_contract(self) -> "TaxonomyCreateRequest":
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
        if self.budgeting_level and not self.planning_enabled:
            raise ValueError("budgeting_level requires planning_enabled.")
        if self.planning_enabled and self.primary_assignment_scope != "instrument":
            raise ValueError("planning_enabled taxonomies must use instrument assignment scope.")
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
    effective_from: date | None = None
    effective_to: date | None = None
    status: str | None = None

    @field_validator("name", "taxonomy_type", "status", "root_default_target_dimension", mode="before")
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
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
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
    effective_from: date | None = None
    effective_to: date | None = None
    status: str = "active"

    @field_validator("target_entity_id", "taxonomy_node_id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @model_validator(mode="after")
    def validate_assignment_contract(self) -> "TaxonomyAssignmentCreateRequest":
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
        return self


class TaxonomyAssignmentUpdateRequest(BaseModel):
    taxonomy_node_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: str | None = None

    @field_validator("taxonomy_node_id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_required_text(value)

    @model_validator(mode="after")
    def validate_assignment_contract(self) -> "TaxonomyAssignmentUpdateRequest":
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
        return self


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
                raise ValueError("taxonomy_node_id cannot be combined with a non-node target_member_type.")
            if self.target_member_id and self.target_member_id != self.taxonomy_node_id:
                raise ValueError("taxonomy_node_id must match target_member_id when both are provided.")
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
    effective_from: date | None = None
    effective_to: date | None = None
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
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
        if not self.weight_enabled and not self.risk_budget_enabled:
            raise ValueError("At least one target dimension must be enabled.")
        if not self.lines:
            raise ValueError("Target set lines are required.")
        return self


class TargetSetUpdateRequest(BaseModel):
    name: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
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
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from.")
        if self.weight_enabled is False and self.risk_budget_enabled is False:
            raise ValueError("At least one target dimension must be enabled.")
        return self


ContributionAxis = Literal["instrument", "account", "instrument_type", "currency", "taxonomy"]
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
    coverage_state: CoverageState
    market_observation_count: int = 0
    return_observation_eligible: bool = False
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
    coverage_state: CoverageState
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
    coverage_state: CoverageState
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
    coverage_state: CoverageState
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
    coverage_state: CoverageState
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


class AccountWorkspaceAccount(BaseModel):
    account: AccountRecord
    default_settlement_cash_account_name: str | None = None
    linked_transaction_count: int
    linked_posting_count: int
    derived_cash_balance: float
    derived_cash_balance_base: float | None = None
    pending_settlement: float = 0.0
    pending_settlement_base: float | None = None
    account_value_base: float | None = None
    position_line_count: int
    position_market_value: float | None = None
    position_market_value_currency: SupportedCurrency | None = None


class AccountsWorkspaceSummary(BaseModel):
    account_count: int
    deposit_account_count: int
    securities_account_count: int
    ledger_posting_count: int
    position_line_count: int


class AccountsWorkspaceResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    summary: AccountsWorkspaceSummary
    derivation_boundary: DerivationBoundaryStatus
    selected_account_id: str | None = None
    accounts: list[AccountWorkspaceAccount]
    ledger_postings: list[LedgerPostingRecord]
    positions: list[AccountPositionRecord]
    linked_transactions_summary: TransactionListSummary | None = None
    linked_transactions: list[TransactionRecord] = Field(default_factory=list)


class LedgerPostingListSummary(BaseModel):
    posting_count: int
    cash_posting_count: int
    position_posting_count: int


class LedgerPostingListResponse(BaseModel):
    portfolio_id: str
    summary: LedgerPostingListSummary
    ledger_postings: list[LedgerPostingRecord]


class TransactionCreateRequest(BaseModel):
    transaction_type: TransactionType
    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account_id: str
    settlement_cash_account_id: str | None = None
    instrument_id: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    gross_amount: float = Field(ge=0)
    counter_amount: float | None = Field(default=None, ge=0)
    fx_rate: float | None = Field(default=None, gt=0)
    fees: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
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
                raise ValueError("Transaction currency must be one of USD, HKD, or CNY.")
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
        return _quantize_numeric_input(value, quantum=QUANTITY_DISPLAY_QUANTUM)

    @field_validator("price", mode="before")
    @classmethod
    def normalize_price_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=PRICE_DISPLAY_QUANTUM)

    @field_validator("gross_amount", "counter_amount", "fees", "taxes", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=AMOUNT_DISPLAY_QUANTUM)

    @model_validator(mode="after")
    def validate_amount_contract(self) -> "TransactionCreateRequest":
        settlement_date = self.settlement_date or self.trade_date
        if settlement_date < self.trade_date:
            raise ValueError("settlement_date must not be earlier than trade_date.")
        if self.entitlement_date is not None and self.entitlement_date > self.trade_date:
            raise ValueError("entitlement_date must not be later than trade_date.")
        if self.acquisition_date is not None and self.acquisition_date > self.trade_date:
            raise ValueError("acquisition_date must not be later than trade_date.")

        if self.transaction_type in {"buy", "sell"}:
            if not self.instrument_id:
                raise ValueError("Security transactions require instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Security transactions require positive quantity.")
            if self.price is None or self.price <= 0:
                raise ValueError("Security transactions require positive price.")
            if not _amount_contract_matches_display_price(
                quantity=self.quantity,
                price=self.price,
                gross_amount=self.gross_amount,
            ):
                raise ValueError("gross_amount must equal quantity multiplied by price for buy and sell.")

        if self.transaction_type in {"dividend", "coupon"}:
            if not self.instrument_id:
                raise ValueError("Income transactions require instrument_id.")
            if self.quantity is not None or self.price is not None:
                raise ValueError("Dividend and coupon must not carry quantity or price.")

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
                raise ValueError("Return of capital does not yet support entitlement_date.")

        if self.transaction_type == "dividend_reinvestment":
            if not self.instrument_id:
                raise ValueError("Dividend reinvestment requires instrument_id.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Dividend reinvestment requires positive quantity.")
            if self.entitlement_date is not None:
                raise ValueError("Dividend reinvestment does not yet support entitlement_date.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Dividend reinvestment must not carry settlement_cash_account_id.")
            if self.price is not None:
                if self.price <= 0:
                    raise ValueError("Dividend reinvestment price must be positive when provided.")
                if not _amount_contract_matches_display_price(
                    quantity=self.quantity,
                    price=self.price,
                    gross_amount=self.gross_amount,
                ):
                    raise ValueError(
                        "gross_amount must equal quantity multiplied by price for dividend reinvestment."
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
                raise ValueError("Cash-flow transactions must not carry quantity or price.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Cash-flow transactions must not carry settlement_cash_account_id.")
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
                raise ValueError("FX conversion must not carry settlement_cash_account_id.")
            if not self.counterparty_account_id:
                raise ValueError("FX conversion requires counterparty_account_id.")
            if self.counter_amount is None or self.counter_amount <= 0:
                raise ValueError("FX conversion requires positive counter_amount.")
            if self.fx_rate is None or self.fx_rate <= 0:
                raise ValueError("FX conversion requires positive fx_rate.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("FX conversion must not carry fees or taxes.")

        if self.transaction_type != "fx_conversion" and (
            self.counter_amount is not None or self.fx_rate is not None
        ):
            raise ValueError("counter_amount and fx_rate are only allowed for fx_conversion.")

        if self.transaction_type != "fx_conversion" and self.counterparty_account_id is not None:
            raise ValueError("counterparty_account_id is only allowed for fx_conversion.")

        if self.transaction_type in {"fee", "tax"}:
            if self.quantity is not None:
                raise ValueError("Fee and tax transactions must not carry quantity.")
            if self.price is not None:
                raise ValueError("Fee and tax transactions must not carry price.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Fee and tax transactions must not carry nested fees or taxes.")
            if self.entitlement_date is not None and not self.instrument_id:
                raise ValueError("entitlement_date on fee and tax requires instrument_id.")

        if (
            self.entitlement_date is not None
            and self.transaction_type not in {"dividend", "coupon", "fee", "tax"}
        ):
            raise ValueError(
                "entitlement_date is only allowed for dividend, coupon, fee, and tax."
            )

        if self.transaction_type in {"transfer_in", "transfer_out"}:
            if self.transfer_scope != "internal_portfolio":
                raise ValueError("Transfer transactions require transfer_scope=internal_portfolio.")
            if self.transfer_object_type is None:
                raise ValueError("Transfer transactions require transfer_object_type.")
            if not self.transfer_group_id:
                raise ValueError("Transfer transactions require transfer_group_id.")
            if self.transfer_object_type == "cash":
                if self.instrument_id is not None or self.quantity is not None or self.price is not None:
                    raise ValueError("Cash transfers must not carry instrument, quantity, or price.")
            if self.transfer_object_type == "position":
                if not self.instrument_id:
                    raise ValueError("Position transfers require instrument_id.")
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError("Position transfers require positive quantity.")
                if self.price is not None:
                    raise ValueError("Position transfers must not carry price.")
                if self.gross_amount <= 0:
                    raise ValueError("Position transfers require transferred cost basis.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Transfer transactions must not carry fees or taxes.")

        if self.transaction_type not in {"transfer_in", "transfer_out"} and (
            self.transfer_scope is not None
            or self.transfer_object_type is not None
            or self.transfer_group_id is not None
        ):
            raise ValueError("Transfer fields are only allowed for transfer transactions.")

        if self.transaction_type == "opening_balance":
            if self.instrument_id:
                if self.acquisition_date is None:
                    self.acquisition_date = self.trade_date
            elif self.acquisition_date is not None:
                raise ValueError("Cash opening balance must not carry acquisition_date.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Opening balance must not carry settlement_cash_account_id.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Opening balance must not carry fees or taxes.")
            if self.instrument_id:
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError("Security opening balance requires positive quantity.")
                if self.price is not None:
                    if self.price <= 0:
                        raise ValueError("Security opening balance price must be positive when provided.")
                    if not _amount_contract_matches_display_price(
                        quantity=self.quantity,
                        price=self.price,
                        gross_amount=self.gross_amount,
                    ):
                        raise ValueError(
                            "gross_amount must equal quantity multiplied by price for security opening balance."
                        )
            elif self.quantity is not None or self.price is not None:
                raise ValueError("Cash opening balance must not carry quantity or price.")
        elif self.acquisition_date is not None:
            raise ValueError("acquisition_date is only allowed for security opening balance.")

        return self


class InternalTransferCreateRequest(BaseModel):
    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    transfer_object_type: TransferObjectType
    from_account_id: str
    to_account_id: str
    instrument_id: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    gross_amount: float | None = Field(default=None, ge=0)
    note: str | None = None
    transfer_group_id: str | None = None

    @model_validator(mode="after")
    def validate_internal_transfer(self) -> "InternalTransferCreateRequest":
        settlement_date = self.settlement_date or self.trade_date
        if settlement_date < self.trade_date:
            raise ValueError("settlement_date must not be earlier than trade_date.")
        if self.from_account_id == self.to_account_id:
            raise ValueError("Internal transfer requires distinct source and destination accounts.")
        if self.transfer_object_type == "cash":
            if self.gross_amount is None or self.gross_amount <= 0:
                raise ValueError("Cash transfer requires positive amount.")
            if self.instrument_id is not None or self.quantity is not None:
                raise ValueError("Cash transfer must not carry instrument_id or quantity.")
        if self.transfer_object_type == "position":
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
        return _quantize_numeric_input(value, quantum=QUANTITY_DISPLAY_QUANTUM)

    @field_validator("gross_amount", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=AMOUNT_DISPLAY_QUANTUM)


class TransactionBatchResponse(BaseModel):
    portfolio_id: str
    created_count: int
    transfer_group_id: str | None = None
    transactions: list[TransactionRecord]


class TransactionDeleteResponse(BaseModel):
    portfolio_id: str
    deleted_count: int
    deleted_transaction_ids: list[str]
    transfer_group_id: str | None = None
