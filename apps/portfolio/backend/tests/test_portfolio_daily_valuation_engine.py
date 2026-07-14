from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, getcontext

import pytest

from portfolio_app.calculations.numeric import calculation_context
from portfolio_app.calculations.portfolio_daily.ledger import (
    BuyEvent,
    CostBasisMethod,
    ExpenseEvent,
    ExpenseKind,
    ExternalCashFlowEvent,
    ExternalFlowKind,
    ExternalFlowTiming,
    FactLineage,
    IncomeEvent,
    IncomeKind,
    FxConversionEvent,
    FxRateConvention,
    LedgerTotals,
    OpeningCashEvent,
    OpeningPositionEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    replay_ledger_series,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    DailyCalculationStatus,
    TwrWindowStatus,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    DailyValuationBook,
    FxValuationFact,
    InstrumentValuationFact,
    MarketFactStatus,
    QuoteValuationFact,
    ValuationContractError,
    ValuationEndpointStatus,
    ValuationReasonCode,
)
from portfolio_app.calculations.portfolio_daily.valuation_engine import (
    ValuationClosureError,
    calculate_exact_portfolio_valuation,
)


pytestmark = pytest.mark.no_database
D = Decimal


def _lineage(key: str) -> FactLineage:
    return FactLineage(f"record-{key}", f"revision-{key}", f"fact/{key}")


def _instrument(instrument_id: str, currency: str) -> InstrumentValuationFact:
    return InstrumentValuationFact(
        instrument_id=instrument_id,
        currency=currency,
        price_unit="currency_per_unit",
        contract_multiplier=D("1"),
        price_factor=D("1"),
        available=True,
    )


def _quote(
    day: int,
    instrument_id: str,
    currency: str,
    price: str,
    *,
    carried: bool = False,
) -> QuoteValuationFact:
    return QuoteValuationFact(
        as_of_date=date(2026, 7, day),
        instrument_id=instrument_id,
        currency=currency,
        status=(MarketFactStatus.CARRY_FORWARD if carried else MarketFactStatus.FRESH),
        price=D(price),
        lineage=_lineage(f"quote-{instrument_id}-{day}"),
        reason_codes=("adopted_carry_forward",) if carried else (),
    )


def _fx(
    day: int, currency: str, rate: str, *, carried: bool = False
) -> FxValuationFact:
    return FxValuationFact(
        as_of_date=date(2026, 7, day),
        from_currency=currency,
        to_currency="CNY",
        status=(MarketFactStatus.CARRY_FORWARD if carried else MarketFactStatus.FRESH),
        rate=D(rate),
        lineages=(_lineage(f"fx-{currency}-{day}"),),
        reason_codes=("adopted_carry_forward",) if carried else (),
    )


def _book(
    *,
    instruments: tuple[InstrumentValuationFact, ...] = (),
    quotes: tuple[QuoteValuationFact, ...] = (),
    fx: tuple[FxValuationFact, ...] = (),
) -> DailyValuationBook:
    return DailyValuationBook(
        instruments=tuple(sorted(instruments, key=lambda row: row.instrument_id)),
        quotes=tuple(
            sorted(quotes, key=lambda row: (row.as_of_date, row.instrument_id))
        ),
        fx_rates=tuple(
            sorted(
                fx, key=lambda row: (row.as_of_date, row.from_currency, row.to_currency)
            )
        ),
    )


def _calculate(
    events: tuple[object, ...], start: int, end: int, book: DailyValuationBook
):
    ledger = replay_ledger_series(
        events,  # type: ignore[arg-type]
        start_date=date(2026, 7, start),
        end_date=date(2026, 7, end),
    )
    return calculate_exact_portfolio_valuation(ledger, book, base_currency="CNY")


