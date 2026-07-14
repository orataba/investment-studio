from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import date
from decimal import Decimal, getcontext

import pytest

from portfolio_app.calculations.numeric import (
    exact_decimal_product,
    exact_decimal_sum,
)

from portfolio_app.calculations.portfolio_daily.ledger import (
    BaseCoverageStatus,
    BuyEvent,
    CostBasisMethod,
    DividendReinvestmentEvent,
    ExpenseEvent,
    ExpenseKind,
    ExternalCashFlowEvent,
    ExternalFlowKind,
    ExternalFlowTiming,
    FactLineage,
    FxConversionEvent,
    FxRateConvention,
    IncomeEvent,
    IncomeKind,
    LEDGER_TOTAL_EFFECT_FIELDS,
    LEDGER_TOTAL_FIELDS,
    LedgerContractError,
    LedgerReasonCode,
    LedgerStatus,
    LedgerTotals,
    MaturityRedemptionEvent,
    OpeningPositionEvent,
    PendingComponentKind,
    PositionTransferEvent,
    PriceEvidenceStatus,
    ReturnOfCapitalEvent,
    SellEvent,
    SplitEvent,
    replay_daily_ledger,
    replay_ledger_series,
)
from portfolio_app.calculations.portfolio_daily.ledger_actions import _expand_event
from portfolio_app.calculations.portfolio_daily.ledger_replay import _prepare
from portfolio_app.calculations.portfolio_daily.ledger_runtime import (
    _ActionKind,
    _ActionPhase,
    _PreparedLedger,
)


pytestmark = pytest.mark.no_database


D = Decimal


def _lineage(name: str) -> FactLineage:
    return FactLineage(
        source_record_id=f"record-{name}",
        source_revision_id=f"revision-{name}",
        manifest_fact_key=f"transaction/{name}/current",
    )


def test_ledger_total_mapping_is_complete_public_and_immutable() -> None:
    assert LEDGER_TOTAL_FIELDS == tuple(
        field.name for field in fields(LedgerTotals) if field.name != "currency"
    )
    assert tuple(LEDGER_TOTAL_EFFECT_FIELDS) == LEDGER_TOTAL_FIELDS

    with pytest.raises(TypeError):
        LEDGER_TOTAL_EFFECT_FIELDS["realized_pnl_local"] = (  # type: ignore[index]
            "gross_income_local"
        )


def _opening(
    *,
    event_id: str = "open",
    sequence: int = 0,
    quantity: str = "10",
    local_cost: str = "100",
    base_cost: str | None = "700",
    method: CostBasisMethod = CostBasisMethod.FIFO,
) -> OpeningPositionEvent:
    local = D(local_cost)
    base = None if base_cost is None else D(base_cost)
    rate = None if base is None else base / local
    return OpeningPositionEvent(
        event_id=event_id,
        sequence=sequence,
        lineage=_lineage(event_id),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2026, 7, 1),
        account_id="position-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        quantity=D(quantity),
        local_cost=local,
        acquisition_local_to_base_rate=rate,
        acquisition_fx_lineage=(
            None if rate is None else _lineage(f"{event_id}-acquisition-fx")
        ),
        cost_basis_method=method,
    )


def _buy(
    *,
    event_id: str,
    sequence: int,
    day: int,
    quantity: str,
    price: str,
    gross: str,
    rate: str | None,
    fees: str = "0",
    method: CostBasisMethod = CostBasisMethod.FIFO,
    multiplier: str = "1",
    factor: str = "1",
    consideration_basis: str,
) -> BuyEvent:
    return BuyEvent(
        event_id=event_id,
        sequence=sequence,
        lineage=_lineage(event_id),
        trade_date=date(2026, 7, day),
        settlement_date=date(2026, 7, day + 1),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        quantity=D(quantity),
        price=D(price),
        contract_multiplier=D(multiplier),
        price_factor=D(factor),
        price_unit="currency_per_unit",
        gross_amount=D(gross),
        consideration_basis=consideration_basis,
        local_to_base_rate=None if rate is None else D(rate),
        local_to_base_lineage=(None if rate is None else _lineage(f"{event_id}-fx")),
        fees=D(fees),
        cost_basis_method=method,
    )


def _sell(
    *,
    event_id: str,
    sequence: int,
    day: int,
    quantity: str,
    price: str,
    gross: str,
    rate: str | None,
    fees: str = "0",
    consideration_basis: str,
) -> SellEvent:
    return SellEvent(
        event_id=event_id,
        sequence=sequence,
        lineage=_lineage(event_id),
        trade_date=date(2026, 7, day),
        settlement_date=date(2026, 7, day + 1),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        quantity=D(quantity),
        price=D(price),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        gross_amount=D(gross),
        consideration_basis=consideration_basis,
        local_to_base_rate=None if rate is None else D(rate),
        local_to_base_lineage=(None if rate is None else _lineage(f"{event_id}-fx")),
        fees=D(fees),
    )


