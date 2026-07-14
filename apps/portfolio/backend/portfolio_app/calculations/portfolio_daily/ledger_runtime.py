"""Mutable replay runtime and exact lot mechanics for Portfolio Daily."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import IntEnum, StrEnum

from portfolio_app.calculations.numeric import (
    calculation_context,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    FactLineage,
    LedgerContractError,
    LedgerEvent,
    LedgerReasonCode,
    ReturnOfCapitalEvent,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    BaseCoverageStatus,
    CashBalance,
    DailyLedgerState,
    DividendReinvestmentEvidence,
    FxConversionEvidence,
    LedgerEffect,
    LedgerEffectKind,
    LEDGER_TOTAL_EFFECT_FIELDS,
    LEDGER_TOTAL_FIELDS,
    LedgerTotals,
    LotDisposition,
    OpeningAnchorEvidence,
    PendingSettlement,
    PositionLot,
    PositionState,
    canonical_lots,
)


class _LedgerFailure(RuntimeError):
    def __init__(
        self,
        reason_code: LedgerReasonCode,
        *,
        event_id: str,
        message: str,
        unavailable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.event_id = event_id
        self.unavailable = unavailable


class _ActionPhase(IntEnum):
    """Methodology-owned intraday order; sequence only orders within a phase."""

    OPENING = 0
    BEGINNING_OF_DAY_FLOW = 5
    CORPORATE_ACTION = 10
    ENTITLEMENT = 15
    RECOGNITION = 16
    POSITION = 20
    TRANSFER = 30
    END_OF_DAY_FLOW = 50
    SETTLEMENT = 60


class _ActionKind(StrEnum):
    OPENING_CASH = "opening_cash"
    OPENING_POSITION = "opening_position"
    BUY_TRADE = "buy_trade"
    BUY_SETTLEMENT = "buy_settlement"
    SELL_TRADE = "sell_trade"
    SELL_SETTLEMENT = "sell_settlement"
    EXTERNAL_FLOW = "external_flow"
    INCOME_RECOGNITION = "income_recognition"
    INCOME_SETTLEMENT = "income_settlement"
    RETURN_OF_CAPITAL_RECOGNITION = "return_of_capital_recognition"
    RETURN_OF_CAPITAL_SETTLEMENT = "return_of_capital_settlement"
    MATURITY_RECOGNITION = "maturity_recognition"
    MATURITY_SETTLEMENT = "maturity_settlement"
    CASH_TRANSFER = "cash_transfer"
    POSITION_TRANSFER = "position_transfer"
    SPLIT = "split"
    FX_RECOGNITION = "fx_recognition"
    FX_SETTLEMENT = "fx_settlement"
    DIVIDEND_REINVESTMENT = "dividend_reinvestment"
    EXPENSE_RECOGNITION = "expense_recognition"
    EXPENSE_SETTLEMENT = "expense_settlement"


@dataclass(frozen=True, slots=True)
class _ScheduledAction:
    effective_date: date
    phase: _ActionPhase
    sequence: int
    event_id: str
    kind: _ActionKind
    event: LedgerEvent


@dataclass(frozen=True, slots=True)
class _PreparedLedger:
    actions: tuple[_ScheduledAction, ...]
    opening_anchor: OpeningAnchorEvidence | None


@dataclass(frozen=True, slots=True)
class _RemovedLot:
    lot: PositionLot
    quantity: Decimal
    local_cost: Decimal
    historical_base_cost: Decimal | None


def _position_key(account_id: str, instrument_id: str) -> tuple[str, str]:
    return account_id, instrument_id


def _cash_key(account_id: str, currency: str) -> tuple[str, str]:
    return account_id, currency


def _pending_id(event_id: str, component: str) -> str:
    return f"{event_id}:{component}"


def _base_value(local: Decimal, rate: Decimal | None) -> Decimal | None:
    if rate is None:
        return None
    return exact_decimal_product(local, rate)


def _sum_optional(values: tuple[Decimal | None, ...]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return exact_decimal_sum(
        tuple(value for value in values if value is not None)
    )


def _allocate_exact(
    total: Decimal,
    weights: tuple[Decimal, ...],
) -> tuple[Decimal, ...]:
    """Allocate exactly, assigning division residual to the final positive row."""

    if not weights:
        if total != 0:
            raise LedgerContractError("cannot allocate a non-zero total to no rows")
        return ()
    weight_total = exact_decimal_sum(weights)
    if weight_total <= 0:
        if total != 0:
            raise LedgerContractError("allocation weights must contain positive value")
        return tuple(Decimal("0") for _ in weights)
    positive = [index for index, weight in enumerate(weights) if weight > 0]
    allocations = [Decimal("0") for _ in weights]
    allocated = Decimal("0")
    for index in positive[:-1]:
        numerator = exact_decimal_product(total, weights[index])
        with calculation_context():
            value = numerator / weight_total
        allocations[index] = value
        allocated = exact_decimal_sum((allocated, value))
    allocations[positive[-1]] = exact_decimal_subtract(total, allocated)
    return tuple(allocations)


def _merge_lineages(
    *groups: tuple[FactLineage, ...],
) -> tuple[FactLineage, ...]:
    return tuple(
        sorted(
            {item for group in groups for item in group},
            key=lambda item: item.manifest_fact_key,
        )
    )


def _position_from_lots(
    *,
    account_id: str,
    instrument_id: str,
    currency: str,
    base_currency: str,
    method: CostBasisMethod,
    lots: tuple[PositionLot, ...],
) -> PositionState:
    ordered = canonical_lots(lots)
    quantity = exact_decimal_sum(tuple(lot.quantity for lot in ordered))
    local_cost = exact_decimal_sum(tuple(lot.local_cost for lot in ordered))
    base_cost = _sum_optional(
        tuple(lot.historical_base_cost for lot in ordered)
    )
    return PositionState(
        account_id=account_id,
        instrument_id=instrument_id,
        currency=currency,
        base_currency=base_currency,
        cost_basis_method=method,
        quantity=quantity,
        local_cost=local_cost,
        historical_base_cost=base_cost,
        lots=ordered,
    )


def _rebalance_moving_average(
    lots: tuple[PositionLot, ...],
) -> tuple[PositionLot, ...]:
    total_local = exact_decimal_sum(tuple(lot.local_cost for lot in lots))
    local_allocations = _allocate_exact(
        total_local,
        tuple(lot.quantity for lot in lots),
    )
    base_total = _sum_optional(tuple(lot.historical_base_cost for lot in lots))
    base_allocations = (
        None
        if base_total is None
        else _allocate_exact(base_total, tuple(lot.quantity for lot in lots))
    )
    pooled_cost_lineages = _merge_lineages(
        *(lot.cost_source_lineages for lot in lots)
    )
    pooled_fx_lineages = _merge_lineages(
        *(lot.cost_fx_lineages for lot in lots)
    )
    return canonical_lots(
        tuple(
            replace(
                lot,
                local_cost=local_allocations[index],
                historical_base_cost=(
                    None
                    if base_allocations is None
                    else base_allocations[index]
                ),
                acquisition_fx_lineage=(
                    None
                    if base_allocations is None
                    else lot.acquisition_fx_lineage
                ),
                cost_source_lineages=pooled_cost_lineages,
                cost_fx_lineages=(
                    () if base_allocations is None else pooled_fx_lineages
                ),
            )
            for index, lot in enumerate(lots)
        )
    )


class _LedgerBuilder:
    def __init__(
        self,
        *,
        as_of_date: date,
        opening_anchor: OpeningAnchorEvidence | None,
    ) -> None:
        self.as_of_date = as_of_date
        self.opening_anchor = opening_anchor
        self.cash: dict[tuple[str, str], Decimal] = {}
        self.positions: dict[tuple[str, str], PositionState] = {}
        self.pending: dict[str, PendingSettlement] = {}
        self.effects: list[LedgerEffect] = []
        self.daily_effects: list[LedgerEffect] = []
        self.dispositions: list[LotDisposition] = []
        self.daily_dispositions: list[LotDisposition] = []
        self.fx_conversions: list[FxConversionEvidence] = []
        self.dividend_reinvestments: list[DividendReinvestmentEvidence] = []
        self._cash_effects: dict[tuple[str, str], Decimal] = {}
        self._pending_effects: dict[tuple[str, str], Decimal] = {}
        self._quantity_effects: dict[tuple[str, str], Decimal] = {}
        self._local_cost_effects: dict[tuple[str, str], Decimal] = {}
        self._base_cost_effects: dict[tuple[str, str], Decimal | None] = {}
        self._cumulative_totals: dict[str, dict[str, Decimal | None]] = {}
        self._daily_totals: dict[str, dict[str, Decimal | None]] = {}

    def begin_day(self, as_of_date: date) -> None:
        self.as_of_date = as_of_date
        self.daily_effects = []
        self.daily_dispositions = []
        self._daily_totals = {}

    def fail(
        self,
        reason_code: LedgerReasonCode,
        *,
        event_id: str,
        message: str,
        unavailable: bool = False,
    ) -> None:
        raise _LedgerFailure(
            reason_code,
            event_id=event_id,
            message=message,
            unavailable=unavailable,
        )

    def position(
        self,
        account_id: str,
        instrument_id: str,
        *,
        event_id: str,
    ) -> PositionState:
        position = self.positions.get(_position_key(account_id, instrument_id))
        if position is None:
            self.fail(
                LedgerReasonCode.POSITION_NOT_FOUND,
                event_id=event_id,
                message=f"position {account_id}/{instrument_id} does not exist",
            )
        return position

    def check_position_contract(
        self,
        position: PositionState,
        *,
        currency: str,
        base_currency: str,
        event_id: str,
    ) -> None:
        if position.currency != currency or position.base_currency != base_currency:
            self.fail(
                LedgerReasonCode.CURRENCY_MISMATCH,
                event_id=event_id,
                message="position local/base currency does not match event",
            )

    def adjust_cash(self, account_id: str, currency: str, delta: Decimal) -> None:
        key = _cash_key(account_id, currency)
        amount = exact_decimal_sum((self.cash.get(key, Decimal("0")), delta))
        if amount == 0:
            self.cash.pop(key, None)
        else:
            self.cash[key] = amount

    def add_pending(self, pending: PendingSettlement) -> None:
        if pending.settlement_id in self.pending:
            self.fail(
                LedgerReasonCode.DUPLICATE_PENDING_SETTLEMENT,
                event_id=pending.event_id,
                message=f"duplicate pending component {pending.settlement_id}",
            )
        self.pending[pending.settlement_id] = pending

    def settle_event(self, event_id: str) -> None:
        pending = tuple(
            sorted(
                (
                    value
                    for value in self.pending.values()
                    if value.event_id == event_id
                ),
                key=lambda value: value.settlement_id,
            )
        )
        if not pending:
            self.fail(
                LedgerReasonCode.PENDING_SETTLEMENT_NOT_FOUND,
                event_id=event_id,
                message=f"event {event_id} has no pending settlement components",
            )
        for item in pending:
            self.pending.pop(item.settlement_id)
            self.adjust_cash(item.account_id, item.currency, item.local_amount)
            self.emit(
                LedgerEffect(
                    effective_date=self.as_of_date,
                    event_id=event_id,
                    kind=LedgerEffectKind.CASH_SETTLEMENT,
                    account_id=item.account_id,
                    currency=item.currency,
                    base_currency=item.base_currency,
                    lineage=item.lineage,
                    instrument_id=item.instrument_id,
                    pending_component_kind=item.component_kind,
                    cash_delta_local=item.local_amount,
                    recognition_cash_delta_base=item.recognition_base_amount,
                    pending_delta_local=exact_decimal_negate(item.local_amount),
                    pending_delta_base=(
                        None
                        if item.recognition_base_amount is None
                        else exact_decimal_negate(item.recognition_base_amount)
                    ),
                )
            )

    @staticmethod
    def _add_optional(
        existing: Decimal | None,
        delta: Decimal | None,
    ) -> Decimal | None:
        if existing is None or delta is None:
            return None
        return exact_decimal_sum((existing, delta))

    def _accumulate_totals(
        self,
        target: dict[str, dict[str, Decimal | None]],
        effect: LedgerEffect,
    ) -> None:
        deltas = tuple(
            getattr(effect, LEDGER_TOTAL_EFFECT_FIELDS[field])
            for field in LEDGER_TOTAL_FIELDS
        )
        if all(delta == 0 for delta in deltas):
            return
        row = target.setdefault(
            effect.currency,
            {field: Decimal("0") for field in LEDGER_TOTAL_FIELDS},
        )
        for field in LEDGER_TOTAL_FIELDS:
            row[field] = self._add_optional(
                row[field],
                getattr(effect, LEDGER_TOTAL_EFFECT_FIELDS[field]),
            )

    def emit(self, effect: LedgerEffect) -> None:
        self.effects.append(effect)
        if effect.effective_date == self.as_of_date:
            self.daily_effects.append(effect)
        cash_key = (effect.account_id, effect.currency)
        self._cash_effects[cash_key] = exact_decimal_sum(
            (self._cash_effects.get(cash_key, Decimal("0")), effect.cash_delta_local)
        )
        self._pending_effects[cash_key] = exact_decimal_sum(
            (
                self._pending_effects.get(cash_key, Decimal("0")),
                effect.pending_delta_local,
            )
        )
        if effect.instrument_id is not None:
            position_key = (effect.account_id, effect.instrument_id)
            self._quantity_effects[position_key] = exact_decimal_sum(
                (
                    self._quantity_effects.get(position_key, Decimal("0")),
                    effect.quantity_delta,
                )
            )
            self._local_cost_effects[position_key] = exact_decimal_sum(
                (
                    self._local_cost_effects.get(position_key, Decimal("0")),
                    effect.cost_basis_delta_local,
                )
            )
            existing_base = self._base_cost_effects.get(
                position_key,
                Decimal("0"),
            )
            self._base_cost_effects[position_key] = self._add_optional(
                existing_base,
                effect.cost_basis_delta_base,
            )
        self._accumulate_totals(self._cumulative_totals, effect)
        if effect.effective_date == self.as_of_date:
            self._accumulate_totals(self._daily_totals, effect)

    def add_dispositions(self, values: tuple[LotDisposition, ...]) -> None:
        self.dispositions.extend(values)
        self.daily_dispositions.extend(values)

    @staticmethod
    def _freeze_totals(
        values: dict[str, dict[str, Decimal | None]],
    ) -> tuple[LedgerTotals, ...]:
        return tuple(
            LedgerTotals(currency=currency, **values[currency])
            for currency in sorted(values)
        )

    def freeze(self) -> DailyLedgerState:
        base_unavailable = any(
            position.historical_base_cost is None
            for position in self.positions.values()
        ) or any(
            item.recognition_base_amount is None
            for item in self.pending.values()
        ) or any(
            item.base_economic_difference is None
            for item in self.fx_conversions
        )
        return DailyLedgerState(
            as_of_date=self.as_of_date,
            cash_balances=tuple(
                CashBalance(account_id=key[0], currency=key[1], amount=value)
                for key, value in sorted(self.cash.items())
            ),
            pending_settlements=tuple(
                self.pending[key] for key in sorted(self.pending)
            ),
            positions=tuple(
                self.positions[key] for key in sorted(self.positions)
            ),
            cumulative_totals=self._freeze_totals(self._cumulative_totals),
            daily_totals=self._freeze_totals(self._daily_totals),
            dispositions=tuple(self.dispositions),
            daily_dispositions=tuple(self.daily_dispositions),
            fx_conversions=tuple(self.fx_conversions),
            dividend_reinvestments=tuple(self.dividend_reinvestments),
            opening_anchor=self.opening_anchor,
            historical_base_coverage_status=(
                BaseCoverageStatus.UNAVAILABLE
                if base_unavailable
                else BaseCoverageStatus.COMPLETE
            ),
            historical_base_coverage_reasons=(
                (LedgerReasonCode.BASE_FX_UNAVAILABLE,)
                if base_unavailable
                else ()
            ),
        )


def _add_position_lot(
    builder: _LedgerBuilder,
    *,
    event_id: str,
    account_id: str,
    instrument_id: str,
    currency: str,
    base_currency: str,
    method: CostBasisMethod,
    lot: PositionLot,
) -> None:
    key = _position_key(account_id, instrument_id)
    existing = builder.positions.get(key)
    if existing is None:
        builder.positions[key] = _position_from_lots(
            account_id=account_id,
            instrument_id=instrument_id,
            currency=currency,
            base_currency=base_currency,
            method=method,
            lots=(lot,),
        )
        return
    builder.check_position_contract(
        existing,
        currency=currency,
        base_currency=base_currency,
        event_id=event_id,
    )
    if existing.cost_basis_method is not method:
        builder.fail(
            LedgerReasonCode.COST_BASIS_METHOD_MISMATCH,
            event_id=event_id,
            message="position cost basis method cannot change during replay",
        )
    if any(item.lot_id == lot.lot_id for item in existing.lots):
        builder.fail(
            LedgerReasonCode.LOT_STATE_NOT_CLOSED,
            event_id=event_id,
            message=f"duplicate lot id {lot.lot_id}",
        )
    lots = canonical_lots(existing.lots + (lot,))
    if method is CostBasisMethod.MOVING_AVERAGE:
        lots = _rebalance_moving_average(lots)
    builder.positions[key] = _position_from_lots(
        account_id=account_id,
        instrument_id=instrument_id,
        currency=currency,
        base_currency=base_currency,
        method=method,
        lots=lots,
    )


def _remove_position_lots(
    builder: _LedgerBuilder,
    *,
    event_id: str,
    account_id: str,
    instrument_id: str,
    quantity: Decimal,
) -> tuple[PositionState, tuple[_RemovedLot, ...]]:
    position = builder.position(account_id, instrument_id, event_id=event_id)
    if quantity > position.quantity:
        builder.fail(
            LedgerReasonCode.OVERSELL,
            event_id=event_id,
            message=(
                f"quantity {quantity} exceeds available {position.quantity}"
            ),
        )
    remaining_quantity = quantity
    remaining_lots: list[PositionLot] = []
    removed: list[_RemovedLot] = []
    for lot in position.lots:
        if remaining_quantity == 0:
            remaining_lots.append(lot)
            continue
        taken = min(remaining_quantity, lot.quantity)
        if taken == lot.quantity:
            local_cost = lot.local_cost
            base_cost = lot.historical_base_cost
        else:
            local_numerator = exact_decimal_product(lot.local_cost, taken)
            base_numerator = (
                None
                if lot.historical_base_cost is None
                else exact_decimal_product(lot.historical_base_cost, taken)
            )
            with calculation_context():
                local_cost = local_numerator / lot.quantity
                base_cost = (
                    None
                    if base_numerator is None
                    else base_numerator / lot.quantity
                )
            remaining_lots.append(
                replace(
                    lot,
                    quantity=exact_decimal_subtract(lot.quantity, taken),
                    local_cost=exact_decimal_subtract(lot.local_cost, local_cost),
                    historical_base_cost=(
                        None
                        if lot.historical_base_cost is None
                        else exact_decimal_subtract(
                            lot.historical_base_cost,
                            base_cost,  # type: ignore[arg-type]
                        )
                    ),
                )
            )
        removed.append(
            _RemovedLot(
                lot=lot,
                quantity=taken,
                local_cost=local_cost,
                historical_base_cost=base_cost,
            )
        )
        remaining_quantity = exact_decimal_subtract(remaining_quantity, taken)
    if remaining_quantity != 0:
        builder.fail(
            LedgerReasonCode.OVERSELL,
            event_id=event_id,
            message="lot removal did not consume requested quantity",
        )
    key = _position_key(account_id, instrument_id)
    if remaining_lots:
        lots = canonical_lots(tuple(remaining_lots))
        if position.cost_basis_method is CostBasisMethod.MOVING_AVERAGE:
            lots = _rebalance_moving_average(lots)
        builder.positions[key] = _position_from_lots(
            account_id=position.account_id,
            instrument_id=position.instrument_id,
            currency=position.currency,
            base_currency=position.base_currency,
            method=position.cost_basis_method,
            lots=lots,
        )
    else:
        builder.positions.pop(key)
    return position, tuple(removed)


def _make_dispositions(
    *,
    event_id: str,
    disposition_date: date,
    position: PositionState,
    removed: tuple[_RemovedLot, ...],
    local_net_proceeds: Decimal,
    base_net_proceeds: Decimal | None,
    disposition_fx_rate: Decimal | None,
    lineage: FactLineage,
    disposition_fx_lineage: FactLineage | None,
) -> tuple[LotDisposition, ...]:
    weights = tuple(item.quantity for item in removed)
    local_allocations = _allocate_exact(local_net_proceeds, weights)
    if (base_net_proceeds is None) != (disposition_fx_rate is None):
        raise LedgerContractError(
            "base disposition proceeds and FX rate must be available together"
        )
    if disposition_fx_rate is not None:
        expected_base_total = exact_decimal_product(
            local_net_proceeds,
            disposition_fx_rate,
        )
        if base_net_proceeds != expected_base_total:
            raise LedgerContractError(
                "base disposition proceeds do not close to local proceeds and FX"
            )
    values: list[LotDisposition] = []
    for index, item in enumerate(removed):
        base_proceeds = (
            None
            if disposition_fx_rate is None
            else exact_decimal_product(
                local_allocations[index],
                disposition_fx_rate,
            )
        )
        base_realized = (
            None
            if base_proceeds is None or item.historical_base_cost is None
            else exact_decimal_subtract(
                base_proceeds,
                item.historical_base_cost,
            )
        )
        values.append(
            LotDisposition(
                disposition_id=f"{event_id}:{item.lot.lot_id}:{index}",
                event_id=event_id,
                disposition_date=disposition_date,
                account_id=position.account_id,
                instrument_id=position.instrument_id,
                currency=position.currency,
                base_currency=position.base_currency,
                lot_id=item.lot.lot_id,
                quantity=item.quantity,
                released_local_cost=item.local_cost,
                released_historical_base_cost=item.historical_base_cost,
                allocated_local_net_proceeds=local_allocations[index],
                allocated_base_net_proceeds=base_proceeds,
                realized_pnl_local=exact_decimal_subtract(
                    local_allocations[index],
                    item.local_cost,
                ),
                realized_pnl_base=base_realized,
                source_lineage=item.lot.lineage,
                source_custody_lineage=item.lot.custody_lineage,
                source_acquisition_fx_lineage=item.lot.acquisition_fx_lineage,
                source_cost_lineages=item.lot.cost_source_lineages,
                source_cost_fx_lineages=item.lot.cost_fx_lineages,
                disposition_lineage=lineage,
                disposition_fx_lineage=disposition_fx_lineage,
            )
        )
    return tuple(values)


def _reduce_position_cost(
    builder: _LedgerBuilder,
    *,
    event: ReturnOfCapitalEvent,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
    position = builder.position(
        event.position_account_id,
        event.instrument_id,
        event_id=event.event_id,
    )
    builder.check_position_contract(
        position,
        currency=event.currency,
        base_currency=event.base_currency,
        event_id=event.event_id,
    )
    local_reduction = min(event.gross_amount, position.local_cost)
    local_excess = exact_decimal_subtract(event.gross_amount, local_reduction)
    local_allocations = _allocate_exact(
        local_reduction,
        tuple(lot.local_cost for lot in position.lots),
    )

    base_gross = _base_value(event.gross_amount, event.local_to_base_rate)
    base_reduction: Decimal | None
    base_excess: Decimal | None
    base_allocations: tuple[Decimal, ...] | None
    if base_gross is None or position.historical_base_cost is None:
        base_reduction = None
        base_excess = None
        base_allocations = None
    else:
        base_reduction = min(base_gross, position.historical_base_cost)
        base_excess = exact_decimal_subtract(base_gross, base_reduction)
        base_allocations = _allocate_exact(
            base_reduction,
            tuple(
                lot.historical_base_cost
                for lot in position.lots
                if lot.historical_base_cost is not None
            ),
        )

    lots = tuple(
        replace(
            lot,
            local_cost=exact_decimal_subtract(
                lot.local_cost,
                local_allocations[index],
            ),
            historical_base_cost=(
                None
                if base_allocations is None
                else exact_decimal_subtract(
                    lot.historical_base_cost,  # type: ignore[arg-type]
                    base_allocations[index],
                )
            ),
            acquisition_fx_lineage=(
                None
                if base_allocations is None
                else lot.acquisition_fx_lineage
            ),
            cost_source_lineages=_merge_lineages(
                lot.cost_source_lineages,
                (event.lineage,),
            ),
            cost_fx_lineages=(
                ()
                if base_allocations is None
                else _merge_lineages(
                    lot.cost_fx_lineages,
                    (
                        ()
                        if event.local_to_base_lineage is None
                        else (event.local_to_base_lineage,)
                    ),
                )
            ),
        )
        for index, lot in enumerate(position.lots)
    )
    if position.cost_basis_method is CostBasisMethod.MOVING_AVERAGE:
        lots = _rebalance_moving_average(lots)
    builder.positions[_position_key(position.account_id, position.instrument_id)] = (
        _position_from_lots(
            account_id=position.account_id,
            instrument_id=position.instrument_id,
            currency=position.currency,
            base_currency=position.base_currency,
            method=position.cost_basis_method,
            lots=lots,
        )
    )
    return local_reduction, local_excess, base_reduction, base_excess
