"""Pure exact-to-publication builder for Portfolio Daily financial outputs.

The ledger and valuation engines own economic measurement.  This module owns
only the versioned publication boundary: strict correspondence checks,
HALF_EVEN quantization, deterministic balanced rounding, explicit closure
adjustments, and typed output rows.  It never reconstructs a missing economic
fact from an approximation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from portfolio_app.calculations.numeric import (
    AMOUNT_SCALE,
    FX_RATE_SCALE,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    PRICE_SCALE,
    QUANTITY_SCALE,
    BalancedRoundingInputRow,
    CalculationNumericError,
    balanced_round_rows,
    calculation_context,
    quantize_decimal,
    quantum_for_scale,
    require_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.attribution_contracts import (
    AttributionContractError,
    ExactGroupAttribution,
    ExactGroupAttributionSeries,
)
from portfolio_app.calculations.portfolio_daily.attribution_engine import (
    calculate_exact_group_attribution,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    OUTPUT_TABLES,
    portfolio_daily_balance_output,
    portfolio_daily_contribution_output,
    portfolio_daily_holding_output,
    portfolio_daily_lot_disposition_output,
    portfolio_daily_lot_output,
    portfolio_daily_run_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    FactLineage,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    BaseCoverageStatus,
    DailyLedgerSnapshot,
    LEDGER_TOTAL_EFFECT_FIELDS,
    LedgerEffectKind,
    LedgerSeriesResult,
    LedgerStatus,
    LotDisposition,
    PositionLot,
    PositionState,
)
from portfolio_app.calculations.portfolio_daily.output_repository import (
    PortfolioDailyFinancialOutputs,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    DailyCalculationStatus,
    FxStatus,
    PortfolioDailyInput,
    TwrAccumulatorState,
    TwrWindowStatus,
    ValuationStatus,
    advance_daily_twr,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    BalanceComponentType,
    ExactDailyPortfolioValuation,
    ExactPortfolioValuationSeries,
    ValuationEndpointStatus,
    ValuationReasonCode,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
)


POSITION_ATTRIBUTION_UNAVAILABLE = "position_attribution_inputs_unavailable"
HOLDING_BOOK_PNL_DETAIL_UNAVAILABLE = "holding_book_pnl_detail_unavailable"
GROUP_PERIOD_UNAVAILABLE = "group_return_period_unavailable"
HISTORICAL_BASE_COST_UNAVAILABLE = "historical_base_cost_unavailable"
DISPOSITION_FX_UNAVAILABLE = "fx_path_unavailable"
FLOW_BASE_MEASUREMENT_UNAVAILABLE = "external_flow_base_measurement_unavailable"
MATCHING_POLICY_VERSION = "portfolio-daily-lot-matching.v1"

_ZERO = Decimal("0")
_ONE = Decimal("1")
_MONEY_QUANTUM = quantum_for_scale(scale=AMOUNT_SCALE)


class PortfolioDailyOutputBuildError(RuntimeError):
    """The exact engines cannot be represented by the output-v1 contract."""


def _reject_float(value: object, *, path: str) -> None:
    if isinstance(value, float):
        raise PortfolioDailyOutputBuildError(f"{path} must not contain a float")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_float(item, path=f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _reject_float(item, path=f"{path}[{index}]")


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        return require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(str(exc)) from exc


def _quantize(value: Decimal, *, scale: int, field_name: str) -> Decimal:
    try:
        return quantize_decimal(value, scale=scale, field_name=field_name)
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(str(exc)) from exc


def _optional_quantize(
    value: Decimal | None,
    *,
    scale: int,
    field_name: str,
) -> Decimal | None:
    return (
        None
        if value is None
        else _quantize(
            value,
            scale=scale,
            field_name=field_name,
        )
    )


def _money(value: Decimal, *, field_name: str) -> Decimal:
    return _quantize(value, scale=AMOUNT_SCALE, field_name=field_name)


def _optional_money(value: Decimal | None, *, field_name: str) -> Decimal | None:
    return _optional_quantize(value, scale=AMOUNT_SCALE, field_name=field_name)


def _rate(value: Decimal, *, field_name: str) -> Decimal:
    return _quantize(value, scale=FX_RATE_SCALE, field_name=field_name)


def _optional_rate(value: Decimal | None, *, field_name: str) -> Decimal | None:
    return _optional_quantize(value, scale=FX_RATE_SCALE, field_name=field_name)


def _optional_method_value(
    value: Decimal | None,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None
    try:
        return require_method_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(str(exc)) from exc


def _optional_wealth_rounding_adjustment(
    value: Decimal | None,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None
    try:
        return require_exact_numeric_typmod(
            value,
            precision=METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
            scale=METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
            field_name=field_name,
        )
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(str(exc)) from exc


def _quantity(value: Decimal, *, field_name: str) -> Decimal:
    return _quantize(value, scale=QUANTITY_SCALE, field_name=field_name)


def _price(value: Decimal, *, field_name: str) -> Decimal:
    return _quantize(value, scale=PRICE_SCALE, field_name=field_name)


def _optional_price(value: Decimal | None, *, field_name: str) -> Decimal | None:
    return _optional_quantize(value, scale=PRICE_SCALE, field_name=field_name)


def _exact_sum(values: Iterable[Decimal]) -> Decimal:
    return exact_decimal_sum(tuple(values))


def _divide(left: Decimal, right: Decimal, *, field_name: str) -> Decimal:
    if right == 0:
        raise PortfolioDailyOutputBuildError(f"{field_name} denominator is zero")
    try:
        with calculation_context():
            return left / right
    except ArithmeticError as exc:
        raise PortfolioDailyOutputBuildError(
            f"{field_name} cannot be calculated exactly under precision 50"
        ) from exc


def _reason_values(values: Iterable[object]) -> tuple[str, ...]:
    resolved: set[str] = set()
    for value in values:
        reason = value.value if hasattr(value, "value") else value
        if not isinstance(reason, str) or not reason or reason != reason.strip():
            raise PortfolioDailyOutputBuildError("reason code is not canonical")
        resolved.add(reason)
    return tuple(sorted(resolved))


def _bridge_rounding_adjustment(
    *,
    left: Decimal,
    right_without_adjustment: Decimal,
    field_name: str,
    max_quanta: int,
) -> Decimal:
    if (
        isinstance(max_quanta, bool)
        or not isinstance(max_quanta, int)
        or max_quanta <= 0
    ):
        raise PortfolioDailyOutputBuildError(
            "rounding bridge max_quanta must be a positive integer"
        )
    adjustment = exact_decimal_subtract(left, right_without_adjustment)
    limit = exact_decimal_product(_MONEY_QUANTUM, Decimal(max_quanta))
    if adjustment.copy_abs() > limit:
        raise PortfolioDailyOutputBuildError(
            f"{field_name} requires {adjustment}, exceeding its {max_quanta}-quantum "
            "multi-component bridge bound"
        )
    return adjustment.copy_abs() if adjustment.is_zero() else adjustment


def _balanced_money(
    rows: Sequence[tuple[tuple[str, ...], Decimal]],
    *,
    field_name: str,
) -> dict[tuple[str, ...], tuple[Decimal, Decimal]]:
    ordered = tuple(sorted(rows, key=lambda item: item[0]))
    if not ordered:
        return {}
    try:
        result = balanced_round_rows(
            tuple(
                BalancedRoundingInputRow(natural_key=key, exact_value=value)
                for key, value in ordered
            ),
            exact_aggregate=_exact_sum(value for _, value in ordered),
            scale=AMOUNT_SCALE,
        )
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(
            f"{field_name} balanced rounding failed: {exc}"
        ) from exc
    return {
        row.natural_key: (row.published_value, row.rounding_adjustment)
        for row in result.rows
    }


def _balanced_rate(
    rows: Sequence[tuple[tuple[str, ...], Decimal]],
    *,
    field_name: str,
) -> dict[tuple[str, ...], tuple[Decimal, Decimal]]:
    ordered = tuple(sorted(rows, key=lambda item: item[0]))
    if not ordered:
        return {}
    try:
        result = balanced_round_rows(
            tuple(
                BalancedRoundingInputRow(natural_key=key, exact_value=value)
                for key, value in ordered
            ),
            exact_aggregate=_exact_sum(value for _, value in ordered),
            scale=FX_RATE_SCALE,
        )
    except CalculationNumericError as exc:
        raise PortfolioDailyOutputBuildError(
            f"{field_name} balanced rounding failed: {exc}"
        ) from exc
    return {
        row.natural_key: (row.published_value, row.rounding_adjustment)
        for row in result.rows
    }


@dataclass(frozen=True, slots=True)
class _TransactionRevision:
    transaction_id: str
    revision_id: str
    revision_number: int
    transaction_type: str | None
    account_id: str | None
    is_tombstone: bool


class _ManifestIndex:
    def __init__(self, manifest: SealedPortfolioDailyManifest) -> None:
        if not isinstance(manifest, SealedPortfolioDailyManifest):
            raise PortfolioDailyOutputBuildError(
                "manifest must be a SealedPortfolioDailyManifest"
            )
        _reject_float(manifest.dependencies.rows_by_table, path="manifest.dependencies")
        rows = manifest.dependencies.rows_by_table
        configs = tuple(rows["portfolio_daily_config_input"])
        if len(configs) != 1:
            raise PortfolioDailyOutputBuildError(
                "sealed manifest requires exactly one portfolio config"
            )
        self.manifest = manifest
        self.config = configs[0]
        self.range_start = self._date(self.config.get("range_start"), "range_start")
        configured_end = self._date(
            self.config.get("effective_as_of"),
            "effective_as_of",
        )
        if configured_end != manifest.effective_as_of:
            raise PortfolioDailyOutputBuildError(
                "manifest effective_as_of does not match its frozen config"
            )
        self.range_end = configured_end
        self.base_currency = self._currency(self.config.get("base_currency"))
        if self.config.get("portfolio_id") != manifest.portfolio_id:
            raise PortfolioDailyOutputBuildError(
                "manifest portfolio_id does not match its frozen config"
            )

        self.transactions: dict[tuple[str, str], _TransactionRevision] = {}
        for row in rows["portfolio_daily_transaction_input"]:
            transaction_id = self._text(row.get("transaction_id"), "transaction_id")
            revision_id = self._text(row.get("revision_id"), "revision_id")
            revision_number = self._positive_integer(
                row.get("revision_number"),
                "revision_number",
            )
            raw_tombstone = row.get("is_tombstone", False)
            if type(raw_tombstone) is not bool:
                raise PortfolioDailyOutputBuildError(
                    "manifest is_tombstone must be bool"
                )
            transaction_type = (
                None
                if raw_tombstone
                else self._text(row.get("transaction_type"), "transaction_type")
            )
            account_id = row.get("account_id")
            if account_id is not None:
                account_id = self._text(account_id, "account_id")
            key = transaction_id, revision_id
            if key in self.transactions:
                raise PortfolioDailyOutputBuildError(
                    "duplicate frozen transaction revision identity"
                )
            self.transactions[key] = _TransactionRevision(
                transaction_id=transaction_id,
                revision_id=revision_id,
                revision_number=revision_number,
                transaction_type=transaction_type,
                account_id=account_id,
                is_tombstone=raw_tombstone,
            )

        self.account_methods: dict[str, CostBasisMethod] = {}
        for row in rows["portfolio_daily_account_input"]:
            account_id = self._text(row.get("account_id"), "account_id")
            raw_method = row.get("cost_basis_method")
            if raw_method is None:
                continue
            try:
                method = CostBasisMethod(str(raw_method))
            except ValueError as exc:
                raise PortfolioDailyOutputBuildError(
                    f"unsupported cost basis method for account {account_id}"
                ) from exc
            self.account_methods[account_id] = method

        self.fx_paths: dict[str, Mapping[str, object]] = {}
        for row in rows["portfolio_daily_fx_path"]:
            path_id = self._text(str(row.get("fx_path_id")), "fx_path_id")
            if path_id in self.fx_paths:
                raise PortfolioDailyOutputBuildError("duplicate frozen FX path")
            self.fx_paths[path_id] = row

    @staticmethod
    def _text(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise PortfolioDailyOutputBuildError(
                f"manifest {field_name} must be a canonical string"
            )
        return value

    @staticmethod
    def _date(value: object, field_name: str) -> date:
        if type(value) is not date:
            raise PortfolioDailyOutputBuildError(
                f"manifest {field_name} must be a date"
            )
        return value

    @staticmethod
    def _currency(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 3
            or value != value.upper()
            or not value.isascii()
            or not value.isalpha()
        ):
            raise PortfolioDailyOutputBuildError(
                "manifest base_currency must be uppercase ISO-like currency"
            )
        return value

    @staticmethod
    def _positive_integer(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PortfolioDailyOutputBuildError(
                f"manifest {field_name} must be a positive integer"
            )
        return value

    def revision(self, lineage: FactLineage, *, role: str) -> _TransactionRevision:
        key = lineage.source_record_id, lineage.source_revision_id
        revision = self.transactions.get(key)
        if revision is None:
            raise PortfolioDailyOutputBuildError(
                f"{role} lineage does not identify a frozen transaction revision"
            )
        if revision.is_tombstone:
            raise PortfolioDailyOutputBuildError(
                f"{role} lineage must not reference a tombstoned transaction"
            )
        expected_key = (
            f"portfolio_daily_transaction_input/{revision.transaction_id}/"
            f"{revision.revision_id}"
        )
        if lineage.manifest_fact_key != expected_key:
            raise PortfolioDailyOutputBuildError(
                f"{role} lineage manifest fact key is inconsistent"
            )
        return revision

    def cost_method(self, account_id: str) -> CostBasisMethod:
        method = self.account_methods.get(account_id)
        if method is None:
            raise PortfolioDailyOutputBuildError(
                f"position account {account_id} has no frozen cost method"
            )
        return method

    def fx_rate(
        self,
        lineage: FactLineage | None,
        *,
        valuation_date: date,
        from_currency: str,
    ) -> Decimal | None:
        if from_currency == self.base_currency:
            return _ONE
        if lineage is None:
            return None
        row = self.fx_paths.get(lineage.source_record_id)
        if row is None:
            raise PortfolioDailyOutputBuildError(
                "FX lineage does not identify a frozen FX path"
            )
        if (
            lineage.manifest_fact_key
            != (
                "portfolio_daily_fx_path/"
                f"{valuation_date.isoformat()}/{from_currency}/{self.base_currency}"
            )
            or row.get("valuation_date") != valuation_date
            or row.get("from_currency") != from_currency
            or row.get("to_currency") != self.base_currency
            or row.get("resolution_status") != "resolved"
        ):
            raise PortfolioDailyOutputBuildError(
                "FX lineage and frozen path contract are inconsistent"
            )
        raw_rate = _decimal(row.get("resolved_rate"), field_name="resolved_rate")
        return _rate(raw_rate, field_name="ledger_fx_rate")


@dataclass(slots=True)
class _BuildRows:
    snapshots: list[dict[str, object]]
    holdings: list[dict[str, object]]
    balances: list[dict[str, object]]
    lots: list[dict[str, object]]
    dispositions: list[dict[str, object]]
    contributions: list[dict[str, object]]


def _optional_ledger_total(
    snapshot: DailyLedgerSnapshot,
    field_name: str,
) -> Decimal | None:
    values: list[Decimal] = []
    for row in snapshot.state.daily_totals:
        value = getattr(row, field_name)
        if value is None:
            return None
        values.append(_decimal(value, field_name=f"ledger_total.{field_name}"))
    return _exact_sum(values)


def _validate_daily_effect_totals(snapshot: DailyLedgerSnapshot) -> None:
    if any(
        effect.effective_date != snapshot.state.as_of_date
        for effect in snapshot.daily_effects
    ):
        raise PortfolioDailyOutputBuildError(
            "daily ledger effects must match their snapshot date"
        )
    actual_rows = snapshot.state.daily_totals
    actual_currencies = tuple(row.currency for row in actual_rows)
    if actual_currencies != tuple(sorted(set(actual_currencies))):
        raise PortfolioDailyOutputBuildError(
            "daily ledger totals must be unique and canonically ordered"
        )
    expected: dict[str, dict[str, Decimal | None]] = {}
    for effect in snapshot.daily_effects:
        deltas = tuple(
            getattr(effect, effect_field)
            for effect_field in LEDGER_TOTAL_EFFECT_FIELDS.values()
        )
        if all(delta == _ZERO for delta in deltas):
            continue
        row = expected.setdefault(
            effect.currency,
            {total_field: _ZERO for total_field in LEDGER_TOTAL_EFFECT_FIELDS},
        )
        for total_field, effect_field in LEDGER_TOTAL_EFFECT_FIELDS.items():
            current = row[total_field]
            delta = getattr(effect, effect_field)
            row[total_field] = (
                None
                if current is None or delta is None
                else _exact_sum((current, delta))
            )
    actual = {
        row.currency: {
            total_field: getattr(row, total_field)
            for total_field in LEDGER_TOTAL_EFFECT_FIELDS
        }
        for row in actual_rows
    }
    if actual != expected:
        raise PortfolioDailyOutputBuildError(
            "daily ledger totals do not close exactly to daily effects"
        )


def _pending_component_type(
    *,
    kind: str,
    local_amount: Decimal,
) -> BalanceComponentType:
    if kind in {
        "trade_settlement",
        "fx_conversion_source_leg",
        "fx_conversion_target_leg",
    }:
        return (
            BalanceComponentType.PENDING_RECEIVABLE
            if local_amount > 0
            else BalanceComponentType.PENDING_PAYABLE
        )
    if kind == "income_accrual" and local_amount > 0:
        return BalanceComponentType.INCOME_ACCRUAL
    if kind == "fee_accrual" and local_amount < 0:
        return BalanceComponentType.FEE_ACCRUAL
    if kind == "tax_accrual" and local_amount < 0:
        return BalanceComponentType.TAX_ACCRUAL
    if (
        kind
        in {
            "return_of_capital_accrual",
            "maturity_accrual",
            "cash_in_lieu_accrual",
        }
        and local_amount > 0
    ):
        return BalanceComponentType.OTHER_ACCRUAL
    raise PortfolioDailyOutputBuildError(
        "ledger pending component sign/type contract is invalid"
    )


def _validate_ledger_balance_correspondence(
    snapshot: DailyLedgerSnapshot,
    day: ExactDailyPortfolioValuation,
) -> None:
    expected: dict[tuple[str, str, str, str], Decimal] = {}
    for cash in snapshot.state.cash_balances:
        key = (
            cash.account_id,
            BalanceComponentType.SETTLED_CASH.value,
            "cash",
            cash.currency,
        )
        if key in expected:
            raise PortfolioDailyOutputBuildError("duplicate ledger cash balance key")
        expected[key] = cash.amount
    for pending in snapshot.state.pending_settlements:
        component = _pending_component_type(
            kind=pending.component_kind.value,
            local_amount=pending.local_amount,
        )
        magnitude = (
            exact_decimal_negate(pending.local_amount)
            if pending.local_amount < 0
            else pending.local_amount
        )
        key = (
            pending.account_id,
            component.value,
            pending.settlement_id,
            pending.currency,
        )
        if key in expected:
            raise PortfolioDailyOutputBuildError("duplicate ledger pending balance key")
        expected[key] = magnitude
    actual = {
        (
            balance.account_id,
            balance.component_type.value,
            balance.component_key,
            balance.currency,
        ): balance.local_amount
        for balance in day.balances
    }
    if actual != expected:
        raise PortfolioDailyOutputBuildError(
            "valuation balances do not correspond one-for-one with ledger balances"
        )


def _validate_disposition_effects(snapshot: DailyLedgerSnapshot) -> None:
    grouped: dict[str, list[LotDisposition]] = defaultdict(list)
    for disposition in snapshot.state.daily_dispositions:
        grouped[disposition.event_id].append(disposition)
    for event_id, dispositions in grouped.items():
        effects = tuple(
            effect
            for effect in snapshot.daily_effects
            if effect.event_id == event_id
            and effect.kind
            in {LedgerEffectKind.SELL_TRADE, LedgerEffectKind.MATURITY_REDEMPTION}
        )
        if not effects:
            raise PortfolioDailyOutputBuildError(
                "daily disposition has no matching ledger economic effect"
            )
        disposition_local = _exact_sum(
            value.realized_pnl_local for value in dispositions
        )
        effect_local = _exact_sum(effect.realized_pnl_delta_local for effect in effects)
        if disposition_local != effect_local:
            raise PortfolioDailyOutputBuildError(
                "lot disposition local P&L does not close to ledger effects"
            )
        disposition_base_values = tuple(
            value.realized_pnl_base for value in dispositions
        )
        effect_base_values = tuple(effect.realized_pnl_delta_base for effect in effects)
        disposition_base = (
            None
            if any(value is None for value in disposition_base_values)
            else _exact_sum(
                value for value in disposition_base_values if value is not None
            )
        )
        effect_base = (
            None
            if any(value is None for value in effect_base_values)
            else _exact_sum(value for value in effect_base_values if value is not None)
        )
        if disposition_base != effect_base:
            raise PortfolioDailyOutputBuildError(
                "lot disposition base P&L does not close to ledger effects"
            )
    disposition_events = set(grouped)
    economic_effect_events = {
        effect.event_id
        for effect in snapshot.daily_effects
        if effect.kind
        in {LedgerEffectKind.SELL_TRADE, LedgerEffectKind.MATURITY_REDEMPTION}
    }
    if disposition_events != economic_effect_events:
        raise PortfolioDailyOutputBuildError(
            "sell and maturity effects must correspond exactly to daily dispositions"
        )


def _validate_ledger_totals(
    snapshot: DailyLedgerSnapshot,
    day: ExactDailyPortfolioValuation,
) -> None:
    expected_flow_in = _optional_ledger_total(snapshot, "external_flow_in_base")
    expected_flow_out = _optional_ledger_total(snapshot, "external_flow_out_base")
    if (
        day.external_flow_in != expected_flow_in
        or day.external_flow_out != expected_flow_out
    ):
        raise PortfolioDailyOutputBuildError(
            "valuation external flows do not close to ledger daily totals"
        )
    if day.book_pnl.measured:
        comparisons = {
            "realized_pnl": "realized_pnl_base",
            "gross_income": "gross_income_base",
            "expensed_fees": "expensed_fees_base",
            "expensed_taxes": "expensed_taxes_base",
            "fx_conversion_effect": "fx_conversion_effect_base",
        }
        for book_field, ledger_field in comparisons.items():
            if getattr(day.book_pnl, book_field) != _optional_ledger_total(
                snapshot,
                ledger_field,
            ):
                raise PortfolioDailyOutputBuildError(
                    f"book {book_field} does not close to ledger daily totals"
                )


def _validate_exact_daily_closure(day: ExactDailyPortfolioValuation) -> None:
    book = day.book_pnl
    if book.measured:
        assert book.economic_pnl is not None
        assert book.realized_pnl is not None
        assert book.unrealized_change is not None
        assert book.gross_income is not None
        assert book.expensed_fees is not None
        assert book.expensed_taxes is not None
        assert book.cash_fx_effect is not None
        assert book.pending_fx_effect is not None
        assert book.accrual_fx_effect is not None
        assert book.fx_conversion_effect is not None
        component_total = _exact_sum(
            (
                book.realized_pnl,
                book.unrealized_change,
                book.gross_income,
                exact_decimal_negate(book.expensed_fees),
                exact_decimal_negate(book.expensed_taxes),
                book.cash_fx_effect,
                book.pending_fx_effect,
                book.accrual_fx_effect,
                book.fx_conversion_effect,
            )
        )
        if component_total != book.economic_pnl:
            raise PortfolioDailyOutputBuildError(
                "book P&L components do not close exactly to economic P&L"
            )
    if book.economic_measured:
        if (
            day.opening_nav is None
            or day.closing_nav is None
            or day.external_flow_in is None
            or day.external_flow_out is None
            or book.economic_pnl is None
        ):
            raise PortfolioDailyOutputBuildError(
                "measured economic P&L requires both NAV boundaries and flows"
            )
        economic = exact_decimal_subtract(
            _exact_sum((day.closing_nav, day.external_flow_out)),
            _exact_sum((day.opening_nav, day.external_flow_in)),
        )
        if economic != book.economic_pnl:
            raise PortfolioDailyOutputBuildError(
                "daily NAV bridge does not close exactly to economic P&L"
            )

    twr = day.twr
    if twr.status is DailyCalculationStatus.CALCULATED:
        assert twr.adjusted_beginning_value is not None
        assert twr.adjusted_ending_value is not None
        assert twr.subperiod_twr_method50 is not None
        expected_return = _divide(
            exact_decimal_subtract(
                twr.adjusted_ending_value,
                twr.adjusted_beginning_value,
            ),
            twr.adjusted_beginning_value,
            field_name="subperiod_twr_method50",
        )
        if expected_return != twr.subperiod_twr_method50:
            raise PortfolioDailyOutputBuildError(
                "subperiod TWR does not close to its exact boundaries"
            )
        if twr.cumulative_twr_method50 is None:
            raise PortfolioDailyOutputBuildError(
                "calculated TWR requires method50 cumulative return"
            )
        if twr.next_state.status is TwrWindowStatus.ACTIVE:
            assert twr.next_state.wealth_index_method50 is not None
            if (
                twr.next_state.wealth_index_method50
                != twr.wealth_index_method50
            ):
                raise PortfolioDailyOutputBuildError(
                    "TWR state/output method50 wealth does not agree"
                )


def _validate_balance_rollup(day: ExactDailyPortfolioValuation) -> None:
    grouped: dict[BalanceComponentType, list[Decimal | None]] = defaultdict(list)
    for balance in day.balances:
        grouped[balance.component_type].append(balance.base_amount)

    def optional_sum(values: Sequence[Decimal | None]) -> Decimal | None:
        if any(value is None for value in values):
            return None
        return _exact_sum(value for value in values if value is not None)

    expected = {
        "settled_cash": optional_sum(grouped[BalanceComponentType.SETTLED_CASH]),
        "pending_receivable": optional_sum(
            grouped[BalanceComponentType.PENDING_RECEIVABLE]
        ),
        "pending_payable": optional_sum(grouped[BalanceComponentType.PENDING_PAYABLE]),
        "accrual_receivable": optional_sum(
            (
                *grouped[BalanceComponentType.INCOME_ACCRUAL],
                *grouped[BalanceComponentType.OTHER_ACCRUAL],
            )
        ),
        "accrual_payable": optional_sum(
            (
                *grouped[BalanceComponentType.FEE_ACCRUAL],
                *grouped[BalanceComponentType.TAX_ACCRUAL],
            )
        ),
    }
    for field_name, expected_value in expected.items():
        if getattr(day, field_name) != expected_value:
            raise PortfolioDailyOutputBuildError(
                f"balance rows do not close exactly to {field_name}"
            )
    if day.coverage_status is not CoverageStatus.COMPLETE:
        return
    settled = expected["settled_cash"]
    receivable = expected["pending_receivable"]
    payable = expected["pending_payable"]
    accrual_receivable = expected["accrual_receivable"]
    accrual_payable = expected["accrual_payable"]
    if any(
        value is None
        for value in (
            settled,
            receivable,
            payable,
            accrual_receivable,
            accrual_payable,
        )
    ):
        raise PortfolioDailyOutputBuildError(
            "complete portfolio NAV cannot contain an unavailable balance"
        )
    assert day.position_market_value is not None
    assert day.closing_nav is not None
    assert settled is not None
    assert receivable is not None
    assert payable is not None
    assert accrual_receivable is not None
    assert accrual_payable is not None
    nav_total = _exact_sum(
        (
            day.position_market_value,
            settled,
            receivable,
            exact_decimal_negate(payable),
            accrual_receivable,
            exact_decimal_negate(accrual_payable),
        )
    )
    if nav_total != day.closing_nav:
        raise PortfolioDailyOutputBuildError(
            "typed holding/balance rows do not close exactly to closing NAV"
        )


def _validate_valuation_aggregation(day: ExactDailyPortfolioValuation) -> None:
    children = (*day.holdings, *day.balances)
    expected_reasons = tuple(
        sorted(
            {reason for child in children for reason in child.reason_codes},
            key=lambda reason: reason.value,
        )
    )
    if day.reason_codes != expected_reasons:
        raise PortfolioDailyOutputBuildError(
            "portfolio valuation reasons do not equal the child reason union"
        )
    known = any(
        holding.market_value_base is not None for holding in day.holdings
    ) or any(balance.base_amount is not None for balance in day.balances)
    expected_coverage = (
        CoverageStatus.COMPLETE
        if day.closing_nav is not None
        else CoverageStatus.PARTIAL
        if known
        else CoverageStatus.UNAVAILABLE
    )
    if day.coverage_status is not expected_coverage:
        raise PortfolioDailyOutputBuildError(
            "portfolio valuation coverage does not match child measurements"
        )
    expected_endpoint_reasons = tuple(
        sorted(
            {reason for child in children for reason in child.endpoint_reason_codes},
            key=lambda reason: reason.value,
        )
    )
    if day.endpoint_reason_codes != expected_endpoint_reasons:
        raise PortfolioDailyOutputBuildError(
            "portfolio endpoint reasons do not equal the child reason union"
        )
    expected_endpoint = (
        ValuationEndpointStatus.UNAVAILABLE
        if day.closing_nav is None
        else ValuationEndpointStatus.CARRY_FORWARD
        if expected_endpoint_reasons
        else ValuationEndpointStatus.FRESH
    )
    if day.endpoint_status is not expected_endpoint:
        raise PortfolioDailyOutputBuildError(
            "portfolio endpoint status does not match child freshness"
        )


def _validate_series(
    index: _ManifestIndex,
    ledger: LedgerSeriesResult,
    valuation: ExactPortfolioValuationSeries,
) -> tuple[tuple[DailyLedgerSnapshot, ExactDailyPortfolioValuation], ...]:
    if not isinstance(ledger, LedgerSeriesResult):
        raise PortfolioDailyOutputBuildError("ledger must be a LedgerSeriesResult")
    if ledger.status is not LedgerStatus.SUCCEEDED:
        raise PortfolioDailyOutputBuildError(
            f"Portfolio Daily output requires a succeeded ledger, not {ledger.status.value}"
        )
    if ledger.reason_codes or ledger.failed_event_id is not None:
        raise PortfolioDailyOutputBuildError(
            "succeeded ledger cannot retain failure evidence"
        )
    if not isinstance(valuation, ExactPortfolioValuationSeries):
        raise PortfolioDailyOutputBuildError(
            "valuation must be an ExactPortfolioValuationSeries"
        )
    ledger_dates = tuple(item.state.as_of_date for item in ledger.snapshots)
    valuation_dates = tuple(item.as_of_date for item in valuation.days)
    if not ledger_dates or ledger_dates != valuation_dates:
        raise PortfolioDailyOutputBuildError(
            "ledger and valuation dates must be identical and non-empty"
        )
    if ledger_dates[0] != index.range_start or ledger_dates[-1] != index.range_end:
        raise PortfolioDailyOutputBuildError(
            "calculated date range does not match the sealed config"
        )
    expected = index.range_start
    for actual in ledger_dates:
        if actual != expected:
            raise PortfolioDailyOutputBuildError(
                "Portfolio Daily v1 requires an unbroken calendar-day series"
            )
        expected = date.fromordinal(expected.toordinal() + 1)
    pairs = tuple(zip(ledger.snapshots, valuation.days, strict=True))
    daily_effects = tuple(
        effect for snapshot in ledger.snapshots for effect in snapshot.daily_effects
    )
    in_range_effects = tuple(
        effect
        for effect in ledger.effects
        if index.range_start <= effect.effective_date <= index.range_end
    )
    if daily_effects != in_range_effects:
        raise PortfolioDailyOutputBuildError(
            "series effects do not correspond exactly to daily snapshot effects"
        )
    twr_state = TwrAccumulatorState.initial()
    previous_valuation: ExactDailyPortfolioValuation | None = None
    for ledger_day, valuation_day in pairs:
        if valuation_day.base_currency != index.base_currency:
            raise PortfolioDailyOutputBuildError(
                "valuation base currency differs from sealed config"
            )
        position_keys = sorted(
            (position.account_id, position.instrument_id)
            for position in ledger_day.state.positions
        )
        holding_keys = [
            (holding.account_id, holding.instrument_id)
            for holding in valuation_day.holdings
        ]
        if position_keys != holding_keys:
            raise PortfolioDailyOutputBuildError(
                "valuation holdings do not correspond one-for-one with ledger positions"
            )
        positions = {
            (position.account_id, position.instrument_id): position
            for position in ledger_day.state.positions
        }
        for position in positions.values():
            if position.base_currency != index.base_currency:
                raise PortfolioDailyOutputBuildError(
                    "ledger position base currency differs from sealed config"
                )
            if position.cost_basis_method is not index.cost_method(position.account_id):
                raise PortfolioDailyOutputBuildError(
                    "ledger position cost method differs from frozen account"
                )
        for pending in ledger_day.state.pending_settlements:
            if pending.base_currency != index.base_currency:
                raise PortfolioDailyOutputBuildError(
                    "ledger pending base currency differs from sealed config"
                )
        for holding in valuation_day.holdings:
            position = positions[(holding.account_id, holding.instrument_id)]
            if (
                holding.quantity != position.quantity
                or holding.currency != position.currency
                or holding.cost_basis_local != position.local_cost
                or holding.cost_basis_base != position.historical_base_cost
            ):
                raise PortfolioDailyOutputBuildError(
                    "holding valuation does not preserve exact ledger quantity/cost"
                )
        holding_base_values = tuple(
            holding.market_value_base for holding in valuation_day.holdings
        )
        exact_position = (
            None
            if any(value is None for value in holding_base_values)
            else _exact_sum(value for value in holding_base_values if value is not None)
        )
        if exact_position != valuation_day.position_market_value:
            raise PortfolioDailyOutputBuildError(
                "holding values do not close exactly to portfolio position value"
            )
        if valuation_day.coverage_status is CoverageStatus.COMPLETE:
            if valuation_day.nav_closure_residual != _ZERO:
                raise PortfolioDailyOutputBuildError(
                    "complete daily NAV does not close exactly"
                )
        _validate_balance_rollup(valuation_day)
        _validate_valuation_aggregation(valuation_day)
        _validate_exact_daily_closure(valuation_day)
        _validate_daily_effect_totals(ledger_day)
        _validate_ledger_balance_correspondence(ledger_day, valuation_day)
        _validate_disposition_effects(ledger_day)
        _validate_ledger_totals(ledger_day, valuation_day)
        if previous_valuation is None:
            if valuation_day.opening_nav is not None:
                raise PortfolioDailyOutputBuildError(
                    "first Portfolio Daily opening NAV must be unavailable"
                )
        elif valuation_day.opening_nav != previous_valuation.closing_nav:
            raise PortfolioDailyOutputBuildError(
                "daily opening NAV does not equal the prior closing boundary"
            )
        flows_available = (
            valuation_day.external_flow_in is not None
            and valuation_day.external_flow_out is not None
        )
        return_ready = valuation_day.closing_nav is not None and flows_available
        performance_coverage = (
            CoverageStatus.COMPLETE
            if return_ready
            else valuation_day.coverage_status
            if valuation_day.closing_nav is None
            else CoverageStatus.PARTIAL
        )
        performance_fx_unavailable = (
            ValuationReasonCode.FX_PATH_UNAVAILABLE in valuation_day.reason_codes
            or not flows_available
        )
        expected_twr = advance_daily_twr(
            PortfolioDailyInput(
                as_of_date=valuation_day.as_of_date,
                measured_nav=(valuation_day.closing_nav if return_ready else None),
                external_flow_in=(
                    valuation_day.external_flow_in
                    if valuation_day.external_flow_in is not None
                    else _ZERO
                ),
                external_flow_out=(
                    valuation_day.external_flow_out
                    if valuation_day.external_flow_out is not None
                    else _ZERO
                ),
                coverage_status=performance_coverage,
                valuation_status=(
                    ValuationStatus.CARRY_FORWARD
                    if valuation_day.endpoint_status
                    is ValuationEndpointStatus.CARRY_FORWARD
                    else ValuationStatus.FRESH
                ),
                fx_status=(
                    FxStatus.UNAVAILABLE
                    if performance_fx_unavailable
                    else FxStatus.AVAILABLE
                ),
            ),
            twr_state,
        )
        if expected_twr != valuation_day.twr:
            raise PortfolioDailyOutputBuildError(
                "valuation TWR outcome does not match exact sequential replay"
            )
        twr_state = expected_twr.next_state
        previous_valuation = valuation_day
        for position in ledger_day.state.positions:
            if _exact_sum(lot.quantity for lot in position.lots) != position.quantity:
                raise PortfolioDailyOutputBuildError("lot quantity closure failed")
            if (
                _exact_sum(lot.local_cost for lot in position.lots)
                != position.local_cost
            ):
                raise PortfolioDailyOutputBuildError("lot local-cost closure failed")
            base_costs = tuple(lot.historical_base_cost for lot in position.lots)
            if position.historical_base_cost is not None:
                if (
                    any(value is None for value in base_costs)
                    or _exact_sum(value for value in base_costs if value is not None)
                    != position.historical_base_cost
                ):
                    raise PortfolioDailyOutputBuildError("lot base-cost closure failed")
    return pairs


def _holding_rounding(
    day: ExactDailyPortfolioValuation,
) -> tuple[
    dict[tuple[str, str], tuple[Decimal, Decimal]],
    dict[tuple[str, str], tuple[Decimal, Decimal]],
]:
    local: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    grouped_local: dict[str, list[tuple[tuple[str, ...], Decimal]]] = defaultdict(list)
    base_inputs: list[tuple[tuple[str, ...], Decimal]] = []
    for holding in day.holdings:
        if holding.coverage_status is not CoverageStatus.COMPLETE:
            continue
        assert holding.market_value_local is not None
        assert holding.market_value_base is not None
        key = (holding.account_id, holding.instrument_id)
        natural = (holding.account_id, holding.instrument_id)
        grouped_local[holding.currency].append((natural, holding.market_value_local))
        base_inputs.append((natural, holding.market_value_base))
        local[key] = (_ZERO, _ZERO)
    for currency, values in sorted(grouped_local.items()):
        rounded = _balanced_money(values, field_name=f"holding local {currency}")
        for natural, result in rounded.items():
            local[(natural[0], natural[1])] = result
    base_result = _balanced_money(base_inputs, field_name="holding base")
    base = {(natural[0], natural[1]): result for natural, result in base_result.items()}
    return local, base


def _build_holding_rows(
    *,
    portfolio_id: str,
    day: ExactDailyPortfolioValuation,
) -> list[dict[str, object]]:
    local_rounding, base_rounding = _holding_rounding(day)
    rows: list[dict[str, object]] = []
    for holding in day.holdings:
        key = holding.account_id, holding.instrument_id
        complete = holding.coverage_status is CoverageStatus.COMPLETE
        if complete:
            local_published: Decimal | None
            base_published: Decimal | None
            local_published, local_adjustment = local_rounding[key]
            base_published, base_adjustment = base_rounding[key]
        else:
            local_published = base_published = None
            local_adjustment = base_adjustment = _ZERO
        closing_nav = day.closing_nav
        weight = None
        if complete and closing_nav is not None and closing_nav != 0:
            assert holding.market_value_base is not None
            weight = _rate(
                _divide(
                    holding.market_value_base,
                    closing_nav,
                    field_name="portfolio_weight",
                ),
                field_name="portfolio_weight",
            )
        valuation_reasons = _reason_values(holding.reason_codes)
        endpoint_reasons = _reason_values(holding.endpoint_reason_codes)
        book_pnl_reasons = (HOLDING_BOOK_PNL_DETAIL_UNAVAILABLE,)
        unavailable_count = (
            int(not complete)
            + 1  # exact per-holding daily book bridge is not provided by valuation-v1
            + 1  # position attribution is not provided by valuation-v1
        )
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "as_of_date": day.as_of_date,
                "account_id": holding.account_id,
                "instrument_id": holding.instrument_id,
                "currency": holding.currency,
                "quantity_exact": holding.quantity,
                "quantity": _quantity(holding.quantity, field_name="quantity"),
                "measured_price": holding.price is not None,
                "measured_market_value": complete,
                "measured_book_pnl": False,
                "unavailable_component_count": unavailable_count,
                "adopted_price_exact": holding.price,
                "price": _optional_price(holding.price, field_name="price"),
                "contract_multiplier_exact": holding.contract_multiplier,
                "contract_multiplier": _optional_quantize(
                    holding.contract_multiplier,
                    scale=QUANTITY_SCALE,
                    field_name="contract_multiplier",
                ),
                "price_factor_exact": holding.price_factor,
                "price_factor": _optional_quantize(
                    holding.price_factor,
                    scale=QUANTITY_SCALE,
                    field_name="price_factor",
                ),
                "adopted_fx_rate_exact": holding.fx_rate_to_base,
                "fx_rate_to_base": _optional_rate(
                    holding.fx_rate_to_base,
                    field_name="fx_rate_to_base",
                ),
                "market_value_local_exact": (
                    holding.market_value_local if complete else None
                ),
                "market_value_base_exact": (
                    holding.market_value_base if complete else None
                ),
                "market_value_local": local_published,
                "market_value_base": base_published,
                "cost_basis_local": _money(
                    holding.cost_basis_local,
                    field_name="cost_basis_local",
                ),
                "cost_basis_base": _optional_money(
                    holding.cost_basis_base,
                    field_name="cost_basis_base",
                ),
                "economic_pnl_daily_base": None,
                "realized_pnl_daily_base": None,
                "unrealized_pnl_beginning_base": None,
                "unrealized_pnl_ending_base": None,
                "unrealized_pnl_change_base": None,
                "gross_income_daily_base": None,
                "return_of_capital_daily_base": None,
                "capitalized_fee_daily_base": None,
                "capitalized_tax_daily_base": None,
                "expensed_fee_daily_base": None,
                "expensed_tax_daily_base": None,
                "disposal_fee_in_realized_daily_base": None,
                "disposal_tax_in_realized_daily_base": None,
                "local_price_effect_daily_base": None,
                "position_fx_effect_daily_base": None,
                "fx_conversion_effect_daily_base": None,
                "pnl_component_rounding_adjustment_base": _ZERO,
                "position_attribution_residual_exact": None,
                "position_attribution_rounding_adjustment_base": _ZERO,
                "portfolio_weight": weight,
                "return_contribution": None,
                "local_rounding_adjustment": local_adjustment,
                "base_rounding_adjustment": base_adjustment,
                "valuation_coverage_state": holding.coverage_status.value,
                "valuation_coverage_reason_codes": valuation_reasons,
                "book_pnl_coverage_state": CoverageStatus.UNAVAILABLE.value,
                "book_pnl_reason_codes": book_pnl_reasons,
                "position_attribution_coverage_state": CoverageStatus.UNAVAILABLE.value,
                "position_attribution_reason_codes": (
                    POSITION_ATTRIBUTION_UNAVAILABLE,
                ),
                "valuation_endpoint_status": holding.endpoint_status.value,
                "valuation_reason_codes": endpoint_reasons,
            }
        )
    return rows


def _build_balance_rows(
    *,
    portfolio_id: str,
    day: ExactDailyPortfolioValuation,
) -> list[dict[str, object]]:
    grouped: dict[str, list[tuple[tuple[str, ...], Decimal]]] = defaultdict(list)

    def rollup_bucket(component: BalanceComponentType) -> str:
        if component in {
            BalanceComponentType.INCOME_ACCRUAL,
            BalanceComponentType.OTHER_ACCRUAL,
        }:
            return "accrual_receivable"
        if component in {
            BalanceComponentType.FEE_ACCRUAL,
            BalanceComponentType.TAX_ACCRUAL,
        }:
            return "accrual_payable"
        return component.value

    for balance in day.balances:
        if balance.coverage_status is CoverageStatus.COMPLETE:
            assert balance.base_amount is not None
            natural = (
                balance.account_id,
                balance.component_type.value,
                balance.component_key,
                balance.currency,
            )
            grouped[rollup_bucket(balance.component_type)].append(
                (natural, balance.base_amount)
            )
    rounded: dict[tuple[str, ...], tuple[Decimal, Decimal]] = {}
    for component_type, values in sorted(grouped.items()):
        rounded.update(_balanced_money(values, field_name=f"balance {component_type}"))

    rows: list[dict[str, object]] = []
    for balance in day.balances:
        local = _money(balance.local_amount, field_name="balance.local_amount")
        if local != balance.local_amount:
            raise PortfolioDailyOutputBuildError(
                "output-v1 cannot represent a balance local amount beyond scale 8"
            )
        natural = (
            balance.account_id,
            balance.component_type.value,
            balance.component_key,
            balance.currency,
        )
        complete = balance.coverage_status is CoverageStatus.COMPLETE
        published, adjustment = rounded.get(natural, (None, _ZERO))
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "as_of_date": day.as_of_date,
                "account_id": balance.account_id,
                "component_type": balance.component_type.value,
                "component_key": balance.component_key,
                "currency": balance.currency,
                "measured_base_amount": complete,
                "local_amount": local,
                "adopted_fx_rate_exact": balance.fx_rate_to_base if complete else None,
                "fx_rate_to_base": (
                    _optional_rate(balance.fx_rate_to_base, field_name="balance.fx")
                    if complete
                    else None
                ),
                "base_amount_exact": balance.base_amount if complete else None,
                "base_amount": published if complete else None,
                "base_rounding_adjustment": adjustment,
                "coverage_state": balance.coverage_status.value,
                "reason_codes": _reason_values(balance.reason_codes),
            }
        )
    return rows


def _lot_rate(
    index: _ManifestIndex,
    *,
    lot: PositionLot,
    position: PositionState,
) -> Decimal | None:
    if lot.historical_base_cost is None:
        return None
    if position.currency == position.base_currency:
        return _ONE
    if len(lot.cost_fx_lineages) != 1:
        return None
    rate = index.fx_rate(
        lot.cost_fx_lineages[0],
        valuation_date=lot.acquisition_date,
        from_currency=position.currency,
    )
    if rate is None:
        return None
    if exact_decimal_product(lot.local_cost, rate) != lot.historical_base_cost:
        # Moving-average pooling and return-of-capital can preserve exact base
        # cost without leaving one meaningful acquisition rate.  Null is the
        # only truthful output in that case; base cost remains measured.
        return None
    return rate


def _unit_cost(
    *,
    total_cost: Decimal,
    quantity: Decimal,
    field_name: str,
) -> tuple[Decimal, Decimal]:
    unit = _price(
        _divide(total_cost, quantity, field_name=field_name),
        field_name=field_name,
    )
    residual = exact_decimal_subtract(
        total_cost,
        exact_decimal_product(quantity, unit),
    )
    return unit, residual


def _build_lot_rows(
    *,
    index: _ManifestIndex,
    day: DailyLedgerSnapshot,
) -> list[dict[str, object]]:
    local_rounding: dict[tuple[str, ...], tuple[Decimal, Decimal]] = {}
    base_rounding: dict[tuple[str, ...], tuple[Decimal, Decimal]] = {}
    for position in sorted(
        day.state.positions,
        key=lambda value: (value.account_id, value.instrument_id),
    ):
        local_values = [
            (
                (position.account_id, position.instrument_id, lot.lot_id),
                lot.local_cost,
            )
            for lot in position.lots
        ]
        local_rounding.update(
            _balanced_money(local_values, field_name="lot local cost")
        )
        base_values = [
            (
                (position.account_id, position.instrument_id, lot.lot_id),
                lot.historical_base_cost,
            )
            for lot in position.lots
            if lot.historical_base_cost is not None
        ]
        if base_values:
            base_rounding.update(
                _balanced_money(base_values, field_name="lot base cost")
            )

    rows: list[dict[str, object]] = []
    for position in sorted(
        day.state.positions,
        key=lambda value: (value.account_id, value.instrument_id),
    ):
        for lot in position.lots:
            source = index.revision(lot.lineage, role="lot acquisition")
            custody = index.revision(lot.custody_lineage, role="lot custody")
            key = position.account_id, position.instrument_id, lot.lot_id
            local_published, local_adjustment = local_rounding[key]
            local_unit, local_unit_residual = _unit_cost(
                total_cost=lot.local_cost,
                quantity=lot.quantity,
                field_name="unit_cost_local",
            )
            measured_base = lot.historical_base_cost is not None
            acquisition_rate = _lot_rate(index, lot=lot, position=position)
            if measured_base:
                assert lot.historical_base_cost is not None
                base_published, base_adjustment = base_rounding[key]
                base_unit, base_unit_residual = _unit_cost(
                    total_cost=lot.historical_base_cost,
                    quantity=lot.quantity,
                    field_name="unit_cost_base",
                )
                base_reasons: tuple[str, ...] = ()
            else:
                base_published = base_adjustment = None
                base_unit = base_unit_residual = None
                base_reasons = (HISTORICAL_BASE_COST_UNAVAILABLE,)
            rows.append(
                {
                    "portfolio_id": index.manifest.portfolio_id,
                    "as_of_date": day.state.as_of_date,
                    "account_id": position.account_id,
                    "instrument_id": position.instrument_id,
                    "lot_id": lot.lot_id,
                    "source_transaction_id": source.transaction_id,
                    "source_revision_id": source.revision_id,
                    "source_revision_number": source.revision_number,
                    "custody_transaction_id": custody.transaction_id,
                    "custody_revision_id": custody.revision_id,
                    "custody_revision_number": custody.revision_number,
                    "acquisition_date": lot.acquisition_date,
                    "currency": position.currency,
                    "open_quantity_exact": lot.quantity,
                    "open_quantity": _quantity(
                        lot.quantity,
                        field_name="open_quantity",
                    ),
                    "measured_base_cost": measured_base,
                    "acquisition_fx_rate_exact": acquisition_rate,
                    "acquisition_fx_rate": _optional_rate(
                        acquisition_rate,
                        field_name="acquisition_fx_rate",
                    ),
                    "cost_basis_local_exact": lot.local_cost,
                    "cost_basis_local": local_published,
                    "unit_cost_local": local_unit,
                    "unit_cost_local_rounding_residual_exact": local_unit_residual,
                    "cost_basis_base_exact": lot.historical_base_cost,
                    "cost_basis_base": base_published,
                    "unit_cost_base": base_unit,
                    "unit_cost_base_rounding_residual_exact": base_unit_residual,
                    "local_cost_rounding_adjustment": local_adjustment,
                    "base_cost_rounding_adjustment": base_adjustment,
                    "base_cost_coverage_state": (
                        BaseCoverageStatus.COMPLETE.value
                        if measured_base
                        else BaseCoverageStatus.UNAVAILABLE.value
                    ),
                    "base_cost_reason_codes": base_reasons,
                }
            )
    return rows


def _disposition_kind(revision: _TransactionRevision) -> str:
    if revision.transaction_type == "sell":
        return "sale"
    if revision.transaction_type == "maturity_redemption":
        return "maturity"
    if (
        revision.transaction_type == "transfer_out"
        # Transfer-out rows are reserved by output-v1 even though ledger-v1
        # carries lots rather than realizing them and therefore normally emits
        # no LotDisposition for a transfer.
    ):
        return "transfer_out"
    raise PortfolioDailyOutputBuildError(
        "lot disposition lineage has an unsupported transaction type"
    )


def _disposition_base_rate(
    index: _ManifestIndex,
    disposition: LotDisposition,
) -> Decimal | None:
    values = (
        disposition.allocated_base_net_proceeds,
        disposition.released_historical_base_cost,
        disposition.realized_pnl_base,
    )
    if any(value is None for value in values):
        return None
    rate = index.fx_rate(
        disposition.disposition_fx_lineage,
        valuation_date=disposition.disposition_date,
        from_currency=disposition.currency,
    )
    if rate is None:
        raise PortfolioDailyOutputBuildError(
            "measured base disposition has no exact disposition-date FX"
        )
    assert disposition.allocated_base_net_proceeds is not None
    expected_proceeds = exact_decimal_product(
        disposition.allocated_local_net_proceeds,
        rate,
    )
    if expected_proceeds != disposition.allocated_base_net_proceeds:
        raise PortfolioDailyOutputBuildError(
            "lot-level base proceeds do not close to local proceeds and exact FX"
        )
    assert disposition.released_historical_base_cost is not None
    assert disposition.realized_pnl_base is not None
    if disposition.realized_pnl_base != exact_decimal_subtract(
        disposition.allocated_base_net_proceeds,
        disposition.released_historical_base_cost,
    ):
        raise PortfolioDailyOutputBuildError(
            "lot-level base realized P&L does not close exactly"
        )
    return rate


def _build_disposition_rows(
    *,
    index: _ManifestIndex,
    ledger_days: Sequence[DailyLedgerSnapshot],
) -> list[dict[str, object]]:
    groups: dict[tuple[date, str], list[LotDisposition]] = defaultdict(list)
    for day in ledger_days:
        for disposition in day.state.daily_dispositions:
            if disposition.disposition_date != day.state.as_of_date:
                raise PortfolioDailyOutputBuildError(
                    "daily disposition date differs from its ledger snapshot"
                )
            groups[(day.state.as_of_date, disposition.event_id)].append(disposition)

    def natural(value: LotDisposition) -> tuple[str, str]:
        return value.lot_id, value.disposition_id

    values_by_date: dict[date, list[LotDisposition]] = defaultdict(list)
    for (as_of_date, _event_id), values in groups.items():
        values_by_date[as_of_date].extend(values)
    day_rounding: dict[
        date,
        tuple[
            dict[tuple[str, ...], tuple[Decimal, Decimal]],
            dict[tuple[str, ...], tuple[Decimal, Decimal]],
            dict[str, Decimal],
            dict[tuple[str, ...], tuple[Decimal, Decimal]],
            dict[tuple[str, ...], tuple[Decimal, Decimal]],
        ],
    ] = {}
    for as_of_date, raw_day_values in sorted(values_by_date.items()):
        day_values = tuple(
            sorted(raw_day_values, key=lambda value: value.disposition_id)
        )
        local_proceeds = _balanced_money(
            [
                (natural(value), value.allocated_local_net_proceeds)
                for value in day_values
            ],
            field_name="daily disposition local proceeds",
        )
        local_cost = _balanced_money(
            [(natural(value), value.released_local_cost) for value in day_values],
            field_name="daily disposition local allocated cost",
        )
        measured_base: dict[str, Decimal] = {}
        for value in day_values:
            rate = _disposition_base_rate(index, value)
            if rate is not None:
                measured_base[value.disposition_id] = rate
        base_proceeds = _balanced_money(
            [
                (natural(value), value.allocated_base_net_proceeds)
                for value in day_values
                if value.disposition_id in measured_base
                and value.allocated_base_net_proceeds is not None
            ],
            field_name="daily disposition base proceeds",
        )
        base_cost = _balanced_money(
            [
                (natural(value), value.released_historical_base_cost)
                for value in day_values
                if value.disposition_id in measured_base
                and value.released_historical_base_cost is not None
            ],
            field_name="daily disposition base allocated cost",
        )
        day_rounding[as_of_date] = (
            local_proceeds,
            local_cost,
            measured_base,
            base_proceeds,
            base_cost,
        )

    rows: list[dict[str, object]] = []
    for (as_of_date, _event_id), raw_values in sorted(groups.items()):
        indexed: list[tuple[int, LotDisposition]] = []
        for value in raw_values:
            try:
                raw_index = value.disposition_id.rsplit(":", 1)[1]
                match_index = int(raw_index)
            except (IndexError, ValueError) as exc:
                raise PortfolioDailyOutputBuildError(
                    "disposition_id must retain the ledger match index suffix"
                ) from exc
            if match_index < 0 or str(match_index) != raw_index:
                raise PortfolioDailyOutputBuildError(
                    "disposition match index must be canonical and non-negative"
                )
            indexed.append((match_index, value))
        indexed.sort(key=lambda item: item[0])
        if [index for index, _ in indexed] != list(range(len(indexed))):
            raise PortfolioDailyOutputBuildError(
                "disposition match indices must be contiguous from zero"
            )
        values = tuple(value for _, value in indexed)
        sequence_by_id = {
            value.disposition_id: match_index + 1 for match_index, value in indexed
        }
        (
            local_proceeds,
            local_cost,
            measured_base,
            base_proceeds,
            base_cost,
        ) = day_rounding[as_of_date]
        for value in values:
            acquisition = index.revision(
                value.source_lineage,
                role="disposition acquisition",
            )
            custody = index.revision(
                value.source_custody_lineage,
                role="disposition custody",
            )
            disposition_revision = index.revision(
                value.disposition_lineage,
                role="disposition",
            )
            method = index.cost_method(value.account_id)
            key = natural(value)
            local_proceeds_value, local_proceeds_adjustment = local_proceeds[key]
            local_cost_value, local_cost_adjustment = local_cost[key]
            local_pnl_value = exact_decimal_subtract(
                local_proceeds_value,
                local_cost_value,
            )
            local_pnl_adjustment = _bridge_rounding_adjustment(
                left=local_pnl_value,
                right_without_adjustment=_money(
                    value.realized_pnl_local,
                    field_name="realized_pnl_local",
                ),
                field_name="realized_pnl_local_rounding_adjustment",
                max_quanta=3,
            )
            is_measured_base = value.disposition_id in measured_base
            if is_measured_base:
                proceeds_base_value, proceeds_base_adjustment = base_proceeds[key]
                cost_base_value, cost_base_adjustment = base_cost[key]
                pnl_base_value = exact_decimal_subtract(
                    proceeds_base_value,
                    cost_base_value,
                )
                assert value.realized_pnl_base is not None
                pnl_base_adjustment = _bridge_rounding_adjustment(
                    left=pnl_base_value,
                    right_without_adjustment=_money(
                        value.realized_pnl_base,
                        field_name="realized_pnl_base",
                    ),
                    field_name="realized_pnl_base_rounding_adjustment",
                    max_quanta=3,
                )
                base_reasons: tuple[str, ...] = ()
            else:
                proceeds_base_value = proceeds_base_adjustment = None
                cost_base_value = cost_base_adjustment = None
                pnl_base_value = pnl_base_adjustment = None
                reason_values: list[str] = []
                if value.released_historical_base_cost is None:
                    reason_values.append(HISTORICAL_BASE_COST_UNAVAILABLE)
                if value.allocated_base_net_proceeds is None:
                    reason_values.append(DISPOSITION_FX_UNAVAILABLE)
                base_reasons = _reason_values(reason_values)
            rows.append(
                {
                    "portfolio_id": index.manifest.portfolio_id,
                    "as_of_date": as_of_date,
                    "account_id": value.account_id,
                    "instrument_id": value.instrument_id,
                    "lot_id": value.lot_id,
                    "acquisition_transaction_id": acquisition.transaction_id,
                    "acquisition_revision_id": acquisition.revision_id,
                    "acquisition_revision_number": acquisition.revision_number,
                    "custody_transaction_id": custody.transaction_id,
                    "custody_revision_id": custody.revision_id,
                    "custody_revision_number": custody.revision_number,
                    "match_sequence": sequence_by_id[value.disposition_id],
                    "disposition_transaction_id": disposition_revision.transaction_id,
                    "disposition_revision_id": disposition_revision.revision_id,
                    "disposition_revision_number": disposition_revision.revision_number,
                    "disposition_date": value.disposition_date,
                    "disposition_kind": _disposition_kind(disposition_revision),
                    "matching_method": method.value,
                    "matching_policy_version": MATCHING_POLICY_VERSION,
                    "currency": value.currency,
                    "disposed_quantity_exact": value.quantity,
                    "disposed_quantity": _quantity(
                        value.quantity,
                        field_name="disposed_quantity",
                    ),
                    "proceeds_local_exact": value.allocated_local_net_proceeds,
                    "proceeds_local": local_proceeds_value,
                    "allocated_cost_local_exact": value.released_local_cost,
                    "allocated_cost_local": local_cost_value,
                    "realized_pnl_local_exact": value.realized_pnl_local,
                    "realized_pnl_local": local_pnl_value,
                    "proceeds_local_rounding_adjustment": local_proceeds_adjustment,
                    "allocated_cost_local_rounding_adjustment": local_cost_adjustment,
                    "realized_pnl_local_rounding_adjustment": local_pnl_adjustment,
                    "measured_base_pnl": is_measured_base,
                    "disposition_fx_rate_exact": measured_base.get(
                        value.disposition_id
                    ),
                    "disposition_fx_rate": _optional_rate(
                        measured_base.get(value.disposition_id),
                        field_name="disposition_fx_rate",
                    ),
                    "proceeds_base_exact": (
                        value.allocated_base_net_proceeds if is_measured_base else None
                    ),
                    "proceeds_base": proceeds_base_value,
                    "allocated_cost_base_exact": (
                        value.released_historical_base_cost
                        if is_measured_base
                        else None
                    ),
                    "allocated_cost_base": cost_base_value,
                    "realized_pnl_base_exact": (
                        value.realized_pnl_base if is_measured_base else None
                    ),
                    "realized_pnl_base": pnl_base_value,
                    "proceeds_base_rounding_adjustment": proceeds_base_adjustment,
                    "allocated_cost_base_rounding_adjustment": cost_base_adjustment,
                    "realized_pnl_base_rounding_adjustment": pnl_base_adjustment,
                    "base_pnl_coverage_state": (
                        CoverageStatus.COMPLETE.value
                        if is_measured_base
                        else CoverageStatus.UNAVAILABLE.value
                    ),
                    "base_pnl_reason_codes": base_reasons,
                }
            )
    return rows


def _published_realized_pnl_by_date(
    *,
    valuation_days: Sequence[ExactDailyPortfolioValuation],
    disposition_rows: Sequence[Mapping[str, object]],
) -> dict[date, Decimal | None]:
    """Roll published disposition P&L into the daily portfolio P&L field.

    Disposition proceeds and cost are balanced across the whole date and the
    published realized amount is their accounting difference.  The snapshot
    must consume that published child total instead of independently rounding
    the same exact realized P&L and creating a visible roll-up break.
    """

    rows_by_date: dict[date, list[Mapping[str, object]]] = defaultdict(list)
    for row in disposition_rows:
        as_of_date = row.get("as_of_date")
        if type(as_of_date) is not date:
            raise PortfolioDailyOutputBuildError(
                "disposition output has an invalid as_of_date"
            )
        rows_by_date[as_of_date].append(row)

    result: dict[date, Decimal | None] = {}
    for day in valuation_days:
        book = day.book_pnl
        if not book.measured:
            result[day.as_of_date] = None
            continue
        assert book.realized_pnl is not None
        rows = rows_by_date.get(day.as_of_date, [])
        if any(not bool(row["measured_base_pnl"]) for row in rows):
            raise PortfolioDailyOutputBuildError(
                "complete daily book P&L cannot contain an unmeasured disposition"
            )
        disposition_exact = _exact_sum(
            _decimal(
                row["realized_pnl_base_exact"],
                field_name="realized_pnl_base_exact",
            )
            for row in rows
        )
        disposition_published = _exact_sum(
            _decimal(
                row["realized_pnl_base"],
                field_name="realized_pnl_base",
            )
            for row in rows
        )
        non_disposition_exact = exact_decimal_subtract(
            book.realized_pnl,
            disposition_exact,
        )
        published = exact_decimal_sum(
            (
                disposition_published,
                _money(
                    non_disposition_exact,
                    field_name="non_disposition_realized_pnl_daily",
                ),
            )
        )
        _bridge_rounding_adjustment(
            left=published,
            right_without_adjustment=_money(
                book.realized_pnl,
                field_name="realized_pnl_daily",
            ),
            field_name="realized_pnl_daily_rollup_adjustment",
            max_quanta=2,
        )
        result[day.as_of_date] = published
    return result


def _return_values(
    day: ExactDailyPortfolioValuation,
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
]:
    twr = day.twr
    state = twr.next_state
    anchor_nav = state.anchor_nav if state.status is TwrWindowStatus.ACTIVE else None
    return (
        twr.subperiod_twr_method50,
        twr.cumulative_twr_method50,
        twr.wealth_index_method50,
        twr.peak_wealth_index_method50,
        twr.drawdown_method50,
        twr.wealth_chain_rounding_adjustment_exact,
        anchor_nav,
    )


def _chain_status(status: DailyCalculationStatus) -> str:
    return {
        DailyCalculationStatus.CALCULATED: "active",
        DailyCalculationStatus.NO_NEW_VALUATION: "no_new_valuation",
        DailyCalculationStatus.BROKEN: "broken",
        DailyCalculationStatus.REANCHORED: "reanchor",
    }[status]


def _snapshot_adjustments(
    day: ExactDailyPortfolioValuation,
    *,
    realized_pnl_published: Decimal | None,
    unrealized_change_published: Decimal | None,
) -> tuple[Decimal, Decimal, Decimal]:
    nav_adjustment = _ZERO
    pnl_bridge_adjustment = _ZERO
    pnl_component_adjustment = _ZERO
    if day.coverage_status is CoverageStatus.COMPLETE:
        assert day.closing_nav is not None
        assert day.position_market_value is not None
        assert day.settled_cash is not None
        assert day.pending_receivable is not None
        assert day.pending_payable is not None
        assert day.accrual_receivable is not None
        assert day.accrual_payable is not None
        rhs = _exact_sum(
            (
                _money(day.position_market_value, field_name="position_market_value"),
                _money(day.settled_cash, field_name="settled_cash"),
                _money(day.pending_receivable, field_name="pending_receivable"),
                exact_decimal_negate(
                    _money(day.pending_payable, field_name="pending_payable")
                ),
                _money(day.accrual_receivable, field_name="accrual_receivable"),
                exact_decimal_negate(
                    _money(day.accrual_payable, field_name="accrual_payable")
                ),
            )
        )
        nav_adjustment = _bridge_rounding_adjustment(
            left=_money(day.closing_nav, field_name="closing_nav"),
            right_without_adjustment=rhs,
            field_name="nav_rounding_adjustment",
            max_quanta=4,
        )

    book = day.book_pnl
    if book.measured:
        assert book.economic_pnl is not None
        assert book.realized_pnl is not None
        assert book.unrealized_change is not None
        assert book.gross_income is not None
        assert book.expensed_fees is not None
        assert book.expensed_taxes is not None
        assert book.cash_fx_effect is not None
        assert book.pending_fx_effect is not None
        assert book.accrual_fx_effect is not None
        assert book.fx_conversion_effect is not None
        if realized_pnl_published is None or unrealized_change_published is None:
            raise PortfolioDailyOutputBuildError(
                "measured book P&L requires publication rollup values"
            )
        rhs = _exact_sum(
            (
                realized_pnl_published,
                unrealized_change_published,
                _money(book.gross_income, field_name="gross_income_daily"),
                exact_decimal_negate(
                    _money(book.expensed_fees, field_name="expensed_fee_daily")
                ),
                exact_decimal_negate(
                    _money(book.expensed_taxes, field_name="expensed_tax_daily")
                ),
                _money(book.cash_fx_effect, field_name="cash_fx_effect_daily"),
                _money(book.pending_fx_effect, field_name="pending_fx_effect_daily"),
                _money(book.accrual_fx_effect, field_name="accrual_fx_effect_daily"),
                _money(
                    book.fx_conversion_effect, field_name="fx_conversion_effect_daily"
                ),
            )
        )
        pnl_component_adjustment = _bridge_rounding_adjustment(
            left=_money(book.economic_pnl, field_name="economic_pnl"),
            right_without_adjustment=rhs,
            field_name="pnl_component_rounding_adjustment",
            max_quanta=5,
        )
    if day.coverage_status is CoverageStatus.COMPLETE and book.economic_measured:
        assert day.closing_nav is not None
        assert day.opening_nav is not None
        assert day.external_flow_in is not None
        assert day.external_flow_out is not None
        assert book.economic_pnl is not None
        left = _exact_sum(
            (
                _money(day.closing_nav, field_name="closing_nav"),
                _money(day.external_flow_out, field_name="external_flow_out"),
            )
        )
        right = _exact_sum(
            (
                _money(day.opening_nav, field_name="opening_nav"),
                _money(day.external_flow_in, field_name="external_flow_in"),
                _money(book.economic_pnl, field_name="economic_pnl"),
            )
        )
        pnl_bridge_adjustment = _bridge_rounding_adjustment(
            left=left,
            right_without_adjustment=right,
            field_name="pnl_rounding_adjustment",
            max_quanta=3,
        )
    return nav_adjustment, pnl_bridge_adjustment, pnl_component_adjustment


def _build_snapshot_row(
    *,
    portfolio_id: str,
    day: ExactDailyPortfolioValuation,
    realized_pnl_published: Decimal | None,
) -> dict[str, object]:
    book = day.book_pnl
    flow_in = _optional_money(
        day.external_flow_in,
        field_name="external_flow_in",
    )
    flow_out = _optional_money(
        day.external_flow_out,
        field_name="external_flow_out",
    )
    measured_flows = flow_in is not None and flow_out is not None
    if measured_flows:
        flow_coverage = CoverageStatus.COMPLETE
        flow_reasons: tuple[str, ...] = ()
    elif flow_in is not None or flow_out is not None:
        flow_coverage = CoverageStatus.PARTIAL
        flow_reasons = (FLOW_BASE_MEASUREMENT_UNAVAILABLE,)
    else:
        flow_coverage = CoverageStatus.UNAVAILABLE
        flow_reasons = (FLOW_BASE_MEASUREMENT_UNAVAILABLE,)
    if book.measured:
        assert book.unrealized_beginning is not None
        assert book.unrealized_ending is not None
        unrealized_beginning_published = _money(
            book.unrealized_beginning,
            field_name="unrealized_pnl_beginning",
        )
        unrealized_ending_published = _money(
            book.unrealized_ending,
            field_name="unrealized_pnl_ending",
        )
        unrealized_change_published = exact_decimal_subtract(
            unrealized_ending_published,
            unrealized_beginning_published,
        )
    else:
        unrealized_beginning_published = None
        unrealized_ending_published = None
        unrealized_change_published = None
    nav_adjustment, pnl_adjustment, component_adjustment = _snapshot_adjustments(
        day,
        realized_pnl_published=realized_pnl_published,
        unrealized_change_published=unrealized_change_published,
    )
    (
        subperiod_twr_method50,
        cumulative_twr_method50,
        wealth_index_method50,
        peak_wealth_index_method50,
        drawdown_method50,
        wealth_chain_rounding_adjustment_exact,
        anchor_nav,
    ) = _return_values(day)
    return_measured = day.twr.status is DailyCalculationStatus.CALCULATED
    if return_measured and any(
        value is None
        for value in (
            subperiod_twr_method50,
            cumulative_twr_method50,
            wealth_index_method50,
            peak_wealth_index_method50,
            drawdown_method50,
            wealth_chain_rounding_adjustment_exact,
        )
    ):
        raise PortfolioDailyOutputBuildError(
            "calculated TWR cannot be represented by output-v1"
        )
    if return_measured:
        return_coverage = CoverageStatus.COMPLETE
        return_reasons: tuple[str, ...] = ()
    else:
        return_coverage = CoverageStatus.UNAVAILABLE
        return_reasons = _reason_values(day.twr.reason_codes)
    nav_reasons = _reason_values(day.reason_codes)
    book_reasons = _reason_values(book.reason_codes)
    endpoint_reasons = _reason_values(day.endpoint_reason_codes)
    unavailable_count = (
        sum(
            holding.coverage_status is not CoverageStatus.COMPLETE
            for holding in day.holdings
        )
        + sum(
            balance.coverage_status is not CoverageStatus.COMPLETE
            for balance in day.balances
        )
        + int(day.coverage_status is not CoverageStatus.COMPLETE)
        + int(not book.measured)
        + int(not return_measured)
        + int(not measured_flows)
        + 1  # position attribution is unavailable under valuation-v1
    )
    state = day.twr.next_state
    anchor_date = state.anchor_date if state.status is TwrWindowStatus.ACTIVE else None
    return {
        "portfolio_id": portfolio_id,
        "as_of_date": day.as_of_date,
        "base_currency": day.base_currency,
        "measured_nav": day.coverage_status is CoverageStatus.COMPLETE,
        "measured_position_market_value": day.position_market_value is not None,
        "measured_book_pnl": book.measured,
        "measured_return": return_measured,
        "measured_external_flows": measured_flows,
        "unavailable_component_count": unavailable_count,
        "opening_nav": _optional_money(day.opening_nav, field_name="opening_nav"),
        "closing_nav": _optional_money(day.closing_nav, field_name="closing_nav"),
        "position_market_value": _optional_money(
            day.position_market_value,
            field_name="position_market_value",
        ),
        "settled_cash": _optional_money(day.settled_cash, field_name="settled_cash"),
        "pending_receivable": _optional_money(
            day.pending_receivable,
            field_name="pending_receivable",
        ),
        "pending_payable": _optional_money(
            day.pending_payable,
            field_name="pending_payable",
        ),
        "accrual_receivable": _optional_money(
            day.accrual_receivable,
            field_name="accrual_receivable",
        ),
        "accrual_payable": _optional_money(
            day.accrual_payable,
            field_name="accrual_payable",
        ),
        "external_flow_in": flow_in,
        "external_flow_out": flow_out,
        "economic_pnl": _optional_money(book.economic_pnl, field_name="economic_pnl"),
        "realized_pnl_daily": (realized_pnl_published if book.measured else None),
        "unrealized_pnl_beginning": (
            unrealized_beginning_published if book.measured else None
        ),
        "unrealized_pnl_ending": (
            unrealized_ending_published if book.measured else None
        ),
        "unrealized_pnl_change": (
            unrealized_change_published if book.measured else None
        ),
        "gross_income_daily": (
            _optional_money(book.gross_income, field_name="gross_income_daily")
            if book.measured
            else None
        ),
        "return_of_capital_daily": None,
        "capitalized_fee_daily": None,
        "capitalized_tax_daily": None,
        "expensed_fee_daily": (
            _optional_money(book.expensed_fees, field_name="expensed_fee_daily")
            if book.measured
            else None
        ),
        "expensed_tax_daily": (
            _optional_money(book.expensed_taxes, field_name="expensed_tax_daily")
            if book.measured
            else None
        ),
        "disposal_fee_in_realized_daily": None,
        "disposal_tax_in_realized_daily": None,
        "local_price_effect_daily": None,
        "position_fx_effect_daily": None,
        "position_attribution_residual_exact": None,
        "position_attribution_rounding_adjustment": _ZERO,
        "cash_fx_effect_daily": (
            _optional_money(book.cash_fx_effect, field_name="cash_fx_effect_daily")
            if book.measured
            else None
        ),
        "pending_fx_effect_daily": (
            _optional_money(
                book.pending_fx_effect, field_name="pending_fx_effect_daily"
            )
            if book.measured
            else None
        ),
        "accrual_fx_effect_daily": (
            _optional_money(
                book.accrual_fx_effect, field_name="accrual_fx_effect_daily"
            )
            if book.measured
            else None
        ),
        "fx_conversion_effect_daily": (
            _optional_money(
                book.fx_conversion_effect,
                field_name="fx_conversion_effect_daily",
            )
            if book.measured
            else None
        ),
        "pnl_component_rounding_adjustment": component_adjustment,
        "subperiod_twr_method50": _optional_method_value(
            subperiod_twr_method50,
            field_name="subperiod_twr_method50",
        ),
        "subperiod_twr_published": _optional_rate(
            subperiod_twr_method50,
            field_name="subperiod_twr_published",
        ),
        "cumulative_twr_method50": _optional_method_value(
            cumulative_twr_method50,
            field_name="cumulative_twr_method50",
        ),
        "cumulative_twr_published": _optional_rate(
            cumulative_twr_method50,
            field_name="cumulative_twr_published",
        ),
        "wealth_index_method50": _optional_method_value(
            wealth_index_method50,
            field_name="wealth_index_method50",
        ),
        "wealth_index_published": _optional_rate(
            wealth_index_method50,
            field_name="wealth_index_published",
        ),
        "peak_wealth_index_method50": _optional_method_value(
            peak_wealth_index_method50,
            field_name="peak_wealth_index_method50",
        ),
        "peak_wealth_index_published": _optional_rate(
            peak_wealth_index_method50,
            field_name="peak_wealth_index_published",
        ),
        "drawdown_method50": _optional_method_value(
            drawdown_method50,
            field_name="drawdown_method50",
        ),
        "drawdown_published": _optional_rate(
            drawdown_method50,
            field_name="drawdown_published",
        ),
        "wealth_chain_rounding_adjustment_exact": (
            _optional_wealth_rounding_adjustment(
                wealth_chain_rounding_adjustment_exact,
                field_name="wealth_chain_rounding_adjustment_exact",
            )
        ),
        "reliable_anchor_date": anchor_date,
        "reliable_anchor_nav_exact": anchor_nav,
        "reliable_anchor_nav": _optional_money(
            anchor_nav,
            field_name="reliable_anchor_nav",
        ),
        "return_period_start_date": day.twr.return_period_start_date,
        "return_period_end_date": (
            day.as_of_date if day.twr.return_period_start_date is not None else None
        ),
        "return_period_day_count": day.twr.return_period_day_count,
        "calculation_status": day.twr.status.value,
        "return_chain_status": _chain_status(day.twr.status),
        "nav_rounding_adjustment": nav_adjustment,
        "pnl_rounding_adjustment": pnl_adjustment,
        "nav_coverage_state": day.coverage_status.value,
        "nav_reason_codes": nav_reasons,
        "book_pnl_coverage_state": (
            CoverageStatus.COMPLETE.value
            if book.measured
            else CoverageStatus.UNAVAILABLE.value
        ),
        "book_pnl_reason_codes": book_reasons,
        "return_coverage_state": return_coverage.value,
        "return_reason_codes": return_reasons,
        "flow_coverage_state": flow_coverage.value,
        "flow_reason_codes": flow_reasons,
        "position_attribution_coverage_state": CoverageStatus.UNAVAILABLE.value,
        "position_attribution_reason_codes": (POSITION_ATTRIBUTION_UNAVAILABLE,),
        "valuation_endpoint_status": day.endpoint_status.value,
        "valuation_reason_codes": endpoint_reasons,
    }


def _portfolio_contribution_row(
    *,
    portfolio_id: str,
    day: ExactDailyPortfolioValuation,
    anchor: ExactDailyPortfolioValuation | None,
    period_days: tuple[ExactDailyPortfolioValuation, ...],
) -> dict[str, object]:
    measured = False
    reasons: tuple[str, ...]
    opening_exact = closing_exact = economic_exact = contribution_method50 = None
    external_in_exact = external_out_exact = None
    opening = closing = economic = contribution_published = None
    flow_in = flow_out = None
    adjustment = _ZERO
    twr = day.twr
    exact_return_period = (
        twr.status is DailyCalculationStatus.CALCULATED
        and anchor is not None
        and bool(period_days)
        and period_days[-1].as_of_date == day.as_of_date
        and twr.return_period_start_date == anchor.as_of_date
        and twr.return_period_day_count == (day.as_of_date - anchor.as_of_date).days
        and anchor.closing_nav is not None
        and day.closing_nav is not None
        and all(
            period_day.book_pnl.economic_measured
            and period_day.book_pnl.economic_pnl is not None
            and period_day.external_flow_in is not None
            and period_day.external_flow_out is not None
            for period_day in period_days
        )
    )
    if exact_return_period:
        assert anchor is not None and anchor.closing_nav is not None
        assert day.closing_nav is not None
        assert twr.subperiod_twr_method50 is not None
        opening_exact = anchor.closing_nav
        closing_exact = day.closing_nav
        external_in_exact = _exact_sum(
            period_day.external_flow_in
            for period_day in period_days
            if period_day.external_flow_in is not None
        )
        external_out_exact = _exact_sum(
            period_day.external_flow_out
            for period_day in period_days
            if period_day.external_flow_out is not None
        )
        economic_exact = _exact_sum(
            period_day.book_pnl.economic_pnl
            for period_day in period_days
            if period_day.book_pnl.economic_pnl is not None
        )
        denominator = exact_decimal_sum((opening_exact, external_in_exact))
        exact_contribution = _divide(
            economic_exact,
            denominator,
            field_name="portfolio contribution",
        )
        if exact_contribution != twr.subperiod_twr_method50:
            raise PortfolioDailyOutputBuildError(
                "portfolio contribution does not close to method50 subperiod TWR"
            )
        exact_bridge = exact_decimal_subtract(
            exact_decimal_sum((closing_exact, external_out_exact)),
            exact_decimal_sum((opening_exact, external_in_exact, economic_exact)),
        )
        if (
            exact_bridge != 0
            or twr.adjusted_beginning_value != denominator
            or twr.adjusted_ending_value
            != exact_decimal_sum((closing_exact, external_out_exact))
        ):
            raise PortfolioDailyOutputBuildError(
                "portfolio return-period contribution bridge does not close exactly"
            )
        opening = _money(opening_exact, field_name="contribution.opening_nav")
        closing = _money(closing_exact, field_name="contribution.closing_nav")
        flow_in = _money(external_in_exact, field_name="contribution.flow_in")
        flow_out = _money(external_out_exact, field_name="contribution.flow_out")
        economic = _money(economic_exact, field_name="contribution.economic_pnl")
        contribution_method50 = exact_contribution
        left = exact_decimal_sum((closing, flow_out))
        right = exact_decimal_sum((opening, flow_in, economic))
        adjustment = _bridge_rounding_adjustment(
            left=left,
            right_without_adjustment=right,
            field_name="contribution.rounding_adjustment_base",
            max_quanta=4,
        )
        contribution_published = _rate(
            exact_contribution,
            field_name="contribution_published",
        )
        measured = True
        reasons = ()
    else:
        reason_values: list[object] = list(twr.reason_codes)
        reason_values.append(GROUP_PERIOD_UNAVAILABLE)
        reasons = _reason_values(reason_values)
    return {
        "portfolio_id": portfolio_id,
        "as_of_date": day.as_of_date,
        "axis": "portfolio",
        "group_key": portfolio_id,
        "group_label": portfolio_id,
        "measured": measured,
        "opening_nav_exact": opening_exact,
        "opening_nav": opening,
        "opening_nav_rounding_adjustment": _ZERO if measured else None,
        "closing_nav_exact": closing_exact,
        "closing_nav": closing,
        "closing_nav_rounding_adjustment": _ZERO if measured else None,
        "external_flow_in_exact": external_in_exact,
        "external_flow_in": flow_in,
        "external_flow_in_rounding_adjustment": _ZERO if measured else None,
        "external_flow_out_exact": external_out_exact,
        "external_flow_out": flow_out,
        "external_flow_out_rounding_adjustment": _ZERO if measured else None,
        "internal_flow_in_exact": _ZERO if measured else None,
        "internal_flow_in": _ZERO if measured else None,
        "internal_flow_in_rounding_adjustment": _ZERO if measured else None,
        "internal_flow_out_exact": _ZERO if measured else None,
        "internal_flow_out": _ZERO if measured else None,
        "internal_flow_out_rounding_adjustment": _ZERO if measured else None,
        "economic_pnl_exact": economic_exact,
        "economic_pnl": economic,
        "economic_pnl_rounding_adjustment": _ZERO if measured else None,
        "contribution_method50": _optional_method_value(
            contribution_method50,
            field_name="contribution_method50",
        ),
        "contribution_published": contribution_published,
        "contribution_division_adjustment_exact": _ZERO,
        "contribution_rounding_adjustment": _ZERO if measured else None,
        "closure_residual_exact": _ZERO,
        "rounding_adjustment_base": adjustment,
        "coverage_state": (
            CoverageStatus.COMPLETE.value
            if measured
            else CoverageStatus.UNAVAILABLE.value
        ),
        "reason_codes": reasons,
    }


def _group_contribution_rows(
    *,
    portfolio_id: str,
    rows: tuple[ExactGroupAttribution, ...],
) -> list[dict[str, object]]:
    if not rows:
        return []
    axes = {row.axis for row in rows}
    dates = {row.as_of_date for row in rows}
    if len(axes) != 1 or len(dates) != 1:
        raise PortfolioDailyOutputBuildError(
            "group publication batch must contain one axis and date"
        )
    measured_states = {row.measured for row in rows}
    if len(measured_states) != 1:
        raise PortfolioDailyOutputBuildError(
            "one axis/date cannot mix measured and unavailable groups"
        )
    if not next(iter(measured_states)):
        return [
            {
                "portfolio_id": portfolio_id,
                "as_of_date": row.as_of_date,
                "axis": row.axis.value,
                "group_key": row.group_key,
                "group_label": row.group_label,
                "measured": False,
                "opening_nav_exact": None,
                "opening_nav": None,
                "opening_nav_rounding_adjustment": None,
                "closing_nav_exact": None,
                "closing_nav": None,
                "closing_nav_rounding_adjustment": None,
                "external_flow_in_exact": None,
                "external_flow_in": None,
                "external_flow_in_rounding_adjustment": None,
                "external_flow_out_exact": None,
                "external_flow_out": None,
                "external_flow_out_rounding_adjustment": None,
                "internal_flow_in_exact": None,
                "internal_flow_in": None,
                "internal_flow_in_rounding_adjustment": None,
                "internal_flow_out_exact": None,
                "internal_flow_out": None,
                "internal_flow_out_rounding_adjustment": None,
                "economic_pnl_exact": None,
                "economic_pnl": None,
                "economic_pnl_rounding_adjustment": None,
                "contribution_method50": None,
                "contribution_published": None,
                "contribution_division_adjustment_exact": _ZERO,
                "contribution_rounding_adjustment": None,
                "closure_residual_exact": row.closure_residual_exact,
                "rounding_adjustment_base": _ZERO,
                "coverage_state": row.coverage_status.value,
                "reason_codes": row.reason_codes,
            }
            for row in rows
        ]
    money_fields = (
        "opening_value",
        "closing_value",
        "external_flow_in",
        "external_flow_out",
        "internal_flow_in",
        "internal_flow_out",
        "economic_pnl",
    )
    rounded: dict[
        str,
        dict[tuple[str, ...], tuple[Decimal, Decimal]],
    ] = {}
    for field_name in money_fields:
        values: list[tuple[tuple[str, ...], Decimal]] = []
        for row in rows:
            value = getattr(row, field_name)
            if value is None:
                raise PortfolioDailyOutputBuildError(
                    "measured group attribution lost an exact value"
                )
            values.append(((row.group_key,), value))
        rounded[field_name] = _balanced_money(
            values,
            field_name=f"group contribution {field_name}",
        )
    contribution_values: list[tuple[tuple[str, ...], Decimal]] = []
    for row in rows:
        if row.contribution_method50 is None:
            raise PortfolioDailyOutputBuildError(
                "measured group attribution lost method50 contribution"
            )
        contribution_values.append(
            (
                (row.group_key,),
                exact_decimal_sum(
                    (
                        row.contribution_method50,
                        row.contribution_division_adjustment_exact,
                    )
                ),
            )
        )
    rounded_contribution = _balanced_rate(
        contribution_values,
        field_name="group contribution rate",
    )
    output: list[dict[str, object]] = []
    for row in rows:
        natural = (row.group_key,)
        exact_values = {
            field_name: getattr(row, field_name) for field_name in money_fields
        }
        published = {
            field_name: rounded[field_name][natural][0] for field_name in money_fields
        }
        field_adjustments = {
            field_name: rounded[field_name][natural][1] for field_name in money_fields
        }
        left = exact_decimal_sum(
            (
                published["closing_value"],
                published["external_flow_out"],
                published["internal_flow_out"],
            )
        )
        right = exact_decimal_sum(
            (
                published["opening_value"],
                published["external_flow_in"],
                published["internal_flow_in"],
                published["economic_pnl"],
            )
        )
        bridge_adjustment = _bridge_rounding_adjustment(
            left=left,
            right_without_adjustment=right,
            field_name="group contribution rounding_adjustment_base",
            max_quanta=11,
        )
        contribution_published, contribution_adjustment = rounded_contribution[
            natural
        ]
        output.append(
            {
                "portfolio_id": portfolio_id,
                "as_of_date": row.as_of_date,
                "axis": row.axis.value,
                "group_key": row.group_key,
                "group_label": row.group_label,
                "measured": True,
                "opening_nav_exact": exact_values["opening_value"],
                "opening_nav": published["opening_value"],
                "opening_nav_rounding_adjustment": field_adjustments["opening_value"],
                "closing_nav_exact": exact_values["closing_value"],
                "closing_nav": published["closing_value"],
                "closing_nav_rounding_adjustment": field_adjustments["closing_value"],
                "external_flow_in_exact": exact_values["external_flow_in"],
                "external_flow_in": published["external_flow_in"],
                "external_flow_in_rounding_adjustment": field_adjustments[
                    "external_flow_in"
                ],
                "external_flow_out_exact": exact_values["external_flow_out"],
                "external_flow_out": published["external_flow_out"],
                "external_flow_out_rounding_adjustment": field_adjustments[
                    "external_flow_out"
                ],
                "internal_flow_in_exact": exact_values["internal_flow_in"],
                "internal_flow_in": published["internal_flow_in"],
                "internal_flow_in_rounding_adjustment": field_adjustments[
                    "internal_flow_in"
                ],
                "internal_flow_out_exact": exact_values["internal_flow_out"],
                "internal_flow_out": published["internal_flow_out"],
                "internal_flow_out_rounding_adjustment": field_adjustments[
                    "internal_flow_out"
                ],
                "economic_pnl_exact": exact_values["economic_pnl"],
                "economic_pnl": published["economic_pnl"],
                "economic_pnl_rounding_adjustment": field_adjustments["economic_pnl"],
                "contribution_method50": _optional_method_value(
                    row.contribution_method50,
                    field_name="contribution_method50",
                ),
                "contribution_published": contribution_published,
                "contribution_division_adjustment_exact": (
                    row.contribution_division_adjustment_exact
                ),
                "contribution_rounding_adjustment": contribution_adjustment,
                "closure_residual_exact": row.closure_residual_exact,
                "rounding_adjustment_base": bridge_adjustment,
                "coverage_state": row.coverage_status.value,
                "reason_codes": row.reason_codes,
            }
        )
    return output


def _build_contribution_rows(
    *,
    portfolio_id: str,
    day: ExactDailyPortfolioValuation,
    anchor: ExactDailyPortfolioValuation | None,
    period_days: tuple[ExactDailyPortfolioValuation, ...],
    attribution: ExactGroupAttributionSeries,
) -> list[dict[str, object]]:
    rows = [
        _portfolio_contribution_row(
            portfolio_id=portfolio_id,
            day=day,
            anchor=anchor,
            period_days=period_days,
        )
    ]
    day_rows = attribution.for_day(day.as_of_date)
    for axis in ("account", "instrument", "currency", "taxonomy"):
        rows.extend(
            _group_contribution_rows(
                portfolio_id=portfolio_id,
                rows=tuple(row for row in day_rows if row.axis.value == axis),
            )
        )
    return rows


def _validate_contribution_publication(
    contribution_rows: Sequence[Mapping[str, object]],
) -> None:
    by_date: dict[date, list[Mapping[str, object]]] = defaultdict(list)
    for row in contribution_rows:
        as_of_date = row["as_of_date"]
        if type(as_of_date) is not date:
            raise PortfolioDailyOutputBuildError(
                "contribution as_of_date must be a date"
            )
        by_date[as_of_date].append(row)
    exact_to_published = (
        ("opening_nav_exact", "opening_nav"),
        ("closing_nav_exact", "closing_nav"),
        ("external_flow_in_exact", "external_flow_in"),
        ("external_flow_out_exact", "external_flow_out"),
        ("economic_pnl_exact", "economic_pnl"),
    )
    for as_of_date, day_rows in sorted(by_date.items()):
        portfolio_rows = [row for row in day_rows if row["axis"] == "portfolio"]
        if len(portfolio_rows) != 1:
            raise PortfolioDailyOutputBuildError(
                "contribution publication requires one portfolio row per date"
            )
        portfolio = portfolio_rows[0]
        for axis in ("account", "instrument", "currency", "taxonomy"):
            groups = [row for row in day_rows if row["axis"] == axis]
            if not bool(portfolio["measured"]):
                if any(bool(row["measured"]) for row in groups):
                    raise PortfolioDailyOutputBuildError(
                        f"{axis} groups cannot be measured when portfolio period is unavailable"
                    )
                continue
            if not groups:
                raise PortfolioDailyOutputBuildError(
                    f"{axis} requires a group slice on {as_of_date}"
                )
            measured_states = {bool(row["measured"]) for row in groups}
            if len(measured_states) != 1:
                raise PortfolioDailyOutputBuildError(
                    f"{axis} cannot mix measured and unavailable groups on {as_of_date}"
                )
            if measured_states == {False}:
                # Secondary attribution may be unavailable even though the
                # primary portfolio return is measured (for example, when
                # offsetting group quotients exceed the method50 domain).
                continue
            for exact_field, published_field in exact_to_published:
                exact_total = _exact_sum(
                    _decimal(row[exact_field], field_name=exact_field) for row in groups
                )
                published_total = _exact_sum(
                    _decimal(row[published_field], field_name=published_field)
                    for row in groups
                )
                if exact_total != portfolio[exact_field]:
                    raise PortfolioDailyOutputBuildError(
                        f"{axis} exact {exact_field} does not roll up to portfolio"
                    )
                if published_total != portfolio[published_field]:
                    raise PortfolioDailyOutputBuildError(
                        f"{axis} published {published_field} does not roll up to portfolio"
                    )
            for suffix in ("exact", "published"):
                in_field = (
                    "internal_flow_in_exact"
                    if suffix == "exact"
                    else "internal_flow_in"
                )
                out_field = (
                    "internal_flow_out_exact"
                    if suffix == "exact"
                    else "internal_flow_out"
                )
                if _exact_sum(
                    _decimal(row[in_field], field_name=in_field) for row in groups
                ) != _exact_sum(
                    _decimal(row[out_field], field_name=out_field) for row in groups
                ):
                    raise PortfolioDailyOutputBuildError(
                        f"{axis} {suffix} internal movements do not cancel"
                    )
            if (
                _exact_sum(
                    exact_decimal_sum(
                        (
                            _decimal(
                                row["contribution_method50"],
                                field_name="contribution_method50",
                            ),
                            _decimal(
                                row["contribution_division_adjustment_exact"],
                                field_name=(
                                    "contribution_division_adjustment_exact"
                                ),
                            ),
                        )
                    )
                    for row in groups
                )
                != exact_decimal_sum(
                    (
                        _decimal(
                            portfolio["contribution_method50"],
                            field_name="portfolio.contribution_method50",
                        ),
                        _decimal(
                            portfolio["contribution_division_adjustment_exact"],
                            field_name=(
                                "portfolio.contribution_division_adjustment_exact"
                            ),
                        ),
                    )
                )
            ):
                raise PortfolioDailyOutputBuildError(
                    f"{axis} effective contributions do not roll up to portfolio"
                )
            if portfolio["contribution_division_adjustment_exact"] != 0:
                raise PortfolioDailyOutputBuildError(
                    "portfolio contribution cannot carry a division adjustment"
                )
            if (
                _exact_sum(
                    _decimal(
                        row["contribution_published"],
                        field_name="contribution_published",
                    )
                    for row in groups
                )
                != portfolio["contribution_published"]
            ):
                raise PortfolioDailyOutputBuildError(
                    f"{axis} published contributions do not roll up to portfolio"
                )
            denominator = _exact_sum(
                (
                    _decimal(
                        portfolio["opening_nav_exact"],
                        field_name="opening_nav_exact",
                    ),
                    _decimal(
                        portfolio["external_flow_in_exact"],
                        field_name="external_flow_in_exact",
                    ),
                )
            )
            for row in groups:
                raw = _divide(
                    _decimal(
                        row["economic_pnl_exact"], field_name="economic_pnl_exact"
                    ),
                    denominator,
                    field_name="group contribution audit",
                )
                if raw != row["contribution_method50"]:
                    raise PortfolioDailyOutputBuildError(
                        "group method50 contribution does not reproduce its division"
                    )
            if (
                _exact_sum(
                    _decimal(
                        row["rounding_adjustment_base"],
                        field_name="rounding_adjustment_base",
                    )
                    for row in groups
                )
                != portfolio["rounding_adjustment_base"]
            ):
                raise PortfolioDailyOutputBuildError(
                    f"{axis} group bridge adjustments do not roll up to portfolio"
                )


def _base_rounding_adjustment_total(rows: _BuildRows) -> Decimal:
    values: list[Decimal] = []
    for row in rows.snapshots:
        values.extend(
            _decimal(row[field], field_name=field)
            for field in (
                "nav_rounding_adjustment",
                "pnl_rounding_adjustment",
                "pnl_component_rounding_adjustment",
                "position_attribution_rounding_adjustment",
            )
        )
    for row in rows.holdings:
        values.extend(
            _decimal(row[field], field_name=field)
            for field in (
                "base_rounding_adjustment",
                "pnl_component_rounding_adjustment_base",
                "position_attribution_rounding_adjustment_base",
            )
        )
    for row in rows.balances:
        values.append(
            _decimal(
                row["base_rounding_adjustment"],
                field_name="base_rounding_adjustment",
            )
        )
    for row in rows.lots:
        value = row["base_cost_rounding_adjustment"]
        if value is not None:
            values.append(_decimal(value, field_name="base_cost_rounding_adjustment"))
    for row in rows.dispositions:
        for field in (
            "proceeds_base_rounding_adjustment",
            "allocated_cost_base_rounding_adjustment",
            "realized_pnl_base_rounding_adjustment",
        ):
            value = row[field]
            if value is not None:
                values.append(_decimal(value, field_name=field))
    for row in rows.contributions:
        values.append(
            _decimal(
                row["rounding_adjustment_base"],
                field_name="rounding_adjustment_base",
            )
        )
        for field_name in (
            "opening_nav_rounding_adjustment",
            "closing_nav_rounding_adjustment",
            "external_flow_in_rounding_adjustment",
            "external_flow_out_rounding_adjustment",
            "internal_flow_in_rounding_adjustment",
            "internal_flow_out_rounding_adjustment",
            "economic_pnl_rounding_adjustment",
        ):
            value = row[field_name]
            if value is not None:
                values.append(_decimal(value, field_name=field_name))
    total = _exact_sum(values)
    published = _money(total, field_name="run.rounding_adjustment_base")
    if published != total:
        raise PortfolioDailyOutputBuildError(
            "base rounding adjustment total is not money-scaled"
        )
    return published


def _run_row(
    *,
    index: _ManifestIndex,
    rows: _BuildRows,
) -> dict[str, object]:
    measured_nav_count = sum(bool(row["measured_nav"]) for row in rows.snapshots)
    # This definition is shared verbatim with pd_validate_publication().  The
    # snapshot count describes unavailable aggregate components; typed child
    # rows are then counted independently as publication evidence.
    unavailable_count = (
        sum(int(row["unavailable_component_count"]) for row in rows.snapshots)
        + sum(int(row["unavailable_component_count"]) for row in rows.holdings)
        + sum(not bool(row["measured_base_amount"]) for row in rows.balances)
        + sum(not bool(row["measured_base_cost"]) for row in rows.lots)
        + sum(not bool(row["measured_base_pnl"]) for row in rows.dispositions)
        + sum(not bool(row["measured"]) for row in rows.contributions)
    )
    reasons: set[str] = set()
    for row in rows.snapshots:
        for field in (
            "nav_reason_codes",
            "book_pnl_reason_codes",
            "return_reason_codes",
            "flow_reason_codes",
            "position_attribution_reason_codes",
            "valuation_reason_codes",
        ):
            reason_values = row[field]
            if not isinstance(reason_values, (tuple, list)):
                raise PortfolioDailyOutputBuildError(
                    f"{field} must be a reason-code sequence"
                )
            reasons.update(_reason_values(reason_values))
    for row in rows.contributions:
        reason_values = row["reason_codes"]
        if not isinstance(reason_values, (tuple, list)):
            raise PortfolioDailyOutputBuildError(
                "contribution reason_codes must be a sequence"
            )
        reasons.update(_reason_values(reason_values))
    if not reasons:
        coverage = CoverageStatus.COMPLETE
    elif measured_nav_count:
        coverage = CoverageStatus.PARTIAL
    else:
        coverage = CoverageStatus.UNAVAILABLE
    canonical_reasons = tuple(sorted(reasons))
    if coverage is CoverageStatus.COMPLETE and canonical_reasons:
        raise PortfolioDailyOutputBuildError("complete run cannot retain reasons")
    if coverage is not CoverageStatus.COMPLETE and not canonical_reasons:
        raise PortfolioDailyOutputBuildError("incomplete run requires reasons")
    return {
        "portfolio_id": index.manifest.portfolio_id,
        "range_start": index.range_start,
        "range_end": index.range_end,
        "methodology_version": METHODOLOGY_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "closure_status": "passed",
        "snapshot_count": len(rows.snapshots),
        "measured_nav_count": measured_nav_count,
        "holding_count": len(rows.holdings),
        "balance_count": len(rows.balances),
        "lot_count": len(rows.lots),
        "lot_disposition_count": len(rows.dispositions),
        "contribution_count": len(rows.contributions),
        "unavailable_component_count": unavailable_count,
        "ledger_balance_residual_exact": _ZERO,
        "nav_bridge_residual_exact": _ZERO,
        "pnl_residual_exact": _ZERO,
        "twr_residual_exact": _ZERO,
        "lot_residual_exact": _ZERO,
        "rounding_adjustment_base": _base_rounding_adjustment_total(rows),
        "coverage_state": coverage.value,
        "reason_codes": canonical_reasons,
    }


def _assert_output_columns(outputs: PortfolioDailyFinancialOutputs) -> None:
    execution = {"run_id", "output_fencing_token", "worker_id", "calculated_at"}
    for table in OUTPUT_TABLES:
        expected = {column.name for column in table.c} - execution
        if table is portfolio_daily_run_output:
            expected.remove("canonical_output_hash")
        for row in outputs.rows_by_table[table.name]:
            actual = set(row)
            if actual != expected:
                raise PortfolioDailyOutputBuildError(
                    f"{table.name} builder columns mismatch: "
                    f"missing={sorted(expected - actual)}, "
                    f"unexpected={sorted(actual - expected)}"
                )


def build_portfolio_daily_financial_outputs(
    manifest: SealedPortfolioDailyManifest,
    ledger_series: LedgerSeriesResult,
    valuation_series: ExactPortfolioValuationSeries,
) -> PortfolioDailyFinancialOutputs:
    """Build all seven output-v1 tables from one sealed exact calculation.

    This function is side-effect free and is the worker-facing seam between
    pure calculation and attempt-fenced persistence.
    """

    index = _ManifestIndex(manifest)
    pairs = _validate_series(index, ledger_series, valuation_series)
    try:
        attribution = calculate_exact_group_attribution(
            manifest,
            ledger_series,
            valuation_series,
        )
    except AttributionContractError as exc:
        raise PortfolioDailyOutputBuildError(
            f"exact group attribution failed: {exc}"
        ) from exc
    rows = _BuildRows(
        snapshots=[],
        holdings=[],
        balances=[],
        lots=[],
        dispositions=[],
        contributions=[],
    )
    rows.dispositions.extend(
        _build_disposition_rows(
            index=index,
            ledger_days=tuple(pair[0] for pair in pairs),
        )
    )
    realized_pnl_by_date = _published_realized_pnl_by_date(
        valuation_days=tuple(pair[1] for pair in pairs),
        disposition_rows=rows.dispositions,
    )
    valuation_days = tuple(pair[1] for pair in pairs)
    valuation_index = {
        valuation_day.as_of_date: index
        for index, valuation_day in enumerate(valuation_days)
    }
    for current_index, (ledger_day, valuation_day) in enumerate(pairs):
        contribution_anchor: ExactDailyPortfolioValuation | None = None
        contribution_period: tuple[ExactDailyPortfolioValuation, ...] = ()
        if valuation_day.twr.status is DailyCalculationStatus.CALCULATED:
            period_start = valuation_day.twr.return_period_start_date
            if period_start is None or period_start not in valuation_index:
                raise PortfolioDailyOutputBuildError(
                    "calculated contribution endpoint has no valuation anchor"
                )
            anchor_index = valuation_index[period_start]
            if anchor_index >= current_index:
                raise PortfolioDailyOutputBuildError(
                    "calculated contribution endpoint must follow its anchor"
                )
            contribution_anchor = valuation_days[anchor_index]
            contribution_period = valuation_days[anchor_index + 1 : current_index + 1]
        rows.holdings.extend(
            _build_holding_rows(
                portfolio_id=manifest.portfolio_id,
                day=valuation_day,
            )
        )
        rows.balances.extend(
            _build_balance_rows(
                portfolio_id=manifest.portfolio_id,
                day=valuation_day,
            )
        )
        rows.lots.extend(_build_lot_rows(index=index, day=ledger_day))
        rows.snapshots.append(
            _build_snapshot_row(
                portfolio_id=manifest.portfolio_id,
                day=valuation_day,
                realized_pnl_published=realized_pnl_by_date[valuation_day.as_of_date],
            )
        )
        rows.contributions.extend(
            _build_contribution_rows(
                portfolio_id=manifest.portfolio_id,
                day=valuation_day,
                anchor=contribution_anchor,
                period_days=contribution_period,
                attribution=attribution,
            )
        )
    _validate_contribution_publication(rows.contributions)
    run = _run_row(index=index, rows=rows)
    output = PortfolioDailyFinancialOutputs(
        MappingProxyType(
            {
                portfolio_daily_run_output.name: (MappingProxyType(run),),
                portfolio_daily_snapshot_output.name: tuple(
                    MappingProxyType(row) for row in rows.snapshots
                ),
                portfolio_daily_holding_output.name: tuple(
                    MappingProxyType(row) for row in rows.holdings
                ),
                portfolio_daily_balance_output.name: tuple(
                    MappingProxyType(row) for row in rows.balances
                ),
                portfolio_daily_lot_output.name: tuple(
                    MappingProxyType(row) for row in rows.lots
                ),
                portfolio_daily_lot_disposition_output.name: tuple(
                    MappingProxyType(row) for row in rows.dispositions
                ),
                portfolio_daily_contribution_output.name: tuple(
                    MappingProxyType(row) for row in rows.contributions
                ),
            }
        )
    )
    _assert_output_columns(output)
    return output


__all__ = [
    "HOLDING_BOOK_PNL_DETAIL_UNAVAILABLE",
    "GROUP_PERIOD_UNAVAILABLE",
    "HISTORICAL_BASE_COST_UNAVAILABLE",
    "MATCHING_POLICY_VERSION",
    "POSITION_ATTRIBUTION_UNAVAILABLE",
    "PortfolioDailyOutputBuildError",
    "build_portfolio_daily_financial_outputs",
]
