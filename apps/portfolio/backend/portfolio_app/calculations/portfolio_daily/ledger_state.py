"""Exact immutable state and evidence records emitted by ledger replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    exact_decimal_negate,
    exact_decimal_subtract,
    exact_decimal_sum,
    require_decimal,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    ExternalFlowTiming,
    FactLineage,
    FxRateConvention,
    LedgerContractError,
    LedgerReasonCode,
    require_currency,
    require_date,
    require_enum,
    require_sequence,
    require_text,
)


class LedgerStatus(StrEnum):
    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class BaseCoverageStatus(StrEnum):
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


class PriceEvidenceStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class PendingComponentKind(StrEnum):
    TRADE_SETTLEMENT = "trade_settlement"
    INCOME_ACCRUAL = "income_accrual"
    FEE_ACCRUAL = "fee_accrual"
    TAX_ACCRUAL = "tax_accrual"
    RETURN_OF_CAPITAL_ACCRUAL = "return_of_capital_accrual"
    MATURITY_ACCRUAL = "maturity_accrual"
    FX_CONVERSION_SOURCE_LEG = "fx_conversion_source_leg"
    FX_CONVERSION_TARGET_LEG = "fx_conversion_target_leg"


class LedgerEffectKind(StrEnum):
    OPENING_CASH = "opening_cash"
    OPENING_POSITION = "opening_position"
    BUY_TRADE = "buy_trade"
    SELL_TRADE = "sell_trade"
    CASH_SETTLEMENT = "cash_settlement"
    EXTERNAL_FLOW = "external_flow"
    INCOME_RECOGNITION = "income_recognition"
    RETURN_OF_CAPITAL = "return_of_capital"
    MATURITY_REDEMPTION = "maturity_redemption"
    CASH_TRANSFER = "cash_transfer"
    POSITION_TRANSFER = "position_transfer"
    SPLIT = "split"
    FX_CONVERSION = "fx_conversion"
    DIVIDEND_REINVESTMENT = "dividend_reinvestment"
    EXPENSE_RECOGNITION = "expense_recognition"


def _exact(value: object, *, field_name: str) -> Decimal:
    try:
        return require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise LedgerContractError(str(exc)) from exc


def _optional_exact(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _exact(value, field_name=field_name)


def _canonical_lineages(
    values: tuple[FactLineage, ...],
) -> tuple[FactLineage, ...]:
    return tuple(sorted(set(values), key=lambda item: item.manifest_fact_key))


@dataclass(frozen=True, slots=True)
class PositionLot:
    lot_id: str
    opening_sequence: int
    acquisition_date: date
    quantity: Decimal
    local_cost: Decimal
    historical_base_cost: Decimal | None
    lineage: FactLineage
    custody_lineage: FactLineage
    acquisition_fx_lineage: FactLineage | None
    cost_source_lineages: tuple[FactLineage, ...]
    cost_fx_lineages: tuple[FactLineage, ...]

    def __post_init__(self) -> None:
        require_text(self.lot_id, field_name="lot_id")
        require_sequence(self.opening_sequence)
        require_date(self.acquisition_date, field_name="acquisition_date")
        quantity = _exact(self.quantity, field_name="quantity")
        local = _exact(self.local_cost, field_name="local_cost")
        base = _optional_exact(
            self.historical_base_cost,
            field_name="historical_base_cost",
        )
        if quantity <= 0 or local < 0 or (base is not None and base < 0):
            raise LedgerContractError(
                "lot quantity must be positive and costs non-negative"
            )
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")
        if not isinstance(self.custody_lineage, FactLineage):
            raise LedgerContractError("custody_lineage must be a FactLineage")
        if self.historical_base_cost is None:
            if self.acquisition_fx_lineage is not None:
                raise LedgerContractError(
                    "acquisition FX lineage must be null when base cost is unavailable"
                )
        elif self.acquisition_fx_lineage is not None and not isinstance(
            self.acquisition_fx_lineage,
            FactLineage,
        ):
            raise LedgerContractError(
                "acquisition_fx_lineage must be a FactLineage or null"
            )
        for field_name in ("cost_source_lineages", "cost_fx_lineages"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(
                not isinstance(item, FactLineage) for item in values
            ):
                raise LedgerContractError(
                    f"{field_name} must be an immutable FactLineage tuple"
                )
            if _canonical_lineages(values) != values:
                raise LedgerContractError(
                    f"{field_name} must be unique and canonically ordered"
                )
        if not self.cost_source_lineages:
            raise LedgerContractError("lot cost requires source lineage")
        if self.historical_base_cost is None and self.cost_fx_lineages:
            raise LedgerContractError(
                "unavailable historical base cost cannot retain FX cost lineage"
            )


def canonical_lots(lots: tuple[PositionLot, ...]) -> tuple[PositionLot, ...]:
    return tuple(
        sorted(
            lots,
            key=lambda lot: (
                lot.acquisition_date,
                lot.opening_sequence,
                lot.lot_id,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class PositionState:
    account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    cost_basis_method: CostBasisMethod
    quantity: Decimal
    local_cost: Decimal
    historical_base_cost: Decimal | None
    lots: tuple[PositionLot, ...]

    def __post_init__(self) -> None:
        require_text(self.account_id, field_name="account_id")
        require_text(self.instrument_id, field_name="instrument_id")
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_enum(
            self.cost_basis_method,
            CostBasisMethod,
            field_name="cost_basis_method",
        )
        quantity = _exact(self.quantity, field_name="quantity")
        local = _exact(self.local_cost, field_name="local_cost")
        base = _optional_exact(
            self.historical_base_cost,
            field_name="historical_base_cost",
        )
        if quantity <= 0 or local < 0 or (base is not None and base < 0):
            raise LedgerContractError(
                "position quantity must be positive and costs non-negative"
            )
        if not isinstance(self.lots, tuple) or not self.lots:
            raise LedgerContractError("positions require an immutable non-empty lot set")
        if any(not isinstance(lot, PositionLot) for lot in self.lots):
            raise LedgerContractError("lots must contain PositionLot values")
        if self.currency != self.base_currency and any(
            lot.historical_base_cost is not None
            and lot.acquisition_fx_lineage is None
            for lot in self.lots
        ):
            raise LedgerContractError(
                "non-base historical lot cost requires acquisition FX lineage"
            )
        if canonical_lots(self.lots) != self.lots:
            raise LedgerContractError("lots must be canonically ordered")
        if len({lot.lot_id for lot in self.lots}) != len(self.lots):
            raise LedgerContractError("lot ids must be unique within a position")
        lot_quantity = exact_decimal_sum(
            tuple(lot.quantity for lot in self.lots)
        )
        lot_local = exact_decimal_sum(
            tuple(lot.local_cost for lot in self.lots)
        )
        if lot_quantity != quantity or lot_local != local:
            raise LedgerContractError(
                "lot quantity and local cost must close exactly to position"
            )
        base_costs = tuple(lot.historical_base_cost for lot in self.lots)
        if any(value is None for value in base_costs):
            if base is not None:
                raise LedgerContractError(
                    "position historical base cost must be unavailable when a lot is unavailable"
                )
        else:
            lot_base = exact_decimal_sum(
                tuple(value for value in base_costs if value is not None)
            )
            if base != lot_base:
                raise LedgerContractError(
                    "lot historical base cost must close exactly to position"
                )
        if self.currency == self.base_currency and base != local:
            raise LedgerContractError(
                "base-currency position costs must be identical"
            )


@dataclass(frozen=True, slots=True)
class CashBalance:
    account_id: str
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        require_text(self.account_id, field_name="account_id")
        require_currency(self.currency)
        _exact(self.amount, field_name="amount")


@dataclass(frozen=True, slots=True)
class PendingSettlement:
    settlement_id: str
    component_kind: PendingComponentKind
    recognition_date: date
    settlement_date: date
    account_id: str
    currency: str
    base_currency: str
    local_amount: Decimal
    recognition_base_amount: Decimal | None
    instrument_id: str | None
    lineage: FactLineage
    event_id: str

    def __post_init__(self) -> None:
        require_text(self.settlement_id, field_name="settlement_id")
        require_enum(
            self.component_kind,
            PendingComponentKind,
            field_name="component_kind",
        )
        recognition = require_date(
            self.recognition_date,
            field_name="recognition_date",
        )
        settlement = require_date(
            self.settlement_date,
            field_name="settlement_date",
        )
        if settlement < recognition:
            raise LedgerContractError(
                "pending settlement date must not precede recognition date"
            )
        require_text(self.account_id, field_name="account_id")
        currency = require_currency(self.currency)
        base = require_currency(self.base_currency, field_name="base_currency")
        local = _exact(self.local_amount, field_name="local_amount")
        base_amount = _optional_exact(
            self.recognition_base_amount,
            field_name="recognition_base_amount",
        )
        if local == 0:
            raise LedgerContractError("pending local_amount must be non-zero")
        if currency == base and base_amount != local:
            raise LedgerContractError(
                "base-currency pending amount must equal local amount"
            )
        if self.instrument_id is not None:
            require_text(self.instrument_id, field_name="instrument_id")
        if self.component_kind in {
            PendingComponentKind.TRADE_SETTLEMENT,
            PendingComponentKind.RETURN_OF_CAPITAL_ACCRUAL,
            PendingComponentKind.MATURITY_ACCRUAL,
        } and self.instrument_id is None:
            raise LedgerContractError(
                f"{self.component_kind.value} requires instrument_id"
            )
        if self.component_kind in {
            PendingComponentKind.FX_CONVERSION_SOURCE_LEG,
            PendingComponentKind.FX_CONVERSION_TARGET_LEG,
        } and self.instrument_id is not None:
            raise LedgerContractError("FX conversion pending legs cannot carry instrument_id")
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")
        require_text(self.event_id, field_name="event_id")


@dataclass(frozen=True, slots=True)
class LotDisposition:
    disposition_id: str
    event_id: str
    disposition_date: date
    account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    lot_id: str
    quantity: Decimal
    released_local_cost: Decimal
    released_historical_base_cost: Decimal | None
    allocated_local_net_proceeds: Decimal
    allocated_base_net_proceeds: Decimal | None
    realized_pnl_local: Decimal
    realized_pnl_base: Decimal | None
    source_lineage: FactLineage
    source_custody_lineage: FactLineage
    source_acquisition_fx_lineage: FactLineage | None
    source_cost_lineages: tuple[FactLineage, ...]
    source_cost_fx_lineages: tuple[FactLineage, ...]
    disposition_lineage: FactLineage
    disposition_fx_lineage: FactLineage | None

    def __post_init__(self) -> None:
        for field_name in (
            "disposition_id",
            "event_id",
            "account_id",
            "instrument_id",
            "lot_id",
        ):
            require_text(getattr(self, field_name), field_name=field_name)
        require_date(self.disposition_date, field_name="disposition_date")
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        quantity = _exact(self.quantity, field_name="quantity")
        local_cost = _exact(
            self.released_local_cost,
            field_name="released_local_cost",
        )
        base_cost = _optional_exact(
            self.released_historical_base_cost,
            field_name="released_historical_base_cost",
        )
        local_proceeds = _exact(
            self.allocated_local_net_proceeds,
            field_name="allocated_local_net_proceeds",
        )
        base_proceeds = _optional_exact(
            self.allocated_base_net_proceeds,
            field_name="allocated_base_net_proceeds",
        )
        local_realized = _exact(
            self.realized_pnl_local,
            field_name="realized_pnl_local",
        )
        base_realized = _optional_exact(
            self.realized_pnl_base,
            field_name="realized_pnl_base",
        )
        if quantity <= 0 or local_cost < 0 or (base_cost is not None and base_cost < 0):
            raise LedgerContractError("disposition quantity/cost contract is invalid")
        if local_realized != exact_decimal_subtract(local_proceeds, local_cost):
            raise LedgerContractError("local disposition P&L does not close")
        if base_proceeds is None or base_cost is None:
            if base_realized is not None:
                raise LedgerContractError(
                    "base realized P&L requires both base proceeds and base cost"
                )
        elif base_realized != exact_decimal_subtract(base_proceeds, base_cost):
            raise LedgerContractError("base disposition P&L does not close")
        if (
            not isinstance(self.source_lineage, FactLineage)
            or not isinstance(self.source_custody_lineage, FactLineage)
            or not isinstance(self.disposition_lineage, FactLineage)
        ):
            raise LedgerContractError("disposition lineage must be complete")
        for field_name in (
            "source_acquisition_fx_lineage",
            "disposition_fx_lineage",
        ):
            lineage = getattr(self, field_name)
            if lineage is not None and not isinstance(lineage, FactLineage):
                raise LedgerContractError(
                    f"{field_name} must be a FactLineage or null"
                )
        for field_name in ("source_cost_lineages", "source_cost_fx_lineages"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or _canonical_lineages(values) != values:
                raise LedgerContractError(
                    f"{field_name} must be canonical immutable lineage"
                )


@dataclass(frozen=True, slots=True)
class LedgerTotals:
    currency: str
    external_flow_in_local: Decimal = Decimal("0")
    external_flow_in_base: Decimal | None = Decimal("0")
    external_flow_out_local: Decimal = Decimal("0")
    external_flow_out_base: Decimal | None = Decimal("0")
    gross_income_local: Decimal = Decimal("0")
    gross_income_base: Decimal | None = Decimal("0")
    capitalized_fees_local: Decimal = Decimal("0")
    capitalized_taxes_local: Decimal = Decimal("0")
    expensed_fees_local: Decimal = Decimal("0")
    expensed_fees_base: Decimal | None = Decimal("0")
    expensed_taxes_local: Decimal = Decimal("0")
    expensed_taxes_base: Decimal | None = Decimal("0")
    disposal_fees_local: Decimal = Decimal("0")
    disposal_taxes_local: Decimal = Decimal("0")
    realized_pnl_local: Decimal = Decimal("0")
    realized_pnl_base: Decimal | None = Decimal("0")
    return_of_capital_local: Decimal = Decimal("0")
    return_of_capital_base: Decimal | None = Decimal("0")
    fx_conversion_effect_base: Decimal | None = Decimal("0")

    def __post_init__(self) -> None:
        require_currency(self.currency)
        for field_name in self.__dataclass_fields__:
            if field_name == "currency":
                continue
            _optional_exact(getattr(self, field_name), field_name=field_name)

    @property
    def economic_realized_and_income_pnl_local(self) -> Decimal:
        return exact_decimal_sum(
            (
                self.realized_pnl_local,
                self.gross_income_local,
                exact_decimal_negate(self.expensed_fees_local),
                exact_decimal_negate(self.expensed_taxes_local),
            )
        )


@dataclass(frozen=True, slots=True)
class LedgerEffect:
    effective_date: date
    event_id: str
    kind: LedgerEffectKind
    account_id: str
    currency: str
    base_currency: str
    lineage: FactLineage
    instrument_id: str | None = None
    pending_component_kind: PendingComponentKind | None = None
    external_flow_timing: ExternalFlowTiming | None = None
    cash_delta_local: Decimal = Decimal("0")
    recognition_cash_delta_base: Decimal | None = Decimal("0")
    pending_delta_local: Decimal = Decimal("0")
    pending_delta_base: Decimal | None = Decimal("0")
    quantity_delta: Decimal = Decimal("0")
    cost_basis_delta_local: Decimal = Decimal("0")
    cost_basis_delta_base: Decimal | None = Decimal("0")
    realized_pnl_delta_local: Decimal = Decimal("0")
    realized_pnl_delta_base: Decimal | None = Decimal("0")
    gross_income_local: Decimal = Decimal("0")
    gross_income_base: Decimal | None = Decimal("0")
    capitalized_fees_local: Decimal = Decimal("0")
    capitalized_taxes_local: Decimal = Decimal("0")
    expensed_fees_local: Decimal = Decimal("0")
    expensed_fees_base: Decimal | None = Decimal("0")
    expensed_taxes_local: Decimal = Decimal("0")
    expensed_taxes_base: Decimal | None = Decimal("0")
    disposal_fees_local: Decimal = Decimal("0")
    disposal_taxes_local: Decimal = Decimal("0")
    return_of_capital_local: Decimal = Decimal("0")
    return_of_capital_base: Decimal | None = Decimal("0")
    external_flow_in_local: Decimal = Decimal("0")
    external_flow_in_base: Decimal | None = Decimal("0")
    external_flow_out_local: Decimal = Decimal("0")
    external_flow_out_base: Decimal | None = Decimal("0")
    fx_conversion_effect_base: Decimal | None = Decimal("0")

    def __post_init__(self) -> None:
        require_date(self.effective_date, field_name="effective_date")
        require_text(self.event_id, field_name="event_id")
        require_enum(self.kind, LedgerEffectKind, field_name="kind")
        require_text(self.account_id, field_name="account_id")
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")
        if self.instrument_id is not None:
            require_text(self.instrument_id, field_name="instrument_id")
        if self.pending_component_kind is not None:
            require_enum(
                self.pending_component_kind,
                PendingComponentKind,
                field_name="pending_component_kind",
            )
        if self.external_flow_timing is not None:
            require_enum(
                self.external_flow_timing,
                ExternalFlowTiming,
                field_name="external_flow_timing",
            )
        for field_name in self.__dataclass_fields__:
            if field_name in {
                "effective_date",
                "event_id",
                "kind",
                "account_id",
                "currency",
                "base_currency",
                "lineage",
                "instrument_id",
                "pending_component_kind",
                "external_flow_timing",
            }:
                continue
            _optional_exact(getattr(self, field_name), field_name=field_name)


LEDGER_TOTAL_EFFECT_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "external_flow_in_local": "external_flow_in_local",
        "external_flow_in_base": "external_flow_in_base",
        "external_flow_out_local": "external_flow_out_local",
        "external_flow_out_base": "external_flow_out_base",
        "gross_income_local": "gross_income_local",
        "gross_income_base": "gross_income_base",
        "capitalized_fees_local": "capitalized_fees_local",
        "capitalized_taxes_local": "capitalized_taxes_local",
        "expensed_fees_local": "expensed_fees_local",
        "expensed_fees_base": "expensed_fees_base",
        "expensed_taxes_local": "expensed_taxes_local",
        "expensed_taxes_base": "expensed_taxes_base",
        "disposal_fees_local": "disposal_fees_local",
        "disposal_taxes_local": "disposal_taxes_local",
        "realized_pnl_local": "realized_pnl_delta_local",
        "realized_pnl_base": "realized_pnl_delta_base",
        "return_of_capital_local": "return_of_capital_local",
        "return_of_capital_base": "return_of_capital_base",
        "fx_conversion_effect_base": "fx_conversion_effect_base",
    }
)
LEDGER_TOTAL_FIELDS = tuple(LEDGER_TOTAL_EFFECT_FIELDS)

if tuple(
    field_name
    for field_name in LedgerTotals.__dataclass_fields__
    if field_name != "currency"
) != LEDGER_TOTAL_FIELDS:
    raise RuntimeError(
        "LedgerTotals fields and LEDGER_TOTAL_EFFECT_FIELDS must remain identical"
    )
if not set(LEDGER_TOTAL_EFFECT_FIELDS.values()).issubset(
    LedgerEffect.__dataclass_fields__
):
    raise RuntimeError(
        "LEDGER_TOTAL_EFFECT_FIELDS references an unknown LedgerEffect field"
    )


@dataclass(frozen=True, slots=True)
class FxConversionEvidence:
    event_id: str
    trade_date: date
    settlement_date: date
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal
    effective_fx_rate: Decimal
    rate_convention: FxRateConvention
    base_currency: str
    source_base_value: Decimal | None
    target_base_value: Decimal | None
    base_economic_difference: Decimal | None
    lineage: FactLineage

    def __post_init__(self) -> None:
        require_text(self.event_id, field_name="event_id")
        require_date(self.trade_date, field_name="trade_date")
        require_date(self.settlement_date, field_name="settlement_date")
        require_currency(self.source_currency, field_name="source_currency")
        require_currency(self.target_currency, field_name="target_currency")
        require_currency(self.base_currency, field_name="base_currency")
        _exact(self.source_amount, field_name="source_amount")
        _exact(self.target_amount, field_name="target_amount")
        _exact(self.effective_fx_rate, field_name="effective_fx_rate")
        require_enum(
            self.rate_convention,
            FxRateConvention,
            field_name="rate_convention",
        )
        source = _optional_exact(self.source_base_value, field_name="source_base_value")
        target = _optional_exact(self.target_base_value, field_name="target_base_value")
        difference = _optional_exact(
            self.base_economic_difference,
            field_name="base_economic_difference",
        )
        if source is None or target is None:
            if difference is not None:
                raise LedgerContractError(
                    "FX base difference requires both base leg values"
                )
        elif difference != exact_decimal_subtract(target, source):
            raise LedgerContractError("FX base economic difference does not close")
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")


@dataclass(frozen=True, slots=True)
class DividendReinvestmentEvidence:
    event_id: str
    recognition_date: date
    instrument_id: str
    quantity: Decimal
    gross_income: Decimal
    price: Decimal | None
    price_status: PriceEvidenceStatus
    contract_multiplier: Decimal
    price_factor: Decimal
    price_unit: str
    lineage: FactLineage

    def __post_init__(self) -> None:
        require_text(self.event_id, field_name="event_id")
        require_date(self.recognition_date, field_name="recognition_date")
        require_text(self.instrument_id, field_name="instrument_id")
        _exact(self.quantity, field_name="quantity")
        _exact(self.gross_income, field_name="gross_income")
        price = _optional_exact(self.price, field_name="price")
        require_enum(self.price_status, PriceEvidenceStatus, field_name="price_status")
        if (price is None) != (self.price_status is PriceEvidenceStatus.UNAVAILABLE):
            raise LedgerContractError("price and price_status are inconsistent")
        _exact(self.contract_multiplier, field_name="contract_multiplier")
        _exact(self.price_factor, field_name="price_factor")
        require_text(self.price_unit, field_name="price_unit")
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")


@dataclass(frozen=True, slots=True)
class OpeningAnchorEvidence:
    anchor_date: date
    event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DailyLedgerState:
    as_of_date: date
    cash_balances: tuple[CashBalance, ...]
    pending_settlements: tuple[PendingSettlement, ...]
    positions: tuple[PositionState, ...]
    cumulative_totals: tuple[LedgerTotals, ...]
    daily_totals: tuple[LedgerTotals, ...]
    dispositions: tuple[LotDisposition, ...]
    daily_dispositions: tuple[LotDisposition, ...]
    fx_conversions: tuple[FxConversionEvidence, ...]
    dividend_reinvestments: tuple[DividendReinvestmentEvidence, ...]
    opening_anchor: OpeningAnchorEvidence | None
    historical_base_coverage_status: BaseCoverageStatus
    historical_base_coverage_reasons: tuple[LedgerReasonCode, ...]


@dataclass(frozen=True, slots=True)
class DailyLedgerResult:
    status: LedgerStatus
    reason_codes: tuple[LedgerReasonCode, ...]
    state: DailyLedgerState | None
    effects: tuple[LedgerEffect, ...]
    failed_event_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class DailyLedgerSnapshot:
    state: DailyLedgerState
    daily_effects: tuple[LedgerEffect, ...]


@dataclass(frozen=True, slots=True)
class LedgerSeriesResult:
    status: LedgerStatus
    reason_codes: tuple[LedgerReasonCode, ...]
    snapshots: tuple[DailyLedgerSnapshot, ...]
    effects: tuple[LedgerEffect, ...]
    failed_event_id: str | None = None
    diagnostic: str | None = None


__all__ = [
    "BaseCoverageStatus",
    "CashBalance",
    "DailyLedgerResult",
    "DailyLedgerSnapshot",
    "DailyLedgerState",
    "DividendReinvestmentEvidence",
    "FxConversionEvidence",
    "LedgerEffect",
    "LedgerEffectKind",
    "LEDGER_TOTAL_EFFECT_FIELDS",
    "LEDGER_TOTAL_FIELDS",
    "LedgerSeriesResult",
    "LedgerStatus",
    "LedgerTotals",
    "LotDisposition",
    "OpeningAnchorEvidence",
    "PriceEvidenceStatus",
    "PendingComponentKind",
    "PendingSettlement",
    "PositionLot",
    "PositionState",
    "canonical_lots",
]
