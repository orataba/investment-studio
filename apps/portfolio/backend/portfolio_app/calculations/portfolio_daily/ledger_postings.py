"""Accounting postings for each exact Portfolio Daily ledger event."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

from portfolio_app.calculations.numeric import (
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    BuyEvent,
    CashTransferEvent,
    DividendReinvestmentEvent,
    ExpenseEvent,
    ExpenseKind,
    ExternalCashFlowEvent,
    ExternalFlowKind,
    FxConversionEvent,
    IncomeEvent,
    LedgerContractError,
    LedgerEvent,
    LedgerFactKind,
    LedgerReasonCode,
    MaturityRedemptionEvent,
    OpeningCashEvent,
    OpeningPositionEvent,
    PositionTransferEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    SplitEvent,
    quantize_ledger_fact,
    require_fact,
)
from portfolio_app.calculations.portfolio_daily.ledger_runtime import (
    _ActionKind,
    _LedgerBuilder,
    _ScheduledAction,
    _add_position_lot,
    _base_value,
    _cash_key,
    _make_dispositions,
    _pending_id,
    _position_from_lots,
    _position_key,
    _reduce_position_cost,
    _remove_position_lots,
    _sum_optional,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    DividendReinvestmentEvidence,
    FxConversionEvidence,
    LedgerEffect,
    LedgerEffectKind,
    PendingComponentKind,
    PendingSettlement,
    PriceEvidenceStatus,
    PositionLot,
)

def _pending(
    *,
    event: LedgerEvent,
    component: str,
    component_kind: PendingComponentKind,
    recognition_date: date,
    settlement_date: date,
    account_id: str,
    currency: str,
    base_currency: str,
    local_amount: Decimal,
    local_to_base_rate: Decimal | None,
    instrument_id: str | None,
) -> PendingSettlement:
    return PendingSettlement(
        settlement_id=_pending_id(event.event_id, component),
        component_kind=component_kind,
        recognition_date=recognition_date,
        settlement_date=settlement_date,
        account_id=account_id,
        currency=currency,
        base_currency=base_currency,
        local_amount=local_amount,
        recognition_base_amount=_base_value(local_amount, local_to_base_rate),
        instrument_id=instrument_id,
        lineage=event.lineage,
        event_id=event.event_id,
    )


def _emit_pending_recognition(
    builder: _LedgerBuilder,
    *,
    pending: PendingSettlement,
    kind: LedgerEffectKind,
    economic_fields: dict[str, Decimal | None] | None = None,
) -> None:
    builder.add_pending(pending)
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=pending.event_id,
            kind=kind,
            account_id=pending.account_id,
            currency=pending.currency,
            base_currency=pending.base_currency,
            lineage=pending.lineage,
            instrument_id=pending.instrument_id,
            pending_component_kind=pending.component_kind,
            pending_delta_local=pending.local_amount,
            pending_delta_base=pending.recognition_base_amount,
            **(economic_fields or {}),
        )
    )


def _apply_opening_cash(
    builder: _LedgerBuilder,
    event: OpeningCashEvent,
) -> None:
    key = _cash_key(event.account_id, event.currency)
    if key in builder.cash:
        builder.fail(
            LedgerReasonCode.CASH_BALANCE_ALREADY_EXISTS,
            event_id=event.event_id,
            message=f"opening cash balance {key} already exists",
        )
    builder.adjust_cash(event.account_id, event.currency, event.amount)
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.OPENING_CASH,
            account_id=event.account_id,
            currency=event.currency,
            base_currency=event.currency,
            lineage=event.lineage,
            cash_delta_local=event.amount,
            recognition_cash_delta_base=event.amount,
        )
    )


def _apply_opening_position(
    builder: _LedgerBuilder,
    event: OpeningPositionEvent,
) -> None:
    key = _position_key(event.account_id, event.instrument_id)
    if key in builder.positions:
        builder.fail(
            LedgerReasonCode.POSITION_ALREADY_EXISTS,
            event_id=event.event_id,
            message=f"opening position {key} already exists",
        )
    historical_base_cost = _base_value(
        event.local_cost,
        event.acquisition_local_to_base_rate,
    )
    lot = PositionLot(
        lot_id=event.event_id,
        opening_sequence=event.sequence,
        acquisition_date=event.acquisition_date,
        quantity=event.quantity,
        local_cost=event.local_cost,
        historical_base_cost=historical_base_cost,
        lineage=event.lineage,
        custody_lineage=event.lineage,
        acquisition_fx_lineage=event.acquisition_fx_lineage,
        cost_source_lineages=(event.lineage,),
        cost_fx_lineages=(
            ()
            if event.acquisition_fx_lineage is None
            else (event.acquisition_fx_lineage,)
        ),
    )
    _add_position_lot(
        builder,
        event_id=event.event_id,
        account_id=event.account_id,
        instrument_id=event.instrument_id,
        currency=event.currency,
        base_currency=event.base_currency,
        method=event.cost_basis_method,
        lot=lot,
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.OPENING_POSITION,
            account_id=event.account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=event.quantity,
            cost_basis_delta_local=event.local_cost,
            cost_basis_delta_base=historical_base_cost,
        )
    )


def _apply_buy(builder: _LedgerBuilder, event: BuyEvent) -> None:
    local_cost = exact_decimal_sum(
        (event.gross_amount, event.fees, event.taxes)
    )
    base_cost = _base_value(local_cost, event.local_to_base_rate)
    _add_position_lot(
        builder,
        event_id=event.event_id,
        account_id=event.position_account_id,
        instrument_id=event.instrument_id,
        currency=event.currency,
        base_currency=event.base_currency,
        method=event.cost_basis_method,
        lot=PositionLot(
            lot_id=event.event_id,
            opening_sequence=event.sequence,
            acquisition_date=event.trade_date,
            quantity=event.quantity,
            local_cost=local_cost,
            historical_base_cost=base_cost,
            lineage=event.lineage,
            custody_lineage=event.lineage,
            acquisition_fx_lineage=event.local_to_base_lineage,
            cost_source_lineages=(event.lineage,),
            cost_fx_lineages=(
                ()
                if event.local_to_base_lineage is None
                else (event.local_to_base_lineage,)
            ),
        ),
    )
    pending = _pending(
        event=event,
        component="trade",
        component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        recognition_date=event.trade_date,
        settlement_date=event.settlement_date,
        account_id=event.cash_account_id,
        currency=event.currency,
        base_currency=event.base_currency,
        local_amount=exact_decimal_negate(local_cost),
        local_to_base_rate=event.local_to_base_rate,
        instrument_id=event.instrument_id,
    )
    _emit_pending_recognition(
        builder,
        pending=pending,
        kind=LedgerEffectKind.BUY_TRADE,
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.BUY_TRADE,
            account_id=event.position_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=event.quantity,
            cost_basis_delta_local=local_cost,
            cost_basis_delta_base=base_cost,
            capitalized_fees_local=event.fees,
            capitalized_taxes_local=event.taxes,
        )
    )


def _apply_sell(builder: _LedgerBuilder, event: SellEvent) -> None:
    local_net = exact_decimal_sum(
        (
            event.gross_amount,
            exact_decimal_negate(event.fees),
            exact_decimal_negate(event.taxes),
        )
    )
    if local_net < 0:
        builder.fail(
            LedgerReasonCode.NEGATIVE_NET_CASH,
            event_id=event.event_id,
            message="sell fees and taxes exceed gross proceeds",
        )
    position, removed = _remove_position_lots(
        builder,
        event_id=event.event_id,
        account_id=event.position_account_id,
        instrument_id=event.instrument_id,
        quantity=event.quantity,
    )
    builder.check_position_contract(
        position,
        currency=event.currency,
        base_currency=event.base_currency,
        event_id=event.event_id,
    )
    released_local = exact_decimal_sum(
        tuple(item.local_cost for item in removed)
    )
    released_base = _sum_optional(
        tuple(item.historical_base_cost for item in removed)
    )
    base_net = _base_value(local_net, event.local_to_base_rate)
    realized_local = exact_decimal_subtract(local_net, released_local)
    realized_base = (
        None
        if base_net is None or released_base is None
        else exact_decimal_subtract(base_net, released_base)
    )
    dispositions = _make_dispositions(
        event_id=event.event_id,
        disposition_date=event.trade_date,
        position=position,
        removed=removed,
        local_net_proceeds=local_net,
        base_net_proceeds=base_net,
        disposition_fx_rate=event.local_to_base_rate,
        lineage=event.lineage,
        disposition_fx_lineage=event.local_to_base_lineage,
    )
    builder.add_dispositions(dispositions)
    pending = _pending(
        event=event,
        component="trade",
        component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        recognition_date=event.trade_date,
        settlement_date=event.settlement_date,
        account_id=event.cash_account_id,
        currency=event.currency,
        base_currency=event.base_currency,
        local_amount=local_net,
        local_to_base_rate=event.local_to_base_rate,
        instrument_id=event.instrument_id,
    )
    _emit_pending_recognition(
        builder,
        pending=pending,
        kind=LedgerEffectKind.SELL_TRADE,
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.SELL_TRADE,
            account_id=event.position_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=exact_decimal_negate(event.quantity),
            cost_basis_delta_local=exact_decimal_negate(released_local),
            cost_basis_delta_base=(
                None
                if released_base is None
                else exact_decimal_negate(released_base)
            ),
            realized_pnl_delta_local=realized_local,
            realized_pnl_delta_base=realized_base,
            disposal_fees_local=event.fees,
            disposal_taxes_local=event.taxes,
        )
    )


def _apply_external_flow(
    builder: _LedgerBuilder,
    event: ExternalCashFlowEvent,
) -> None:
    signed = (
        event.amount
        if event.kind is ExternalFlowKind.DEPOSIT
        else exact_decimal_negate(event.amount)
    )
    base_amount = _base_value(event.amount, event.local_to_base_rate)
    builder.adjust_cash(event.account_id, event.currency, signed)
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.EXTERNAL_FLOW,
            account_id=event.account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            external_flow_timing=event.timing,
            cash_delta_local=signed,
            recognition_cash_delta_base=(
                None
                if base_amount is None
                else base_amount
                if event.kind is ExternalFlowKind.DEPOSIT
                else exact_decimal_negate(base_amount)
            ),
            external_flow_in_local=(
                event.amount
                if event.kind is ExternalFlowKind.DEPOSIT
                else Decimal("0")
            ),
            external_flow_in_base=(
                base_amount
                if event.kind is ExternalFlowKind.DEPOSIT
                else Decimal("0")
            ),
            external_flow_out_local=(
                event.amount
                if event.kind is ExternalFlowKind.WITHDRAWAL
                else Decimal("0")
            ),
            external_flow_out_base=(
                base_amount
                if event.kind is ExternalFlowKind.WITHDRAWAL
                else Decimal("0")
            ),
        )
    )


def _apply_income(builder: _LedgerBuilder, event: IncomeEvent) -> None:
    income = _pending(
        event=event,
        component="income",
        component_kind=PendingComponentKind.INCOME_ACCRUAL,
        recognition_date=event.recognition_date,
        settlement_date=event.settlement_date,
        account_id=event.cash_account_id,
        currency=event.currency,
        base_currency=event.base_currency,
        local_amount=event.gross_amount,
        local_to_base_rate=event.local_to_base_rate,
        instrument_id=event.instrument_id,
    )
    _emit_pending_recognition(
        builder,
        pending=income,
        kind=LedgerEffectKind.INCOME_RECOGNITION,
        economic_fields={
            "gross_income_local": event.gross_amount,
            "gross_income_base": _base_value(
                event.gross_amount,
                event.local_to_base_rate,
            ),
        },
    )
    if event.fees:
        fee = _pending(
            event=event,
            component="fee",
            component_kind=PendingComponentKind.FEE_ACCRUAL,
            recognition_date=event.recognition_date,
            settlement_date=event.settlement_date,
            account_id=event.cash_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            local_amount=exact_decimal_negate(event.fees),
            local_to_base_rate=event.local_to_base_rate,
            instrument_id=event.instrument_id,
        )
        _emit_pending_recognition(
            builder,
            pending=fee,
            kind=LedgerEffectKind.INCOME_RECOGNITION,
            economic_fields={
                "expensed_fees_local": event.fees,
                "expensed_fees_base": _base_value(
                    event.fees,
                    event.local_to_base_rate,
                ),
            },
        )
    if event.taxes:
        tax = _pending(
            event=event,
            component="tax",
            component_kind=PendingComponentKind.TAX_ACCRUAL,
            recognition_date=event.recognition_date,
            settlement_date=event.settlement_date,
            account_id=event.cash_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            local_amount=exact_decimal_negate(event.taxes),
            local_to_base_rate=event.local_to_base_rate,
            instrument_id=event.instrument_id,
        )
        _emit_pending_recognition(
            builder,
            pending=tax,
            kind=LedgerEffectKind.INCOME_RECOGNITION,
            economic_fields={
                "expensed_taxes_local": event.taxes,
                "expensed_taxes_base": _base_value(
                    event.taxes,
                    event.local_to_base_rate,
                ),
            },
        )


def _apply_return_of_capital(
    builder: _LedgerBuilder,
    event: ReturnOfCapitalEvent,
) -> None:
    local_reduction, local_excess, base_reduction, base_excess = (
        _reduce_position_cost(builder, event=event)
    )
    roc = _pending(
        event=event,
        component="return_of_capital",
        component_kind=PendingComponentKind.RETURN_OF_CAPITAL_ACCRUAL,
        recognition_date=event.recognition_date,
        settlement_date=event.settlement_date,
        account_id=event.cash_account_id,
        currency=event.currency,
        base_currency=event.base_currency,
        local_amount=event.gross_amount,
        local_to_base_rate=event.local_to_base_rate,
        instrument_id=event.instrument_id,
    )
    _emit_pending_recognition(
        builder,
        pending=roc,
        kind=LedgerEffectKind.RETURN_OF_CAPITAL,
        economic_fields={
            "return_of_capital_local": event.gross_amount,
            "return_of_capital_base": _base_value(
                event.gross_amount,
                event.local_to_base_rate,
            ),
        },
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.RETURN_OF_CAPITAL,
            account_id=event.position_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            cost_basis_delta_local=exact_decimal_negate(local_reduction),
            cost_basis_delta_base=(
                None
                if base_reduction is None
                else exact_decimal_negate(base_reduction)
            ),
            realized_pnl_delta_local=local_excess,
            realized_pnl_delta_base=base_excess,
        )
    )
    if event.fees:
        _emit_pending_recognition(
            builder,
            pending=_pending(
                event=event,
                component="fee",
                component_kind=PendingComponentKind.FEE_ACCRUAL,
                recognition_date=event.recognition_date,
                settlement_date=event.settlement_date,
                account_id=event.cash_account_id,
                currency=event.currency,
                base_currency=event.base_currency,
                local_amount=exact_decimal_negate(event.fees),
                local_to_base_rate=event.local_to_base_rate,
                instrument_id=event.instrument_id,
            ),
            kind=LedgerEffectKind.RETURN_OF_CAPITAL,
            economic_fields={
                "expensed_fees_local": event.fees,
                "expensed_fees_base": _base_value(
                    event.fees,
                    event.local_to_base_rate,
                ),
            },
        )
    if event.taxes:
        _emit_pending_recognition(
            builder,
            pending=_pending(
                event=event,
                component="tax",
                component_kind=PendingComponentKind.TAX_ACCRUAL,
                recognition_date=event.recognition_date,
                settlement_date=event.settlement_date,
                account_id=event.cash_account_id,
                currency=event.currency,
                base_currency=event.base_currency,
                local_amount=exact_decimal_negate(event.taxes),
                local_to_base_rate=event.local_to_base_rate,
                instrument_id=event.instrument_id,
            ),
            kind=LedgerEffectKind.RETURN_OF_CAPITAL,
            economic_fields={
                "expensed_taxes_local": event.taxes,
                "expensed_taxes_base": _base_value(
                    event.taxes,
                    event.local_to_base_rate,
                ),
            },
        )


def _apply_maturity(
    builder: _LedgerBuilder,
    event: MaturityRedemptionEvent,
) -> None:
    local_net = exact_decimal_sum(
        (
            event.gross_amount,
            exact_decimal_negate(event.fees),
            exact_decimal_negate(event.taxes),
        )
    )
    if local_net < 0:
        builder.fail(
            LedgerReasonCode.NEGATIVE_NET_CASH,
            event_id=event.event_id,
            message="maturity fees and taxes exceed gross proceeds",
        )
    position, removed = _remove_position_lots(
        builder,
        event_id=event.event_id,
        account_id=event.position_account_id,
        instrument_id=event.instrument_id,
        quantity=event.quantity,
    )
    builder.check_position_contract(
        position,
        currency=event.currency,
        base_currency=event.base_currency,
        event_id=event.event_id,
    )
    released_local = exact_decimal_sum(
        tuple(item.local_cost for item in removed)
    )
    released_base = _sum_optional(
        tuple(item.historical_base_cost for item in removed)
    )
    base_net = _base_value(local_net, event.local_to_base_rate)
    realized_base = (
        None
        if base_net is None or released_base is None
        else exact_decimal_subtract(base_net, released_base)
    )
    builder.add_dispositions(
        _make_dispositions(
            event_id=event.event_id,
            disposition_date=event.recognition_date,
            position=position,
            removed=removed,
            local_net_proceeds=local_net,
            base_net_proceeds=base_net,
            disposition_fx_rate=event.local_to_base_rate,
            lineage=event.lineage,
            disposition_fx_lineage=event.local_to_base_lineage,
        )
    )
    pending = _pending(
        event=event,
        component="maturity",
        component_kind=PendingComponentKind.MATURITY_ACCRUAL,
        recognition_date=event.recognition_date,
        settlement_date=event.settlement_date,
        account_id=event.cash_account_id,
        currency=event.currency,
        base_currency=event.base_currency,
        local_amount=local_net,
        local_to_base_rate=event.local_to_base_rate,
        instrument_id=event.instrument_id,
    )
    _emit_pending_recognition(
        builder,
        pending=pending,
        kind=LedgerEffectKind.MATURITY_REDEMPTION,
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.MATURITY_REDEMPTION,
            account_id=event.position_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=exact_decimal_negate(event.quantity),
            cost_basis_delta_local=exact_decimal_negate(released_local),
            cost_basis_delta_base=(
                None
                if released_base is None
                else exact_decimal_negate(released_base)
            ),
            realized_pnl_delta_local=exact_decimal_subtract(
                local_net,
                released_local,
            ),
            realized_pnl_delta_base=realized_base,
            disposal_fees_local=event.fees,
            disposal_taxes_local=event.taxes,
        )
    )


def _apply_cash_transfer(
    builder: _LedgerBuilder,
    event: CashTransferEvent,
) -> None:
    negative_amount = exact_decimal_negate(event.amount)
    builder.adjust_cash(event.source_account_id, event.currency, negative_amount)
    builder.adjust_cash(event.destination_account_id, event.currency, event.amount)
    for account, delta, lineage in (
        (event.source_account_id, negative_amount, event.lineage),
        (
            event.destination_account_id,
            event.amount,
            event.counterparty_lineage,
        ),
    ):
        builder.emit(
            LedgerEffect(
                effective_date=builder.as_of_date,
                event_id=event.event_id,
                kind=LedgerEffectKind.CASH_TRANSFER,
                account_id=account,
                currency=event.currency,
                base_currency=event.currency,
                lineage=lineage,
                cash_delta_local=delta,
                recognition_cash_delta_base=delta,
            )
        )


def _apply_position_transfer(
    builder: _LedgerBuilder,
    event: PositionTransferEvent,
) -> None:
    source, removed = _remove_position_lots(
        builder,
        event_id=event.event_id,
        account_id=event.source_account_id,
        instrument_id=event.instrument_id,
        quantity=event.quantity,
    )
    if source.currency != event.currency:
        builder.fail(
            LedgerReasonCode.CURRENCY_MISMATCH,
            event_id=event.event_id,
            message="position transfer currency does not match source",
        )
    local_cost = exact_decimal_sum(tuple(item.local_cost for item in removed))
    transferred_cost_fact = quantize_ledger_fact(
        local_cost,
        kind=LedgerFactKind.AMOUNT,
        field_name="transferred_local_cost",
    )
    if transferred_cost_fact != event.declared_local_cost:
        builder.fail(
            LedgerReasonCode.GROSS_AMOUNT_MISMATCH,
            event_id=event.event_id,
            message=(
                "position transfer declared_local_cost does not match source lots: "
                f"expected {transferred_cost_fact}"
            ),
        )
    base_cost = _sum_optional(
        tuple(item.historical_base_cost for item in removed)
    )
    for index, item in enumerate(removed):
        _add_position_lot(
            builder,
            event_id=event.event_id,
            account_id=event.destination_account_id,
            instrument_id=event.instrument_id,
            currency=source.currency,
            base_currency=source.base_currency,
            method=source.cost_basis_method,
            lot=PositionLot(
                lot_id=f"{item.lot.lot_id}:xfer:{event.event_id}:{index}",
                opening_sequence=item.lot.opening_sequence,
                acquisition_date=item.lot.acquisition_date,
                quantity=item.quantity,
                local_cost=item.local_cost,
                historical_base_cost=item.historical_base_cost,
                lineage=item.lot.lineage,
                custody_lineage=event.counterparty_lineage,
                acquisition_fx_lineage=item.lot.acquisition_fx_lineage,
                cost_source_lineages=item.lot.cost_source_lineages,
                cost_fx_lineages=item.lot.cost_fx_lineages,
            ),
        )
    for account, sign, lineage in (
        (event.source_account_id, Decimal("-1"), event.lineage),
        (
            event.destination_account_id,
            Decimal("1"),
            event.counterparty_lineage,
        ),
    ):
        builder.emit(
            LedgerEffect(
                effective_date=builder.as_of_date,
                event_id=event.event_id,
                kind=LedgerEffectKind.POSITION_TRANSFER,
                account_id=account,
                currency=source.currency,
                base_currency=source.base_currency,
                lineage=lineage,
                instrument_id=event.instrument_id,
                quantity_delta=exact_decimal_product(sign, event.quantity),
                cost_basis_delta_local=exact_decimal_product(sign, local_cost),
                cost_basis_delta_base=(
                    None
                    if base_cost is None
                    else exact_decimal_product(sign, base_cost)
                ),
            )
        )


def _apply_split(builder: _LedgerBuilder, event: SplitEvent) -> None:
    position_keys = tuple(
        key
        for key in sorted(builder.positions)
        if key[1] == event.instrument_id
    )
    for position_key in position_keys:
        _apply_split_to_position(builder, event, position_key=position_key)


def _apply_split_to_position(
    builder: _LedgerBuilder,
    event: SplitEvent,
    *,
    position_key: tuple[str, str],
) -> None:
    position = builder.positions[position_key]
    builder.check_position_contract(
        position,
        currency=event.currency,
        base_currency=event.base_currency,
        event_id=event.event_id,
    )
    split_lots = tuple(
        replace(
            lot,
            quantity=_exact_split_quantity(
                lot.quantity,
                numerator=event.ratio_numerator,
                denominator=event.ratio_denominator,
                event_id=event.event_id,
            ),
        )
        for lot in position.lots
    )
    split_quantity = exact_decimal_sum(
        tuple(lot.quantity for lot in split_lots)
    )
    quantity_delta = exact_decimal_subtract(
        split_quantity,
        position.quantity,
    )
    builder.positions[_position_key(position.account_id, position.instrument_id)] = (
        _position_from_lots(
            account_id=position.account_id,
            instrument_id=position.instrument_id,
            currency=position.currency,
            base_currency=position.base_currency,
            method=position.cost_basis_method,
            lots=split_lots,
        )
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.SPLIT,
            account_id=position.account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=quantity_delta,
        )
    )


def _exact_split_quantity(
    quantity: Decimal,
    *,
    numerator: Decimal,
    denominator: Decimal,
    event_id: str,
) -> Decimal:
    """Return an exact terminating split result; never context-round a ratio."""

    fraction = Fraction(quantity) * Fraction(numerator) / Fraction(denominator)
    residual_denominator = fraction.denominator
    powers_of_two = 0
    powers_of_five = 0
    while residual_denominator % 2 == 0:
        residual_denominator //= 2
        powers_of_two += 1
    while residual_denominator % 5 == 0:
        residual_denominator //= 5
        powers_of_five += 1
    if residual_denominator != 1:
        raise LedgerContractError(
            "exact split ratio produces a non-terminating decimal quantity",
            reason_code=LedgerReasonCode.NUMERIC_FAILURE,
            event_id=event_id,
        )
    scale = max(powers_of_two, powers_of_five)
    coefficient = (
        fraction.numerator
        * 2 ** (scale - powers_of_two)
        * 5 ** (scale - powers_of_five)
    )
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(character) for character in str(abs(coefficient)))
    exact = Decimal((sign, digits, -scale))
    return require_fact(
        exact,
        kind=LedgerFactKind.QUANTITY,
        field_name="split_quantity",
        minimum=Decimal("0"),
    )
def _apply_fx_conversion(
    builder: _LedgerBuilder,
    event: FxConversionEvent,
) -> None:
    source_base = _base_value(event.source_amount, event.source_to_base_rate)
    target_base = _base_value(event.target_amount, event.target_to_base_rate)
    difference = (
        None
        if source_base is None or target_base is None
        else exact_decimal_subtract(target_base, source_base)
    )
    source = _pending(
        event=event,
        component="fx_source",
        component_kind=PendingComponentKind.FX_CONVERSION_SOURCE_LEG,
        recognition_date=event.trade_date,
        settlement_date=event.settlement_date,
        account_id=event.source_account_id,
        currency=event.source_currency,
        base_currency=event.base_currency,
        local_amount=exact_decimal_negate(event.source_amount),
        local_to_base_rate=event.source_to_base_rate,
        instrument_id=None,
    )
    target = _pending(
        event=event,
        component="fx_target",
        component_kind=PendingComponentKind.FX_CONVERSION_TARGET_LEG,
        recognition_date=event.trade_date,
        settlement_date=event.settlement_date,
        account_id=event.target_account_id,
        currency=event.target_currency,
        base_currency=event.base_currency,
        local_amount=event.target_amount,
        local_to_base_rate=event.target_to_base_rate,
        instrument_id=None,
    )
    for pending in (source, target):
        _emit_pending_recognition(
            builder,
            pending=pending,
            kind=LedgerEffectKind.FX_CONVERSION,
            economic_fields=(
                {"fx_conversion_effect_base": difference}
                if pending is target
                else {}
            ),
        )
    builder.fx_conversions.append(
        FxConversionEvidence(
            event_id=event.event_id,
            trade_date=event.trade_date,
            settlement_date=event.settlement_date,
            source_currency=event.source_currency,
            target_currency=event.target_currency,
            source_amount=event.source_amount,
            target_amount=event.target_amount,
            effective_fx_rate=event.effective_fx_rate,
            rate_convention=event.rate_convention,
            base_currency=event.base_currency,
            source_base_value=source_base,
            target_base_value=target_base,
            base_economic_difference=difference,
            lineage=event.lineage,
        )
    )


def _apply_dividend_reinvestment(
    builder: _LedgerBuilder,
    event: DividendReinvestmentEvent,
) -> None:
    base_cost = _base_value(event.gross_income, event.local_to_base_rate)
    _add_position_lot(
        builder,
        event_id=event.event_id,
        account_id=event.position_account_id,
        instrument_id=event.instrument_id,
        currency=event.currency,
        base_currency=event.base_currency,
        method=event.cost_basis_method,
        lot=PositionLot(
            lot_id=event.event_id,
            opening_sequence=event.sequence,
            acquisition_date=event.recognition_date,
            quantity=event.quantity,
            local_cost=event.gross_income,
            historical_base_cost=base_cost,
            lineage=event.lineage,
            custody_lineage=event.lineage,
            acquisition_fx_lineage=event.local_to_base_lineage,
            cost_source_lineages=(event.lineage,),
            cost_fx_lineages=(
                ()
                if event.local_to_base_lineage is None
                else (event.local_to_base_lineage,)
            ),
        ),
    )
    builder.dividend_reinvestments.append(
        DividendReinvestmentEvidence(
            event_id=event.event_id,
            recognition_date=event.recognition_date,
            instrument_id=event.instrument_id,
            quantity=event.quantity,
            gross_income=event.gross_income,
            price=event.price,
            price_status=(
                PriceEvidenceStatus.UNAVAILABLE
                if event.price is None
                else PriceEvidenceStatus.AVAILABLE
            ),
            contract_multiplier=event.contract_multiplier,
            price_factor=event.price_factor,
            price_unit=event.price_unit,
            lineage=event.lineage,
        )
    )
    builder.emit(
        LedgerEffect(
            effective_date=builder.as_of_date,
            event_id=event.event_id,
            kind=LedgerEffectKind.DIVIDEND_REINVESTMENT,
            account_id=event.position_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            lineage=event.lineage,
            instrument_id=event.instrument_id,
            quantity_delta=event.quantity,
            cost_basis_delta_local=event.gross_income,
            cost_basis_delta_base=base_cost,
            gross_income_local=event.gross_income,
            gross_income_base=base_cost,
        )
    )


def _apply_expense(builder: _LedgerBuilder, event: ExpenseEvent) -> None:
    component_kind = (
        PendingComponentKind.FEE_ACCRUAL
        if event.kind is ExpenseKind.FEE
        else PendingComponentKind.TAX_ACCRUAL
    )
    base_amount = _base_value(event.amount, event.local_to_base_rate)
    _emit_pending_recognition(
        builder,
        pending=_pending(
            event=event,
            component=event.kind.value,
            component_kind=component_kind,
            recognition_date=event.recognition_date,
            settlement_date=event.settlement_date,
            account_id=event.cash_account_id,
            currency=event.currency,
            base_currency=event.base_currency,
            local_amount=exact_decimal_negate(event.amount),
            local_to_base_rate=event.local_to_base_rate,
            instrument_id=event.instrument_id,
        ),
        kind=LedgerEffectKind.EXPENSE_RECOGNITION,
        economic_fields=(
            {
                "expensed_fees_local": event.amount,
                "expensed_fees_base": base_amount,
            }
            if event.kind is ExpenseKind.FEE
            else {
                "expensed_taxes_local": event.amount,
                "expensed_taxes_base": base_amount,
            }
        ),
    )


def _apply_action(builder: _LedgerBuilder, action: _ScheduledAction) -> None:
    event = action.event
    if action.kind is _ActionKind.OPENING_CASH:
        assert isinstance(event, OpeningCashEvent)
        _apply_opening_cash(builder, event)
    elif action.kind is _ActionKind.OPENING_POSITION:
        assert isinstance(event, OpeningPositionEvent)
        _apply_opening_position(builder, event)
    elif action.kind is _ActionKind.BUY_TRADE:
        assert isinstance(event, BuyEvent)
        _apply_buy(builder, event)
    elif action.kind is _ActionKind.SELL_TRADE:
        assert isinstance(event, SellEvent)
        _apply_sell(builder, event)
    elif action.kind is _ActionKind.EXTERNAL_FLOW:
        assert isinstance(event, ExternalCashFlowEvent)
        _apply_external_flow(builder, event)
    elif action.kind is _ActionKind.INCOME_RECOGNITION:
        assert isinstance(event, IncomeEvent)
        _apply_income(builder, event)
    elif action.kind is _ActionKind.RETURN_OF_CAPITAL_RECOGNITION:
        assert isinstance(event, ReturnOfCapitalEvent)
        _apply_return_of_capital(builder, event)
    elif action.kind is _ActionKind.MATURITY_RECOGNITION:
        assert isinstance(event, MaturityRedemptionEvent)
        _apply_maturity(builder, event)
    elif action.kind is _ActionKind.CASH_TRANSFER:
        assert isinstance(event, CashTransferEvent)
        _apply_cash_transfer(builder, event)
    elif action.kind is _ActionKind.POSITION_TRANSFER:
        assert isinstance(event, PositionTransferEvent)
        _apply_position_transfer(builder, event)
    elif action.kind is _ActionKind.SPLIT:
        assert isinstance(event, SplitEvent)
        _apply_split(builder, event)
    elif action.kind is _ActionKind.FX_RECOGNITION:
        assert isinstance(event, FxConversionEvent)
        _apply_fx_conversion(builder, event)
    elif action.kind is _ActionKind.DIVIDEND_REINVESTMENT:
        assert isinstance(event, DividendReinvestmentEvent)
        _apply_dividend_reinvestment(builder, event)
    elif action.kind is _ActionKind.EXPENSE_RECOGNITION:
        assert isinstance(event, ExpenseEvent)
        _apply_expense(builder, event)
    elif action.kind in {
        _ActionKind.BUY_SETTLEMENT,
        _ActionKind.SELL_SETTLEMENT,
        _ActionKind.INCOME_SETTLEMENT,
        _ActionKind.RETURN_OF_CAPITAL_SETTLEMENT,
        _ActionKind.MATURITY_SETTLEMENT,
        _ActionKind.FX_SETTLEMENT,
        _ActionKind.EXPENSE_SETTLEMENT,
    }:
        builder.settle_event(event.event_id)
    else:  # pragma: no cover - closed enum defense
        raise LedgerContractError(f"unsupported action {action.kind.value}")


def _clean(values: dict[tuple[str, str], Decimal]) -> dict[tuple[str, str], Decimal]:
    return {key: value for key, value in values.items() if value != 0}


def _validate_state_closure(builder: _LedgerBuilder) -> None:
    if _clean(builder._cash_effects) != builder.cash:
        builder.fail(
            LedgerReasonCode.STATE_NOT_CLOSED,
            event_id="__ledger__",
            message="cash effects do not close to cash balances",
        )
    pending_state: dict[tuple[str, str], Decimal] = {}
    for item in builder.pending.values():
        key = (item.account_id, item.currency)
        pending_state[key] = exact_decimal_sum(
            (pending_state.get(key, Decimal("0")), item.local_amount)
        )
    if _clean(builder._pending_effects) != _clean(pending_state):
        builder.fail(
            LedgerReasonCode.STATE_NOT_CLOSED,
            event_id="__ledger__",
            message="pending effects do not close to pending components",
        )
    if any(item.settlement_date <= builder.as_of_date for item in builder.pending.values()):
        builder.fail(
            LedgerReasonCode.PENDING_SETTLEMENT_NOT_FOUND,
            event_id="__ledger__",
            message="past-due pending component survived replay",
        )
    quantity_state = {
        key: position.quantity for key, position in builder.positions.items()
    }
    local_cost_state = {
        key: position.local_cost
        for key, position in builder.positions.items()
        if position.local_cost != 0
    }
    if _clean(builder._quantity_effects) != quantity_state or _clean(
        builder._local_cost_effects
    ) != local_cost_state:
        builder.fail(
            LedgerReasonCode.STATE_NOT_CLOSED,
            event_id="__ledger__",
            message="position effects do not close to local quantity/cost state",
        )
    for key in builder._base_cost_effects.keys() | builder.positions.keys():
        position = builder.positions.get(key)
        state_base = (
            Decimal("0")
            if position is None
            else position.historical_base_cost
        )
        effect_base = builder._base_cost_effects.get(key, Decimal("0"))
        if effect_base is not None and state_base != effect_base:
            builder.fail(
                LedgerReasonCode.STATE_NOT_CLOSED,
                event_id="__ledger__",
                message="position effects do not close to historical base cost",
            )