def _return_of_capital(
    *,
    event_id: str = "roc",
    sequence: int,
    day: int,
    gross: str,
    rate: str | None,
) -> ReturnOfCapitalEvent:
    return ReturnOfCapitalEvent(
        event_id=event_id,
        sequence=sequence,
        lineage=_lineage(event_id),
        recognition_date=date(2026, 7, day),
        settlement_date=date(2026, 7, day + 1),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        gross_amount=D(gross),
        local_to_base_rate=None if rate is None else D(rate),
        local_to_base_lineage=(None if rate is None else _lineage(f"{event_id}-fx")),
    )


def test_intraday_action_phase_contract_keeps_entitlements_before_trades() -> None:
    dividend = IncomeEvent(
        event_id="dividend",
        sequence=1,
        lineage=_lineage("dividend"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        kind=IncomeKind.DIVIDEND,
        gross_amount=D("10"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("dividend-fx"),
    )
    interest = replace(
        dividend,
        event_id="interest",
        sequence=2,
        lineage=_lineage("interest"),
        instrument_id=None,
        kind=IncomeKind.INTEREST,
    )
    roc = _return_of_capital(sequence=3, day=2, gross="20", rate="7")
    maturity = MaturityRedemptionEvent(
        event_id="maturity",
        sequence=4,
        lineage=_lineage("maturity"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        quantity=D("1"),
        gross_amount=D("10"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("maturity-fx"),
    )
    reinvestment = DividendReinvestmentEvent(
        event_id="reinvestment",
        sequence=5,
        lineage=_lineage("reinvestment"),
        recognition_date=date(2026, 7, 2),
        position_account_id="position-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        gross_income=D("10"),
        quantity=D("1"),
        price=D("10"),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        consideration_basis="exact_quantity_price",
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("reinvestment-fx"),
    )
    expense = ExpenseEvent(
        event_id="expense",
        sequence=6,
        lineage=_lineage("expense"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        kind=ExpenseKind.FEE,
        amount=D("1"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("expense-fx"),
    )
    fx_trade = FxConversionEvent(
        event_id="fx-trade",
        sequence=7,
        lineage=_lineage("fx-trade"),
        trade_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        source_account_id="cny-cash",
        target_account_id="usd-cash",
        source_currency="CNY",
        target_currency="USD",
        source_amount=D("700"),
        target_amount=D("100"),
        effective_fx_rate=D("7"),
        rate_convention=FxRateConvention.SOURCE_PER_TARGET,
        base_currency="CNY",
        source_to_base_rate=D("1"),
        target_to_base_rate=D("7"),
        source_to_base_lineage=None,
        target_to_base_lineage=_lineage("fx-trade-target-fx"),
    )
    buy = _buy(
        event_id="buy",
        sequence=8,
        day=2,
        quantity="1",
        price="10",
        gross="10",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    sell = _sell(
        event_id="sell",
        sequence=9,
        day=2,
        quantity="1",
        price="10",
        gross="10",
        rate="7",
        consideration_basis="exact_quantity_price",
    )

    assert (
        _ActionPhase.CORPORATE_ACTION
        < _ActionPhase.ENTITLEMENT
        < _ActionPhase.RECOGNITION
        < _ActionPhase.POSITION
        < _ActionPhase.TRANSFER
        < _ActionPhase.SETTLEMENT
    )
    expected_phases = {
        dividend.event_id: _ActionPhase.ENTITLEMENT,
        interest.event_id: _ActionPhase.RECOGNITION,
        roc.event_id: _ActionPhase.ENTITLEMENT,
        maturity.event_id: _ActionPhase.RECOGNITION,
        reinvestment.event_id: _ActionPhase.ENTITLEMENT,
        expense.event_id: _ActionPhase.RECOGNITION,
        fx_trade.event_id: _ActionPhase.POSITION,
        buy.event_id: _ActionPhase.POSITION,
        sell.event_id: _ActionPhase.POSITION,
    }
    for event in (
        dividend,
        interest,
        roc,
        maturity,
        reinvestment,
        expense,
        fx_trade,
        buy,
        sell,
    ):
        assert _expand_event(event)[0].phase is expected_phases[event.event_id]

    prepared = _prepare(
        (
            sell,
            reinvestment,
            fx_trade,
            expense,
            maturity,
            roc,
            buy,
            interest,
            dividend,
        )
    )
    assert isinstance(prepared, _PreparedLedger)
    same_day_trades = tuple(
        action
        for action in prepared.actions
        if action.effective_date == date(2026, 7, 2)
        and action.phase is _ActionPhase.POSITION
    )
    assert tuple(action.kind for action in same_day_trades) == (
        _ActionKind.FX_RECOGNITION,
        _ActionKind.BUY_TRADE,
        _ActionKind.SELL_TRADE,
    )
    assert tuple(action.sequence for action in same_day_trades) == (7, 8, 9)


def test_trade_fact_uses_quantity_price_multiplier_and_price_factor() -> None:
    event = _buy(
        event_id="bond-buy",
        sequence=1,
        day=1,
        quantity="1000",
        price="98.5",
        multiplier="1",
        factor="0.01",
        gross="985",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    assert event.gross_amount == D("985")

    with pytest.raises(LedgerContractError) as exc_info:
        _buy(
            event_id="bad-bond-buy",
            sequence=2,
            day=1,
            quantity="1000",
            price="98.5",
            multiplier="1",
            factor="0.01",
            gross="98500",
            rate="7",
            consideration_basis="exact_quantity_price",
        )
    assert exc_info.value.reason_code is LedgerReasonCode.GROSS_AMOUNT_MISMATCH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 1.0),
        ("price", 10.0),
        ("contract_multiplier", 1.0),
        ("price_factor", 1.0),
        ("gross_amount", 10.0),
        ("local_to_base_rate", 7.0),
    ],
)
def test_every_financial_fact_rejects_binary_float(field: str, value: float) -> None:
    values: dict[str, object] = {
        "event_id": "float",
        "sequence": 1,
        "lineage": _lineage("float"),
        "trade_date": date(2026, 7, 1),
        "settlement_date": date(2026, 7, 2),
        "position_account_id": "position-account",
        "cash_account_id": "cash-account",
        "instrument_id": "fund-a",
        "currency": "USD",
        "base_currency": "CNY",
        "quantity": D("1"),
        "price": D("10"),
        "contract_multiplier": D("1"),
        "price_factor": D("1"),
        "price_unit": "currency_per_unit",
        "gross_amount": D("10"),
        "consideration_basis": "exact_quantity_price",
        "local_to_base_rate": D("7"),
        "local_to_base_lineage": _lineage("float-fx"),
    }
    values[field] = value
    with pytest.raises(LedgerContractError, match="float"):
        BuyEvent(**values)  # type: ignore[arg-type]


def test_fifo_disposition_releases_local_and_historical_base_cost_from_same_lot() -> (
    None
):
    buy = _buy(
        event_id="buy",
        sequence=1,
        day=1,
        quantity="10",
        price="10",
        gross="100",
        fees="2",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    sell = _sell(
        event_id="sell",
        sequence=2,
        day=3,
        quantity="4",
        price="15",
        gross="60",
        fees="1",
        rate="8",
        consideration_basis="exact_quantity_price",
    )
    result = replay_daily_ledger((buy, sell), as_of_date=date(2026, 7, 3))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    position = result.state.positions[0]
    disposition = result.state.dispositions[0]
    assert position.quantity == D("6")
    assert position.local_cost == D("61.2")
    assert position.historical_base_cost == D("428.4")
    assert disposition.released_local_cost == D("40.8")
    assert disposition.released_historical_base_cost == D("285.6")
    assert disposition.allocated_local_net_proceeds == D("59")
    assert disposition.allocated_base_net_proceeds == D("472")
    assert disposition.realized_pnl_local == D("18.2")
    assert disposition.realized_pnl_base == D("186.4")
    assert disposition.source_lineage == buy.lineage
    assert disposition.disposition_lineage == sell.lineage


def test_recurring_lot_allocation_and_cross_fx_translation_both_close_exactly() -> None:
    buys = tuple(
        _buy(
            event_id=f"buy-{index}",
            sequence=index,
            day=index,
            quantity="1",
            price="1",
            gross="1",
            rate="7",
            consideration_basis="exact_quantity_price",
        )
        for index in range(1, 4)
    )
    fx_rate = D("7.123456789012345678")
    sell = _sell(
        event_id="sell-recurring",
        sequence=4,
        day=5,
        quantity="3",
        price="33.333333333333",
        gross="100",
        rate=str(fx_rate),
        consideration_basis="source_reported",
    )

    original_precision = getcontext().prec
    try:
        getcontext().prec = 6
        result = replay_daily_ledger((*buys, sell), as_of_date=date(2026, 7, 5))
    finally:
        getcontext().prec = original_precision

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    dispositions = result.state.dispositions
    assert len(dispositions) == 3
    assert exact_decimal_sum(
        tuple(item.allocated_local_net_proceeds for item in dispositions)
    ) == D("100")
    assert exact_decimal_sum(
        tuple(item.allocated_base_net_proceeds for item in dispositions)  # type: ignore[arg-type]
    ) == exact_decimal_product(D("100"), fx_rate)
    for item in dispositions:
        assert item.allocated_base_net_proceeds == exact_decimal_product(
            item.allocated_local_net_proceeds,
            fx_rate,
        )


def test_effect_and_cash_accumulators_do_not_truncate_beyond_context_precision() -> (
    None
):
    rate = D("99999999999999999999999999999999.123456789012345678")
    first_amount = D("999999999999999999999999999999999999999999.12345678")
    second_amount = D("0.00000001")
    flows = tuple(
        ExternalCashFlowEvent(
            event_id=f"deposit-{index}",
            sequence=index,
            lineage=_lineage(f"deposit-{index}"),
            value_date=date(2026, 7, 1),
            account_id="cash-account",
            currency="USD",
            base_currency="CNY",
            kind=ExternalFlowKind.DEPOSIT,
            timing=ExternalFlowTiming.BEGINNING_OF_DAY,
            amount=amount,
            local_to_base_rate=rate,
            local_to_base_lineage=_lineage(f"deposit-{index}-fx"),
        )
        for index, amount in enumerate((first_amount, second_amount), start=1)
    )

    original_precision = getcontext().prec
    try:
        getcontext().prec = 6
        result = replay_daily_ledger(flows, as_of_date=date(2026, 7, 1))
    finally:
        getcontext().prec = original_precision

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    expected_local = exact_decimal_sum((first_amount, second_amount))
    expected_base = exact_decimal_sum(
        tuple(
            exact_decimal_product(amount, rate)
            for amount in (first_amount, second_amount)
        )
    )
    assert result.state.cash_balances[0].amount == expected_local
    assert result.state.daily_totals[0].external_flow_in_local == expected_local
    assert result.state.daily_totals[0].external_flow_in_base == expected_base


def test_opening_historical_base_cost_is_derived_from_acquisition_date_fx() -> None:
    opening = OpeningPositionEvent(
        event_id="open-fx",
        sequence=0,
        lineage=_lineage("open-fx"),
        effective_date=date(2026, 7, 1),
        acquisition_date=date(2024, 2, 9),
        account_id="position-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        quantity=D("3"),
        local_cost=D("10.12345678"),
        acquisition_local_to_base_rate=D("7.123456789012345678"),
        acquisition_fx_lineage=_lineage("open-fx-rate"),
        cost_basis_method=CostBasisMethod.FIFO,
    )
    result = replay_daily_ledger((opening,), as_of_date=date(2026, 7, 1))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    expected = D("10.12345678") * D("7.123456789012345678")
    lot = result.state.positions[0].lots[0]
    assert lot.historical_base_cost == expected
    assert lot.acquisition_fx_lineage == opening.acquisition_fx_lineage


def test_moving_average_keeps_lineage_and_releases_the_exact_pool_average() -> None:
    first = _buy(
        event_id="buy-1",
        sequence=1,
        day=1,
        quantity="10",
        price="10",
        gross="100",
        rate="7",
        method=CostBasisMethod.MOVING_AVERAGE,
        consideration_basis="exact_quantity_price",
    )
    second = _buy(
        event_id="buy-2",
        sequence=2,
        day=3,
        quantity="10",
        price="20",
        gross="200",
        rate="8",
        method=CostBasisMethod.MOVING_AVERAGE,
        consideration_basis="exact_quantity_price",
    )
    sell = _sell(
        event_id="sell",
        sequence=3,
        day=5,
        quantity="5",
        price="10",
        gross="50",
        rate="9",
        consideration_basis="exact_quantity_price",
    )
    result = replay_daily_ledger((first, second, sell), as_of_date=date(2026, 7, 5))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    disposition = result.state.dispositions[0]
    assert disposition.lot_id == "buy-1"
    assert disposition.released_local_cost == D("75")
    assert disposition.released_historical_base_cost == D("575")
    assert {item.manifest_fact_key for item in disposition.source_cost_lineages} == {
        first.lineage.manifest_fact_key,
        second.lineage.manifest_fact_key,
    }
    assert result.state.positions[0].local_cost == D("225")
    assert result.state.positions[0].historical_base_cost == D("1725")


def test_pending_trade_income_fee_and_tax_are_strongly_typed() -> None:
    buy = _buy(
        event_id="buy",
        sequence=1,
        day=1,
        quantity="1",
        price="10",
        gross="10",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    income = IncomeEvent(
        event_id="income",
        sequence=2,
        lineage=_lineage("income"),
        recognition_date=date(2026, 7, 1),
        settlement_date=date(2026, 7, 3),
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        kind=IncomeKind.DIVIDEND,
        gross_amount=D("5"),
        fees=D("0.5"),
        taxes=D("1"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("income-fx"),
    )
    result = replay_daily_ledger((buy, income), as_of_date=date(2026, 7, 1))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert {item.component_kind for item in result.state.pending_settlements} == {
        PendingComponentKind.TRADE_SETTLEMENT,
        PendingComponentKind.INCOME_ACCRUAL,
        PendingComponentKind.FEE_ACCRUAL,
        PendingComponentKind.TAX_ACCRUAL,
    }
    assert all(
        item.lineage.manifest_fact_key for item in result.state.pending_settlements
    )


def test_reinvestment_without_price_uses_authoritative_gross_and_marks_price_unavailable() -> (
    None
):
    event = DividendReinvestmentEvent(
        event_id="reinvest",
        sequence=1,
        lineage=_lineage("reinvest"),
        recognition_date=date(2026, 7, 1),
        position_account_id="position-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        gross_income=D("123.45678901"),
        quantity=D("7.123456789012"),
        price=None,
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        consideration_basis="source_reported",
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("reinvest-fx"),
    )
    result = replay_daily_ledger((event,), as_of_date=date(2026, 7, 1))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert result.state.positions[0].local_cost == D("123.45678901")
    evidence = result.state.dividend_reinvestments[0]
    assert evidence.price is None
    assert evidence.price_status is PriceEvidenceStatus.UNAVAILABLE


def test_reinvestment_exact_quantity_price_basis_rejects_non_closing_terms() -> None:
    with pytest.raises(LedgerContractError) as exc_info:
        DividendReinvestmentEvent(
            event_id="bad-reinvest",
            sequence=1,
            lineage=_lineage("bad-reinvest"),
            recognition_date=date(2026, 7, 1),
            position_account_id="position-account",
            instrument_id="fund-a",
            currency="USD",
            base_currency="CNY",
            gross_income=D("11"),
            quantity=D("2"),
            price=D("5"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("7"),
            local_to_base_lineage=_lineage("bad-reinvest-fx"),
        )
    assert exc_info.value.reason_code is LedgerReasonCode.GROSS_AMOUNT_MISMATCH


def test_fx_conversion_local_legs_do_not_depend_on_base_fx_coverage() -> None:
    event = FxConversionEvent(
        event_id="fx",
        sequence=1,
        lineage=_lineage("fx"),
        trade_date=date(2026, 7, 1),
        settlement_date=date(2026, 7, 2),
        source_account_id="usd-cash",
        target_account_id="hkd-cash",
        source_currency="USD",
        target_currency="HKD",
        source_amount=D("100"),
        target_amount=D("780"),
        effective_fx_rate=D("7.8"),
        rate_convention=FxRateConvention.TARGET_PER_SOURCE,
        base_currency="CNY",
        source_to_base_rate=None,
        target_to_base_rate=None,
        source_to_base_lineage=None,
        target_to_base_lineage=None,
    )
    recognition = replay_daily_ledger((event,), as_of_date=date(2026, 7, 1))
    settlement = replay_daily_ledger((event,), as_of_date=date(2026, 7, 2))

    assert recognition.status is LedgerStatus.SUCCEEDED
    assert recognition.state is not None
    assert (
        recognition.state.historical_base_coverage_status
        is BaseCoverageStatus.UNAVAILABLE
    )
    assert {item.local_amount for item in recognition.state.pending_settlements} == {
        D("-100"),
        D("780"),
    }
    assert settlement.status is LedgerStatus.SUCCEEDED
    assert settlement.state is not None
    assert {
        (item.currency, item.amount) for item in settlement.state.cash_balances
    } == {
        ("USD", D("-100")),
        ("HKD", D("780")),
    }
    evidence = settlement.state.fx_conversions[0]
    assert evidence.base_economic_difference is None


def test_fx_conversion_allows_one_known_base_leg_without_blocking_local_replay() -> (
    None
):
    event = FxConversionEvent(
        event_id="fx-partial",
        sequence=1,
        lineage=_lineage("fx-partial"),
        trade_date=date(2026, 7, 1),
        settlement_date=date(2026, 7, 1),
        source_account_id="cny-cash",
        target_account_id="usd-cash",
        source_currency="CNY",
        target_currency="USD",
        source_amount=D("700"),
        target_amount=D("100"),
        effective_fx_rate=D("7"),
        rate_convention=FxRateConvention.SOURCE_PER_TARGET,
        base_currency="CNY",
        source_to_base_rate=D("1"),
        target_to_base_rate=None,
        source_to_base_lineage=None,
        target_to_base_lineage=None,
    )
    result = replay_daily_ledger((event,), as_of_date=date(2026, 7, 1))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert {(row.currency, row.amount) for row in result.state.cash_balances} == {
        ("CNY", D("-700")),
        ("USD", D("100")),
    }
    assert result.state.fx_conversions[0].source_base_value == D("700")
    assert result.state.fx_conversions[0].target_base_value is None


def test_return_of_capital_caps_local_and_base_independently_and_realizes_excess() -> (
    None
):
    opening = _opening(local_cost="50", base_cost="100")
    roc = ReturnOfCapitalEvent(
        event_id="roc",
        sequence=1,
        lineage=_lineage("roc"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        gross_amount=D("60"),
        local_to_base_rate=D("3"),
        local_to_base_lineage=_lineage("roc-fx"),
    )
    result = replay_daily_ledger((opening, roc), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    position = result.state.positions[0]
    totals = result.state.daily_totals[0]
    assert position.local_cost == D("0")
    assert position.historical_base_cost == D("0")
    assert totals.realized_pnl_local == D("10")
    assert totals.realized_pnl_base == D("80")
    assert totals.return_of_capital_local == D("60")
    assert totals.return_of_capital_base == D("180")


def test_same_day_return_of_capital_precedes_full_disposition() -> None:
    opening = _opening(local_cost="100", base_cost="700")
    sell = _sell(
        event_id="full-sell",
        sequence=1,
        day=2,
        quantity="10",
        price="15",
        gross="150",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    roc = _return_of_capital(sequence=2, day=2, gross="20", rate="7")

    result = replay_daily_ledger(
        (opening, sell, roc),
        as_of_date=date(2026, 7, 2),
    )

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert result.state.positions == ()
    assert len(result.state.dispositions) == 1
    disposition = result.state.dispositions[0]
    assert disposition.released_local_cost == D("80")
    assert disposition.released_historical_base_cost == D("560")
    assert disposition.realized_pnl_local == D("70")
    assert disposition.realized_pnl_base == D("490")
    totals = result.state.daily_totals[0]
    assert totals.return_of_capital_local == D("20")
    assert totals.return_of_capital_base == D("140")
    assert totals.realized_pnl_local == D("70")
    assert totals.realized_pnl_base == D("490")


def test_same_day_return_of_capital_reduces_cost_before_partial_disposition() -> None:
    opening = _opening(local_cost="100", base_cost="700")
    sell = _sell(
        event_id="partial-sell",
        sequence=1,
        day=2,
        quantity="4",
        price="15",
        gross="60",
        rate="7",
        consideration_basis="exact_quantity_price",
    )
    roc = _return_of_capital(sequence=2, day=2, gross="20", rate="7")

    result = replay_daily_ledger(
        (opening, sell, roc),
        as_of_date=date(2026, 7, 2),
    )

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert len(result.state.positions) == 1
    position = result.state.positions[0]
    assert position.quantity == D("6")
    assert position.local_cost == D("48")
    assert position.historical_base_cost == D("336")
    assert len(result.state.dispositions) == 1
    disposition = result.state.dispositions[0]
    assert disposition.released_local_cost == D("32")
    assert disposition.released_historical_base_cost == D("224")
    assert disposition.realized_pnl_local == D("28")
    assert disposition.realized_pnl_base == D("196")
    totals = result.state.daily_totals[0]
    assert totals.return_of_capital_local == D("20")
    assert totals.return_of_capital_base == D("140")
    assert totals.realized_pnl_local == D("28")
    assert totals.realized_pnl_base == D("196")


def test_paired_internal_transfer_is_applied_once_and_preserves_both_revisions() -> (
    None
):
    opening = _opening()
    transfer_in_lineage = _lineage("transfer-in")
    transfer = PositionTransferEvent(
        event_id="transfer-pair",
        sequence=1,
        lineage=_lineage("transfer-out"),
        counterparty_lineage=transfer_in_lineage,
        effective_date=date(2026, 7, 2),
        source_account_id="position-account",
        destination_account_id="destination-account",
        instrument_id="fund-a",
        currency="USD",
        quantity=D("4"),
        declared_local_cost=D("40"),
    )
    result = replay_daily_ledger((opening, transfer), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    positions = {row.account_id: row for row in result.state.positions}
    assert positions["position-account"].quantity == D("6")
    assert positions["destination-account"].quantity == D("4")
    assert sum((row.quantity for row in positions.values()), D("0")) == D("10")
    destination_lot = positions["destination-account"].lots[0]
    assert destination_lot.lineage == opening.lineage
    assert destination_lot.custody_lineage == transfer_in_lineage
    transfer_effects = [
        effect for effect in result.effects if effect.event_id == "transfer-pair"
    ]
    assert {effect.lineage for effect in transfer_effects} == {
        transfer.lineage,
        transfer_in_lineage,
    }

    reverse_duplicate = PositionTransferEvent(
        event_id="transfer-pair-duplicate",
        sequence=2,
        lineage=transfer_in_lineage,
        counterparty_lineage=transfer.lineage,
        effective_date=date(2026, 7, 2),
        source_account_id="position-account",
        destination_account_id="destination-account",
        instrument_id="fund-a",
        currency="USD",
        quantity=D("4"),
        declared_local_cost=D("40"),
    )
    rejected = replay_daily_ledger(
        (opening, transfer, reverse_duplicate),
        as_of_date=date(2026, 7, 2),
    )
    assert rejected.status is LedgerStatus.FAILED
    assert rejected.reason_codes == (LedgerReasonCode.DUPLICATE_SOURCE_FACT,)


def test_transfer_then_sell_keeps_cost_origin_and_current_custody_lineage() -> None:
    opening = _opening()
    transfer_in_lineage = _lineage("transfer-in")
    transfer = PositionTransferEvent(
        event_id="transfer-pair",
        sequence=1,
        lineage=_lineage("transfer-out"),
        counterparty_lineage=transfer_in_lineage,
        effective_date=date(2026, 7, 2),
        source_account_id="position-account",
        destination_account_id="destination-account",
        instrument_id="fund-a",
        currency="USD",
        quantity=D("4"),
        declared_local_cost=D("40"),
    )
    sell = replace(
        _sell(
            event_id="sell-after-transfer",
            sequence=2,
            day=3,
            quantity="2",
            price="15",
            gross="30",
            rate="7",
            consideration_basis="exact_quantity_price",
        ),
        position_account_id="destination-account",
        cash_account_id="destination-cash-account",
    )

    result = replay_daily_ledger(
        (opening, transfer, sell),
        as_of_date=date(2026, 7, 3),
    )

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    destination = next(
        row for row in result.state.positions if row.account_id == "destination-account"
    )
    assert destination.lots[0].lineage == opening.lineage
    assert destination.lots[0].custody_lineage == transfer_in_lineage
    disposition = result.state.dispositions[0]
    assert disposition.source_lineage == opening.lineage
    assert disposition.source_custody_lineage == transfer_in_lineage
    assert disposition.disposition_lineage == sell.lineage


def test_instrument_scope_split_applies_once_to_each_live_account_in_stable_order() -> (
    None
):
    first = _opening(event_id="open-a", sequence=0)
    second = replace(
        _opening(
            event_id="open-b",
            sequence=1,
            quantity="3",
            local_cost="60",
            base_cost="420",
        ),
        account_id="another-account",
    )
    split = SplitEvent(
        event_id="split",
        sequence=2,
        lineage=_lineage("split"),
        effective_date=date(2026, 7, 2),
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        ratio_numerator=D("2"),
        ratio_denominator=D("1"),
    )

    result = replay_daily_ledger((first, second, split), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    positions = {item.account_id: item for item in result.state.positions}
    assert positions["position-account"].quantity == D("20")
    assert positions["another-account"].quantity == D("6")
    assert positions["position-account"].local_cost == D("100")
    assert positions["another-account"].local_cost == D("60")
    split_effects = [item for item in result.effects if item.event_id == "split"]
    assert [item.account_id for item in split_effects] == [
        "another-account",
        "position-account",
    ]


def test_exact_split_quantity_does_not_round_a_coefficient_larger_than_28_digits() -> (
    None
):
    quantity = D("12345678901234567890123456.123456789012")
    opening = _opening(quantity=str(quantity), local_cost="100")
    split = SplitEvent(
        event_id="large-split",
        sequence=1,
        lineage=_lineage("large-split"),
        effective_date=date(2026, 7, 2),
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        ratio_numerator=D("2"),
        ratio_denominator=D("1"),
    )

    result = replay_daily_ledger((opening, split), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert result.state.positions[0].quantity == D(
        "24691357802469135780246912.246913578024"
    )


def test_exact_split_fails_instead_of_rounding_a_nonterminating_quantity() -> None:
    split = SplitEvent(
        event_id="third-split",
        sequence=1,
        lineage=_lineage("third-split"),
        effective_date=date(2026, 7, 2),
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        ratio_numerator=D("1"),
        ratio_denominator=D("3"),
    )

    result = replay_daily_ledger((_opening(), split), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.FAILED
    assert result.reason_codes == (LedgerReasonCode.NUMERIC_FAILURE,)
    assert result.failed_event_id == "third-split"


def test_missing_roc_fx_preserves_local_ledger_and_invalidates_base_cost_only() -> None:
    opening = _opening(local_cost="50", base_cost="100")
    roc = ReturnOfCapitalEvent(
        event_id="roc",
        sequence=1,
        lineage=_lineage("roc"),
        recognition_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        position_account_id="position-account",
        cash_account_id="cash-account",
        instrument_id="fund-a",
        currency="USD",
        base_currency="CNY",
        gross_amount=D("20"),
        local_to_base_rate=None,
        local_to_base_lineage=None,
    )
    result = replay_daily_ledger((opening, roc), as_of_date=date(2026, 7, 2))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    assert result.state.positions[0].local_cost == D("30")
    assert result.state.positions[0].historical_base_cost is None
    assert result.state.daily_totals[0].realized_pnl_local == D("0")
    assert result.state.daily_totals[0].realized_pnl_base is None
    assert (
        result.state.historical_base_coverage_status is BaseCoverageStatus.UNAVAILABLE
    )


def test_external_flow_contract_enforces_bod_inflow_and_eod_outflow() -> None:
    with pytest.raises(LedgerContractError, match="beginning_of_day"):
        ExternalCashFlowEvent(
            event_id="bad-flow",
            sequence=1,
            lineage=_lineage("bad-flow"),
            value_date=date(2026, 7, 1),
            account_id="cash-account",
            currency="USD",
            base_currency="CNY",
            kind=ExternalFlowKind.DEPOSIT,
            timing=ExternalFlowTiming.END_OF_DAY,
            amount=D("100"),
            local_to_base_rate=D("7"),
            local_to_base_lineage=_lineage("bad-flow-fx"),
        )

    deposit = ExternalCashFlowEvent(
        event_id="deposit",
        sequence=1,
        lineage=_lineage("deposit"),
        value_date=date(2026, 7, 1),
        account_id="cash-account",
        currency="USD",
        base_currency="CNY",
        kind=ExternalFlowKind.DEPOSIT,
        timing=ExternalFlowTiming.BEGINNING_OF_DAY,
        amount=D("100"),
        local_to_base_rate=D("7"),
        local_to_base_lineage=_lineage("deposit-fx"),
    )
    withdrawal = ExternalCashFlowEvent(
        event_id="withdrawal",
        sequence=2,
        lineage=_lineage("withdrawal"),
        value_date=date(2026, 7, 1),
        account_id="cash-account",
        currency="USD",
        base_currency="CNY",
        kind=ExternalFlowKind.WITHDRAWAL,
        timing=ExternalFlowTiming.END_OF_DAY,
        amount=D("30"),
        local_to_base_rate=D("8"),
        local_to_base_lineage=_lineage("withdrawal-fx"),
    )
    result = replay_daily_ledger((withdrawal, deposit), as_of_date=date(2026, 7, 1))

    assert result.status is LedgerStatus.SUCCEEDED
    assert result.state is not None
    totals = result.state.daily_totals[0]
    assert totals.external_flow_in_local == D("100")
    assert totals.external_flow_in_base == D("700")
    assert totals.external_flow_out_local == D("30")
    assert totals.external_flow_out_base == D("240")
    assert result.state.cash_balances[0].amount == D("70")


def test_series_engine_is_single_pass_deterministic_and_daily_totals_reset() -> None:
    buy = _buy(
        event_id="buy",
        sequence=1,
        day=1,
        quantity="2",
        price="10",
        gross="20",
        rate="7",
        fees="1",
        consideration_basis="exact_quantity_price",
    )
    first = replay_ledger_series(
        (buy,),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
    )
    second = replay_ledger_series(
        (buy,),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
    )

    assert first == second
    assert first.status is LedgerStatus.SUCCEEDED
    assert len(first.snapshots) == 3
    assert first.snapshots[0].state.daily_totals[0].capitalized_fees_local == 1
    assert first.snapshots[1].state.daily_totals == ()
    assert first.snapshots[2].state.daily_totals == ()


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if is_dataclass(value):
        for field in fields(value):
            _assert_no_float(getattr(value, field.name))
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _assert_no_float(item)


def test_all_public_ledger_outputs_remain_decimal_not_float() -> None:
    result = replay_daily_ledger((_opening(),), as_of_date=date(2026, 7, 1))
    assert result.status is LedgerStatus.SUCCEEDED
    _assert_no_float(result)
