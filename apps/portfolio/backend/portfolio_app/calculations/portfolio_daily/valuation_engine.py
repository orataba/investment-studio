"""Pure exact Portfolio Daily valuation and book-P&L engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from portfolio_app.calculations.portfolio_daily.ledger import (
    DailyLedgerSnapshot,
    LedgerEffect,
    LedgerSeriesResult,
    LedgerStatus,
    LedgerTotals,
    PendingComponentKind,
    PositionState,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    FxStatus,
    PortfolioDailyInput,
    TwrAccumulatorState,
    ValuationStatus,
    advance_daily_twr,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    BalanceComponentType,
    DailyValuationBook,
    ExactBalanceValuation,
    ExactBookPnl,
    ExactDailyPortfolioValuation,
    ExactHoldingValuation,
    ExactPortfolioValuationSeries,
    InstrumentValuationFact,
    MarketFactStatus,
    QuoteValuationFact,
    ValuationContractError,
    ValuationEndpointStatus,
    ValuationReasonCode,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
)


class ValuationEngineError(ValuationContractError):
    """A ledger/book invariant prevents a canonical valuation."""


class ValuationClosureError(ValuationEngineError):
    def __init__(self, *, as_of_date: date, kind: str, residual: Decimal) -> None:
        self.as_of_date = as_of_date
        self.kind = kind
        self.residual = residual
        self.reason_code = (
            ValuationReasonCode.NAV_CLOSURE_FAILED
            if kind == "nav"
            else ValuationReasonCode.BOOK_PNL_CLOSURE_FAILED
        )
        super().__init__(
            f"{kind} closure failed on {as_of_date.isoformat()}: residual={residual}"
        )


def _reasons(
    *groups: tuple[ValuationReasonCode, ...],
) -> tuple[ValuationReasonCode, ...]:
    return tuple(
        sorted({item for group in groups for item in group}, key=lambda x: x.value)
    )


def _currency(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 3
        or value != value.upper()
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValuationEngineError(
            "base_currency must be three uppercase ASCII letters"
        )
    return value


@dataclass(frozen=True, slots=True)
class _Fx:
    rate: Decimal | None
    coverage: CoverageStatus
    endpoint: ValuationEndpointStatus
    coverage_reasons: tuple[ValuationReasonCode, ...]
    endpoint_reasons: tuple[ValuationReasonCode, ...]


@dataclass(frozen=True, slots=True)
class _Day:
    snapshot: DailyLedgerSnapshot
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
    coverage: CoverageStatus
    endpoint: ValuationEndpointStatus
    coverage_reasons: tuple[ValuationReasonCode, ...]
    endpoint_reasons: tuple[ValuationReasonCode, ...]
    nav_residual: Decimal | None


class _Book:
    def __init__(self, facts: DailyValuationBook, base_currency: str) -> None:
        self.base_currency = base_currency
        self.instruments = {row.instrument_id: row for row in facts.instruments}
        self.quotes = {(row.as_of_date, row.instrument_id): row for row in facts.quotes}
        self.fx = {
            (row.as_of_date, row.from_currency, row.to_currency): row
            for row in facts.fx_rates
        }

    def resolve_fx(self, as_of_date: date, currency: str) -> _Fx:
        if currency == self.base_currency:
            return _Fx(
                Decimal("1"),
                CoverageStatus.COMPLETE,
                ValuationEndpointStatus.FRESH,
                (),
                (),
            )
        fact = self.fx.get((as_of_date, currency, self.base_currency))
        if fact is None or fact.status is MarketFactStatus.UNAVAILABLE:
            reason = (ValuationReasonCode.FX_PATH_UNAVAILABLE,)
            return _Fx(
                None,
                CoverageStatus.UNAVAILABLE,
                ValuationEndpointStatus.UNAVAILABLE,
                reason,
                reason,
            )
        assert fact.rate is not None
        if fact.status is MarketFactStatus.CARRY_FORWARD:
            return _Fx(
                fact.rate,
                CoverageStatus.COMPLETE,
                ValuationEndpointStatus.CARRY_FORWARD,
                (),
                (ValuationReasonCode.FX_PATH_CARRIED,),
            )
        return _Fx(
            fact.rate, CoverageStatus.COMPLETE, ValuationEndpointStatus.FRESH, (), ()
        )


def _holding(
    position: PositionState, as_of_date: date, book: _Book
) -> ExactHoldingValuation:
    if position.base_currency != book.base_currency:
        raise ValuationEngineError(
            "position base currency does not match portfolio base currency"
        )
    instrument: InstrumentValuationFact | None = book.instruments.get(
        position.instrument_id
    )
    quote: QuoteValuationFact | None = book.quotes.get(
        (as_of_date, position.instrument_id)
    )
    if instrument is not None and instrument.currency != position.currency:
        raise ValuationEngineError("position and instrument currencies do not match")
    contract_ok = instrument is not None and instrument.available
    quote_ok = quote is not None and quote.status is not MarketFactStatus.UNAVAILABLE
    if quote is not None and quote.currency != position.currency:
        raise ValuationEngineError("position and quote currencies do not match")
    fx = book.resolve_fx(as_of_date, position.currency)
    price = quote.price if quote_ok else None
    multiplier = instrument.contract_multiplier if contract_ok else None
    factor = instrument.price_factor if contract_ok else None
    local_value = (
        exact_decimal_product(position.quantity, price, multiplier, factor)
        if price is not None and multiplier is not None and factor is not None
        else None
    )
    base_value = (
        exact_decimal_product(local_value, fx.rate)
        if local_value is not None and fx.rate is not None
        else None
    )
    coverage_reasons: list[ValuationReasonCode] = []
    if not contract_ok:
        coverage_reasons.append(ValuationReasonCode.INSTRUMENT_CONTRACT_UNAVAILABLE)
    if not quote_ok:
        coverage_reasons.append(ValuationReasonCode.MARKET_DATA_QUOTE_UNAVAILABLE)
    if fx.rate is None:
        coverage_reasons.append(ValuationReasonCode.FX_PATH_UNAVAILABLE)
    reasons = _reasons(tuple(coverage_reasons))
    coverage = (
        CoverageStatus.COMPLETE
        if base_value is not None
        else CoverageStatus.PARTIAL
        if local_value is not None
        else CoverageStatus.UNAVAILABLE
    )
    endpoint_reasons = reasons
    if coverage is CoverageStatus.COMPLETE:
        endpoint_reasons = _reasons(
            (ValuationReasonCode.MARKET_DATA_QUOTE_CARRIED,)
            if quote is not None and quote.status is MarketFactStatus.CARRY_FORWARD
            else (),
            fx.endpoint_reasons,
        )
        endpoint = (
            ValuationEndpointStatus.CARRY_FORWARD
            if endpoint_reasons
            else ValuationEndpointStatus.FRESH
        )
    else:
        endpoint = ValuationEndpointStatus.UNAVAILABLE
    base_cost = position.historical_base_cost
    book_complete = base_value is not None and base_cost is not None
    book_reasons = (
        ()
        if book_complete
        else _reasons(
            (ValuationReasonCode.HISTORICAL_BASE_COST_UNAVAILABLE,)
            if base_cost is None
            else (),
            (ValuationReasonCode.BOOK_PNL_COMPONENT_UNAVAILABLE,)
            if base_value is None
            else (),
        )
    )
    unrealized = (
        exact_decimal_subtract(base_value, base_cost)
        if book_complete and base_value is not None and base_cost is not None
        else None
    )
    return ExactHoldingValuation(
        as_of_date=as_of_date,
        account_id=position.account_id,
        instrument_id=position.instrument_id,
        currency=position.currency,
        quantity=position.quantity,
        cost_basis_local=position.local_cost,
        cost_basis_base=base_cost,
        price=price,
        contract_multiplier=multiplier,
        price_factor=factor,
        fx_rate_to_base=fx.rate,
        market_value_local=local_value,
        market_value_base=base_value,
        unrealized_pnl_base=unrealized,
        coverage_status=coverage,
        endpoint_status=endpoint,
        reason_codes=reasons,
        endpoint_reason_codes=endpoint_reasons,
        book_pnl_coverage_status=(
            CoverageStatus.COMPLETE if book_complete else CoverageStatus.UNAVAILABLE
        ),
        book_pnl_reason_codes=book_reasons,
    )


_PENDING = {
    "trade_settlement",
    "fx_conversion_source_leg",
    "fx_conversion_target_leg",
}
_INCOME = {"income_accrual"}
_FEE = {"fee_accrual"}
_TAX = {"tax_accrual"}
_OTHER = {"return_of_capital_accrual", "maturity_accrual", "cash_in_lieu_accrual"}
_ACCRUAL = _INCOME | _FEE | _TAX | _OTHER


def _pending_type(kind: PendingComponentKind, amount: Decimal) -> BalanceComponentType:
    value = kind.value
    if value in _PENDING:
        return (
            BalanceComponentType.PENDING_RECEIVABLE
            if amount > 0
            else BalanceComponentType.PENDING_PAYABLE
        )
    if value in _INCOME:
        if amount <= 0:
            raise ValuationEngineError("income accrual must be positive")
        return BalanceComponentType.INCOME_ACCRUAL
    if value in _FEE:
        if amount >= 0:
            raise ValuationEngineError("fee accrual must be negative")
        return BalanceComponentType.FEE_ACCRUAL
    if value in _TAX:
        if amount >= 0:
            raise ValuationEngineError("tax accrual must be negative")
        return BalanceComponentType.TAX_ACCRUAL
    if value in _OTHER:
        if amount <= 0:
            raise ValuationEngineError("other accrual must be positive")
        return BalanceComponentType.OTHER_ACCRUAL
    raise ValuationEngineError(f"unsupported pending component kind {value}")


def _balances(
    snapshot: DailyLedgerSnapshot, book: _Book
) -> tuple[ExactBalanceValuation, ...]:
    day = snapshot.state.as_of_date
    rows: list[ExactBalanceValuation] = []
    for cash in snapshot.state.cash_balances:
        fx = book.resolve_fx(day, cash.currency)
        rows.append(
            ExactBalanceValuation(
                as_of_date=day,
                account_id=cash.account_id,
                component_type=BalanceComponentType.SETTLED_CASH,
                component_key="cash",
                currency=cash.currency,
                local_amount=cash.amount,
                fx_rate_to_base=fx.rate,
                base_amount=(
                    exact_decimal_product(cash.amount, fx.rate)
                    if fx.rate is not None
                    else None
                ),
                coverage_status=fx.coverage,
                endpoint_status=fx.endpoint,
                reason_codes=fx.coverage_reasons,
                endpoint_reason_codes=fx.endpoint_reasons,
            )
        )
    for pending in snapshot.state.pending_settlements:
        if pending.base_currency != book.base_currency:
            raise ValuationEngineError(
                "pending base currency does not match portfolio base currency"
            )
        component_type = _pending_type(pending.component_kind, pending.local_amount)
        magnitude = (
            exact_decimal_negate(pending.local_amount)
            if pending.local_amount < 0
            else pending.local_amount
        )
        fx = book.resolve_fx(day, pending.currency)
        rows.append(
            ExactBalanceValuation(
                as_of_date=day,
                account_id=pending.account_id,
                component_type=component_type,
                component_key=pending.settlement_id,
                currency=pending.currency,
                local_amount=magnitude,
                fx_rate_to_base=fx.rate,
                base_amount=(
                    exact_decimal_product(magnitude, fx.rate)
                    if fx.rate is not None
                    else None
                ),
                coverage_status=fx.coverage,
                endpoint_status=fx.endpoint,
                reason_codes=fx.coverage_reasons,
                endpoint_reason_codes=fx.endpoint_reasons,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.account_id,
                row.component_type.value,
                row.component_key,
                row.currency,
            ),
        )
    )


def _optional_sum(values: tuple[Decimal | None, ...]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return exact_decimal_sum(tuple(value for value in values if value is not None))


def _balance_total(
    rows: tuple[ExactBalanceValuation, ...], kinds: set[BalanceComponentType]
) -> Decimal | None:
    selected = tuple(row for row in rows if row.component_type in kinds)
    return _optional_sum(tuple(row.base_amount for row in selected))


def _daily_total(rows: tuple[LedgerTotals, ...], field: str) -> Decimal | None:
    return _optional_sum(tuple(getattr(row, field) for row in rows))


def _make_day(
    snapshot: DailyLedgerSnapshot, book: _Book, previous: _Day | None
) -> _Day:
    day = snapshot.state.as_of_date
    holdings = tuple(
        _holding(position, day, book) for position in snapshot.state.positions
    )
    balances = _balances(snapshot, book)
    position_value = _optional_sum(tuple(row.market_value_base for row in holdings))
    settled = _balance_total(balances, {BalanceComponentType.SETTLED_CASH})
    receivable = _balance_total(balances, {BalanceComponentType.PENDING_RECEIVABLE})
    payable = _balance_total(balances, {BalanceComponentType.PENDING_PAYABLE})
    accrual_receivable = _balance_total(
        balances,
        {BalanceComponentType.INCOME_ACCRUAL, BalanceComponentType.OTHER_ACCRUAL},
    )
    accrual_payable = _balance_total(
        balances, {BalanceComponentType.FEE_ACCRUAL, BalanceComponentType.TAX_ACCRUAL}
    )
    components = (
        position_value,
        settled,
        receivable,
        payable,
        accrual_receivable,
        accrual_payable,
    )
    closing = None
    residual = None
    if all(value is not None for value in components):
        assert (
            position_value is not None
            and settled is not None
            and receivable is not None
        )
        assert (
            payable is not None
            and accrual_receivable is not None
            and accrual_payable is not None
        )
        nav_components = (
            position_value,
            settled,
            receivable,
            exact_decimal_negate(payable),
            accrual_receivable,
            exact_decimal_negate(accrual_payable),
        )
        closing = exact_decimal_sum(nav_components)
        residual = exact_decimal_subtract(
            closing,
            exact_decimal_sum(nav_components),
        )
        if residual != 0:
            raise ValuationClosureError(as_of_date=day, kind="nav", residual=residual)
    opening = (
        previous.closing_nav
        if previous is not None and previous.closing_nav is not None
        else None
    )
    flow_in = _daily_total(snapshot.state.daily_totals, "external_flow_in_base")
    flow_out = _daily_total(snapshot.state.daily_totals, "external_flow_out_base")
    if flow_in is not None and flow_in < 0 or flow_out is not None and flow_out < 0:
        raise ValuationEngineError("external flows must be non-negative magnitudes")
    coverage_reasons = _reasons(*(row.reason_codes for row in (*holdings, *balances)))
    known = any(row.market_value_base is not None for row in holdings) or any(
        row.base_amount is not None for row in balances
    )
    coverage = (
        CoverageStatus.COMPLETE
        if closing is not None
        else (CoverageStatus.PARTIAL if known else CoverageStatus.UNAVAILABLE)
    )
    endpoint_reasons = _reasons(
        *(row.endpoint_reason_codes for row in (*holdings, *balances))
    )
    endpoint = (
        ValuationEndpointStatus.UNAVAILABLE
        if closing is None
        else ValuationEndpointStatus.CARRY_FORWARD
        if endpoint_reasons
        else ValuationEndpointStatus.FRESH
    )
    return _Day(
        snapshot,
        holdings,
        balances,
        opening,
        closing,
        position_value,
        settled,
        receivable,
        payable,
        accrual_receivable,
        accrual_payable,
        flow_in,
        flow_out,
        coverage,
        endpoint,
        coverage_reasons,
        endpoint_reasons,
        residual,
    )


def _signed_balance_total(
    rows: tuple[ExactBalanceValuation, ...], category: str
) -> Decimal | None:
    if category == "cash":
        selected = tuple(
            row
            for row in rows
            if row.component_type is BalanceComponentType.SETTLED_CASH
        )
    elif category == "pending":
        selected = tuple(
            row
            for row in rows
            if row.component_type
            in {
                BalanceComponentType.PENDING_RECEIVABLE,
                BalanceComponentType.PENDING_PAYABLE,
            }
        )
    else:
        selected = tuple(
            row
            for row in rows
            if row.component_type
            in {
                BalanceComponentType.INCOME_ACCRUAL,
                BalanceComponentType.FEE_ACCRUAL,
                BalanceComponentType.TAX_ACCRUAL,
                BalanceComponentType.OTHER_ACCRUAL,
            }
        )
    values: list[Decimal | None] = []
    for row in selected:
        values.append(
            None
            if row.base_amount is None
            else (
                row.base_amount
                if row.nav_sign > 0
                else exact_decimal_negate(row.base_amount)
            )
        )
    return _optional_sum(tuple(values))


def _effect_category(kind: PendingComponentKind | None) -> str:
    if kind is None:
        raise ValuationEngineError("non-zero pending effect requires component kind")
    if kind.value in _PENDING:
        return "pending"
    if kind.value in _ACCRUAL:
        return "accrual"
    raise ValuationEngineError(f"unsupported pending component kind {kind.value}")


def _recognition_total(
    effects: tuple[LedgerEffect, ...], category: str
) -> Decimal | None:
    values: list[Decimal | None] = []
    for effect in effects:
        if category == "cash":
            if effect.cash_delta_local != 0:
                values.append(effect.recognition_cash_delta_base)
        elif (
            effect.pending_delta_local != 0
            and _effect_category(effect.pending_component_kind) == category
        ):
            values.append(effect.pending_delta_base)
    return _optional_sum(tuple(values))


def _unrealized(rows: tuple[ExactHoldingValuation, ...]) -> Decimal | None:
    return _optional_sum(tuple(row.unrealized_pnl_base for row in rows))


def _book_pnl(current: _Day, previous: _Day | None) -> ExactBookPnl:
    reasons: list[ValuationReasonCode] = []
    economic_ready = (
        previous is not None
        and previous.closing_nav is not None
        and current.closing_nav is not None
        and current.external_flow_in is not None
        and current.external_flow_out is not None
    )
    economic = nav_residual = None
    if economic_ready:
        assert (
            previous is not None
            and previous.closing_nav is not None
            and current.closing_nav is not None
        )
        assert (
            current.external_flow_in is not None
            and current.external_flow_out is not None
        )
        economic = exact_decimal_sum(
            (
                current.closing_nav,
                current.external_flow_out,
                exact_decimal_negate(previous.closing_nav),
                exact_decimal_negate(current.external_flow_in),
            )
        )
        nav_residual = exact_decimal_subtract(
            exact_decimal_sum((current.closing_nav, current.external_flow_out)),
            exact_decimal_sum(
                (previous.closing_nav, current.external_flow_in, economic)
            ),
        )
        if nav_residual != 0:
            raise ValuationClosureError(
                as_of_date=current.snapshot.state.as_of_date,
                kind="pnl_nav_bridge",
                residual=nav_residual,
            )
    else:
        reasons.append(ValuationReasonCode.OPENING_MEASUREMENT_UNAVAILABLE)
    beginning_unrealized = (
        _unrealized(previous.holdings) if previous is not None else None
    )
    ending_unrealized = _unrealized(current.holdings)
    if beginning_unrealized is None or ending_unrealized is None:
        reasons.append(ValuationReasonCode.HISTORICAL_BASE_COST_UNAVAILABLE)
    daily = current.snapshot.state.daily_totals
    realized = _daily_total(daily, "realized_pnl_base")
    income = _daily_total(daily, "gross_income_base")
    fees = _daily_total(daily, "expensed_fees_base")
    taxes = _daily_total(daily, "expensed_taxes_base")
    conversion = _daily_total(daily, "fx_conversion_effect_base")
    fx_parts: dict[str, Decimal | None] = {}
    if previous is not None:
        for category in ("cash", "pending", "accrual"):
            beginning = _signed_balance_total(previous.balances, category)
            ending = _signed_balance_total(current.balances, category)
            recognition = _recognition_total(current.snapshot.daily_effects, category)
            fx_parts[category] = (
                exact_decimal_sum(
                    (
                        ending,
                        exact_decimal_negate(beginning),
                        exact_decimal_negate(recognition),
                    )
                )
                if ending is not None
                and beginning is not None
                and recognition is not None
                else None
            )
    else:
        fx_parts = {"cash": None, "pending": None, "accrual": None}
    required = (
        economic,
        beginning_unrealized,
        ending_unrealized,
        realized,
        income,
        fees,
        taxes,
        conversion,
        fx_parts["cash"],
        fx_parts["pending"],
        fx_parts["accrual"],
    )
    measured = all(value is not None for value in required)
    if not measured:
        reasons.append(ValuationReasonCode.BOOK_PNL_COMPONENT_UNAVAILABLE)
        return ExactBookPnl(
            economic_measured=economic is not None,
            measured=False,
            economic_pnl=economic,
            realized_pnl=None,
            unrealized_beginning=None,
            unrealized_ending=None,
            unrealized_change=None,
            gross_income=None,
            expensed_fees=None,
            expensed_taxes=None,
            cash_fx_effect=None,
            pending_fx_effect=None,
            accrual_fx_effect=None,
            fx_conversion_effect=None,
            monetary_balance_fx_effect=None,
            component_closure_residual=None,
            nav_bridge_residual=nav_residual,
            reason_codes=_reasons(tuple(reasons)),
        )
    assert (
        economic is not None
        and beginning_unrealized is not None
        and ending_unrealized is not None
    )
    assert (
        realized is not None
        and income is not None
        and fees is not None
        and taxes is not None
        and conversion is not None
    )
    cash_fx, pending_fx, accrual_fx = (
        fx_parts["cash"],
        fx_parts["pending"],
        fx_parts["accrual"],
    )
    assert cash_fx is not None and pending_fx is not None and accrual_fx is not None
    unrealized_change = exact_decimal_subtract(ending_unrealized, beginning_unrealized)
    monetary = exact_decimal_sum((cash_fx, pending_fx, accrual_fx))
    explained = exact_decimal_sum(
        (
            realized,
            unrealized_change,
            income,
            exact_decimal_negate(fees),
            exact_decimal_negate(taxes),
            monetary,
            conversion,
        )
    )
    residual = exact_decimal_subtract(economic, explained)
    if residual != 0:
        raise ValuationClosureError(
            as_of_date=current.snapshot.state.as_of_date,
            kind="book_pnl",
            residual=residual,
        )
    return ExactBookPnl(
        economic_measured=True,
        measured=True,
        economic_pnl=economic,
        realized_pnl=realized,
        unrealized_beginning=beginning_unrealized,
        unrealized_ending=ending_unrealized,
        unrealized_change=unrealized_change,
        gross_income=income,
        expensed_fees=fees,
        expensed_taxes=taxes,
        cash_fx_effect=cash_fx,
        pending_fx_effect=pending_fx,
        accrual_fx_effect=accrual_fx,
        fx_conversion_effect=conversion,
        monetary_balance_fx_effect=monetary,
        component_closure_residual=residual,
        nav_bridge_residual=nav_residual,
        reason_codes=(),
    )


def _validate_series(series: LedgerSeriesResult) -> None:
    if (
        not isinstance(series, LedgerSeriesResult)
        or series.status is not LedgerStatus.SUCCEEDED
    ):
        raise ValuationEngineError("valuation requires a succeeded LedgerSeriesResult")
    dates = tuple(snapshot.state.as_of_date for snapshot in series.snapshots)
    if not dates:
        raise ValuationEngineError("ledger series must contain at least one snapshot")
    if any(
        snapshot.daily_effects
        != tuple(
            effect
            for effect in snapshot.daily_effects
            if effect.effective_date == snapshot.state.as_of_date
        )
        for snapshot in series.snapshots
    ):
        raise ValuationEngineError("daily effects must match their snapshot date")
    if any(right != left + timedelta(days=1) for left, right in zip(dates, dates[1:])):
        raise ValuationEngineError(
            "ledger snapshots must be unique, ordered, and contiguous"
        )


def calculate_exact_portfolio_valuation(
    ledger_series: LedgerSeriesResult,
    valuation_book: DailyValuationBook,
    *,
    base_currency: str,
    initial_twr_state: TwrAccumulatorState | None = None,
) -> ExactPortfolioValuationSeries:
    """Calculate a deterministic exact series from sealed facts only."""

    _validate_series(ledger_series)
    if not isinstance(valuation_book, DailyValuationBook):
        raise ValuationEngineError("valuation_book must be a DailyValuationBook")
    book = _Book(valuation_book, _currency(base_currency))
    state = (
        TwrAccumulatorState.initial()
        if initial_twr_state is None
        else initial_twr_state
    )
    if not isinstance(state, TwrAccumulatorState):
        raise ValuationEngineError("initial_twr_state must be a TwrAccumulatorState")
    days: list[ExactDailyPortfolioValuation] = []
    previous: _Day | None = None
    for snapshot in ledger_series.snapshots:
        measured = _make_day(snapshot, book, previous)
        flow_ready = (
            measured.external_flow_in is not None
            and measured.external_flow_out is not None
        )
        return_ready = measured.closing_nav is not None and flow_ready
        performance_coverage = (
            CoverageStatus.COMPLETE
            if return_ready
            else measured.coverage
            if measured.closing_nav is None
            else CoverageStatus.PARTIAL
        )
        performance_fx_unavailable = (
            ValuationReasonCode.FX_PATH_UNAVAILABLE in measured.coverage_reasons
            or not flow_ready
        )
        period = PortfolioDailyInput(
            as_of_date=snapshot.state.as_of_date,
            measured_nav=measured.closing_nav if return_ready else None,
            external_flow_in=measured.external_flow_in
            if measured.external_flow_in is not None
            else Decimal("0"),
            external_flow_out=measured.external_flow_out
            if measured.external_flow_out is not None
            else Decimal("0"),
            coverage_status=performance_coverage,
            valuation_status=(
                ValuationStatus.CARRY_FORWARD
                if measured.endpoint is ValuationEndpointStatus.CARRY_FORWARD
                else ValuationStatus.FRESH
            ),
            fx_status=(
                FxStatus.UNAVAILABLE
                if performance_fx_unavailable
                else FxStatus.AVAILABLE
            ),
        )
        outcome = advance_daily_twr(period, state)
        pnl = _book_pnl(measured, previous)
        day = ExactDailyPortfolioValuation(
            as_of_date=snapshot.state.as_of_date,
            base_currency=book.base_currency,
            holdings=measured.holdings,
            balances=measured.balances,
            opening_nav=measured.opening_nav,
            closing_nav=measured.closing_nav,
            position_market_value=measured.position_market_value,
            settled_cash=measured.settled_cash,
            pending_receivable=measured.pending_receivable,
            pending_payable=measured.pending_payable,
            accrual_receivable=measured.accrual_receivable,
            accrual_payable=measured.accrual_payable,
            external_flow_in=measured.external_flow_in,
            external_flow_out=measured.external_flow_out,
            coverage_status=measured.coverage,
            endpoint_status=measured.endpoint,
            reason_codes=measured.coverage_reasons,
            endpoint_reason_codes=measured.endpoint_reasons,
            nav_closure_residual=measured.nav_residual,
            book_pnl=pnl,
            twr=outcome,
        )
        days.append(day)
        state = outcome.next_state
        previous = measured
    return ExactPortfolioValuationSeries(days=tuple(days), final_twr_state=state)


__all__ = [
    "ValuationClosureError",
    "ValuationEngineError",
    "calculate_exact_portfolio_valuation",
]
