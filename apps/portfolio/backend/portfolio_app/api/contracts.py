from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from portfolio_ops_instrument_core import (
    CorporateActionEvent as CorporateActionEventContract,
    DataStatus as CoverageState,
    IdentifierType,
    InstrumentCore as InstrumentCoreContract,
    InstrumentIdentifier as InstrumentIdentifierContract,
    InstrumentType,
    MetricFamily,
    PriceUnit,
    QuoteBasis,
    QuoteSelectionPolicy,
    ReturnSemantics,
    canonical_price_contract,
    parse_persisted_price_contract,
    validate_market_data_identity,
)


AccountScopedInstrumentType = Literal[
    "fund",
    "etf",
    "bond",
    "equity",
    "fcn",
    "option",
    "other",
]
AccountType = Literal["deposit_account", "securities_account"]
CostBasisMethod = Literal["moving_average", "fifo"]
SupportedCurrency = Literal["USD", "HKD", "CNY"]
TaxonomyAssignmentScope = Literal["instrument", "account", "cash_bucket"]
TargetMemberType = Literal[
    "taxonomy_node",
    "instrument",
    "account",
    "cash_bucket",
    "derivative_bucket",
]
DefaultTargetDimension = Literal["weight", "risk_budget"]
TransactionCommandType = Literal[
    "buy",
    "sell",
    "option_write",
    "option_buy_to_close",
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
    "lifecycle_event",
    "opening_balance",
]
TransactionType = TransactionCommandType | Literal["transfer_in", "transfer_out"]
OptionAction = Literal[
    "buy_to_open",
    "sell_to_close",
    "sell_to_open",
    "buy_to_close",
]
LifecycleEventType = Literal[
    "fcn_knock_in",
    "fcn_knock_out",
    "fcn_maturity",
    "option_long_expiry",
    "option_long_exercise",
    "option_writer_expiry",
    "option_assignment",
]
POSITION_EFFECTIVE_COMMAND_TYPES = frozenset(
    {
        "buy",
        "sell",
        "dividend_reinvestment",
        "maturity_redemption",
    }
)
FeeCategory = Literal[
    "unknown",
    "transaction_cost",
    "management_fee",
    "custody_fee",
    "administration_fee",
    "performance_fee",
    "other",
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
    "position_recognition_bridge",
    "security_income_cash",
    "security_redemption_cash",
    "security_reinvestment_position",
    "account_income_cash",
    "account_expense_cash",
    "corporate_action_position_adjustment",
    "option_premium_liability",
    "option_liability_release",
]
PositionLotStatus = Literal["open", "closed"]
PositionLotCloseReason = Literal["disposed", "transferred", "corporate_action"]
LedgerSourceType = TransactionType | Literal["corporate_action"]
PositionLotOpeningType = TransactionType | Literal["corporate_action"]
ResearchRunStatus = Literal["running", "completed", "failed"]
ResearchRunReliabilityState = Literal["current", "stale", "unassessed", "not_completed"]
ResearchExecutionStatus = Literal["ready", "manual_review_required"]
ResearchLifecycle = Literal["held", "observed", "former"]
ResearchEligibility = Literal["eligible", "pm_review_required"]
ResearchArtifactPreviewKind = Literal["text", "html", "binary"]
ResearchAsOfMode = Literal["dynamic", "pinned"]
ResearchTargetDimension = Literal["scope_default", "weight", "risk_budget"]
ResearchCapitalMode = Literal["unit_notional", "fixed_gross", "target_volatility", "volatility_cap"]
ResearchCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]
ResearchMissingReturnPolicy = Literal["strict", "complete_case_drop"]
ResearchBacktestRebalanceFrequency = Literal["1w", "1m", "3m"]
PortfolioCalculationFrequency = Literal["daily", "weekly", "monthly"]
PortfolioRiskCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]
PortfolioRiskResultStatus = Literal["available", "insufficient_samples", "unavailable"]
PerformanceStartBoundaryKind = Literal[
    "close_eod",
    "funded_bod",
    "imported_opening_eod",
]
XirrSolverStatus = Literal[
    "unique_root",
    "invalid_cash_flows",
    "no_root",
    "multiple_roots_or_non_unique",
]
PortfolioRiskCovarianceModel = Literal["ewma_vol_shrinkage_corr_covariance", "ewma_covariance", "sample_covariance"]
PortfolioRiskContributionMode = Literal["signed", "abs"]
TargetSetType = Literal["saa", "taa"]

