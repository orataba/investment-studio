"""Strict immutable contracts for sealed-input Portfolio Daily valuation.

These records deliberately separate measurement coverage from endpoint
freshness.  A carried quote can still measure management NAV, but it cannot
create a new return endpoint.  Missing price, valuation factors, or FX never
becomes a zero-valued asset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    require_decimal,
)
from portfolio_app.calculations.portfolio_daily.ledger import FactLineage
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    DailyTwrOutcome,
    TwrAccumulatorState,
)


class ValuationContractError(CalculationNumericError):
    pass


class MarketFactStatus(StrEnum):
    FRESH = "fresh"
    CARRY_FORWARD = "carry_forward"
    UNAVAILABLE = "unavailable"


class ValuationEndpointStatus(StrEnum):
    FRESH = "fresh"
    CARRY_FORWARD = "carry_forward"
    UNAVAILABLE = "unavailable"


class BalanceComponentType(StrEnum):
    SETTLED_CASH = "settled_cash"
    PENDING_RECEIVABLE = "pending_receivable"
    PENDING_PAYABLE = "pending_payable"
    INCOME_ACCRUAL = "income_accrual"
    FEE_ACCRUAL = "fee_accrual"
    TAX_ACCRUAL = "tax_accrual"
    OTHER_ACCRUAL = "other_accrual"


class ValuationReasonCode(StrEnum):
    INPUT_CONTRACT_VIOLATION = "input_contract_violation"
    INSTRUMENT_CONTRACT_UNAVAILABLE = "instrument_contract_unavailable"
    MARKET_DATA_QUOTE_UNAVAILABLE = "market_data_quote_unavailable"
    MARKET_DATA_QUOTE_CARRIED = "market_data_quote_carried"
    FX_PATH_UNAVAILABLE = "fx_path_unavailable"
    FX_PATH_CARRIED = "fx_path_carried"
    HISTORICAL_BASE_COST_UNAVAILABLE = "historical_base_cost_unavailable"
    OPENING_MEASUREMENT_UNAVAILABLE = "opening_measurement_unavailable"
    BOOK_PNL_COMPONENT_UNAVAILABLE = "book_pnl_component_unavailable"
    NAV_CLOSURE_FAILED = "nav_closure_failed"
    BOOK_PNL_CLOSURE_FAILED = "book_pnl_closure_failed"


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValuationContractError(
            f"{field_name} must be a non-empty canonical string"
        )
    return value


def _currency(value: object, *, field_name: str = "currency") -> str:
    resolved = _text(value, field_name=field_name)
    if (
        len(resolved) != 3
        or resolved != resolved.upper()
        or not resolved.isascii()
        or not resolved.isalpha()
    ):
        raise ValuationContractError(
            f"{field_name} must be a three-letter uppercase ASCII currency"
        )
    return resolved


def _date(value: object, *, field_name: str) -> date:
    if type(value) is not date:
        raise ValuationContractError(f"{field_name} must be a date")
    return value


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        return require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise ValuationContractError(str(exc)) from exc


def _optional_decimal(value: object, *, field_name: str) -> Decimal | None:
    return None if value is None else _decimal(value, field_name=field_name)


def _reasons(values: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in values
    ):
        raise ValuationContractError(
            f"{field_name} must be an immutable canonical string tuple"
        )
    if tuple(sorted(set(values))) != values:
        raise ValuationContractError(f"{field_name} must be sorted and unique")
    return values


def _valuation_reasons(
    values: object,
    *,
    field_name: str,
) -> tuple[ValuationReasonCode, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(value, ValuationReasonCode) for value in values
    ):
        raise ValuationContractError(
            f"{field_name} must be an immutable ValuationReasonCode tuple"
        )
    if tuple(sorted(set(values), key=lambda value: value.value)) != values:
        raise ValuationContractError(f"{field_name} must be sorted and unique")
    return values


def _coverage(
    value: object,
    *,
    field_name: str,
) -> CoverageStatus:
    if not isinstance(value, CoverageStatus):
        raise ValuationContractError(f"{field_name} must be a CoverageStatus")
    return value


def _endpoint(
    value: object,
    *,
    field_name: str,
) -> ValuationEndpointStatus:
    if not isinstance(value, ValuationEndpointStatus):
        raise ValuationContractError(f"{field_name} must be a ValuationEndpointStatus")
    return value


@dataclass(frozen=True, slots=True)
class InstrumentValuationFact:
    instrument_id: str
    currency: str
    price_unit: str | None
    contract_multiplier: Decimal | None
    price_factor: Decimal | None
    available: bool
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.instrument_id, field_name="instrument_id")
        _currency(self.currency)
        if type(self.available) is not bool:
            raise ValuationContractError("available must be a bool")
        reasons = _reasons(self.reason_codes, field_name="reason_codes")
        multiplier = _optional_decimal(
            self.contract_multiplier,
            field_name="contract_multiplier",
        )
        factor = _optional_decimal(self.price_factor, field_name="price_factor")
        if self.available:
            _text(self.price_unit, field_name="price_unit")
            if multiplier is None or multiplier <= 0 or factor is None or factor <= 0:
                raise ValuationContractError(
                    "available instrument valuation requires positive factors"
                )
            if reasons:
                raise ValuationContractError(
                    "available instrument valuation cannot carry reason codes"
                )
        else:
            if not reasons:
                raise ValuationContractError(
                    "unavailable instrument valuation requires reason codes"
                )
            if (
                self.price_unit is not None
                and multiplier is not None
                and factor is not None
            ):
                raise ValuationContractError(
                    "a fully specified instrument contract cannot be unavailable"
                )


@dataclass(frozen=True, slots=True)
class QuoteValuationFact:
    as_of_date: date
    instrument_id: str
    currency: str
    status: MarketFactStatus
    price: Decimal | None
    lineage: FactLineage | None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _text(self.instrument_id, field_name="instrument_id")
        _currency(self.currency)
        if not isinstance(self.status, MarketFactStatus):
            raise ValuationContractError("status must be a MarketFactStatus")
        price = _optional_decimal(self.price, field_name="price")
        reasons = _reasons(self.reason_codes, field_name="reason_codes")
        if self.status is MarketFactStatus.FRESH:
            if price is None or price <= 0 or not isinstance(self.lineage, FactLineage):
                raise ValuationContractError(
                    "fresh quote requires a positive price and exact lineage"
                )
            if reasons:
                raise ValuationContractError("fresh quote cannot carry reason codes")
        elif self.status is MarketFactStatus.CARRY_FORWARD:
            if price is None or price <= 0 or not isinstance(self.lineage, FactLineage):
                raise ValuationContractError(
                    "carried quote requires a positive price and exact lineage"
                )
            if not reasons:
                raise ValuationContractError("carried quote requires a reason code")
        elif price is not None or self.lineage is not None or not reasons:
            raise ValuationContractError(
                "unavailable quote must be null-valued and explained"
            )


@dataclass(frozen=True, slots=True)
class FxValuationFact:
    as_of_date: date
    from_currency: str
    to_currency: str
    status: MarketFactStatus
    rate: Decimal | None
    lineages: tuple[FactLineage, ...]
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        source = _currency(self.from_currency, field_name="from_currency")
        target = _currency(self.to_currency, field_name="to_currency")
        if not isinstance(self.status, MarketFactStatus):
            raise ValuationContractError("status must be a MarketFactStatus")
        rate = _optional_decimal(self.rate, field_name="rate")
        if not isinstance(self.lineages, tuple) or any(
            not isinstance(value, FactLineage) for value in self.lineages
        ):
            raise ValuationContractError("lineages must be a FactLineage tuple")
        if (
            tuple(sorted(set(self.lineages), key=lambda value: value.manifest_fact_key))
            != self.lineages
        ):
            raise ValuationContractError("FX lineages must be sorted and unique")
        reasons = _reasons(self.reason_codes, field_name="reason_codes")
        if self.status is MarketFactStatus.FRESH:
            if rate is None or rate <= 0 or reasons:
                raise ValuationContractError(
                    "fresh FX requires a positive unexplained rate"
                )
            if source == target:
                if rate != Decimal("1") or self.lineages:
                    raise ValuationContractError(
                        "identity FX must be exact one without source revisions"
                    )
            elif not self.lineages:
                raise ValuationContractError("non-identity FX requires leg lineage")
        elif self.status is MarketFactStatus.CARRY_FORWARD:
            if (
                source == target
                or rate is None
                or rate <= 0
                or not self.lineages
                or not reasons
            ):
                raise ValuationContractError("carried FX evidence is incomplete")
        elif rate is not None or self.lineages or not reasons:
            raise ValuationContractError(
                "unavailable FX must be null-valued and explained"
            )


@dataclass(frozen=True, slots=True)
class DailyValuationBook:
    instruments: tuple[InstrumentValuationFact, ...]
    quotes: tuple[QuoteValuationFact, ...]
    fx_rates: tuple[FxValuationFact, ...]

    def __post_init__(self) -> None:
        for field_name, expected_type in (
            ("instruments", InstrumentValuationFact),
            ("quotes", QuoteValuationFact),
            ("fx_rates", FxValuationFact),
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, expected_type) for value in values
            ):
                raise ValuationContractError(
                    f"{field_name} must be an immutable {expected_type.__name__} tuple"
                )
        instrument_keys = [value.instrument_id for value in self.instruments]
        quote_keys = [(value.as_of_date, value.instrument_id) for value in self.quotes]
        fx_keys = [
            (value.as_of_date, value.from_currency, value.to_currency)
            for value in self.fx_rates
        ]
        if instrument_keys != sorted(instrument_keys) or len(
            set(instrument_keys)
        ) != len(instrument_keys):
            raise ValuationContractError("instrument facts must be unique and sorted")
        if quote_keys != sorted(quote_keys) or len(set(quote_keys)) != len(quote_keys):
            raise ValuationContractError("quote facts must be unique and sorted")
        if fx_keys != sorted(fx_keys) or len(set(fx_keys)) != len(fx_keys):
            raise ValuationContractError("FX facts must be unique and sorted")

        instruments = {value.instrument_id: value for value in self.instruments}
        for quote in self.quotes:
            instrument = instruments.get(quote.instrument_id)
            if instrument is None:
                raise ValuationContractError(
                    "every quote fact must reference an instrument fact"
                )
            if quote.currency != instrument.currency:
                raise ValuationContractError(
                    "quote and instrument currencies must match exactly"
                )


@dataclass(frozen=True, slots=True)
class ExactHoldingValuation:
    as_of_date: date
    account_id: str
    instrument_id: str
    currency: str
    quantity: Decimal
    cost_basis_local: Decimal
    cost_basis_base: Decimal | None
    price: Decimal | None
    contract_multiplier: Decimal | None
    price_factor: Decimal | None
    fx_rate_to_base: Decimal | None
    market_value_local: Decimal | None
    market_value_base: Decimal | None
    unrealized_pnl_base: Decimal | None
    coverage_status: CoverageStatus
    endpoint_status: ValuationEndpointStatus
    reason_codes: tuple[ValuationReasonCode, ...]
    endpoint_reason_codes: tuple[ValuationReasonCode, ...]
    book_pnl_coverage_status: CoverageStatus
    book_pnl_reason_codes: tuple[ValuationReasonCode, ...]

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _text(self.account_id, field_name="account_id")
        _text(self.instrument_id, field_name="instrument_id")
        _currency(self.currency)
        quantity = _decimal(self.quantity, field_name="quantity")
        local_cost = _decimal(self.cost_basis_local, field_name="cost_basis_local")
        base_cost = _optional_decimal(
            self.cost_basis_base,
            field_name="cost_basis_base",
        )
        if quantity <= 0 or local_cost < 0 or (base_cost is not None and base_cost < 0):
            raise ValuationContractError(
                "holding quantity must be positive and costs non-negative"
            )
        price = _optional_decimal(self.price, field_name="price")
        multiplier = _optional_decimal(
            self.contract_multiplier,
            field_name="contract_multiplier",
        )
        factor = _optional_decimal(self.price_factor, field_name="price_factor")
        fx = _optional_decimal(self.fx_rate_to_base, field_name="fx_rate_to_base")
        local_value = _optional_decimal(
            self.market_value_local,
            field_name="market_value_local",
        )
        base_value = _optional_decimal(
            self.market_value_base,
            field_name="market_value_base",
        )
        unrealized = _optional_decimal(
            self.unrealized_pnl_base,
            field_name="unrealized_pnl_base",
        )
        coverage = _coverage(self.coverage_status, field_name="coverage_status")
        reasons = _valuation_reasons(self.reason_codes, field_name="reason_codes")
        endpoint = _endpoint(self.endpoint_status, field_name="endpoint_status")
        endpoint_reasons = _valuation_reasons(
            self.endpoint_reason_codes,
            field_name="endpoint_reason_codes",
        )
        book_coverage = _coverage(
            self.book_pnl_coverage_status,
            field_name="book_pnl_coverage_status",
        )
        book_reasons = _valuation_reasons(
            self.book_pnl_reason_codes,
            field_name="book_pnl_reason_codes",
        )
        valuation_values = (price, multiplier, factor, fx, local_value, base_value)
        for field_name, value in (
            ("price", price),
            ("contract_multiplier", multiplier),
            ("price_factor", factor),
            ("fx_rate_to_base", fx),
        ):
            if value is not None and value <= 0:
                raise ValuationContractError(f"{field_name} must be positive")
        local_inputs_complete = all(
            value is not None for value in (price, multiplier, factor)
        )
        if (local_value is not None) != local_inputs_complete:
            raise ValuationContractError(
                "local market value requires price and both valuation factors"
            )
        if local_value is not None:
            assert price is not None
            assert multiplier is not None
            assert factor is not None
            expected_local = exact_decimal_product(
                quantity,
                price,
                multiplier,
                factor,
            )
            if local_value != expected_local:
                raise ValuationContractError(
                    "holding local market value does not close exactly"
                )
        if (base_value is not None) != (local_value is not None and fx is not None):
            raise ValuationContractError(
                "base market value requires local market value and FX"
            )
        if base_value is not None:
            assert local_value is not None
            assert fx is not None
            expected_base = exact_decimal_product(local_value, fx)
            if base_value != expected_base:
                raise ValuationContractError(
                    "holding base market value does not close exactly"
                )
        if coverage is CoverageStatus.COMPLETE:
            if any(value is None for value in valuation_values) or reasons:
                raise ValuationContractError(
                    "complete holding valuation requires every exact value"
                )
        elif not reasons or base_value is not None:
            raise ValuationContractError(
                "an incomplete holding valuation cannot retain a base value"
            )
        elif coverage is CoverageStatus.PARTIAL and local_value is None:
            raise ValuationContractError(
                "partial holding coverage requires a measured local market value"
            )
        elif coverage is CoverageStatus.UNAVAILABLE and local_value is not None:
            raise ValuationContractError(
                "a locally measured holding must use partial rather than unavailable coverage"
            )
        if endpoint is ValuationEndpointStatus.FRESH:
            if coverage is not CoverageStatus.COMPLETE or endpoint_reasons:
                raise ValuationContractError(
                    "fresh holding endpoint requires complete fresh valuation"
                )
        elif endpoint is ValuationEndpointStatus.CARRY_FORWARD:
            if coverage is not CoverageStatus.COMPLETE or not endpoint_reasons:
                raise ValuationContractError(
                    "carried holding endpoint requires complete carried evidence"
                )
        elif coverage is CoverageStatus.COMPLETE or not endpoint_reasons:
            raise ValuationContractError(
                "unavailable holding endpoint requires unavailable valuation"
            )
        if book_coverage is CoverageStatus.COMPLETE:
            if base_cost is None or base_value is None or book_reasons:
                raise ValuationContractError(
                    "complete holding book P&L requires base cost and market value"
                )
            if unrealized != exact_decimal_subtract(base_value, base_cost):
                raise ValuationContractError(
                    "holding unrealized P&L does not close exactly"
                )
        elif book_coverage is not CoverageStatus.UNAVAILABLE or not book_reasons:
            raise ValuationContractError(
                "unavailable holding book P&L must be explained"
            )
        elif unrealized is not None:
            raise ValuationContractError(
                "unavailable holding book P&L cannot retain unrealized P&L"
            )


@dataclass(frozen=True, slots=True)
class ExactBalanceValuation:
    as_of_date: date
    account_id: str
    component_type: BalanceComponentType
    component_key: str
    currency: str
    local_amount: Decimal
    fx_rate_to_base: Decimal | None
    base_amount: Decimal | None
    coverage_status: CoverageStatus
    endpoint_status: ValuationEndpointStatus
    reason_codes: tuple[ValuationReasonCode, ...]
    endpoint_reason_codes: tuple[ValuationReasonCode, ...]

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _text(self.account_id, field_name="account_id")
        if not isinstance(self.component_type, BalanceComponentType):
            raise ValuationContractError(
                "component_type must be a BalanceComponentType"
            )
        _text(self.component_key, field_name="component_key")
        _currency(self.currency)
        local = _decimal(self.local_amount, field_name="local_amount")
        if self.component_type is not BalanceComponentType.SETTLED_CASH and local <= 0:
            raise ValuationContractError(
                "pending and accrual balance rows use positive magnitudes"
            )
        fx = _optional_decimal(self.fx_rate_to_base, field_name="fx_rate_to_base")
        base = _optional_decimal(self.base_amount, field_name="base_amount")
        coverage = _coverage(self.coverage_status, field_name="coverage_status")
        reasons = _valuation_reasons(self.reason_codes, field_name="reason_codes")
        endpoint = _endpoint(self.endpoint_status, field_name="endpoint_status")
        endpoint_reasons = _valuation_reasons(
            self.endpoint_reason_codes,
            field_name="endpoint_reason_codes",
        )
        if coverage is CoverageStatus.COMPLETE:
            if fx is None or base is None or fx <= 0 or reasons:
                raise ValuationContractError(
                    "complete balance valuation requires positive FX and base amount"
                )
            expected_base = exact_decimal_product(local, fx)
            if base != expected_base:
                raise ValuationContractError(
                    "balance base amount does not close exactly"
                )
        elif (
            coverage is not CoverageStatus.UNAVAILABLE
            or not reasons
            or (fx is not None or base is not None)
        ):
            raise ValuationContractError(
                "unavailable balance valuation must be null-valued and explained"
            )
        if endpoint is ValuationEndpointStatus.FRESH:
            if coverage is not CoverageStatus.COMPLETE or endpoint_reasons:
                raise ValuationContractError(
                    "fresh balance endpoint requires complete fresh FX"
                )
        elif endpoint is ValuationEndpointStatus.CARRY_FORWARD:
            if coverage is not CoverageStatus.COMPLETE or not endpoint_reasons:
                raise ValuationContractError(
                    "carried balance endpoint requires carried FX evidence"
                )
        elif coverage is CoverageStatus.COMPLETE or not endpoint_reasons:
            raise ValuationContractError(
                "unavailable balance endpoint requires unavailable FX"
            )

    @property
    def nav_sign(self) -> Decimal:
        return (
            Decimal("-1")
            if self.component_type
            in {
                BalanceComponentType.PENDING_PAYABLE,
                BalanceComponentType.FEE_ACCRUAL,
                BalanceComponentType.TAX_ACCRUAL,
            }
            else Decimal("1")
        )


@dataclass(frozen=True, slots=True)
class ExactBookPnl:
    economic_measured: bool
    measured: bool
    economic_pnl: Decimal | None
    realized_pnl: Decimal | None
    unrealized_beginning: Decimal | None
    unrealized_ending: Decimal | None
    unrealized_change: Decimal | None
    gross_income: Decimal | None
    expensed_fees: Decimal | None
    expensed_taxes: Decimal | None
    cash_fx_effect: Decimal | None
    pending_fx_effect: Decimal | None
    accrual_fx_effect: Decimal | None
    fx_conversion_effect: Decimal | None
    monetary_balance_fx_effect: Decimal | None
    component_closure_residual: Decimal | None
    nav_bridge_residual: Decimal | None
    reason_codes: tuple[ValuationReasonCode, ...]

    def __post_init__(self) -> None:
        if type(self.economic_measured) is not bool or type(self.measured) is not bool:
            raise ValuationContractError(
                "economic_measured and measured must be bool values"
            )
        field_names = (
            "economic_pnl",
            "realized_pnl",
            "unrealized_beginning",
            "unrealized_ending",
            "unrealized_change",
            "gross_income",
            "expensed_fees",
            "expensed_taxes",
            "cash_fx_effect",
            "pending_fx_effect",
            "accrual_fx_effect",
            "fx_conversion_effect",
            "monetary_balance_fx_effect",
            "component_closure_residual",
            "nav_bridge_residual",
        )
        values = {
            field_name: _optional_decimal(
                getattr(self, field_name),
                field_name=field_name,
            )
            for field_name in field_names
        }
        reasons = _valuation_reasons(self.reason_codes, field_name="reason_codes")
        if self.economic_measured:
            if self.economic_pnl is None or self.nav_bridge_residual != 0:
                raise ValuationContractError(
                    "measured economic P&L requires an exact closed NAV bridge"
                )
        elif self.economic_pnl is not None or self.nav_bridge_residual is not None:
            raise ValuationContractError("unmeasured economic P&L must be null-valued")
        if not self.measured:
            explanatory_fields = set(field_names) - {
                "economic_pnl",
                "nav_bridge_residual",
            }
            if (
                any(values[field] is not None for field in explanatory_fields)
                or not reasons
            ):
                raise ValuationContractError(
                    "unmeasured book bridge must have null explanatory fields and reasons"
                )
            return
        if not self.economic_measured:
            raise ValuationContractError(
                "a measured book bridge requires measured economic P&L"
            )
        if any(value is None for value in values.values()) or reasons:
            raise ValuationContractError(
                "measured book P&L requires every exact component"
            )
        assert self.unrealized_beginning is not None
        assert self.unrealized_ending is not None
        assert self.unrealized_change is not None
        assert self.cash_fx_effect is not None
        assert self.pending_fx_effect is not None
        assert self.accrual_fx_effect is not None
        assert self.monetary_balance_fx_effect is not None
        if self.unrealized_change != exact_decimal_subtract(
            self.unrealized_ending,
            self.unrealized_beginning,
        ):
            raise ValuationContractError("unrealized P&L change does not close exactly")
        if self.monetary_balance_fx_effect != exact_decimal_sum(
            (
                self.cash_fx_effect,
                self.pending_fx_effect,
                self.accrual_fx_effect,
            )
        ):
            raise ValuationContractError(
                "monetary balance FX effects do not close exactly"
            )
        if self.component_closure_residual != 0 or self.nav_bridge_residual != 0:
            raise ValuationContractError(
                "measured book P&L closure residuals must be exact zero"
            )
        assert self.economic_pnl is not None
        assert self.realized_pnl is not None
        assert self.gross_income is not None
        assert self.expensed_fees is not None
        assert self.expensed_taxes is not None
        assert self.fx_conversion_effect is not None
        explained = exact_decimal_sum(
            (
                self.realized_pnl,
                self.unrealized_change,
                self.gross_income,
                exact_decimal_negate(self.expensed_fees),
                exact_decimal_negate(self.expensed_taxes),
                self.monetary_balance_fx_effect,
                self.fx_conversion_effect,
            )
        )
        if explained != self.economic_pnl:
            raise ValuationContractError(
                "book P&L components do not close exactly to economic P&L"
            )


@dataclass(frozen=True, slots=True)
class ExactDailyPortfolioValuation:
    as_of_date: date
    base_currency: str
    holdings: tuple[ExactHoldingValuation, ...]
    balances: tuple[ExactBalanceValuation, ...]
    opening_nav: Decimal | None
    closing_nav: Decimal | None
    position_market_value: Decimal | None
    settled_cash: Decimal | None
    pending_receivable: Decimal | None
    pending_payable: Decimal | None
    accrual_receivable: Decimal | None
    accrual_payable: Decimal | None
    external_flow_in: Decimal | None
    external_flow_out: Decimal | None
    coverage_status: CoverageStatus
    endpoint_status: ValuationEndpointStatus
    reason_codes: tuple[ValuationReasonCode, ...]
    endpoint_reason_codes: tuple[ValuationReasonCode, ...]
    nav_closure_residual: Decimal | None
    book_pnl: ExactBookPnl
    twr: DailyTwrOutcome

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _currency(self.base_currency, field_name="base_currency")
        if not isinstance(self.holdings, tuple) or any(
            not isinstance(value, ExactHoldingValuation) for value in self.holdings
        ):
            raise ValuationContractError(
                "holdings must be an immutable ExactHoldingValuation tuple"
            )
        if not isinstance(self.balances, tuple) or any(
            not isinstance(value, ExactBalanceValuation) for value in self.balances
        ):
            raise ValuationContractError(
                "balances must be an immutable ExactBalanceValuation tuple"
            )
        holding_keys = [
            (value.account_id, value.instrument_id) for value in self.holdings
        ]
        balance_keys = [
            (
                value.account_id,
                value.component_type.value,
                value.component_key,
                value.currency,
            )
            for value in self.balances
        ]
        if holding_keys != sorted(holding_keys) or len(set(holding_keys)) != len(
            holding_keys
        ):
            raise ValuationContractError(
                "holding valuations must be unique and canonically ordered"
            )
        if balance_keys != sorted(balance_keys) or len(set(balance_keys)) != len(
            balance_keys
        ):
            raise ValuationContractError(
                "balance valuations must be unique and canonically ordered"
            )
        for value in (*self.holdings, *self.balances):
            if value.as_of_date != self.as_of_date:
                raise ValuationContractError(
                    "all valuation rows must share the portfolio date"
                )
        amount_fields = (
            "opening_nav",
            "closing_nav",
            "position_market_value",
            "settled_cash",
            "pending_receivable",
            "pending_payable",
            "accrual_receivable",
            "accrual_payable",
            "external_flow_in",
            "external_flow_out",
            "nav_closure_residual",
        )
        amounts = {
            field_name: _optional_decimal(
                getattr(self, field_name),
                field_name=field_name,
            )
            for field_name in amount_fields
        }
        for field_name in ("external_flow_in", "external_flow_out"):
            value = amounts[field_name]
            if value is not None and value < 0:
                raise ValuationContractError(
                    f"{field_name} must be a non-negative magnitude"
                )
        coverage = _coverage(self.coverage_status, field_name="coverage_status")
        reasons = _valuation_reasons(self.reason_codes, field_name="reason_codes")
        endpoint = _endpoint(self.endpoint_status, field_name="endpoint_status")
        endpoint_reasons = _valuation_reasons(
            self.endpoint_reason_codes,
            field_name="endpoint_reason_codes",
        )
        measured_fields = (
            "closing_nav",
            "position_market_value",
            "settled_cash",
            "pending_receivable",
            "pending_payable",
            "accrual_receivable",
            "accrual_payable",
            "nav_closure_residual",
        )
        if coverage is CoverageStatus.COMPLETE:
            if any(amounts[field] is None for field in measured_fields) or reasons:
                raise ValuationContractError(
                    "complete portfolio NAV requires every exact component"
                )
            if amounts["nav_closure_residual"] != 0:
                raise ValuationContractError(
                    "complete portfolio NAV residual must be exact zero"
                )
            assert self.position_market_value is not None
            assert self.settled_cash is not None
            assert self.pending_receivable is not None
            assert self.pending_payable is not None
            assert self.accrual_receivable is not None
            assert self.accrual_payable is not None
            assert self.closing_nav is not None
            nav = exact_decimal_sum(
                (
                    self.position_market_value,
                    self.settled_cash,
                    self.pending_receivable,
                    exact_decimal_negate(self.pending_payable),
                    self.accrual_receivable,
                    exact_decimal_negate(self.accrual_payable),
                )
            )
            if nav != self.closing_nav:
                raise ValuationContractError(
                    "portfolio NAV components do not close exactly"
                )
        else:
            if amounts["closing_nav"] is not None or not reasons:
                raise ValuationContractError(
                    "incomplete portfolio NAV must be null and explained"
                )
            if amounts["nav_closure_residual"] is not None:
                raise ValuationContractError(
                    "incomplete portfolio NAV cannot claim closure"
                )
        if endpoint is ValuationEndpointStatus.FRESH:
            if coverage is not CoverageStatus.COMPLETE or endpoint_reasons:
                raise ValuationContractError(
                    "fresh portfolio endpoint requires complete fresh NAV"
                )
        elif endpoint is ValuationEndpointStatus.CARRY_FORWARD:
            if coverage is not CoverageStatus.COMPLETE or not endpoint_reasons:
                raise ValuationContractError(
                    "carried portfolio endpoint requires carried evidence"
                )
        elif coverage is CoverageStatus.COMPLETE or not endpoint_reasons:
            raise ValuationContractError("unavailable endpoint requires incomplete NAV")
        if not isinstance(self.book_pnl, ExactBookPnl):
            raise ValuationContractError("book_pnl must be an ExactBookPnl")
        if self.book_pnl.economic_measured:
            if (
                self.opening_nav is None
                or self.closing_nav is None
                or self.external_flow_in is None
                or self.external_flow_out is None
                or self.book_pnl.economic_pnl is None
            ):
                raise ValuationContractError(
                    "measured economic P&L requires both NAV boundaries and flows"
                )
            economic = exact_decimal_subtract(
                exact_decimal_sum((self.closing_nav, self.external_flow_out)),
                exact_decimal_sum((self.opening_nav, self.external_flow_in)),
            )
            if economic != self.book_pnl.economic_pnl:
                raise ValuationContractError(
                    "economic P&L does not close exactly to daily NAV boundaries"
                )
        if not isinstance(self.twr, DailyTwrOutcome):
            raise ValuationContractError("twr must be a DailyTwrOutcome")
        if self.twr.as_of_date != self.as_of_date:
            raise ValuationContractError("TWR and valuation dates must match")


@dataclass(frozen=True, slots=True)
class ExactPortfolioValuationSeries:
    days: tuple[ExactDailyPortfolioValuation, ...]
    final_twr_state: TwrAccumulatorState

    def __post_init__(self) -> None:
        if not isinstance(self.days, tuple) or any(
            not isinstance(value, ExactDailyPortfolioValuation) for value in self.days
        ):
            raise ValuationContractError(
                "days must be an immutable ExactDailyPortfolioValuation tuple"
            )
        dates = [value.as_of_date for value in self.days]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise ValuationContractError(
                "daily valuations must be unique and strictly ordered"
            )
        if not isinstance(self.final_twr_state, TwrAccumulatorState):
            raise ValuationContractError(
                "final_twr_state must be a TwrAccumulatorState"
            )
        if self.days:
            if self.days[-1].twr.next_state != self.final_twr_state:
                raise ValuationContractError(
                    "final TWR state must equal the last daily outcome state"
                )


__all__ = [
    "BalanceComponentType",
    "DailyValuationBook",
    "ExactBalanceValuation",
    "ExactBookPnl",
    "ExactDailyPortfolioValuation",
    "ExactHoldingValuation",
    "ExactPortfolioValuationSeries",
    "FxValuationFact",
    "InstrumentValuationFact",
    "MarketFactStatus",
    "QuoteValuationFact",
    "ValuationContractError",
    "ValuationEndpointStatus",
    "ValuationReasonCode",
    "exact_decimal_product",
    "exact_decimal_negate",
    "exact_decimal_subtract",
    "exact_decimal_sum",
]