def test_foreign_cash_fx_effect_and_first_day_anchor_are_exact() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cash",
        currency="USD",
        amount=D("100"),
    )
    result = _calculate(
        (opening,), 1, 2, _book(fx=(_fx(1, "USD", "7"), _fx(2, "USD", "8")))
    )
    first, second = result.days
    assert first.opening_nav is None
    assert first.closing_nav == D("700")
    assert first.book_pnl.measured is False
    assert first.book_pnl.economic_measured is False
    assert first.twr.status is DailyCalculationStatus.REANCHORED
    assert second.opening_nav == first.closing_nav
    assert second.closing_nav == D("800")
    assert second.book_pnl.measured is True
    assert second.book_pnl.economic_pnl == D("100")
    assert second.book_pnl.cash_fx_effect == D("100")
    assert second.book_pnl.component_closure_residual == 0
    with calculation_context():
        expected_return = (D("800") - D("700")) / D("700")
    assert second.twr.subperiod_twr_method50 == expected_return


def test_buy_sell_and_pending_settlement_close_without_double_counting() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cny-cash",
        currency="CNY",
        amount=D("1000"),
    )
    buy = BuyEvent(
        event_id="buy",
        sequence=1,
        lineage=_lineage("buy"),
        trade_date=date(2026, 7, 1),
        settlement_date=date(2026, 7, 2),
        position_account_id="position",
        cash_account_id="usd-cash",
        instrument_id="fund",
        currency="USD",
        base_currency="CNY",
        quantity=D("10"),
        price=D("10"),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        gross_amount=D("100"),
        consideration_basis="exact_quantity_price",
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("buy-fx"),
    )
    sell = SellEvent(
        event_id="sell",
        sequence=2,
        lineage=_lineage("sell"),
        trade_date=date(2026, 7, 3),
        settlement_date=date(2026, 7, 4),
        position_account_id="position",
        cash_account_id="usd-cash",
        instrument_id="fund",
        currency="USD",
        base_currency="CNY",
        quantity=D("4"),
        price=D("15"),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        gross_amount=D("60"),
        consideration_basis="exact_quantity_price",
        local_to_base_rate=D("8"),
        local_to_base_lineage=_lineage("sell-fx"),
    )
    facts = _book(
        instruments=(_instrument("fund", "USD"),),
        quotes=tuple(
            _quote(day, "fund", "USD", "10" if day < 3 else "15") for day in range(1, 5)
        ),
        fx=tuple(_fx(day, "USD", "7" if day < 3 else "8") for day in range(1, 5)),
    )
    result = _calculate((opening, buy, sell), 1, 4, facts)
    assert result.days[0].closing_nav == D("1000")
    assert result.days[1].closing_nav == D("1000")
    sold = result.days[2]
    assert sold.pending_receivable == D("480")
    assert sold.closing_nav == D("1400")
    assert sold.book_pnl.realized_pnl == D("200")
    assert sold.book_pnl.unrealized_change == D("300")
    assert sold.book_pnl.cash_fx_effect == D("-100")
    assert sold.book_pnl.economic_pnl == D("400")
    assert sold.book_pnl.component_closure_residual == 0
    settled = result.days[3]
    assert settled.pending_receivable == 0
    assert settled.closing_nav == D("1400")
    assert settled.book_pnl.economic_pnl == 0