SUPPORTED_PORTFOLIO_CURRENCIES: tuple[SupportedCurrency, ...] = ("USD", "HKD", "CNY")
SUPPORTED_RISK_WINDOW_DAYS = {30, 90, 180, 366, 730}
QUANTITY_DISPLAY_QUANTUM = Decimal("0.01")
AMOUNT_DISPLAY_QUANTUM = Decimal("0.01")
PRICE_DISPLAY_QUANTUM = Decimal("0.0001")
QUANTITY_SOURCE_QUANTUM = Decimal("0.000000000001")
PRICE_SOURCE_QUANTUM = Decimal("0.000000000001")
AMOUNT_SOURCE_QUANTUM = Decimal("0.00000001")
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
    price_scale: object = 1,
) -> bool:
    resolved_quantity = _to_decimal(quantity)
    resolved_price = _to_decimal(price)
    resolved_gross_amount = _to_decimal(gross_amount)
    resolved_price_scale = _to_decimal(price_scale)
    if (
        resolved_quantity is None
        or resolved_price is None
        or resolved_gross_amount is None
        or resolved_quantity <= 0
        or resolved_price <= 0
        or resolved_price_scale is None
        or resolved_price_scale <= 0
    ):
        return False

    expected_gross_amount = resolved_quantity * resolved_price * resolved_price_scale
    if abs(resolved_gross_amount - expected_gross_amount) <= AMOUNT_CONTRACT_EPSILON:
        return True

    derived_display_price = (
        resolved_gross_amount / resolved_quantity / resolved_price_scale
    ).quantize(
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
    return resolved_value.quantize(quantum, rounding=ROUND_HALF_UP)


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


class InstrumentLatestMarketDataPoint(BaseModel):
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    price_unit: PriceUnit
    price_scale: Decimal = Field(gt=0)
    provider: str | None = None
    status: CoverageState

    @model_validator(mode="after")
    def validate_point_contract(self) -> "InstrumentLatestMarketDataPoint":
        validate_market_data_identity(
            metric_family=self.metric_family,
            quote_basis=self.quote_basis,
        )
        parse_persisted_price_contract(
            price_unit=self.price_unit,
            price_scale=self.price_scale,
        )
        return self


class InstrumentOption(BaseModel):
    instrument_core: InstrumentCoreContract
    coverage_state: CoverageState
    latest_market_data: list[InstrumentLatestMarketDataPoint]
    quote_selection_policy: QuoteSelectionPolicy

    @model_validator(mode="after")
    def validate_latest_market_data_contract(self) -> "InstrumentOption":
        for point in self.latest_market_data:
            canonical_unit, canonical_scale = canonical_price_contract(
                instrument_type=self.instrument_core.instrument_type,
                metric_family=point.metric_family,
                quote_basis=point.quote_basis,
            )
            if point.price_unit != canonical_unit or point.price_scale != canonical_scale:
                raise ValueError(
                    "latest_market_data price contract does not match the canonical "
                    "instrument identity."
                )
        return self


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


class OptionContractTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlying_instrument_id: str = Field(min_length=1)
    option_type: Literal["call", "put"]
    expiry_date: date
    strike: Decimal = Field(gt=0, lt=Decimal("1e16"))
    contract_multiplier: Decimal = Field(gt=0, lt=Decimal("1e16"))
    settlement_type: Literal["physical", "cash"]

    @field_validator("underlying_instrument_id", mode="before")
    @classmethod
    def normalize_underlying(cls, value: object) -> object:
        return _normalize_required_text(value)


class FCNContractTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notional: Decimal = Field(gt=0, lt=Decimal("1e20"))
    issue_date: date
    maturity_date: date
    issuer: str = Field(min_length=1)
    counterparty: str = Field(min_length=1)
    underlying_instrument_ids: list[str] = Field(min_length=1)
    deliverable_instrument_ids: list[str] = Field(default_factory=list)
    barrier_type: Literal["none", "knock_in", "knock_out", "dual"] = "none"
    barrier_level: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e16"))

    @field_validator("issuer", "counterparty", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @field_validator(
        "underlying_instrument_ids",
        "deliverable_instrument_ids",
        mode="before",
    )
    @classmethod
    def normalize_instrument_ids(cls, value: object) -> object:
        return _normalize_optional_text_list(value)

    @model_validator(mode="after")
    def validate_terms(self) -> "FCNContractTerms":
        if self.maturity_date < self.issue_date:
            raise ValueError("maturity_date must not precede issue_date.")
        if self.barrier_type == "none" and self.barrier_level is not None:
            raise ValueError("barrier_level must be omitted when barrier_type is none.")
        if self.barrier_type != "none" and self.barrier_level is None:
            raise ValueError("barrier_level is required for an active barrier.")
        return self


class DerivativeContractCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    derivative_contract_id: str = Field(min_length=1, max_length=200)
    contract_name: str = Field(min_length=1)
    contract_type: Literal["fcn", "option"]
    external_reference: str | None = Field(default=None, max_length=200)
    terms: OptionContractTerms | FCNContractTerms

    @field_validator("derivative_contract_id", "contract_name", mode="before")
    @classmethod
    def normalize_required_fields(cls, value: object) -> object:
        return _normalize_required_text(value)

    @field_validator("external_reference", mode="before")
    @classmethod
    def normalize_external_reference(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_terms_match_type(self) -> "DerivativeContractCreate":
        if self.contract_type == "option" and not isinstance(
            self.terms, OptionContractTerms
        ):
            raise ValueError("Option contracts require option terms.")
        if self.contract_type == "fcn" and not isinstance(self.terms, FCNContractTerms):
            raise ValueError("FCN contracts require FCN terms.")
        return self


class DerivativeContractRecord(DerivativeContractCreate):
    portfolio_id: str
    account_id: str
    currency: SupportedCurrency
    created_at: str


class DerivativeContractListResponse(BaseModel):
    portfolio_id: str
    derivative_contracts: list[DerivativeContractRecord]


class TransactionRecord(BaseModel):
    transaction_id: str
    transaction_sequence: int = Field(ge=1)
    portfolio_id: str
    transaction_type: TransactionType
    # Read-only canonical meaning derived from the current persisted action.
    option_action: OptionAction | None = None
    lifecycle_event_type: LifecycleEventType | None = None
    flow_scope: FlowScope
    trade_date: date
    trade_time: str
    trade_at: str
    trade_timezone: str
    trade_time_is_estimated: bool = False
    settlement_date: date
    position_effective_date: date | None = None
    economic_date: date
    external_flow_date: date | None = None
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account: AccountRecord
    settlement_cash_account: AccountRecord | None = None
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
    quantity: float | None = None
    source_quantity: str | None = None
    price: float | None = None
    source_price: str | None = None
    gross_amount: float
    source_gross_amount: str | None = None
    counter_amount: float | None = None
    source_counter_amount: str | None = None
    fx_rate: float | None = None
    source_fx_rate: str | None = None
    fees: float = 0.0
    source_fees: str | None = None
    fee_category: FeeCategory = "unknown"
    taxes: float = 0.0
    source_taxes: str | None = None
    currency: str
    transfer_scope: TransferScope | None = None
    transfer_object_type: TransferObjectType | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    source_system: str | None = None
    external_reference: str | None = None
    net_cash_effect: float | None = None
    note: str | None = None
    created_at: str | None = None
    row_version: int = Field(default=1, ge=1)


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
    attribution_account_id: str | None = None
    settlement_cash_account_id: str | None = None
    posting_role: PostingRole
    source_transaction_type: LedgerSourceType
    trade_date: date
    settlement_date: date
    effective_date: date
    recognition_start_date: date | None = None
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
    cash_amount_delta: float | None = None
    pending_amount_delta: float | None = None
    quantity_delta: float | None = None
    cost_basis_delta: float | None = None
    liability_amount_delta: float | None = None
    realized_pnl_delta: float | None = None
    option_action: OptionAction | None = None
    obligation_id: str | None = None
    currency: str
    transfer_group_id: str | None = None
    note: str | None = None
    corporate_action_event: CorporateActionEventContract | None = None


class AccountPositionRecord(BaseModel):
    position_id: str | None = None
    account_id: str
    position_reference_id: str
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
    quantity: float
    cost_basis: float | None = None
    last_price: float | None = None
    market_value: float | None = None
    carrying_value: float | None = None
    fair_value: float | None = None
    fair_value_coverage_status: CoverageState
    valuation_basis: Literal["market_quote", "carried_cost"]
    coverage_status: str
    currency: str
    cost_basis_method: CostBasisMethod | None = None
    open_position_lot_count: int = 0


class PositionRecord(BaseModel):
    position_id: str
    portfolio_id: str
    position_reference_id: str
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
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
    return_semantics: ReturnSemantics = "unknown"
    metric_family: str | None = None
    currency: str
    coverage_state: CoverageState = "unavailable"
    selection_reason: str | None = None
    split_adjusted: bool = False
    points: list[InstrumentPriceChartPoint] = Field(default_factory=list)
    summary: InstrumentPriceChartSummary


class TransactionExecutionQuoteResponse(BaseModel):
    portfolio_id: str
    instrument_id: str
    requested_as_of_date: date
    selection_role: Literal["trading", "valuation"] | None = None
    value: float | None = Field(default=None, gt=0)
    quote_date: date | None = None
    quote_basis: QuoteBasis | None = None
    metric_family: MetricFamily | None = None
    currency: str
    provider: str | None = None
    status: Literal["complete", "unavailable"]
    stale: bool = False
    price_unit: PriceUnit | None = None
    price_scale: float | None = Field(default=None, gt=0)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_execution_quote_contract(self) -> "TransactionExecutionQuoteResponse":
        if self.status == "complete":
            required_fields = {
                "selection_role": self.selection_role,
                "value": self.value,
                "quote_date": self.quote_date,
                "quote_basis": self.quote_basis,
                "metric_family": self.metric_family,
                "price_unit": self.price_unit,
                "price_scale": self.price_scale,
            }
            missing = [name for name, value in required_fields.items() if value is None]
            if missing:
                raise ValueError(
                    "Complete execution quote is missing: " + ", ".join(missing) + "."
                )
            if self.unavailable_reason is not None:
                raise ValueError("Complete execution quote cannot have unavailable_reason.")
            assert self.metric_family is not None
            assert self.quote_basis is not None
            assert self.price_unit is not None
            assert self.price_scale is not None
            validate_market_data_identity(
                metric_family=self.metric_family,
                quote_basis=self.quote_basis,
            )
            parse_persisted_price_contract(
                price_unit=self.price_unit,
                price_scale=self.price_scale,
            )
            return self

        unavailable_fields = {
            "selection_role": self.selection_role,
            "value": self.value,
            "quote_date": self.quote_date,
            "quote_basis": self.quote_basis,
            "metric_family": self.metric_family,
            "provider": self.provider,
            "price_unit": self.price_unit,
            "price_scale": self.price_scale,
        }
        populated = [name for name, value in unavailable_fields.items() if value is not None]
        if populated:
            raise ValueError(
                "Unavailable execution quote must not populate: "
                + ", ".join(populated)
                + "."
            )
        if self.stale:
            raise ValueError("Unavailable execution quote cannot be stale.")
        if not str(self.unavailable_reason or "").strip():
            raise ValueError("Unavailable execution quote requires unavailable_reason.")
        return self


class PositionLotRealizationRecord(BaseModel):
    realization_id: str
    transaction_id: str
    transaction_type: TransactionType
    trade_date: date
    position_effective_date: date
    quantity: float
    gross_proceeds: float | None = None
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
    position_reference_id: str
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
    currency: str
    cost_basis_method: CostBasisMethod
    opened_by_transaction_id: str
    opening_transaction_type: PositionLotOpeningType
    opened_at: date
    acquisition_date: date
    closed_at: date | None = None
    status: PositionLotStatus
    close_reason: PositionLotCloseReason | None = None
    source_position_lot_id: str | None = None
    corporate_action_event_id: str | None = None
    corporate_action_event: CorporateActionEventContract | None = None
    corporate_action_quantity_out: float | None = None
    corporate_action_cost_basis_out: float | None = None
    predecessor_quantity: float | None = None
    unit_cost_basis_before: float | None = None
    unit_cost_basis_after: float | None = None
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
    realized_gross_proceeds: float = 0.0
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


class TransactionChangeLogRecord(BaseModel):
    change_id: str
    portfolio_id: str
    transaction_id: str
    change_type: Literal["create", "update", "delete"]
    row_version: int = Field(ge=1)
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    request_idempotency_key: str | None = None
    changed_at: str


class TransactionChangeLogSummary(BaseModel):
    change_count: int


class TransactionChangeLogResponse(BaseModel):
    portfolio_id: str
    summary: TransactionChangeLogSummary
    changes: list[TransactionChangeLogRecord] = Field(default_factory=list)


class TransactionWorkspaceResponse(BaseModel):
    portfolio_id: str
    summary: TransactionListSummary
    derivation_boundary: DerivationBoundaryStatus
    selected_transaction_id: str | None = None
    transactions: list[TransactionRecord]
    selected_transaction: TransactionRecord | None = None
    delete_scope_row_versions: dict[str, int]
    ledger_summary: LedgerPostingListSummary
    ledger_postings: list[LedgerPostingRecord]
    related_position_lot_summary: PositionLotListSummary
    related_position_lots: list[PositionLotRecord]
    change_log_summary: TransactionChangeLogSummary
    change_log: list[TransactionChangeLogRecord] = Field(default_factory=list)


InstrumentEventTaskStatus = Literal[
    "pending",
    "processed",
    "not_applicable",
    "source_cancelled",
    "no_entitlement",
    "needs_review",
]


class InstrumentEventTaskLinkedTransaction(BaseModel):
    transaction_id: str
    transaction_type: str
    trade_date: date
    settlement_date: date
    position_effective_date: date | None = None
    entitlement_date: date | None = None
    gross_amount: Decimal
    quantity: Decimal | None = None
    link_role: Literal["distribution", "reinvestment", "reinvestment_purchase"]
    linked_event_revision_id: str
    linked_by: str
    linked_at: str


class InstrumentEventTaskRecord(BaseModel):
    instrument_event_task_id: str
    portfolio_id: str
    account_id: str
    instrument_id: str
    instrument_name: str | None = None
    event_source: str
    event_action_id: str
    current_event_revision_id: str
    event_type: str
    source_revision_kind: Literal["original", "correction", "cancellation"]
    source_event_state: Literal["active", "cancelled"]
    announcement_date: date | None = None
    record_date: date | None = None
    effective_date: date
    payable_date: date | None = None
    cash_per_unit: Decimal | None = None
    unit_ratio: Decimal | None = None
    reinvestment_nav: Decimal | None = None
    entitled_quantity: Decimal
    expected_gross_amount: Decimal | None = None
    resolution_status: Literal["pending", "processed", "not_applicable"]
    reviewed_event_revision_id: str | None = None
    resolution_note: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    status: InstrumentEventTaskStatus
    attention_required: bool
    attention_reason: str | None = None
    linked_transactions: list[InstrumentEventTaskLinkedTransaction] = Field(
        default_factory=list
    )
    row_version: int = Field(ge=1)
    created_at: str
    updated_at: str


class InstrumentEventTaskListResponse(BaseModel):
    portfolio_id: str
    accounting_policy: Literal[
        "official_unit_nav_assume_no_unrecorded_distribution"
    ]
    attention_count: int
    tasks: list[InstrumentEventTaskRecord] = Field(default_factory=list)


class InstrumentEventTaskReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["processed", "not_applicable", "reopened"]
    transaction_ids: list[str] = Field(default_factory=list)
    note: str = Field(min_length=1, max_length=2000)
    reviewed_by: str = Field(min_length=1, max_length=200)
    expected_row_version: int = Field(ge=1)

    @field_validator("transaction_ids", mode="before")
    @classmethod
    def normalize_transaction_ids(cls, value: object) -> object:
        return _normalize_optional_text_list(value)

    @field_validator("note", "reviewed_by", mode="before")
    @classmethod
    def normalize_review_text(cls, value: object) -> object:
        return _normalize_required_text(value)

    @model_validator(mode="after")
    def validate_review_scope(self) -> "InstrumentEventTaskReviewRequest":
        if self.decision == "processed" and not self.transaction_ids:
            raise ValueError("Processed review requires linked transaction_ids.")
        if self.decision != "processed" and self.transaction_ids:
            raise ValueError(
                "Only a processed review may include linked transaction_ids."
            )
        return self


class TransactionPositionPreviewResponse(BaseModel):
    portfolio_id: str
    account_id: str
    position_kind: Literal["instrument", "derivative_contract"]
    position_reference_id: str
    as_of_date: date
    trade_at: str
    quantity: float


class DailySnapshotRecord(BaseModel):
    as_of_date: date
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    coverage_state: CoverageState
    valuation_coverage_state: CoverageState
    return_coverage_state: CoverageState
    book_pnl_coverage_state: CoverageState
    attribution_coverage_state: CoverageState
    return_chain_continuous: bool
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    total_position_count: int = 0
    priced_position_count: int = 0
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    return_observation_exclusion_reason: str | None = None
    cash_balance: float | None = None
    pending_settlement: float | None = None
    position_market_value: float | None = None
    derivative_liability_base: float | None = None
    derivative_liability: float | None = None
    open_option_obligation_count: int = 0
    option_obligation_coverage_state: CoverageState = "complete"
    nav: float | None = None
    open_cost_basis: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    pending_settlement_currency_gains: float | None = None
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
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    as_of_clamp_reason: str | None = None


class DailySnapshotListResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    valuation_timezone: str
    valuation_cutoff_policy: str
    summary: DailySnapshotListSummary
    snapshots: list[DailySnapshotRecord]


class DailySnapshotRecalculationRequest(BaseModel):
    portfolio_ids: list[str] = Field(default_factory=list)
    instrument_ids: list[str] = Field(default_factory=list)
    dirty_from: date | None = None
    refresh_all: bool = False

    @model_validator(mode="after")
    def require_one_unambiguous_target_selector(
        self,
    ) -> "DailySnapshotRecalculationRequest":
        selected = sum(
            (
                bool(self.portfolio_ids),
                bool(self.instrument_ids),
                self.refresh_all,
            )
        )
        if selected != 1:
            raise ValueError(
                "Exactly one of portfolio_ids, instrument_ids, or refresh_all "
                "must select the recalculation target."
            )
        return self


class DailySnapshotRecalculationAccepted(BaseModel):
    portfolio_id: str
    status: Literal["accepted"]
    daily_snapshot_status: Literal["stale", "running"]
    refresh_request_id: str = Field(min_length=1)
    dirty_from: date | None = None


class DailySnapshotRecalculationResponse(BaseModel):
    portfolio_ids: list[str]
    accepted: list[DailySnapshotRecalculationAccepted]


class DailyPerformancePoint(BaseModel):
    as_of_date: date
    coverage_state: CoverageState
    valuation_coverage_state: CoverageState
    return_coverage_state: CoverageState
    book_pnl_coverage_state: CoverageState
    attribution_coverage_state: CoverageState
    return_chain_continuous: bool
    stale_price_flag: bool = False
    stale_fx_flag: bool = False
    market_observation_count: int = 0
    return_observation_eligible: bool = False
    return_observation_exclusion_reason: str | None = None
    performance_basis: Literal["market_value", "operational_carrying_basis"] = "market_value"
    performance_label: str = "Total Portfolio Return"
    beginning_nav: float | None = None
    ending_nav: float | None = None
    pending_settlement: float | None = None
    realized_pnl: float | None = None
    derivative_lifecycle_realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    pending_settlement_currency_gains: float | None = None
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
    valuation_coverage_state: CoverageState
    return_coverage_state: CoverageState
    book_pnl_coverage_state: CoverageState
    attribution_coverage_state: CoverageState
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    as_of_clamp_reason: str | None = None
    start_boundary_kind: PerformanceStartBoundaryKind | None = None
    include_start_date_return: bool = False
    snapshot_count: int
    return_observation_count: int
    risk_return_observation_count: int = 0
    risk_annualization_periods_per_year: float | None = None
    risk_calculation_frequency: PortfolioCalculationFrequency = "daily"
    risk_minimum_sample_count: int = 2
    risk_sample_count: int = 0
    risk_result_status: PortfolioRiskResultStatus = "unavailable"
    risk_unavailable_reason: str | None = None
    performance_basis: Literal["market_value", "operational_carrying_basis"] = "market_value"
    performance_label: str = "Total Portfolio Return"
    ordinary_sleeve_twr_status: Literal["unavailable"] = "unavailable"
    ordinary_sleeve_twr_reason: str
    latest_complete_as_of_date: date | None = None
    start_nav: float | None = None
    end_nav: float | None = None
    external_cash_in: float = 0.0
    external_cash_out: float = 0.0
    net_external_inflow: float = 0.0
    cumulative_twr: float | None = None
    annualization_eligible: bool = False
    annualization_years: float | None = None
    annualization_unavailable_reason: str | None = None
    annualized_twr: float | None = None
    irr: float | None = None
    mwror: float | None = None
    irr_solver_status: XirrSolverStatus | None = None
    irr_unavailable_reason: str | None = None
    absolute_change: float | None = None
    delta: float | None = None
    realized_pnl: float | None = None
    derivative_lifecycle_realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    income_cash_amount: float | None = None
    expense_cash_amount: float | None = None
    cash_currency_gains: float | None = None
    pending_settlement_currency_gains: float | None = None
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
    quality_warnings: list[str] = Field(default_factory=list)


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
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    as_of_clamp_reason: str | None = None
    start_boundary_kind: PerformanceStartBoundaryKind | None = None
    include_start_date_return: bool = False
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
    pending_settlement_currency_gains: float | None = None
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
    position_reference_id: str
    instrument_id: str | None = None
    instrument_ref: InstrumentCoreContract | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractRecord | None = None
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


class AnalyticsScopePolicyRecord(BaseModel):
    analytics_scope_policy_id: str
    portfolio_id: str
    taxonomy_id: str
    taxonomy_node_id: str
    risk_eligible: bool
    risk_budget_eligible: bool
    performance_scope: Literal[
        "ordinary",
        "derivative_lifecycle",
        "operational_only",
        "unallocated",
    ]
    valuation_basis: Literal[
        "market",
        "fair_value",
        "carrying",
        "event",
        "obligation",
        "cash",
        "unknown",
    ]
    exclusion_reason: str | None = None
    effective_from: date
    effective_to: date | None = None
    policy_version: int = Field(ge=1)
    superseded_by_policy_id: str | None = None
    created_at: str


class AnalyticsScopePolicyUpsertRequest(BaseModel):
    risk_eligible: bool
    risk_budget_eligible: bool
    performance_scope: Literal[
        "ordinary",
        "derivative_lifecycle",
        "operational_only",
        "unallocated",
    ]
    valuation_basis: Literal[
        "market",
        "fair_value",
        "carrying",
        "event",
        "obligation",
        "cash",
        "unknown",
    ]
    exclusion_reason: str | None = None
    effective_from: date
    effective_to: date | None = None

    @field_validator("exclusion_reason", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_policy(self) -> "AnalyticsScopePolicyUpsertRequest":
        if self.risk_budget_eligible and not self.risk_eligible:
            raise ValueError("risk_budget_eligible requires risk_eligible.")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        if (
            not self.risk_eligible or self.performance_scope != "ordinary"
        ) and not self.exclusion_reason:
            raise ValueError("Excluded or non-ordinary policies require exclusion_reason.")
        return self


class AnalyticsTaxonomySelectionRecord(BaseModel):
    analytics_taxonomy_selection_id: str
    portfolio_id: str
    taxonomy_id: str | None = None
    effective_from: date
    effective_to: date | None = None
    selection_version: int = Field(ge=1)
    superseded_by_selection_id: str | None = None
    created_at: str


class PortfolioInstrumentUniverseRecord(BaseModel):
    portfolio_id: str
    instrument_id: str
    instrument_ref: InstrumentCoreContract | None = None
    source: str
    holding_state: str
    first_transaction_date: date | None = None
    last_transaction_date: date | None = None
    transaction_count: int = 0
    research_lifecycle: ResearchLifecycle
    research_eligibility: ResearchEligibility
    research_pm_approved: bool = False
    research_pm_approved_at: str | None = None
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


class ResearchInstrumentEligibilityUpdateRequest(BaseModel):
    pm_approved: bool


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
    analytics_scope_policy_version: int = 0
    analytics_scope_policies: list[AnalyticsScopePolicyRecord] = Field(
        default_factory=list
    )
    analytics_taxonomy_selections: list[AnalyticsTaxonomySelectionRecord] = Field(
        default_factory=list
    )
    instrument_universe: list[PortfolioInstrumentUniverseRecord] = Field(default_factory=list)
    target_sets: list[TargetSetRecord] = Field(default_factory=list)
    target_set_lines: list[TargetSetLineRecord] = Field(default_factory=list)
    target_set_integrity_issues: list[TargetSetIntegrityIssueRecord] = Field(default_factory=list)


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


class ResearchTopSleeveWeightBoundRecord(BaseModel):
    taxonomy_node_id: str = Field(min_length=1)
    min_weight: float | None = Field(default=None, ge=0, le=1)
    max_weight: float | None = Field(default=None, ge=0, le=1)

    @field_validator("taxonomy_node_id", mode="before")
    @classmethod
    def validate_taxonomy_node_id(cls, value: object) -> object:
        return _normalize_optional_text(value) or ""

    @model_validator(mode="after")
    def validate_bounds(self) -> "ResearchTopSleeveWeightBoundRecord":
        if self.min_weight is None and self.max_weight is None:
            raise ValueError("top sleeve bound must set min_weight or max_weight.")
        if self.min_weight is not None and self.max_weight is not None and self.min_weight > self.max_weight:
            raise ValueError("top sleeve min_weight cannot exceed max_weight.")
        return self


class ResearchBacktestRobustnessScenarioRecord(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    cash_yield_annual: float = Field(ge=-1, le=1)
    commission_bps: float = Field(ge=0, le=1000)
    tax_bps: float = Field(ge=0, le=1000)
    slippage_bps: float = Field(ge=0, le=1000)
    implementation_delay_days: int = Field(ge=0, le=30)

    @field_validator("scenario_id", "label", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)


class ResearchSettingsRecord(BaseModel):
    portfolio_id: str
    planning_taxonomy_id: str | None = None
    planning_taxonomy_name: str | None = None
    comparator_taxonomy_node_id: str | None = None
    comparator_taxonomy_node_name: str | None = None
    as_of_mode: ResearchAsOfMode = "dynamic"
    as_of_date: date | None = None
    pinned_as_of_date: date | None = None
    lookback_days: int = Field(default=90)
    calculation_frequency: ResearchCalculationFrequency = "auto"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    target_dimension: ResearchTargetDimension = "scope_default"
    capital_mode: ResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] = Field(default_factory=list)
    top_sleeve_weight_bounds: list[ResearchTopSleeveWeightBoundRecord] = Field(default_factory=list)
    backtest_rebalance_frequency: ResearchBacktestRebalanceFrequency = "1m"
    backtest_benchmark_instrument_id: str | None = None
    backtest_cash_yield_annual: float = Field(default=0.02, ge=-1, le=1)
    backtest_commission_bps: float = Field(default=2, ge=0, le=1000)
    backtest_tax_bps: float = Field(default=10, ge=0, le=1000)
    backtest_slippage_bps: float = Field(default=5, ge=0, le=1000)
    backtest_implementation_delay_days: int = Field(default=1, ge=0, le=30)
    backtest_robustness_scenarios: list[
        ResearchBacktestRobustnessScenarioRecord
    ] = Field(default_factory=list)
    backtest_walk_forward_training_months: int = Field(default=24, ge=1, le=120)
    backtest_walk_forward_test_months: int = Field(default=6, ge=1, le=60)
    notes: str | None = None
    updated_at: str | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class ResearchSettingsUpdateRequest(BaseModel):
    planning_taxonomy_id: str | None = None
    comparator_taxonomy_node_id: str | None = None
    as_of_mode: ResearchAsOfMode = "dynamic"
    as_of_date: date | None = None
    lookback_days: int = Field(default=90)
    calculation_frequency: ResearchCalculationFrequency = "auto"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    covariance_model_id: PortfolioRiskCovarianceModel = "ewma_vol_shrinkage_corr_covariance"
    contribution_mode: PortfolioRiskContributionMode = "signed"
    target_dimension: ResearchTargetDimension = "scope_default"
    capital_mode: ResearchCapitalMode = "unit_notional"
    gross_exposure: float | None = Field(default=None, gt=0)
    target_volatility: float | None = Field(default=None, gt=0, le=1)
    max_gross_exposure: float | None = Field(default=None, gt=0)
    frozen_taxonomy_node_ids: list[str] | None = None
    top_sleeve_weight_bounds: list[ResearchTopSleeveWeightBoundRecord] | None = None
    backtest_rebalance_frequency: ResearchBacktestRebalanceFrequency = "1m"
    backtest_benchmark_instrument_id: str | None = None
    backtest_cash_yield_annual: float = Field(default=0.02, ge=-1, le=1)
    backtest_commission_bps: float = Field(default=2, ge=0, le=1000)
    backtest_tax_bps: float = Field(default=10, ge=0, le=1000)
    backtest_slippage_bps: float = Field(default=5, ge=0, le=1000)
    backtest_implementation_delay_days: int = Field(default=1, ge=0, le=30)
    backtest_robustness_scenarios: list[
        ResearchBacktestRobustnessScenarioRecord
    ] | None = None
    backtest_walk_forward_training_months: int = Field(default=24, ge=1, le=120)
    backtest_walk_forward_test_months: int = Field(default=6, ge=1, le=60)
    notes: str | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)

    @field_validator(
        "planning_taxonomy_id",
        "comparator_taxonomy_node_id",
        "backtest_benchmark_instrument_id",
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
    def validate_research_settings(self) -> "ResearchSettingsUpdateRequest":
        scenario_ids = [
            scenario.scenario_id for scenario in self.backtest_robustness_scenarios or []
        ]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("backtest robustness scenario_id values must be unique.")
        if self.as_of_mode == "pinned" and self.as_of_date is None:
            raise ValueError("pinned research mode requires as_of_date.")
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
                raise ValueError("fixed_gross capital mode must not set target_volatility.")
            if self.max_gross_exposure is not None:
                raise ValueError("fixed_gross capital mode must not set max_gross_exposure.")
        elif self.capital_mode in {"target_volatility", "volatility_cap"}:
            if self.target_volatility is None:
                raise ValueError(f"{self.capital_mode} capital mode requires target_volatility.")
            if self.gross_exposure is not None:
                raise ValueError(f"{self.capital_mode} capital mode must not set gross_exposure.")
            if self.capital_mode == "volatility_cap" and self.max_gross_exposure is not None:
                raise ValueError("volatility_cap capital mode must not set max_gross_exposure.")
        if (
            self.max_gross_exposure is not None
            and self.gross_exposure is not None
            and self.max_gross_exposure + 1e-12 < self.gross_exposure
        ):
            raise ValueError("max_gross_exposure cannot be smaller than gross_exposure.")
        return self


class PortfolioRiskPolicyRecord(BaseModel):
    model_name: str = "Production Risk Model"
    model_role: str = "production"
    covariance_model_id: PortfolioRiskCovarianceModel = "ewma_vol_shrinkage_corr_covariance"
    lookback_days: int = Field(default=90)
    calculation_frequency: PortfolioRiskCalculationFrequency = "auto"
    resolved_calculation_frequency: PortfolioCalculationFrequency = "daily"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    contribution_mode: PortfolioRiskContributionMode = "signed"
    parameters: dict[str, object] = Field(default_factory=dict)
    parameters_by_frequency: dict[str, dict[str, object]] = Field(default_factory=dict)

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class PortfolioRiskPolicyUpdateRequest(BaseModel):
    covariance_model_id: PortfolioRiskCovarianceModel = "ewma_vol_shrinkage_corr_covariance"
    lookback_days: int = Field(default=90)
    calculation_frequency: PortfolioRiskCalculationFrequency = "auto"
    missing_return_policy: ResearchMissingReturnPolicy = "strict"
    contribution_mode: PortfolioRiskContributionMode = "signed"

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


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
    quality_warnings: list[str] = Field(default_factory=list)
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


class ResearchSolvedResultRowRecord(BaseModel):
    member_type: str
    member_id: str
    label: str
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    current_weight: float | None = None
    solved_weight: float | None = None
    current_value_base: float | None = None
    target_value_base: float | None = None
    target_risk_share: float | None = None
    forward_risk_contribution: float | None = None


class ResearchSolvedResultGroupRecord(BaseModel):
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    current_weight: float | None = None
    solved_weight: float | None = None
    current_value_base: float | None = None
    target_value_base: float | None = None
    target_risk_share: float | None = None
    forward_risk_contribution: float | None = None
    min_weight: float | None = None
    max_weight: float | None = None
    bound_status: str | None = None
    rows: list[ResearchSolvedResultRowRecord] = Field(default_factory=list)


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
    target_status: str | None = None
    execution_ready: bool | None = None
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
    target_value_base: float | None = None
    base_currency: str
    action: str
    research_lifecycle: ResearchLifecycle | None = None
    research_eligibility: ResearchEligibility | None = None
    research_pm_approved: bool | None = None
    execution_status: ResearchExecutionStatus = "ready"
    execution_note: str | None = None


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
    research_lifecycle: ResearchLifecycle | None = None
    research_eligibility: ResearchEligibility | None = None
    research_pm_approved: bool | None = None
    execution_status: ResearchExecutionStatus = "ready"
    execution_note: str | None = None


class ResearchBacktestPointRecord(BaseModel):
    date: str
    value: float | None = None


class ResearchBacktestSleeveValueRecord(BaseModel):
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    value: float | None = None


class ResearchBacktestSleevePointRecord(BaseModel):
    date: str
    sleeves: list[ResearchBacktestSleeveValueRecord] = Field(default_factory=list)


class ResearchBacktestTargetWeightRecord(BaseModel):
    instrument_id: str
    target_weight: float
    top_sleeve_id: str | None = None
    top_sleeve_label: str
    top_sleeve_path: str | None = None
    first_usable_observation_date: str | None = None


class ResearchBacktestExecutionRecord(BaseModel):
    decision_date: str
    scheduled_execution_date: str
    actual_execution_date: str
    taxonomy_configuration_version: int | None = None
    taxonomy_configuration_effective_from: str | None = None
    target_weights: list[ResearchBacktestTargetWeightRecord] = Field(default_factory=list)
    cash_target_weight: float
    risky_buy_turnover: float
    risky_sell_turnover: float
    cash_leg_turnover: float
    one_way_turnover: float
    commission_cost: float
    tax_cost: float
    slippage_cost: float
    total_cost: float
    nav_before_execution: float
    nav_after_execution: float


class ResearchBacktestContributionReconciliationRecord(BaseModel):
    date: str
    nav_change: float
    linked_contribution: float
    residual: float
    execution_cost_contribution: float


class ResearchBacktestMethodologyRecord(BaseModel):
    name: str
    point_in_time_universe: bool
    point_in_time_taxonomy: bool
    decision_rule: str
    execution_rule: str
    cash_return_rule: str
    cost_rule: str
    contribution_linking: str
    assumptions: dict[str, float | int] = Field(default_factory=dict)


class ResearchBacktestSkippedRebalanceRecord(BaseModel):
    date: str
    reason: str


class ResearchBacktestPointInTimeCoverageRecord(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    decision_count: int = 0
    first_decision_date: str | None = None
    last_decision_date: str | None = None
    configuration_versions_used: list[int] = Field(default_factory=list)
    historical_instrument_count: int = 0
    first_usable_observation_by_instrument: dict[str, str] = Field(default_factory=dict)
    skipped_rebalances: list[ResearchBacktestSkippedRebalanceRecord] = Field(default_factory=list)
    unavailable_reason: str | None = None


class ResearchBacktestMetricsRecord(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    period_return: float | None = None
    ytd_return: float | None = None
    annualization_eligible: bool = False
    annualization_years: float | None = None
    annualization_unavailable_reason: str | None = None
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


class ResearchBacktestRobustnessResultRecord(BaseModel):
    scenario_id: str
    label: str
    cash_yield_annual: float
    commission_bps: float
    tax_bps: float
    slippage_bps: float
    implementation_delay_days: int
    metrics: ResearchBacktestMetricsRecord | None = None
    period_return_delta: float | None = None
    ending_value: float | None = None
    total_turnover: float | None = None
    total_cost: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ResearchBacktestWalkForwardWindowRecord(BaseModel):
    training_start_date: str
    training_end_date: str
    test_start_date: str
    test_end_date: str
    configuration_versions_used: list[int] = Field(default_factory=list)
    points: list[ResearchBacktestPointRecord] = Field(default_factory=list)
    metrics: ResearchBacktestMetricsRecord | None = None
    available: bool
    unavailable_reason: str | None = None


class ResearchBacktestWalkForwardRecord(BaseModel):
    validation_method: Literal["rolling_temporal_holdout"] = "rolling_temporal_holdout"
    parameter_selection: Literal["fixed_point_in_time_policy"] = "fixed_point_in_time_policy"
    parameter_optimization: bool = False
    methodology_note: str | None = None
    available: bool
    unavailable_reason: str | None = None
    training_months: int
    test_months: int
    windows: list[ResearchBacktestWalkForwardWindowRecord] = Field(default_factory=list)
    oos_points: list[ResearchBacktestPointRecord] = Field(default_factory=list)
    oos_metrics: ResearchBacktestMetricsRecord | None = None


class ResearchBacktestRecord(BaseModel):
    rebalance_frequency: ResearchBacktestRebalanceFrequency = "1m"
    common_history_start_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    lookback_days: int = 90
    points: list[ResearchBacktestPointRecord] = Field(default_factory=list)
    metrics: ResearchBacktestMetricsRecord | None = None
    top_sleeve_weight_points: list[ResearchBacktestSleevePointRecord] = Field(default_factory=list)
    top_sleeve_contribution_points: list[ResearchBacktestSleevePointRecord] = Field(default_factory=list)
    contribution_reconciliation_points: list[
        ResearchBacktestContributionReconciliationRecord
    ] = Field(default_factory=list)
    execution_records: list[ResearchBacktestExecutionRecord] = Field(default_factory=list)
    total_turnover: float = 0.0
    total_cost: float = 0.0
    methodology: ResearchBacktestMethodologyRecord | None = None
    point_in_time_coverage: ResearchBacktestPointInTimeCoverageRecord | None = None
    robustness_results: list[ResearchBacktestRobustnessResultRecord] = Field(
        default_factory=list
    )
    walk_forward: ResearchBacktestWalkForwardRecord | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


class ResearchBacktestBenchmarkRecord(BaseModel):
    instrument_id: str | None = None
    label: str | None = None
    points: list[ResearchBacktestPointRecord] = Field(default_factory=list)
    metrics: ResearchBacktestMetricsRecord | None = None
    warnings: list[str] = Field(default_factory=list)


class ResearchBacktestRelativeMetricsRecord(ResearchBacktestMetricsRecord):
    excess_return: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None


class ResearchBacktestBenchmarkComparisonResponse(BaseModel):
    backtest_benchmark: ResearchBacktestBenchmarkRecord | None = None
    backtest_relative_metrics: ResearchBacktestRelativeMetricsRecord | None = None


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
    solved_result_groups: list[ResearchSolvedResultGroupRecord] = Field(default_factory=list)
    solve_event: ResearchSolveEventRecord | None = None
    scope_solve_events: list[ResearchSolveEventRecord] = Field(default_factory=list)
    target_weight_gaps: list[ResearchTargetWeightGapRecord] = Field(default_factory=list)
    backtest: ResearchBacktestRecord | None = None
    backtest_benchmark: ResearchBacktestBenchmarkRecord | None = None
    backtest_relative_metrics: ResearchBacktestRelativeMetricsRecord | None = None
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
    reliability_state: ResearchRunReliabilityState = "unassessed"
    is_current: bool = False
    reliability_reasons: list[str] = Field(default_factory=list)
    artifact_count: int = 0
    artifacts: list[ResearchArtifactRecord] = Field(default_factory=list)
    detail: ResearchRunDetailRecord | None = None

    @field_validator("lookback_days")
    @classmethod
    def validate_lookback_days(cls, value: int) -> int:
        return _validate_risk_window_days(value)


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
    risk_policy: PortfolioRiskPolicyRecord
    current_context: ResearchCurrentContextRecord
    instrument_universe: list[PortfolioInstrumentUniverseRecord] = Field(default_factory=list)
    detail_level: Literal["compact", "selected_run"] = "compact"
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
    effective_from: date

    @field_validator("taxonomy_id", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        return _normalize_optional_text(value)


class DefaultPlanningTaxonomyResponse(BaseModel):
    portfolio_id: str
    default_planning_taxonomy_id: str | None = None


class TaxonomyCreateRequest(BaseModel):
    effective_from: date
    name: str = Field(min_length=1)
    taxonomy_type: str = "custom"
    purpose: str | None = None
    primary_assignment_scope: TaxonomyAssignmentScope = "instrument"
    planning_enabled: bool = False
    budgeting_level: str | None = None
    root_default_target_dimension: DefaultTargetDimension = "weight"
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
        if self.budgeting_level and not self.planning_enabled:
            raise ValueError("budgeting_level requires planning_enabled.")
        if self.planning_enabled and self.primary_assignment_scope != "instrument":
            raise ValueError("planning_enabled taxonomies must use instrument assignment scope.")
        return self


class TaxonomyNodeCreateRequest(BaseModel):
    effective_from: date
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
    effective_from: date
    name: str | None = None
    taxonomy_type: str | None = None
    purpose: str | None = None
    planning_enabled: bool | None = None
    budgeting_level: str | None = None
    root_default_target_dimension: DefaultTargetDimension | None = None
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
        if self.budgeting_level and self.planning_enabled is False:
            raise ValueError("budgeting_level requires planning_enabled.")
        return self


class TaxonomyNodeUpdateRequest(BaseModel):
    effective_from: date
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
    effective_from: date
    target_scope: TaxonomyAssignmentScope
    target_entity_id: str = Field(min_length=1)
    taxonomy_node_id: str = Field(min_length=1)
    status: str = "active"

    @field_validator("target_entity_id", "taxonomy_node_id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _normalize_required_text(value)


class TaxonomyAssignmentUpdateRequest(BaseModel):
    effective_from: date
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
    effective_from: date
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
    effective_from: date
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
    "pending_settlement_currency_gains",
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
    pending_settlement_currency_gains: float | None = None
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
    pending_settlement_currency_gains: float | None = None
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
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    as_of_clamp_reason: str | None = None
    start_boundary_kind: PerformanceStartBoundaryKind | None = None
    include_start_date_return: bool = False
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
    pending_settlement_currency_gains: float | None = None
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
    "pending_settlement_currency_gains",
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
    period_return_coverage_state: CoverageState = "unavailable"
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
    pending_settlement_currency_gains: float | None = None
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
    period_return_coverage_state: CoverageState = "unavailable"
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
    pending_settlement_currency_gains: float | None = None
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
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    as_of_clamp_reason: str | None = None
    start_boundary_kind: PerformanceStartBoundaryKind | None = None
    include_start_date_return: bool = False
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
    risk_basis_coverage_state: CoverageState = "unavailable"
    risk_basis_requested_instrument_count: int = 0
    risk_basis_resolved_instrument_count: int = 0
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
    pending_settlement_currency_gains: float | None = None
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


class AccountWorkspaceAccount(BaseModel):
    account: AccountRecord
    default_settlement_cash_account_name: str | None = None
    linked_transaction_count: int
    linked_posting_count: int
    derived_cash_balance: float
    derived_cash_balance_base: float | None = None
    pending_settlement: float = 0.0
    pending_settlement_base: float | None = None
    derivative_liability: float = 0.0
    derivative_liability_base: float | None = None
    open_option_obligation_count: int = 0
    account_value_base: float | None = None
    valuation_coverage_state: CoverageState = "complete"
    valuation_missing_components: list[str] = Field(default_factory=list)
    position_line_count: int
    position_market_value: float | None = None
    position_market_value_currency: SupportedCurrency | None = None


class AccountsWorkspaceSummary(BaseModel):
    account_count: int
    deposit_account_count: int
    securities_account_count: int
    ledger_posting_count: int
    position_line_count: int
    valuation_coverage_state: CoverageState = "complete"
    valued_account_count: int = 0
    unvalued_account_count: int = 0
    open_option_obligation_count: int = 0
    derivative_liability_base: float | None = None


class AccountsWorkspaceResponse(BaseModel):
    portfolio_id: str
    base_currency: SupportedCurrency
    summary: AccountsWorkspaceSummary
    derivation_boundary: DerivationBoundaryStatus
    selected_account_id: str | None = None
    accounts: list[AccountWorkspaceAccount]
    ledger_postings: list[LedgerPostingRecord]
    positions: list[AccountPositionRecord]
    option_obligations: list[dict[str, object]] = Field(default_factory=list)
    linked_transactions_summary: TransactionListSummary | None = None
    linked_transactions: list[TransactionRecord] = Field(default_factory=list)


class LedgerPostingListSummary(BaseModel):
    posting_count: int
    cash_posting_count: int
    position_posting_count: int
    pending_posting_count: int = 0
    liability_posting_count: int = 0
    option_realized_pnl_posting_count: int = 0


class LedgerPostingListResponse(BaseModel):
    portfolio_id: str
    summary: LedgerPostingListSummary
    ledger_postings: list[LedgerPostingRecord]


class TransactionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionCommandType
    lifecycle_event_type: LifecycleEventType | None = None
    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    position_effective_date: date | None = None
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    account_id: str
    settlement_cash_account_id: str | None = None
    instrument_id: str | None = None
    derivative_contract_id: str | None = None
    derivative_contract: DerivativeContractCreate | None = None
    quantity: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e16"))
    price: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e16"))
    gross_amount: Decimal = Field(ge=0, lt=Decimal("1e20"))
    counter_amount: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e20"))
    fx_rate: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e16"))
    fees: Decimal = Field(default=Decimal("0"), ge=0, lt=Decimal("1e20"))
    fee_category: FeeCategory = "unknown"
    taxes: Decimal = Field(default=Decimal("0"), ge=0, lt=Decimal("1e20"))
    currency: str = Field(min_length=1, max_length=8)
    counterparty_account_id: str | None = None
    source_system: str | None = Field(default=None, max_length=100)
    external_reference: str | None = Field(default=None, max_length=200)
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

    @field_validator(
        "source_system",
        "external_reference",
        mode="before",
    )
    @classmethod
    def normalize_source_identity(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def normalize_quantity_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=QUANTITY_SOURCE_QUANTUM)

    @field_validator("price", mode="before")
    @classmethod
    def normalize_price_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=PRICE_SOURCE_QUANTUM)

    @field_validator("gross_amount", "counter_amount", "fees", "taxes", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=AMOUNT_SOURCE_QUANTUM)

    @field_validator("fx_rate", mode="before")
    @classmethod
    def normalize_fx_rate_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=PRICE_SOURCE_QUANTUM)

    @model_validator(mode="after")
    def validate_amount_contract(self) -> "TransactionCreateRequest":
        if self.instrument_id and self.derivative_contract_id:
            raise ValueError(
                "A transaction may reference an instrument or a derivative contract, not both."
            )
        if self.derivative_contract is not None:
            if not self.derivative_contract_id:
                raise ValueError(
                    "Inline derivative contract creation requires derivative_contract_id."
                )
            if (
                self.derivative_contract.derivative_contract_id
                != self.derivative_contract_id
            ):
                raise ValueError(
                    "derivative_contract_id must match the inline derivative contract."
                )
        has_asset_reference = bool(self.instrument_id or self.derivative_contract_id)
        inline_contract_type = (
            self.derivative_contract.contract_type
            if self.derivative_contract is not None
            else None
        )
        settlement_date = self.settlement_date or self.trade_date
        if settlement_date < self.trade_date:
            raise ValueError("settlement_date must not be earlier than trade_date.")
        if self.position_effective_date is not None:
            if self.transaction_type not in POSITION_EFFECTIVE_COMMAND_TYPES:
                raise ValueError(
                    "position_effective_date is only allowed for position-changing "
                    "security transactions."
                )
            if self.position_effective_date < self.trade_date:
                raise ValueError(
                    "position_effective_date must not be earlier than trade_date."
                )
        if self.entitlement_date is not None and self.entitlement_date > self.trade_date:
            raise ValueError("entitlement_date must not be later than trade_date.")
        if self.acquisition_date is not None and self.acquisition_date > self.trade_date:
            raise ValueError("acquisition_date must not be later than trade_date.")

        if self.external_reference is not None and self.source_system is None:
            raise ValueError("external_reference requires source_system.")
        lifecycle_transaction_types: dict[str, set[str]] = {
            "fcn_knock_in": {"maturity_redemption"},
            "fcn_knock_out": {"maturity_redemption"},
            "fcn_maturity": {"maturity_redemption"},
            "option_long_expiry": {"maturity_redemption"},
            "option_long_exercise": {"maturity_redemption"},
            "option_writer_expiry": {"lifecycle_event"},
            "option_assignment": {"lifecycle_event"},
        }
        if self.lifecycle_event_type is not None:
            allowed_transaction_types = lifecycle_transaction_types[
                self.lifecycle_event_type
            ]
            if self.transaction_type not in allowed_transaction_types:
                raise ValueError(
                    f"{self.lifecycle_event_type} requires transaction_type "
                    + " or ".join(sorted(allowed_transaction_types))
                    + "."
                )
        elif self.transaction_type == "lifecycle_event":
            raise ValueError("lifecycle_event requires lifecycle_event_type.")

        if self.transaction_type == "lifecycle_event":
            if not self.derivative_contract_id:
                raise ValueError("Lifecycle events require derivative_contract_id.")
            if inline_contract_type is not None and inline_contract_type != "option":
                raise ValueError("Non-economic lifecycle events require an option contract.")
            writer_close_event = self.lifecycle_event_type in {
                "option_writer_expiry",
                "option_assignment",
            }
            if writer_close_event:
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError(
                        "Short option expiry and assignment require positive contract quantity."
                    )
            elif self.quantity is not None:
                raise ValueError("This lifecycle event must not carry quantity.")
            if self.price is not None:
                raise ValueError("Lifecycle events must not carry price.")
            if self.gross_amount != 0 or self.fees != 0 or self.taxes != 0:
                raise ValueError("Lifecycle events must not carry cash amounts.")
            if self.settlement_cash_account_id is not None:
                raise ValueError(
                    "Non-economic lifecycle events must not carry settlement_cash_account_id."
                )

        if self.transaction_type in {"buy", "sell"}:
            if not has_asset_reference:
                raise ValueError(
                    "Security transactions require an instrument or derivative contract."
                )
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Security transactions require positive quantity.")
            if self.price is None or self.price <= 0:
                raise ValueError("Security transactions require positive price.")

        if self.transaction_type in {"option_write", "option_buy_to_close"}:
            if not self.derivative_contract_id:
                raise ValueError(
                    "Short option transactions require derivative_contract_id."
                )
            if inline_contract_type is not None and inline_contract_type != "option":
                raise ValueError("Short option transactions require an option contract.")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError(
                    "Short option transactions require positive contract quantity."
                )
            if self.gross_amount <= 0:
                raise ValueError("Short option transactions require positive gross_amount.")
            if self.price is None or self.price <= 0:
                raise ValueError("Short option transactions require positive premium price.")

        if self.transaction_type in {"dividend", "coupon"}:
            if self.transaction_type == "dividend" and not self.instrument_id:
                raise ValueError("Dividend requires instrument_id.")
            if self.transaction_type == "coupon" and not has_asset_reference:
                raise ValueError("Coupon requires an instrument or FCN contract.")
            if (
                self.transaction_type == "coupon"
                and inline_contract_type is not None
                and inline_contract_type != "fcn"
            ):
                raise ValueError("Derivative coupon transactions require an FCN contract.")
            if self.quantity is not None or self.price is not None:
                raise ValueError("Dividend and coupon must not carry quantity or price.")

        if self.transaction_type == "interest":
            if has_asset_reference:
                raise ValueError("Interest must not carry an asset reference.")
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
            if self.settlement_cash_account_id is not None:
                raise ValueError("Dividend reinvestment must not carry settlement_cash_account_id.")
            if self.price is not None:
                if self.price <= 0:
                    raise ValueError("Dividend reinvestment price must be positive when provided.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Dividend reinvestment must not carry fees or taxes.")

        if self.transaction_type == "maturity_redemption":
            if not has_asset_reference:
                raise ValueError(
                    "Maturity redemption requires an instrument or derivative contract."
                )
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("Maturity redemption requires positive quantity.")
            if self.price is not None:
                raise ValueError("Maturity redemption must not carry price.")
            if self.lifecycle_event_type in {
                "option_long_expiry",
                "option_long_exercise",
            } and self.gross_amount != 0:
                raise ValueError(
                    "Long option closure must have zero gross_amount."
                )

        if self.transaction_type in {"deposit", "withdrawal"}:
            if has_asset_reference:
                raise ValueError("Cash-flow transactions must not carry an asset reference.")
            if self.quantity is not None or self.price is not None:
                raise ValueError("Cash-flow transactions must not carry quantity or price.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Cash-flow transactions must not carry settlement_cash_account_id.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Cash-flow transactions must not carry fees or taxes.")

        if self.transaction_type == "fx_conversion":
            if has_asset_reference:
                raise ValueError("FX conversion must not carry an asset reference.")
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
            if self.entitlement_date is not None and not has_asset_reference:
                raise ValueError(
                    "entitlement_date on fee and tax requires an asset reference."
                )

        if (
            self.fee_category != "unknown"
            and self.transaction_type != "fee"
            and self.fees <= 0
        ):
            raise ValueError(
                "fee_category requires a fee transaction or a positive attached fee."
            )

        if (
            self.entitlement_date is not None
            and self.transaction_type
            not in {"dividend", "dividend_reinvestment", "coupon", "fee", "tax"}
        ):
            raise ValueError(
                "entitlement_date is only allowed for dividend, dividend reinvestment, coupon, fee, and tax."
            )

        if self.transaction_type == "opening_balance":
            if has_asset_reference:
                if self.acquisition_date is None:
                    self.acquisition_date = self.trade_date
            elif self.acquisition_date is not None:
                raise ValueError("Cash opening balance must not carry acquisition_date.")
            if self.settlement_cash_account_id is not None:
                raise ValueError("Opening balance must not carry settlement_cash_account_id.")
            if self.fees != 0 or self.taxes != 0:
                raise ValueError("Opening balance must not carry fees or taxes.")
            if has_asset_reference:
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError("Security opening balance requires positive quantity.")
                if self.price is not None:
                    if self.price <= 0:
                        raise ValueError("Security opening balance price must be positive when provided.")
            elif self.quantity is not None or self.price is not None:
                raise ValueError("Cash opening balance must not carry quantity or price.")
        elif self.acquisition_date is not None:
            raise ValueError("acquisition_date is only allowed for security opening balance.")

        return self


class TransactionUpdateRequest(TransactionCreateRequest):
    expected_row_version: int = Field(ge=1)


class InternalTransferCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: date
    trade_time: str | None = None
    settlement_date: date | None = None
    transfer_object_type: TransferObjectType
    from_account_id: str
    to_account_id: str
    instrument_id: str | None = None
    quantity: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e16"))
    gross_amount: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e20"))
    note: str | None = None

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
        return _quantize_numeric_input(value, quantum=QUANTITY_SOURCE_QUANTUM)

    @field_validator("gross_amount", mode="before")
    @classmethod
    def normalize_amount_precision(cls, value: object) -> object:
        return _quantize_numeric_input(value, quantum=AMOUNT_SOURCE_QUANTUM)


class TransactionBatchResponse(BaseModel):
    portfolio_id: str
    created_count: int
    transfer_group_id: str | None = None
    transactions: list[TransactionRecord]


class TransactionCsvPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(min_length=1)
    default_source_system: str | None = Field(default=None, max_length=100)

    @field_validator("default_source_system", mode="before")
    @classmethod
    def normalize_default_source_system(cls, value: object) -> object:
        return _normalize_optional_text(value)


class TransactionCsvImportRequest(TransactionCsvPreviewRequest):
    preview_digest: str = Field(min_length=64, max_length=64)


class TransactionCsvPreviewRow(BaseModel):
    row_number: int = Field(ge=2)
    transaction: TransactionCreateRequest | None = None
    errors: list[str] = Field(default_factory=list)


class TransactionCsvPreviewResponse(BaseModel):
    portfolio_id: str
    preview_digest: str
    headers: list[str]
    row_count: int
    valid_count: int
    error_count: int
    warnings: list[str] = Field(default_factory=list)
    batch_errors: list[str] = Field(default_factory=list)
    rows: list[TransactionCsvPreviewRow]


class TransactionCsvImportResponse(BaseModel):
    portfolio_id: str
    preview_digest: str
    created_count: int
    transactions: list[TransactionRecord]


class TransactionDeleteRequest(BaseModel):
    expected_row_versions: dict[str, int] = Field(min_length=1)

    @field_validator("expected_row_versions", mode="before")
    @classmethod
    def validate_expected_row_versions(cls, value: object) -> object:
        if not isinstance(value, dict) or not value:
            raise ValueError("expected_row_versions must be a non-empty object.")
        for transaction_id, row_version in value.items():
            if not isinstance(transaction_id, str) or not transaction_id.strip():
                raise ValueError("expected_row_versions keys must be transaction IDs.")
            if transaction_id != transaction_id.strip():
                raise ValueError("expected_row_versions keys must not contain surrounding whitespace.")
            if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
                raise ValueError("expected_row_versions values must be positive integers.")
        return value


class TransactionDeleteResponse(BaseModel):
    portfolio_id: str
    deleted_count: int
    deleted_transaction_ids: list[str]
    transfer_group_id: str | None = None
