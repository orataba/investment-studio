"""Immutable input contracts for the exact Portfolio Daily ledger.

The objects in this module are the boundary between a sealed calculation
manifest and the pure replay engine.  They accept only finite ``Decimal``
facts at the declared fact scale.  In particular, no constructor coerces a
binary float, integer, or string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from collections.abc import Mapping
from typing import TypeAlias

from portfolio_app.calculations.numeric import (
    AMOUNT_SCALE,
    FX_RATE_SCALE,
    PRICE_SCALE,
    QUANTITY_SCALE,
    CalculationNumericError,
    exact_decimal_product,
    method_decimal_divide,
    quantize_decimal,
    require_decimal,
    require_method_decimal,
)


class LedgerReasonCode(StrEnum):
    INVALID_EVENT = "invalid_event"
    DUPLICATE_EVENT_ID = "duplicate_event_id"
    DUPLICATE_SEQUENCE = "duplicate_sequence"
    DUPLICATE_SOURCE_FACT = "duplicate_source_fact"
    OPENING_ANCHOR_MISMATCH = "opening_anchor_mismatch"
    GROSS_AMOUNT_MISMATCH = "gross_amount_mismatch"
    POSITION_NOT_FOUND = "position_not_found"
    POSITION_ALREADY_EXISTS = "position_already_exists"
    CASH_BALANCE_ALREADY_EXISTS = "cash_balance_already_exists"
    COST_BASIS_METHOD_MISMATCH = "cost_basis_method_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    OVERSELL = "oversell"
    PENDING_SETTLEMENT_NOT_FOUND = "pending_settlement_not_found"
    DUPLICATE_PENDING_SETTLEMENT = "duplicate_pending_settlement"
    NEGATIVE_NET_CASH = "negative_net_cash"
    TRANSFER_METHOD_CONVERSION_UNAVAILABLE = (
        "transfer_method_conversion_unavailable"
    )
    LOT_STATE_NOT_CLOSED = "lot_state_not_closed"
    STATE_NOT_CLOSED = "state_not_closed"
    BASE_FX_UNAVAILABLE = "base_fx_unavailable"
    NUMERIC_FAILURE = "numeric_failure"


class CostBasisMethod(StrEnum):
    FIFO = "fifo"
    MOVING_AVERAGE = "moving_average"


class ExternalFlowKind(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class ExternalFlowTiming(StrEnum):
    BEGINNING_OF_DAY = "beginning_of_day"
    END_OF_DAY = "end_of_day"


class IncomeKind(StrEnum):
    DIVIDEND = "dividend"
    COUPON = "coupon"
    INTEREST = "interest"


class ExpenseKind(StrEnum):
    FEE = "fee"
    TAX = "tax"


class FxRateConvention(StrEnum):
    TARGET_PER_SOURCE = "target_per_source"
    SOURCE_PER_TARGET = "source_per_target"


class LedgerFactKind(StrEnum):
    QUANTITY = "quantity"
    PRICE = "price"
    AMOUNT = "amount"
    RATIO = "ratio"


LEDGER_FACT_SCALES: Mapping[LedgerFactKind, int] = MappingProxyType(
    {
        LedgerFactKind.QUANTITY: QUANTITY_SCALE,
        LedgerFactKind.PRICE: PRICE_SCALE,
        LedgerFactKind.AMOUNT: AMOUNT_SCALE,
        LedgerFactKind.RATIO: FX_RATE_SCALE,
    }
)


class LedgerContractError(CalculationNumericError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: LedgerReasonCode = LedgerReasonCode.INVALID_EVENT,
        event_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.event_id = event_id


def require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LedgerContractError(
            f"{field_name} must be a non-empty canonical string"
        )
    return value


def require_currency(value: object, *, field_name: str = "currency") -> str:
    currency = require_text(value, field_name=field_name)
    if (
        len(currency) != 3
        or currency != currency.upper()
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise LedgerContractError(
            f"{field_name} must be a three-letter uppercase ASCII currency"
        )
    return currency


def require_date(value: object, *, field_name: str) -> date:
    if type(value) is not date:
        raise LedgerContractError(f"{field_name} must be a date")
    return value


def require_enum(
    value: object,
    enum_type: type[StrEnum],
    *,
    field_name: str,
) -> None:
    if not isinstance(value, enum_type):
        raise LedgerContractError(
            f"{field_name} must be a {enum_type.__name__} value"
        )


def require_sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LedgerContractError("sequence must be a non-negative integer")
    return value


def quantize_ledger_fact(
    value: Decimal,
    *,
    kind: LedgerFactKind,
    field_name: str,
) -> Decimal:
    require_enum(kind, LedgerFactKind, field_name="kind")
    try:
        return quantize_decimal(
            value,
            scale=LEDGER_FACT_SCALES[kind],
            field_name=field_name,
        )
    except CalculationNumericError as exc:
        raise LedgerContractError(str(exc)) from exc


def require_fact(
    value: object,
    *,
    kind: LedgerFactKind,
    field_name: str,
    minimum: Decimal | None = None,
    strictly_positive: bool = False,
) -> Decimal:
    try:
        resolved = require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise LedgerContractError(str(exc)) from exc
    if quantize_ledger_fact(resolved, kind=kind, field_name=field_name) != resolved:
        raise LedgerContractError(
            f"{field_name} exceeds the {LEDGER_FACT_SCALES[kind]}-decimal fact scale"
        )
    if strictly_positive and resolved <= 0:
        raise LedgerContractError(f"{field_name} must be positive")
    if minimum is not None and resolved < minimum:
        raise LedgerContractError(f"{field_name} must be at least {minimum}")
    return resolved


def require_optional_rate(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return require_fact(
        value,
        kind=LedgerFactKind.RATIO,
        field_name=field_name,
        strictly_positive=True,
    )


@dataclass(frozen=True, slots=True)
class FactLineage:
    """Stable origin of an input fact inside a sealed manifest."""

    source_record_id: str
    source_revision_id: str
    manifest_fact_key: str

    def __post_init__(self) -> None:
        require_text(self.source_record_id, field_name="source_record_id")
        require_text(self.source_revision_id, field_name="source_revision_id")
        require_text(self.manifest_fact_key, field_name="manifest_fact_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEventHeader:
    event_id: str
    sequence: int
    lineage: FactLineage

    def __post_init__(self) -> None:
        require_text(self.event_id, field_name="event_id")
        require_sequence(self.sequence)
        if not isinstance(self.lineage, FactLineage):
            raise LedgerContractError("lineage must be a FactLineage")


def _require_account(value: object, *, field_name: str = "account_id") -> None:
    require_text(value, field_name=field_name)


def _require_instrument(value: object) -> None:
    require_text(value, field_name="instrument_id")


def _require_fees_and_taxes(fees: object, taxes: object) -> None:
    require_fact(
        fees,
        kind=LedgerFactKind.AMOUNT,
        field_name="fees",
        minimum=Decimal("0"),
    )
    require_fact(
        taxes,
        kind=LedgerFactKind.AMOUNT,
        field_name="taxes",
        minimum=Decimal("0"),
    )


def _require_settlement_dates(
    recognition_date: object,
    settlement_date: object,
    *,
    recognition_field: str,
) -> None:
    recognition = require_date(recognition_date, field_name=recognition_field)
    settlement = require_date(settlement_date, field_name="settlement_date")
    if settlement < recognition:
        raise LedgerContractError(
            f"settlement_date must not precede {recognition_field}"
        )


def _require_base_rate(
    *,
    currency: str,
    base_currency: str,
    rate: object,
    field_name: str,
) -> Decimal | None:
    local = require_currency(currency)
    base = require_currency(base_currency, field_name="base_currency")
    resolved = require_optional_rate(rate, field_name=field_name)
    if local == base and resolved != Decimal("1"):
        raise LedgerContractError(
            f"{field_name} must be exactly one when currency equals base_currency"
        )
    return resolved


def _require_fx_lineage(
    *,
    currency: str,
    base_currency: str,
    rate: Decimal | None,
    lineage: object,
    field_name: str,
) -> None:
    if rate is None:
        if lineage is not None:
            raise LedgerContractError(
                f"{field_name} must be null when the corresponding FX rate is unavailable"
            )
        return
    if currency != base_currency:
        if not isinstance(lineage, FactLineage):
            raise LedgerContractError(
                f"{field_name} is required for a non-base-currency FX rate"
            )
    elif lineage is not None and not isinstance(lineage, FactLineage):
        raise LedgerContractError(f"{field_name} must be a FactLineage or null")


def _require_trade_terms(
    *,
    quantity: object,
    price: object,
    contract_multiplier: object,
    price_factor: object,
    gross_amount: object,
    consideration_basis: object,
    event_id: str,
) -> None:
    q = require_fact(
        quantity,
        kind=LedgerFactKind.QUANTITY,
        field_name="quantity",
        strictly_positive=True,
    )
    multiplier = require_fact(
        contract_multiplier,
        kind=LedgerFactKind.RATIO,
        field_name="contract_multiplier",
        strictly_positive=True,
    )
    factor = require_fact(
        price_factor,
        kind=LedgerFactKind.RATIO,
        field_name="price_factor",
        strictly_positive=True,
    )
    gross = require_fact(
        gross_amount,
        kind=LedgerFactKind.AMOUNT,
        field_name="gross_amount",
        strictly_positive=True,
    )
    basis = require_text(consideration_basis, field_name="consideration_basis")
    if basis == "source_reported":
        if price is not None:
            require_fact(
                price,
                kind=LedgerFactKind.PRICE,
                field_name="price",
                strictly_positive=True,
            )
        return
    if basis != "exact_quantity_price":
        raise LedgerContractError(
            "consideration_basis must be exact_quantity_price or source_reported",
            event_id=event_id,
        )
    p = require_fact(
        price,
        kind=LedgerFactKind.PRICE,
        field_name="price",
        strictly_positive=True,
    )
    exact = exact_decimal_product(q, p, multiplier, factor)
    if gross != exact:
        raise LedgerContractError(
            "gross_amount must exactly equal quantity * price * "
            f"contract_multiplier * price_factor: expected {exact}",
            reason_code=LedgerReasonCode.GROSS_AMOUNT_MISMATCH,
            event_id=event_id,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningCashEvent(LedgerEventHeader):
    effective_date: date
    account_id: str
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        super(OpeningCashEvent, self).__post_init__()
        require_date(self.effective_date, field_name="effective_date")
        _require_account(self.account_id)
        require_currency(self.currency)
        require_fact(self.amount, kind=LedgerFactKind.AMOUNT, field_name="amount")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningPositionEvent(LedgerEventHeader):
    effective_date: date
    acquisition_date: date
    account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    quantity: Decimal
    local_cost: Decimal
    acquisition_local_to_base_rate: Decimal | None
    acquisition_fx_lineage: FactLineage | None
    cost_basis_method: CostBasisMethod

    def __post_init__(self) -> None:
        super(OpeningPositionEvent, self).__post_init__()
        effective = require_date(self.effective_date, field_name="effective_date")
        acquired = require_date(self.acquisition_date, field_name="acquisition_date")
        if acquired > effective:
            raise LedgerContractError("acquisition_date must not follow effective_date")
        _require_account(self.account_id)
        _require_instrument(self.instrument_id)
        currency = require_currency(self.currency)
        base = require_currency(self.base_currency, field_name="base_currency")
        require_fact(
            self.quantity,
            kind=LedgerFactKind.QUANTITY,
            field_name="quantity",
            strictly_positive=True,
        )
        require_fact(
            self.local_cost,
            kind=LedgerFactKind.AMOUNT,
            field_name="local_cost",
            minimum=Decimal("0"),
        )
        rate = _require_base_rate(
            currency=currency,
            base_currency=base,
            rate=self.acquisition_local_to_base_rate,
            field_name="acquisition_local_to_base_rate",
        )
        _require_fx_lineage(
            currency=currency,
            base_currency=base,
            rate=rate,
            lineage=self.acquisition_fx_lineage,
            field_name="acquisition_fx_lineage",
        )
        require_enum(
            self.cost_basis_method,
            CostBasisMethod,
            field_name="cost_basis_method",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TradeEvent(LedgerEventHeader):
    trade_date: date
    settlement_date: date
    position_account_id: str
    cash_account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    quantity: Decimal
    price: Decimal | None
    contract_multiplier: Decimal
    price_factor: Decimal
    price_unit: str
    gross_amount: Decimal
    consideration_basis: str
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        super(TradeEvent, self).__post_init__()
        _require_settlement_dates(
            self.trade_date,
            self.settlement_date,
            recognition_field="trade_date",
        )
        _require_account(self.position_account_id, field_name="position_account_id")
        _require_account(self.cash_account_id, field_name="cash_account_id")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_text(self.price_unit, field_name="price_unit")
        _require_trade_terms(
            quantity=self.quantity,
            price=self.price,
            contract_multiplier=self.contract_multiplier,
            price_factor=self.price_factor,
            gross_amount=self.gross_amount,
            consideration_basis=self.consideration_basis,
            event_id=self.event_id,
        )
        _require_fees_and_taxes(self.fees, self.taxes)
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BuyEvent(TradeEvent):
    cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO

    def __post_init__(self) -> None:
        super(BuyEvent, self).__post_init__()
        require_enum(
            self.cost_basis_method,
            CostBasisMethod,
            field_name="cost_basis_method",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SellEvent(TradeEvent):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalCashFlowEvent(LedgerEventHeader):
    value_date: date
    account_id: str
    currency: str
    base_currency: str
    kind: ExternalFlowKind
    timing: ExternalFlowTiming
    amount: Decimal
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None

    def __post_init__(self) -> None:
        super(ExternalCashFlowEvent, self).__post_init__()
        require_date(self.value_date, field_name="value_date")
        _require_account(self.account_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_enum(self.kind, ExternalFlowKind, field_name="kind")
        require_enum(self.timing, ExternalFlowTiming, field_name="timing")
        expected_timing = (
            ExternalFlowTiming.BEGINNING_OF_DAY
            if self.kind is ExternalFlowKind.DEPOSIT
            else ExternalFlowTiming.END_OF_DAY
        )
        if self.timing is not expected_timing:
            raise LedgerContractError(
                f"{self.kind.value} must use {expected_timing.value} FX/return timing"
            )
        require_fact(
            self.amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="amount",
            strictly_positive=True,
        )
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class IncomeEvent(LedgerEventHeader):
    """Income entitlement for dividend/coupon, or accrual for cash interest."""

    recognition_date: date
    settlement_date: date
    cash_account_id: str
    currency: str
    base_currency: str
    kind: IncomeKind
    gross_amount: Decimal
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    instrument_id: str | None = None
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        super(IncomeEvent, self).__post_init__()
        _require_settlement_dates(
            self.recognition_date,
            self.settlement_date,
            recognition_field="recognition_date",
        )
        _require_account(self.cash_account_id, field_name="cash_account_id")
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_enum(self.kind, IncomeKind, field_name="kind")
        if self.kind is IncomeKind.INTEREST:
            if self.instrument_id is not None:
                raise LedgerContractError("cash interest must not carry instrument_id")
        elif self.instrument_id is None:
            raise LedgerContractError("dividend and coupon require instrument_id")
        else:
            _require_instrument(self.instrument_id)
        require_fact(
            self.gross_amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="gross_amount",
            strictly_positive=True,
        )
        _require_fees_and_taxes(self.fees, self.taxes)
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReturnOfCapitalEvent(LedgerEventHeader):
    """Distribution entitlement that reduces pre-trade open cost on its date."""

    recognition_date: date
    settlement_date: date
    position_account_id: str
    cash_account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    gross_amount: Decimal
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        super(ReturnOfCapitalEvent, self).__post_init__()
        _require_settlement_dates(
            self.recognition_date,
            self.settlement_date,
            recognition_field="recognition_date",
        )
        _require_account(self.position_account_id, field_name="position_account_id")
        _require_account(self.cash_account_id, field_name="cash_account_id")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_fact(
            self.gross_amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="gross_amount",
            strictly_positive=True,
        )
        _require_fees_and_taxes(self.fees, self.taxes)
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MaturityRedemptionEvent(LedgerEventHeader):
    """Instrument-lifecycle recognition that precedes ordinary same-day trades."""

    recognition_date: date
    settlement_date: date
    position_account_id: str
    cash_account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    quantity: Decimal
    gross_amount: Decimal
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        super(MaturityRedemptionEvent, self).__post_init__()
        _require_settlement_dates(
            self.recognition_date,
            self.settlement_date,
            recognition_field="recognition_date",
        )
        _require_account(self.position_account_id, field_name="position_account_id")
        _require_account(self.cash_account_id, field_name="cash_account_id")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_fact(
            self.quantity,
            kind=LedgerFactKind.QUANTITY,
            field_name="quantity",
            strictly_positive=True,
        )
        require_fact(
            self.gross_amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="gross_amount",
            minimum=Decimal("0"),
        )
        _require_fees_and_taxes(self.fees, self.taxes)
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CashTransferEvent(LedgerEventHeader):
    effective_date: date
    source_account_id: str
    destination_account_id: str
    currency: str
    amount: Decimal
    counterparty_lineage: FactLineage

    def __post_init__(self) -> None:
        super(CashTransferEvent, self).__post_init__()
        require_date(self.effective_date, field_name="effective_date")
        _require_account(self.source_account_id, field_name="source_account_id")
        _require_account(
            self.destination_account_id,
            field_name="destination_account_id",
        )
        if self.source_account_id == self.destination_account_id:
            raise LedgerContractError("transfer accounts must be distinct")
        require_currency(self.currency)
        require_fact(
            self.amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="amount",
            strictly_positive=True,
        )
        if not isinstance(self.counterparty_lineage, FactLineage):
            raise LedgerContractError(
                "counterparty_lineage must identify the paired transfer revision"
            )
        if self.counterparty_lineage == self.lineage:
            raise LedgerContractError("paired transfer revisions must be distinct")


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionTransferEvent(LedgerEventHeader):
    effective_date: date
    source_account_id: str
    destination_account_id: str
    instrument_id: str
    currency: str
    quantity: Decimal
    declared_local_cost: Decimal
    counterparty_lineage: FactLineage

    def __post_init__(self) -> None:
        super(PositionTransferEvent, self).__post_init__()
        require_date(self.effective_date, field_name="effective_date")
        _require_account(self.source_account_id, field_name="source_account_id")
        _require_account(
            self.destination_account_id,
            field_name="destination_account_id",
        )
        if self.source_account_id == self.destination_account_id:
            raise LedgerContractError("transfer accounts must be distinct")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_fact(
            self.quantity,
            kind=LedgerFactKind.QUANTITY,
            field_name="quantity",
            strictly_positive=True,
        )
        require_fact(
            self.declared_local_cost,
            kind=LedgerFactKind.AMOUNT,
            field_name="declared_local_cost",
            minimum=Decimal("0"),
        )
        if not isinstance(self.counterparty_lineage, FactLineage):
            raise LedgerContractError(
                "counterparty_lineage must identify the paired transfer revision"
            )
        if self.counterparty_lineage == self.lineage:
            raise LedgerContractError("paired transfer revisions must be distinct")


@dataclass(frozen=True, slots=True, kw_only=True)
class SplitEvent(LedgerEventHeader):
    """Exact instrument-wide unit change applied to every live position.

    A canonical corporate action is scoped to an instrument, not to an account.
    Account-specific expansion belongs inside replay, where the position state
    at the beginning-of-day corporate-action phase is authoritative.  This
    contract deliberately excludes fractional-share rounding and cash in lieu;
    those require separate issuer terms and valuation facts.
    """

    effective_date: date
    instrument_id: str
    currency: str
    base_currency: str
    ratio_numerator: Decimal
    ratio_denominator: Decimal

    def __post_init__(self) -> None:
        super(SplitEvent, self).__post_init__()
        require_date(self.effective_date, field_name="effective_date")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_fact(
            self.ratio_numerator,
            kind=LedgerFactKind.RATIO,
            field_name="ratio_numerator",
            strictly_positive=True,
        )
        require_fact(
            self.ratio_denominator,
            kind=LedgerFactKind.RATIO,
            field_name="ratio_denominator",
            strictly_positive=True,
        )
        if self.ratio_numerator == self.ratio_denominator:
            raise LedgerContractError("split ratio must change the number of units")


@dataclass(frozen=True, slots=True, kw_only=True)
class FxConversionEvent(LedgerEventHeader):
    """Trade-date cash conversion; recognition postings remain in trade order."""

    trade_date: date
    settlement_date: date
    source_account_id: str
    target_account_id: str
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal
    effective_fx_rate: Decimal
    rate_convention: FxRateConvention
    base_currency: str
    source_to_base_rate: Decimal | None
    target_to_base_rate: Decimal | None
    source_to_base_lineage: FactLineage | None
    target_to_base_lineage: FactLineage | None

    def __post_init__(self) -> None:
        super(FxConversionEvent, self).__post_init__()
        _require_settlement_dates(
            self.trade_date,
            self.settlement_date,
            recognition_field="trade_date",
        )
        _require_account(self.source_account_id, field_name="source_account_id")
        _require_account(self.target_account_id, field_name="target_account_id")
        source = require_currency(self.source_currency, field_name="source_currency")
        target = require_currency(self.target_currency, field_name="target_currency")
        base = require_currency(self.base_currency, field_name="base_currency")
        if source == target:
            raise LedgerContractError("FX conversion currencies must differ")
        source_amount = require_fact(
            self.source_amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="source_amount",
            strictly_positive=True,
        )
        target_amount = require_fact(
            self.target_amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="target_amount",
            strictly_positive=True,
        )
        require_enum(
            self.rate_convention,
            FxRateConvention,
            field_name="rate_convention",
        )
        try:
            rate = require_method_decimal(
                self.effective_fx_rate,
                field_name="effective_fx_rate",
            )
            expected_rate = require_method_decimal(
                method_decimal_divide(
                    target_amount,
                    source_amount,
                )
                if self.rate_convention is FxRateConvention.TARGET_PER_SOURCE
                else method_decimal_divide(source_amount, target_amount),
                field_name="calculated_effective_fx_rate",
            )
        except CalculationNumericError as exc:
            raise LedgerContractError(str(exc), event_id=self.event_id) from exc
        if rate <= 0:
            raise LedgerContractError(
                "effective_fx_rate must be positive",
                event_id=self.event_id,
            )
        if rate != expected_rate:
            raise LedgerContractError(
                "effective_fx_rate must equal the method50 rate implied by actual cash amounts",
                reason_code=LedgerReasonCode.GROSS_AMOUNT_MISMATCH,
                event_id=self.event_id,
            )
        source_base_rate = _require_base_rate(
            currency=source,
            base_currency=base,
            rate=self.source_to_base_rate,
            field_name="source_to_base_rate",
        )
        target_base_rate = _require_base_rate(
            currency=target,
            base_currency=base,
            rate=self.target_to_base_rate,
            field_name="target_to_base_rate",
        )
        _require_fx_lineage(
            currency=source,
            base_currency=base,
            rate=source_base_rate,
            lineage=self.source_to_base_lineage,
            field_name="source_to_base_lineage",
        )
        _require_fx_lineage(
            currency=target,
            base_currency=base,
            rate=target_base_rate,
            lineage=self.target_to_base_lineage,
            field_name="target_to_base_lineage",
        )
        # One canonical base leg can remain known while the other resolver is
        # unavailable.  Local conversion validity never depends on either;
        # the engine simply leaves the base economic difference unavailable.


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendReinvestmentEvent(LedgerEventHeader):
    """Distribution entitlement and exact acquisition applied before trades."""

    recognition_date: date
    position_account_id: str
    instrument_id: str
    currency: str
    base_currency: str
    gross_income: Decimal
    quantity: Decimal
    price: Decimal | None
    contract_multiplier: Decimal
    price_factor: Decimal
    price_unit: str
    consideration_basis: str
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO

    def __post_init__(self) -> None:
        super(DividendReinvestmentEvent, self).__post_init__()
        require_date(self.recognition_date, field_name="recognition_date")
        _require_account(self.position_account_id, field_name="position_account_id")
        _require_instrument(self.instrument_id)
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_text(self.price_unit, field_name="price_unit")
        require_fact(
            self.quantity,
            kind=LedgerFactKind.QUANTITY,
            field_name="quantity",
            strictly_positive=True,
        )
        require_fact(
            self.gross_income,
            kind=LedgerFactKind.AMOUNT,
            field_name="gross_income",
            strictly_positive=True,
        )
        require_fact(
            self.contract_multiplier,
            kind=LedgerFactKind.RATIO,
            field_name="contract_multiplier",
            strictly_positive=True,
        )
        require_fact(
            self.price_factor,
            kind=LedgerFactKind.RATIO,
            field_name="price_factor",
            strictly_positive=True,
        )
        _require_trade_terms(
            quantity=self.quantity,
            price=self.price,
            contract_multiplier=self.contract_multiplier,
            price_factor=self.price_factor,
            gross_amount=self.gross_income,
            consideration_basis=self.consideration_basis,
            event_id=self.event_id,
        )
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )
        require_enum(
            self.cost_basis_method,
            CostBasisMethod,
            field_name="cost_basis_method",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpenseEvent(LedgerEventHeader):
    """Standalone fee or tax accrual recognized before same-day trades."""

    recognition_date: date
    settlement_date: date
    cash_account_id: str
    currency: str
    base_currency: str
    kind: ExpenseKind
    amount: Decimal
    local_to_base_rate: Decimal | None
    local_to_base_lineage: FactLineage | None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        super(ExpenseEvent, self).__post_init__()
        _require_settlement_dates(
            self.recognition_date,
            self.settlement_date,
            recognition_field="recognition_date",
        )
        _require_account(self.cash_account_id, field_name="cash_account_id")
        require_currency(self.currency)
        require_currency(self.base_currency, field_name="base_currency")
        require_enum(self.kind, ExpenseKind, field_name="kind")
        require_fact(
            self.amount,
            kind=LedgerFactKind.AMOUNT,
            field_name="amount",
            strictly_positive=True,
        )
        if self.instrument_id is not None:
            _require_instrument(self.instrument_id)
        rate = _require_base_rate(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=self.local_to_base_rate,
            field_name="local_to_base_rate",
        )
        _require_fx_lineage(
            currency=self.currency,
            base_currency=self.base_currency,
            rate=rate,
            lineage=self.local_to_base_lineage,
            field_name="local_to_base_lineage",
        )


LedgerEvent: TypeAlias = (
    OpeningCashEvent
    | OpeningPositionEvent
    | BuyEvent
    | SellEvent
    | ExternalCashFlowEvent
    | IncomeEvent
    | ReturnOfCapitalEvent
    | MaturityRedemptionEvent
    | CashTransferEvent
    | PositionTransferEvent
    | SplitEvent
    | FxConversionEvent
    | DividendReinvestmentEvent
    | ExpenseEvent
)


SUPPORTED_LEDGER_EVENT_TYPES = (
    OpeningCashEvent,
    OpeningPositionEvent,
    BuyEvent,
    SellEvent,
    ExternalCashFlowEvent,
    IncomeEvent,
    ReturnOfCapitalEvent,
    MaturityRedemptionEvent,
    CashTransferEvent,
    PositionTransferEvent,
    SplitEvent,
    FxConversionEvent,
    DividendReinvestmentEvent,
    ExpenseEvent,
)


__all__ = [
    "BuyEvent",
    "CashTransferEvent",
    "CostBasisMethod",
    "DividendReinvestmentEvent",
    "ExpenseEvent",
    "ExpenseKind",
    "ExternalCashFlowEvent",
    "ExternalFlowKind",
    "ExternalFlowTiming",
    "FactLineage",
    "FxConversionEvent",
    "FxRateConvention",
    "IncomeEvent",
    "IncomeKind",
    "LEDGER_FACT_SCALES",
    "LedgerContractError",
    "LedgerEvent",
    "LedgerFactKind",
    "LedgerReasonCode",
    "MaturityRedemptionEvent",
    "OpeningCashEvent",
    "OpeningPositionEvent",
    "PositionTransferEvent",
    "ReturnOfCapitalEvent",
    "SUPPORTED_LEDGER_EVENT_TYPES",
    "SellEvent",
    "SplitEvent",
    "quantize_ledger_fact",
]