@pytest.mark.parametrize(
    (
        "sell_quantity",
        "sell_gross",
        "ending_quantity",
        "ending_cost",
        "realized_pnl",
        "unrealized_ending",
        "unrealized_change",
    ),
    (
        ("10", "150", None, None, "70", "0", "-20"),
        ("4", "60", "6", "48", "28", "42", "22"),
    ),
)
def test_same_day_return_of_capital_precedes_disposition_in_exact_book_pnl(
    sell_quantity: str,
    sell_gross: str,
    ending_quantity: str | None,
    ending_cost: str | None,
    realized_pnl: str,
    unrealized_ending: str,
    unrealized_change: str,
) -> None:
    opening = OpeningPositionEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=D("10"),
        local_cost=D("100"),
        acquisition_local_to_base_rate=D("1"),
        acquisition_fx_lineage=None,
        cost_basis_method=CostBasisMethod.FIFO,
    )
    sell = SellEvent(
        event_id="sell",
        sequence=1,
        lineage=_lineage("sell"),
        trade_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 2),
        position_account_id="position",
        cash_account_id="cash",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=D(sell_quantity),
        price=D("15"),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        gross_amount=D(sell_gross),
        consideration_basis="exact_quantity_price",
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    roc = ReturnOfCapitalEvent(
        event_id="roc",
        sequence=2,
        lineage=_lineage("roc"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 2),
        position_account_id="position",
        cash_account_id="cash",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        gross_amount=D("20"),
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    result = _calculate(
        (opening, sell, roc),
        1,
        2,
        _book(
            instruments=(_instrument("fund", "CNY"),),
            quotes=(
                _quote(1, "fund", "CNY", "12"),
                _quote(2, "fund", "CNY", "15"),
            ),
        ),
    )

    first, disposition_day = result.days
    assert first.closing_nav == D("120")
    assert disposition_day.closing_nav == D("170")
    assert disposition_day.book_pnl.realized_pnl == D(realized_pnl)
    assert disposition_day.book_pnl.unrealized_beginning == D("20")
    assert disposition_day.book_pnl.unrealized_ending == D(unrealized_ending)
    assert disposition_day.book_pnl.unrealized_change == D(unrealized_change)
    assert disposition_day.book_pnl.economic_pnl == D("50")
    assert disposition_day.book_pnl.component_closure_residual == 0
    if ending_quantity is None:
        assert disposition_day.holdings == ()
    else:
        assert ending_cost is not None
        assert len(disposition_day.holdings) == 1
        holding = disposition_day.holdings[0]
        assert holding.quantity == D(ending_quantity)
        assert holding.cost_basis_local == D(ending_cost)
        assert holding.cost_basis_base == D(ending_cost)


def test_income_accrual_then_settlement_places_fx_effect_in_cash() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cny",
        currency="CNY",
        amount=D("100"),
    )
    income = IncomeEvent(
        event_id="income",
        sequence=1,
        lineage=_lineage("income"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        cash_account_id="usd",
        instrument_id="fund",
        currency="USD",
        base_currency="CNY",
        kind=IncomeKind.DIVIDEND,
        gross_amount=D("10"),
        fees=D("0"),
        taxes=D("0"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("income-fx"),
    )
    result = _calculate(
        (opening, income), 1, 3, _book(fx=(_fx(2, "USD", "7"), _fx(3, "USD", "8")))
    )
    recognition, settlement = result.days[1:]
    assert recognition.accrual_receivable == D("70")
    assert recognition.book_pnl.gross_income == D("70")
    assert recognition.book_pnl.accrual_fx_effect == 0
    assert settlement.accrual_receivable == 0
    assert settlement.settled_cash == D("180")
    assert settlement.book_pnl.economic_pnl == D("10")
    assert settlement.book_pnl.cash_fx_effect == D("10")


def test_sparse_fund_carry_gap_and_flow_break_then_reanchor() -> None:
    opening = OpeningPositionEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=D("10"),
        local_cost=D("100"),
        acquisition_local_to_base_rate=D("1"),
        acquisition_fx_lineage=None,
        cost_basis_method=CostBasisMethod.FIFO,
    )
    flow = ExternalCashFlowEvent(
        event_id="flow",
        sequence=1,
        lineage=_lineage("flow"),
        value_date=date(2026, 7, 4),
        account_id="cash",
        currency="CNY",
        base_currency="CNY",
        amount=D("10"),
        kind=ExternalFlowKind.DEPOSIT,
        timing=ExternalFlowTiming.BEGINNING_OF_DAY,
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    quotes = (
        _quote(1, "fund", "CNY", "10"),
        _quote(2, "fund", "CNY", "10", carried=True),
        _quote(3, "fund", "CNY", "11"),
        _quote(4, "fund", "CNY", "11", carried=True),
        _quote(5, "fund", "CNY", "12"),
        _quote(6, "fund", "CNY", "13"),
    )
    result = _calculate(
        (opening, flow),
        1,
        6,
        _book(instruments=(_instrument("fund", "CNY"),), quotes=quotes),
    )
    assert result.days[1].endpoint_status is ValuationEndpointStatus.CARRY_FORWARD
    assert result.days[1].twr.status is DailyCalculationStatus.NO_NEW_VALUATION
    assert result.days[2].twr.subperiod_twr_method50 == D("0.1")
    assert result.days[3].twr.status is DailyCalculationStatus.BROKEN
    assert result.days[3].twr.next_state.status is TwrWindowStatus.BROKEN
    assert result.days[4].twr.status is DailyCalculationStatus.REANCHORED
    assert result.days[5].twr.status is DailyCalculationStatus.CALCULATED
    with calculation_context():
        expected_return = (D("140") - D("130")) / D("130")
    assert result.days[5].twr.subperiod_twr_method50 == expected_return


def test_bod_inflow_and_eod_outflow_are_performance_neutral() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cash",
        currency="CNY",
        amount=D("100"),
    )
    deposit = ExternalCashFlowEvent(
        event_id="deposit",
        sequence=1,
        lineage=_lineage("deposit"),
        value_date=date(2026, 7, 2),
        account_id="cash",
        currency="CNY",
        base_currency="CNY",
        amount=D("50"),
        kind=ExternalFlowKind.DEPOSIT,
        timing=ExternalFlowTiming.BEGINNING_OF_DAY,
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    withdrawal = ExternalCashFlowEvent(
        event_id="withdrawal",
        sequence=2,
        lineage=_lineage("withdrawal"),
        value_date=date(2026, 7, 3),
        account_id="cash",
        currency="CNY",
        base_currency="CNY",
        amount=D("20"),
        kind=ExternalFlowKind.WITHDRAWAL,
        timing=ExternalFlowTiming.END_OF_DAY,
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    result = _calculate((opening, deposit, withdrawal), 1, 3, _book())
    inflow_day, outflow_day = result.days[1:]
    assert inflow_day.external_flow_in == D("50")
    assert inflow_day.book_pnl.economic_pnl == 0
    assert inflow_day.twr.subperiod_twr_method50 == 0
    assert outflow_day.external_flow_out == D("20")
    assert outflow_day.book_pnl.economic_pnl == 0
    assert outflow_day.twr.subperiod_twr_method50 == 0


def test_missing_quote_nulls_complete_nav_and_does_not_zero_fill() -> None:
    opening = OpeningPositionEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=D("10"),
        local_cost=D("100"),
        acquisition_local_to_base_rate=D("1"),
        acquisition_fx_lineage=None,
        cost_basis_method=CostBasisMethod.FIFO,
    )
    result = _calculate(
        (opening,),
        1,
        2,
        _book(
            instruments=(_instrument("fund", "CNY"),),
            quotes=(_quote(2, "fund", "CNY", "10"),),
        ),
    )
    day = result.days[0]
    assert day.holdings[0].market_value_base is None
    assert day.position_market_value is None
    assert day.closing_nav is None
    assert day.endpoint_status is ValuationEndpointStatus.UNAVAILABLE
    assert day.twr.status is DailyCalculationStatus.BROKEN
    recovered = result.days[1]
    assert recovered.opening_nav is None
    assert recovered.closing_nav == D("100")
    assert recovered.book_pnl.economic_measured is False
    assert recovered.twr.status is DailyCalculationStatus.REANCHORED


def test_missing_fx_preserves_local_value_but_nulls_base_nav() -> None:
    opening = OpeningPositionEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position",
        instrument_id="fund",
        currency="USD",
        base_currency="CNY",
        quantity=D("10"),
        local_cost=D("100"),
        acquisition_local_to_base_rate=D("7"),
        acquisition_fx_lineage=_lineage("acquisition-fx"),
        cost_basis_method=CostBasisMethod.FIFO,
    )
    result = _calculate(
        (opening,),
        1,
        1,
        _book(
            instruments=(_instrument("fund", "USD"),),
            quotes=(_quote(1, "fund", "USD", "12"),),
        ),
    )
    holding = result.days[0].holdings[0]
    assert holding.market_value_local == D("120")
    assert holding.market_value_base is None
    assert result.days[0].position_market_value is None
    assert result.days[0].closing_nav is None


def test_fx_conversion_effect_is_separate_from_cash_retranslation() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cny",
        currency="CNY",
        amount=D("700"),
    )
    conversion = FxConversionEvent(
        event_id="conversion",
        sequence=1,
        lineage=_lineage("conversion"),
        trade_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 2),
        source_account_id="cny",
        target_account_id="usd",
        source_currency="CNY",
        target_currency="USD",
        source_amount=D("700"),
        target_amount=D("100"),
        effective_fx_rate=D("7"),
        rate_convention=FxRateConvention.SOURCE_PER_TARGET,
        base_currency="CNY",
        source_to_base_rate=D("1"),
        target_to_base_rate=D("7.1"),
        source_to_base_lineage=None,
        target_to_base_lineage=_lineage("conversion-target-fx"),
    )
    result = _calculate(
        (opening, conversion),
        1,
        2,
        _book(fx=(_fx(2, "USD", "8"),)),
    )
    day = result.days[1]
    assert day.closing_nav == D("800")
    assert day.book_pnl.economic_pnl == D("100")
    assert day.book_pnl.cash_fx_effect == D("90")
    assert day.book_pnl.fx_conversion_effect == D("10")
    assert day.book_pnl.component_closure_residual == 0


