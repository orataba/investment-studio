"""Deterministic expansion of ledger events into ordered replay actions."""

from __future__ import annotations

from datetime import date

from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    BuyEvent,
    CashTransferEvent,
    DividendReinvestmentEvent,
    ExpenseEvent,
    ExternalCashFlowEvent,
    ExternalFlowTiming,
    FxConversionEvent,
    IncomeEvent,
    IncomeKind,
    LedgerContractError,
    LedgerEvent,
    MaturityRedemptionEvent,
    OpeningCashEvent,
    OpeningPositionEvent,
    PositionTransferEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    SplitEvent,
)
from portfolio_app.calculations.portfolio_daily.ledger_runtime import (
    _ActionKind,
    _ActionPhase,
    _ScheduledAction,
)


def _schedule(
    event: LedgerEvent,
    *,
    effective_date: date,
    phase: _ActionPhase,
    kind: _ActionKind,
) -> _ScheduledAction:
    return _ScheduledAction(
        effective_date=effective_date,
        phase=phase,
        sequence=event.sequence,
        event_id=event.event_id,
        kind=kind,
        event=event,
    )


def _expand_event(event: LedgerEvent) -> tuple[_ScheduledAction, ...]:
    if isinstance(event, OpeningCashEvent):
        return (
            _schedule(
                event,
                effective_date=event.effective_date,
                phase=_ActionPhase.OPENING,
                kind=_ActionKind.OPENING_CASH,
            ),
        )
    if isinstance(event, OpeningPositionEvent):
        return (
            _schedule(
                event,
                effective_date=event.effective_date,
                phase=_ActionPhase.OPENING,
                kind=_ActionKind.OPENING_POSITION,
            ),
        )
    if isinstance(event, BuyEvent):
        return (
            _schedule(
                event,
                effective_date=event.trade_date,
                phase=_ActionPhase.POSITION,
                kind=_ActionKind.BUY_TRADE,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.BUY_SETTLEMENT,
            ),
        )
    if isinstance(event, SellEvent):
        return (
            _schedule(
                event,
                effective_date=event.trade_date,
                phase=_ActionPhase.POSITION,
                kind=_ActionKind.SELL_TRADE,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.SELL_SETTLEMENT,
            ),
        )
    if isinstance(event, ExternalCashFlowEvent):
        phase = (
            _ActionPhase.BEGINNING_OF_DAY_FLOW
            if event.timing is ExternalFlowTiming.BEGINNING_OF_DAY
            else _ActionPhase.END_OF_DAY_FLOW
        )
        return (
            _schedule(
                event,
                effective_date=event.value_date,
                phase=phase,
                kind=_ActionKind.EXTERNAL_FLOW,
            ),
        )
    if isinstance(event, IncomeEvent):
        recognition_phase = (
            _ActionPhase.ENTITLEMENT
            if event.kind in {IncomeKind.DIVIDEND, IncomeKind.COUPON}
            else _ActionPhase.RECOGNITION
        )
        return (
            _schedule(
                event,
                effective_date=event.recognition_date,
                phase=recognition_phase,
                kind=_ActionKind.INCOME_RECOGNITION,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.INCOME_SETTLEMENT,
            ),
        )
    if isinstance(event, ReturnOfCapitalEvent):
        return (
            _schedule(
                event,
                effective_date=event.recognition_date,
                phase=_ActionPhase.ENTITLEMENT,
                kind=_ActionKind.RETURN_OF_CAPITAL_RECOGNITION,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.RETURN_OF_CAPITAL_SETTLEMENT,
            ),
        )
    if isinstance(event, MaturityRedemptionEvent):
        return (
            _schedule(
                event,
                effective_date=event.recognition_date,
                phase=_ActionPhase.RECOGNITION,
                kind=_ActionKind.MATURITY_RECOGNITION,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.MATURITY_SETTLEMENT,
            ),
        )
    if isinstance(event, CashTransferEvent):
        return (
            _schedule(
                event,
                effective_date=event.effective_date,
                phase=_ActionPhase.TRANSFER,
                kind=_ActionKind.CASH_TRANSFER,
            ),
        )
    if isinstance(event, PositionTransferEvent):
        return (
            _schedule(
                event,
                effective_date=event.effective_date,
                phase=_ActionPhase.TRANSFER,
                kind=_ActionKind.POSITION_TRANSFER,
            ),
        )
    if isinstance(event, SplitEvent):
        return (
            _schedule(
                event,
                effective_date=event.effective_date,
                phase=_ActionPhase.CORPORATE_ACTION,
                kind=_ActionKind.SPLIT,
            ),
        )
    if isinstance(event, FxConversionEvent):
        return (
            _schedule(
                event,
                effective_date=event.trade_date,
                phase=_ActionPhase.POSITION,
                kind=_ActionKind.FX_RECOGNITION,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.FX_SETTLEMENT,
            ),
        )
    if isinstance(event, DividendReinvestmentEvent):
        return (
            _schedule(
                event,
                effective_date=event.recognition_date,
                phase=_ActionPhase.ENTITLEMENT,
                kind=_ActionKind.DIVIDEND_REINVESTMENT,
            ),
        )
    if isinstance(event, ExpenseEvent):
        return (
            _schedule(
                event,
                effective_date=event.recognition_date,
                phase=_ActionPhase.RECOGNITION,
                kind=_ActionKind.EXPENSE_RECOGNITION,
            ),
            _schedule(
                event,
                effective_date=event.settlement_date,
                phase=_ActionPhase.SETTLEMENT,
                kind=_ActionKind.EXPENSE_SETTLEMENT,
            ),
        )
    raise LedgerContractError(f"unsupported ledger event {type(event).__name__}")