def test_nonzero_book_residual_fails_with_exact_diagnostic() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cash",
        currency="CNY",
        amount=D("100"),
    )
    ledger = replay_ledger_series(
        (opening,), start_date=date(2026, 7, 1), end_date=date(2026, 7, 2)
    )
    second = ledger.snapshots[1]
    corrupted_state = replace(
        second.state,
        daily_totals=(LedgerTotals(currency="CNY", gross_income_base=D("1")),),
    )
    corrupted = replace(
        ledger,
        snapshots=(ledger.snapshots[0], replace(second, state=corrupted_state)),
    )
    with pytest.raises(ValuationClosureError) as exc_info:
        calculate_exact_portfolio_valuation(corrupted, _book(), base_currency="CNY")
    assert exc_info.value.kind == "book_pnl"
    assert exc_info.value.residual == D("-1")
    assert exc_info.value.reason_code is ValuationReasonCode.BOOK_PNL_CLOSURE_FAILED


def test_total_loss_is_exact_minus_one_and_breaks_next_anchor_state() -> None:
    opening = OpeningCashEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        account_id="cash",
        currency="CNY",
        amount=D("100"),
    )
    expense = ExpenseEvent(
        event_id="loss",
        sequence=1,
        lineage=_lineage("loss"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 2),
        cash_account_id="cash",
        instrument_id=None,
        currency="CNY",
        base_currency="CNY",
        kind=ExpenseKind.FEE,
        amount=D("100"),
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
    )
    result = _calculate((opening, expense), 1, 2, _book())
    loss = result.days[1]
    assert loss.closing_nav == 0
    assert loss.book_pnl.economic_pnl == D("-100")
    assert loss.book_pnl.expensed_fees == D("100")
    assert loss.twr.subperiod_twr_method50 == D("-1")
    assert loss.twr.next_state.status is TwrWindowStatus.BROKEN


def test_financial_contracts_reject_float() -> None:
    with pytest.raises(ValuationContractError, match="float"):
        InstrumentValuationFact(
            instrument_id="fund",
            currency="CNY",
            price_unit="per_unit",
            contract_multiplier=1.0,  # type: ignore[arg-type]
            price_factor=D("1"),
            available=True,
        )


def test_holding_product_is_context_independent_and_deterministic() -> None:
    quantity = D("123456789012345678901234567891")
    price = D("987654321098765432109876543219")
    opening = OpeningPositionEvent(
        event_id="opening",
        sequence=0,
        lineage=_lineage("opening"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=quantity,
        local_cost=D("0"),
        acquisition_local_to_base_rate=D("1"),
        acquisition_fx_lineage=None,
        cost_basis_method=CostBasisMethod.FIFO,
    )
    facts = _book(
        instruments=(_instrument("fund", "CNY"),),
        quotes=(_quote(1, "fund", "CNY", str(price)),),
    )
    old_precision = getcontext().prec
    try:
        getcontext().prec = 6
        first = _calculate((opening,), 1, 1, facts)
        getcontext().prec = 17
        second = _calculate((opening,), 1, 1, facts)
    finally:
        getcontext().prec = old_precision
    expected = D(str(int(quantity) * int(price)))
    assert first.days[0].holdings[0].market_value_local == expected
    assert second == first
