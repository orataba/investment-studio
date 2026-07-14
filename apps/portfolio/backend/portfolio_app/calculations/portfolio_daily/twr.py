"""Exact sparse-endpoint TWR and publication closure primitives.

Fund NAVs are asynchronous.  A carried or stale value is a measurable NAV but
is not a new valuation endpoint.  The accumulator therefore keeps the last
fresh anchor across gaps with no pre-endpoint flow.  A flow inside such a gap
breaks the window because its timing cannot be measured exactly; an EOD outflow
after the eventual fresh endpoint remains exactly measurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from enum import StrEnum

from portfolio_app.calculations.numeric import (
    PUBLICATION_FIELD_SCALES,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    CalculationNumericError,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_add,
    method_decimal_divide,
    method_decimal_multiply,
    method_decimal_subtract,
    quantize_decimal,
    quantum_for_scale,
    require_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)


class PortfolioDailyExactError(CalculationNumericError):
    pass


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ValuationStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    CARRY_FORWARD = "carry_forward"


class FxStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class TwrWindowStatus(StrEnum):
    AWAITING_ANCHOR = "awaiting_anchor"
    ACTIVE = "active"
    BROKEN = "broken"


class DailyCalculationStatus(StrEnum):
    CALCULATED = "calculated"
    REANCHORED = "reanchored"
    NO_NEW_VALUATION = "no_new_valuation"
    BROKEN = "broken"


class PortfolioDailyReasonCode(StrEnum):
    INITIAL_ANCHOR = "initial_anchor"
    PARTIAL_COVERAGE = "partial_coverage"
    UNAVAILABLE_COVERAGE = "unavailable_coverage"
    STALE_VALUATION = "stale_valuation"
    CARRY_FORWARD_VALUATION = "carry_forward_valuation"
    FX_UNAVAILABLE = "fx_unavailable"
    GAP_EXTERNAL_FLOW = "gap_external_flow"
    BROKEN_BOUNDARY = "broken_boundary"
    REANCHORED_AFTER_BREAK = "reanchored_after_break"
    NON_POSITIVE_DENOMINATOR = "non_positive_denominator"
    NON_POSITIVE_REANCHOR_VALUE = "non_positive_reanchor_value"
    NEGATIVE_ADJUSTED_ENDING_VALUE = "negative_adjusted_ending_value"
    NUMERIC_FAILURE = "numeric_failure"
    EXACT_ROLLUP_RESIDUAL_EXCEEDED = "exact_rollup_residual_exceeded"
    PUBLICATION_ROLLUP_RESIDUAL_EXCEEDED = "publication_rollup_residual_exceeded"


class RollupKind(StrEnum):
    PORTFOLIO = "portfolio"
    HOLDING = "holding"
    CONTRIBUTION = "contribution"


class RollupStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


def _enum(value: object, enum_type: type[StrEnum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise PortfolioDailyExactError(
            f"{field_name} must be a {enum_type.__name__} value"
        )


def _date(value: object, *, field_name: str) -> date:
    if type(value) is not date:
        raise PortfolioDailyExactError(f"{field_name} must be a date")
    return value


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        return require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise PortfolioDailyExactError(str(exc)) from exc


def _optional_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value, field_name=field_name)


def _method_decimal(value: object, *, field_name: str) -> Decimal:
    resolved = _decimal(value, field_name=field_name)
    try:
        return require_method_decimal(resolved, field_name=field_name)
    except CalculationNumericError as exc:
        raise PortfolioDailyExactError(str(exc)) from exc


def _rounding_adjustment_decimal(value: object, *, field_name: str) -> Decimal:
    resolved = _decimal(value, field_name=field_name)
    try:
        return require_exact_numeric_typmod(
            resolved,
            precision=METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
            scale=METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
            field_name=field_name,
        )
    except CalculationNumericError as exc:
        raise PortfolioDailyExactError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class PortfolioDailyInput:
    as_of_date: date
    measured_nav: Decimal | None
    external_flow_in: Decimal = Decimal("0")
    external_flow_out: Decimal = Decimal("0")
    coverage_status: CoverageStatus = CoverageStatus.COMPLETE
    valuation_status: ValuationStatus = ValuationStatus.FRESH
    fx_status: FxStatus = FxStatus.AVAILABLE

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        nav = _optional_decimal(self.measured_nav, field_name="measured_nav")
        flow_in = _decimal(self.external_flow_in, field_name="external_flow_in")
        flow_out = _decimal(self.external_flow_out, field_name="external_flow_out")
        if flow_in < 0 or flow_out < 0:
            raise PortfolioDailyExactError("external flows must be non-negative")
        _enum(self.coverage_status, CoverageStatus, field_name="coverage_status")
        _enum(self.valuation_status, ValuationStatus, field_name="valuation_status")
        _enum(self.fx_status, FxStatus, field_name="fx_status")
        measurable = (
            self.coverage_status is CoverageStatus.COMPLETE
            and self.fx_status is FxStatus.AVAILABLE
        )
        if measurable and nav is None:
            raise PortfolioDailyExactError(
                "complete measurement and FX coverage require measured_nav"
            )
        if not measurable and nav is not None:
            raise PortfolioDailyExactError(
                "canonical measured_nav must be null when measurement is incomplete"
            )

    @property
    def has_external_flow(self) -> bool:
        return self.external_flow_in != 0 or self.external_flow_out != 0


@dataclass(frozen=True, slots=True)
class TwrAccumulatorState:
    status: TwrWindowStatus
    wealth_index_method50: Decimal | None
    peak_wealth_index_method50: Decimal | None
    anchor_nav: Decimal | None
    anchor_date: date | None
    last_as_of_date: date | None

    def __post_init__(self) -> None:
        _enum(self.status, TwrWindowStatus, field_name="status")
        if self.anchor_date is not None:
            _date(self.anchor_date, field_name="anchor_date")
        if self.last_as_of_date is not None:
            _date(self.last_as_of_date, field_name="last_as_of_date")
        values = (
            self.wealth_index_method50,
            self.peak_wealth_index_method50,
            self.anchor_nav,
            self.anchor_date,
        )
        if self.status is not TwrWindowStatus.ACTIVE:
            if any(value is not None for value in values):
                raise PortfolioDailyExactError(
                    "a non-active TWR state cannot retain an anchor or wealth index"
                )
            return
        wealth = _method_decimal(
            self.wealth_index_method50,
            field_name="wealth_index_method50",
        )
        peak = _method_decimal(
            self.peak_wealth_index_method50,
            field_name="peak_wealth_index_method50",
        )
        anchor = _decimal(self.anchor_nav, field_name="anchor_nav")
        assert self.anchor_date is not None
        if wealth <= 0 or peak <= 0 or peak < wealth:
            raise PortfolioDailyExactError("wealth/peak TWR invariant failed")
        if anchor <= 0:
            raise PortfolioDailyExactError("anchor_nav must be positive")
        if self.last_as_of_date is None or self.last_as_of_date < self.anchor_date:
            raise PortfolioDailyExactError(
                "active state must end on or after its anchor date"
            )

    @classmethod
    def initial(cls) -> "TwrAccumulatorState":
        return cls(
            status=TwrWindowStatus.AWAITING_ANCHOR,
            wealth_index_method50=None,
            peak_wealth_index_method50=None,
            anchor_nav=None,
            anchor_date=None,
            last_as_of_date=None,
        )

    @classmethod
    def awaiting(cls, *, last_as_of_date: date) -> "TwrAccumulatorState":
        return cls(
            status=TwrWindowStatus.AWAITING_ANCHOR,
            wealth_index_method50=None,
            peak_wealth_index_method50=None,
            anchor_nav=None,
            anchor_date=None,
            last_as_of_date=last_as_of_date,
        )

    @classmethod
    def broken(cls, *, last_as_of_date: date) -> "TwrAccumulatorState":
        return cls(
            status=TwrWindowStatus.BROKEN,
            wealth_index_method50=None,
            peak_wealth_index_method50=None,
            anchor_nav=None,
            anchor_date=None,
            last_as_of_date=last_as_of_date,
        )


@dataclass(frozen=True, slots=True)
class DailyTwrOutcome:
    as_of_date: date
    status: DailyCalculationStatus
    reason_codes: tuple[PortfolioDailyReasonCode, ...]
    return_period_start_date: date | None
    return_period_day_count: int | None
    adjusted_beginning_value: Decimal | None
    adjusted_ending_value: Decimal | None
    subperiod_twr_method50: Decimal | None
    cumulative_twr_method50: Decimal | None
    wealth_index_method50: Decimal | None
    peak_wealth_index_method50: Decimal | None
    drawdown_method50: Decimal | None
    wealth_chain_rounding_adjustment_exact: Decimal | None
    next_state: TwrAccumulatorState

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _enum(self.status, DailyCalculationStatus, field_name="status")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(reason, PortfolioDailyReasonCode)
            for reason in self.reason_codes
        ):
            raise PortfolioDailyExactError(
                "reason_codes must be an immutable enum tuple"
            )
        if self.return_period_start_date is not None:
            _date(self.return_period_start_date, field_name="return_period_start_date")
        if self.return_period_day_count is not None and (
            isinstance(self.return_period_day_count, bool)
            or not isinstance(self.return_period_day_count, int)
            or self.return_period_day_count <= 0
        ):
            raise PortfolioDailyExactError("return_period_day_count must be positive")
        for field_name in (
            "adjusted_beginning_value",
            "adjusted_ending_value",
        ):
            _optional_decimal(getattr(self, field_name), field_name=field_name)
        for field_name in (
            "subperiod_twr_method50",
            "cumulative_twr_method50",
            "wealth_index_method50",
            "peak_wealth_index_method50",
            "drawdown_method50",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _method_decimal(value, field_name=field_name)
        if self.wealth_chain_rounding_adjustment_exact is not None:
            _rounding_adjustment_decimal(
                self.wealth_chain_rounding_adjustment_exact,
                field_name="wealth_chain_rounding_adjustment_exact",
            )
        if not isinstance(self.next_state, TwrAccumulatorState):
            raise PortfolioDailyExactError("next_state must be a TwrAccumulatorState")
        if self.next_state.last_as_of_date != self.as_of_date:
            raise PortfolioDailyExactError("next_state date must equal outcome date")
        if self.status is DailyCalculationStatus.CALCULATED:
            if self.reason_codes or any(
                value is None
                for value in (
                    self.return_period_start_date,
                    self.return_period_day_count,
                    self.adjusted_beginning_value,
                    self.adjusted_ending_value,
                    self.subperiod_twr_method50,
                    self.cumulative_twr_method50,
                    self.wealth_index_method50,
                    self.peak_wealth_index_method50,
                    self.drawdown_method50,
                    self.wealth_chain_rounding_adjustment_exact,
                )
            ):
                raise PortfolioDailyExactError("calculated outcome is incomplete")
        elif not self.reason_codes:
            raise PortfolioDailyExactError("non-calculated outcome requires a reason")
        if self.status is DailyCalculationStatus.REANCHORED:
            if (
                self.return_period_start_date is not None
                or self.return_period_day_count is not None
                or self.adjusted_beginning_value is not None
                or self.adjusted_ending_value is None
                or self.subperiod_twr_method50 is not None
                or self.cumulative_twr_method50 != Decimal("0")
                or self.wealth_index_method50 != Decimal("1")
                or self.peak_wealth_index_method50 != Decimal("1")
                or self.drawdown_method50 != Decimal("0")
                or self.wealth_chain_rounding_adjustment_exact is not None
                or self.next_state.status is not TwrWindowStatus.ACTIVE
            ):
                raise PortfolioDailyExactError("reanchored outcome invariant failed")
        elif self.status is DailyCalculationStatus.NO_NEW_VALUATION:
            if (
                self.return_period_start_date is not None
                or self.return_period_day_count is not None
                or self.adjusted_beginning_value is not None
                or self.adjusted_ending_value is not None
                or self.subperiod_twr_method50 is not None
                or self.wealth_chain_rounding_adjustment_exact is not None
            ):
                raise PortfolioDailyExactError("no-new-valuation invariant failed")
            if self.next_state.status is TwrWindowStatus.AWAITING_ANCHOR:
                if any(
                    value is not None
                    for value in (
                        self.cumulative_twr_method50,
                        self.wealth_index_method50,
                        self.peak_wealth_index_method50,
                        self.drawdown_method50,
                    )
                ):
                    raise PortfolioDailyExactError(
                        "awaiting no-new-valuation cannot retain method evidence"
                    )
            elif self.next_state.status is TwrWindowStatus.ACTIVE:
                wealth = self.next_state.wealth_index_method50
                peak = self.next_state.peak_wealth_index_method50
                assert wealth is not None
                assert peak is not None
                if (
                    self.wealth_index_method50 != wealth
                    or self.peak_wealth_index_method50 != peak
                    or self.cumulative_twr_method50
                    != method_decimal_subtract(wealth, Decimal("1"))
                    or self.drawdown_method50
                    != _method_divide(
                        method_decimal_subtract(wealth, peak),
                        peak,
                    )
                ):
                    raise PortfolioDailyExactError(
                        "active no-new-valuation method evidence mismatch"
                    )
            else:
                raise PortfolioDailyExactError(
                    "no-new-valuation requires awaiting or active next state"
                )
        elif self.status is DailyCalculationStatus.BROKEN:
            if (
                self.return_period_start_date is not None
                or self.return_period_day_count is not None
                or self.subperiod_twr_method50 is not None
                or self.cumulative_twr_method50 is not None
                or self.wealth_index_method50 is not None
                or self.peak_wealth_index_method50 is not None
                or self.drawdown_method50 is not None
                or self.wealth_chain_rounding_adjustment_exact is not None
                or self.next_state.status is not TwrWindowStatus.BROKEN
            ):
                raise PortfolioDailyExactError("broken outcome invariant failed")
        if self.wealth_index_method50 is not None:
            assert self.peak_wealth_index_method50 is not None
            assert self.cumulative_twr_method50 is not None
            assert self.drawdown_method50 is not None
            if (
                self.wealth_index_method50 < 0
                or self.peak_wealth_index_method50 <= 0
                or self.peak_wealth_index_method50 < self.wealth_index_method50
                or self.cumulative_twr_method50
                != method_decimal_subtract(
                    self.wealth_index_method50,
                    Decimal("1"),
                )
                or self.drawdown_method50
                != method_decimal_divide(
                    method_decimal_subtract(
                        self.wealth_index_method50,
                        self.peak_wealth_index_method50,
                    ),
                    self.peak_wealth_index_method50,
                )
            ):
                raise PortfolioDailyExactError(
                    "method50 wealth/cumulative/drawdown invariant failed"
                )


@dataclass(frozen=True, slots=True)
class PortfolioDailyPublication:
    as_of_date: date
    status: DailyCalculationStatus
    reason_codes: tuple[PortfolioDailyReasonCode, ...]
    coverage_status: CoverageStatus
    valuation_status: ValuationStatus
    fx_status: FxStatus
    nav: Decimal | None
    external_flow_in: Decimal
    external_flow_out: Decimal
    return_period_start_date: date | None
    return_period_day_count: int | None
    subperiod_twr_published: Decimal | None
    cumulative_twr_published: Decimal | None
    drawdown_published: Decimal | None

    def __post_init__(self) -> None:
        _date(self.as_of_date, field_name="as_of_date")
        _enum(self.status, DailyCalculationStatus, field_name="status")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(reason, PortfolioDailyReasonCode)
            for reason in self.reason_codes
        ):
            raise PortfolioDailyExactError(
                "publication reasons must be immutable enums"
            )
        _enum(self.coverage_status, CoverageStatus, field_name="coverage_status")
        _enum(self.valuation_status, ValuationStatus, field_name="valuation_status")
        _enum(self.fx_status, FxStatus, field_name="fx_status")
        _optional_decimal(self.nav, field_name="nav")
        _decimal(self.external_flow_in, field_name="external_flow_in")
        _decimal(self.external_flow_out, field_name="external_flow_out")
        if self.return_period_start_date is not None:
            _date(self.return_period_start_date, field_name="return_period_start_date")
        if self.return_period_day_count is not None and (
            isinstance(self.return_period_day_count, bool)
            or not isinstance(self.return_period_day_count, int)
            or self.return_period_day_count <= 0
        ):
            raise PortfolioDailyExactError("publication day count must be positive")
        for field_name in (
            "subperiod_twr_published",
            "cumulative_twr_published",
            "drawdown_published",
        ):
            _optional_decimal(getattr(self, field_name), field_name=field_name)


def _blocking_reasons(
    period: PortfolioDailyInput,
) -> tuple[PortfolioDailyReasonCode, ...]:
    reasons: list[PortfolioDailyReasonCode] = []
    if period.coverage_status is CoverageStatus.PARTIAL:
        reasons.append(PortfolioDailyReasonCode.PARTIAL_COVERAGE)
    elif period.coverage_status is CoverageStatus.UNAVAILABLE:
        reasons.append(PortfolioDailyReasonCode.UNAVAILABLE_COVERAGE)
    if period.fx_status is FxStatus.UNAVAILABLE:
        reasons.append(PortfolioDailyReasonCode.FX_UNAVAILABLE)
    return tuple(reasons)


def _endpoint_reason(period: PortfolioDailyInput) -> PortfolioDailyReasonCode:
    return (
        PortfolioDailyReasonCode.STALE_VALUATION
        if period.valuation_status is ValuationStatus.STALE
        else PortfolioDailyReasonCode.CARRY_FORWARD_VALUATION
    )


def _validate_order(period: PortfolioDailyInput, state: TwrAccumulatorState) -> None:
    if state.last_as_of_date is not None and period.as_of_date <= state.last_as_of_date:
        raise PortfolioDailyExactError(
            "as_of_date must be strictly later than prior state date"
        )


def _method_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Return the methodology-defined precision-50 HALF_EVEN quotient."""

    return method_decimal_divide(numerator, denominator)


def _broken(
    period: PortfolioDailyInput,
    reasons: tuple[PortfolioDailyReasonCode, ...],
    *,
    adjusted_beginning: Decimal | None = None,
    adjusted_ending: Decimal | None = None,
) -> DailyTwrOutcome:
    return DailyTwrOutcome(
        as_of_date=period.as_of_date,
        status=DailyCalculationStatus.BROKEN,
        reason_codes=reasons,
        return_period_start_date=None,
        return_period_day_count=None,
        adjusted_beginning_value=adjusted_beginning,
        adjusted_ending_value=adjusted_ending,
        subperiod_twr_method50=None,
        cumulative_twr_method50=None,
        wealth_index_method50=None,
        peak_wealth_index_method50=None,
        drawdown_method50=None,
        wealth_chain_rounding_adjustment_exact=None,
        next_state=TwrAccumulatorState.broken(last_as_of_date=period.as_of_date),
    )


def _reanchor(
    period: PortfolioDailyInput,
    reason: PortfolioDailyReasonCode,
) -> DailyTwrOutcome:
    assert period.measured_nav is not None
    if period.measured_nav <= 0:
        return _broken(
            period,
            (PortfolioDailyReasonCode.NON_POSITIVE_REANCHOR_VALUE,),
            adjusted_ending=period.measured_nav,
        )
    state = TwrAccumulatorState(
        status=TwrWindowStatus.ACTIVE,
        wealth_index_method50=Decimal("1"),
        peak_wealth_index_method50=Decimal("1"),
        anchor_nav=period.measured_nav,
        anchor_date=period.as_of_date,
        last_as_of_date=period.as_of_date,
    )
    return DailyTwrOutcome(
        as_of_date=period.as_of_date,
        status=DailyCalculationStatus.REANCHORED,
        reason_codes=(reason,),
        return_period_start_date=None,
        return_period_day_count=None,
        adjusted_beginning_value=None,
        adjusted_ending_value=period.measured_nav,
        subperiod_twr_method50=None,
        cumulative_twr_method50=Decimal("0"),
        wealth_index_method50=Decimal("1"),
        peak_wealth_index_method50=Decimal("1"),
        drawdown_method50=Decimal("0"),
        wealth_chain_rounding_adjustment_exact=None,
        next_state=state,
    )


def _no_new_endpoint(
    period: PortfolioDailyInput,
    state: TwrAccumulatorState,
) -> DailyTwrOutcome:
    reason = _endpoint_reason(period)
    if period.has_external_flow:
        return _broken(
            period,
            (reason, PortfolioDailyReasonCode.GAP_EXTERNAL_FLOW),
        )
    if state.status is TwrWindowStatus.BROKEN:
        return _broken(
            period,
            (reason, PortfolioDailyReasonCode.BROKEN_BOUNDARY),
        )
    if state.status is TwrWindowStatus.AWAITING_ANCHOR:
        next_state = TwrAccumulatorState.awaiting(last_as_of_date=period.as_of_date)
        cumulative = drawdown = None
    else:
        assert state.wealth_index_method50 is not None
        assert state.peak_wealth_index_method50 is not None
        assert state.anchor_nav is not None
        assert state.anchor_date is not None
        next_state = TwrAccumulatorState(
            status=TwrWindowStatus.ACTIVE,
            wealth_index_method50=state.wealth_index_method50,
            peak_wealth_index_method50=state.peak_wealth_index_method50,
            anchor_nav=state.anchor_nav,
            anchor_date=state.anchor_date,
            last_as_of_date=period.as_of_date,
        )
        cumulative = method_decimal_subtract(
            state.wealth_index_method50,
            Decimal("1"),
        )
        drawdown = _method_divide(
            method_decimal_subtract(
                state.wealth_index_method50,
                state.peak_wealth_index_method50,
            ),
            state.peak_wealth_index_method50,
        )
    return DailyTwrOutcome(
        as_of_date=period.as_of_date,
        status=DailyCalculationStatus.NO_NEW_VALUATION,
        reason_codes=(reason,),
        return_period_start_date=None,
        return_period_day_count=None,
        adjusted_beginning_value=None,
        adjusted_ending_value=None,
        subperiod_twr_method50=None,
        cumulative_twr_method50=cumulative,
        wealth_index_method50=(
            next_state.wealth_index_method50
            if next_state.status is TwrWindowStatus.ACTIVE
            else None
        ),
        peak_wealth_index_method50=(
            next_state.peak_wealth_index_method50
            if next_state.status is TwrWindowStatus.ACTIVE
            else None
        ),
        drawdown_method50=drawdown,
        wealth_chain_rounding_adjustment_exact=None,
        next_state=next_state,
    )


def advance_daily_twr(
    period: PortfolioDailyInput,
    state: TwrAccumulatorState,
) -> DailyTwrOutcome:
    """Advance TWR using BOD inflows and EOD outflows.

    At a fresh endpoint the exact economic return numerator is
    ``(NAV_end + outflow_EOD) - (NAV_anchor + inflow_BOD)`` and
    ``r = numerator / (NAV_anchor + inflow_BOD)``.  This algebraically equals
    the ratio-minus-one expression, but defining the single precision-50
    division on the economic numerator also gives Portfolio Daily
    contribution one common denominator and one method-precision quotient.  A
    fresh endpoint after one or more carry/stale dates without an intervening
    flow is a multi-day subperiod, recorded explicitly by
    ``return_period_start_date`` and ``return_period_day_count``.  An outflow on
    that endpoint is allowed only because the EOD timing places it after the
    reliable measurement; a BOD endpoint inflow remains unmeasurable.
    """

    if not isinstance(period, PortfolioDailyInput):
        raise PortfolioDailyExactError("period must be a PortfolioDailyInput")
    if not isinstance(state, TwrAccumulatorState):
        raise PortfolioDailyExactError("state must be a TwrAccumulatorState")
    _validate_order(period, state)
    blocking = _blocking_reasons(period)
    if blocking:
        return _broken(period, blocking)
    if period.valuation_status is not ValuationStatus.FRESH:
        return _no_new_endpoint(period, state)
    assert period.measured_nav is not None
    if state.status is TwrWindowStatus.AWAITING_ANCHOR:
        return _reanchor(period, PortfolioDailyReasonCode.INITIAL_ANCHOR)
    if state.status is TwrWindowStatus.BROKEN:
        return _reanchor(period, PortfolioDailyReasonCode.REANCHORED_AFTER_BREAK)

    assert state.anchor_nav is not None
    assert state.anchor_date is not None
    assert state.wealth_index_method50 is not None
    assert state.peak_wealth_index_method50 is not None
    return_period_day_count = (period.as_of_date - state.anchor_date).days
    if period.external_flow_in != 0 and return_period_day_count != 1:
        # A fresh endpoint BOD inflow occurs inside a multi-day return window;
        # without a reliable immediately-prior boundary its capital timing is
        # unknowable and the window must break.  An EOD outflow is different:
        # it occurs after the endpoint valuation and is therefore exactly
        # neutralized by adjusted_ending even after a carry/stale gap.
        return _broken(
            period,
            (PortfolioDailyReasonCode.GAP_EXTERNAL_FLOW,),
        )
    try:
        adjusted_beginning = exact_decimal_sum(
            (state.anchor_nav, period.external_flow_in)
        )
        adjusted_ending = exact_decimal_sum(
            (period.measured_nav, period.external_flow_out)
        )
        if adjusted_beginning <= 0:
            return _broken(
                period,
                (PortfolioDailyReasonCode.NON_POSITIVE_DENOMINATOR,),
                adjusted_beginning=adjusted_beginning,
                adjusted_ending=adjusted_ending,
            )
        if adjusted_ending < 0:
            return _broken(
                period,
                (PortfolioDailyReasonCode.NEGATIVE_ADJUSTED_ENDING_VALUE,),
                adjusted_beginning=adjusted_beginning,
                adjusted_ending=adjusted_ending,
            )
        return_numerator = exact_decimal_subtract(
            adjusted_ending,
            adjusted_beginning,
        )
        subperiod_return = _method_divide(
            return_numerator,
            adjusted_beginning,
        )
        # The methodology has two explicit precision-50 boundaries: first
        # construct the method factor, then apply it to the prior method
        # wealth.  Do not fuse these operations; doing so changes HALF_EVEN
        # results at cancellation boundaries.
        factor = method_decimal_add(Decimal("1"), subperiod_return)
        exact_unrounded_wealth = exact_decimal_product(
            state.wealth_index_method50,
            factor,
        )
        wealth = method_decimal_multiply(
            state.wealth_index_method50,
            factor,
        )
        wealth_rounding_adjustment = exact_decimal_subtract(
            wealth,
            exact_unrounded_wealth,
        )
        peak = max(state.peak_wealth_index_method50, wealth)
        cumulative = method_decimal_subtract(wealth, Decimal("1"))
        drawdown = _method_divide(
            method_decimal_subtract(wealth, peak),
            peak,
        )
        for field_name, value in (
            ("subperiod_twr_method50", subperiod_return),
            ("cumulative_twr_method50", cumulative),
            ("wealth_index_method50", wealth),
            ("peak_wealth_index_method50", peak),
            ("drawdown_method50", drawdown),
        ):
            _method_decimal(value, field_name=field_name)
        _rounding_adjustment_decimal(
            wealth_rounding_adjustment,
            field_name="wealth_chain_rounding_adjustment_exact",
        )
    except (
        DivisionByZero,
        InvalidOperation,
        Overflow,
        PortfolioDailyExactError,
    ):
        return _broken(period, (PortfolioDailyReasonCode.NUMERIC_FAILURE,))

    next_state = (
        TwrAccumulatorState.broken(last_as_of_date=period.as_of_date)
        if period.measured_nav <= 0 or wealth == 0
        else TwrAccumulatorState(
            status=TwrWindowStatus.ACTIVE,
            wealth_index_method50=wealth,
            peak_wealth_index_method50=peak,
            anchor_nav=period.measured_nav,
            anchor_date=period.as_of_date,
            last_as_of_date=period.as_of_date,
        )
    )
    return DailyTwrOutcome(
        as_of_date=period.as_of_date,
        status=DailyCalculationStatus.CALCULATED,
        reason_codes=(),
        return_period_start_date=state.anchor_date,
        return_period_day_count=return_period_day_count,
        adjusted_beginning_value=adjusted_beginning,
        adjusted_ending_value=adjusted_ending,
        subperiod_twr_method50=subperiod_return,
        cumulative_twr_method50=cumulative,
        wealth_index_method50=wealth,
        peak_wealth_index_method50=peak,
        drawdown_method50=drawdown,
        wealth_chain_rounding_adjustment_exact=wealth_rounding_adjustment,
        next_state=next_state,
    )


def quantize_publication_field(value: Decimal, *, field_name: str) -> Decimal:
    if field_name not in PUBLICATION_FIELD_SCALES:
        raise PortfolioDailyExactError(
            f"publication field {field_name!r} has no explicit scale"
        )
    try:
        return quantize_decimal(
            value,
            scale=PUBLICATION_FIELD_SCALES[field_name],
            field_name=field_name,
        )
    except CalculationNumericError as exc:
        raise PortfolioDailyExactError(str(exc)) from exc


def quantize_portfolio_daily_publication(
    period: PortfolioDailyInput,
    outcome: DailyTwrOutcome,
) -> PortfolioDailyPublication:
    if period.as_of_date != outcome.as_of_date:
        raise PortfolioDailyExactError("period/outcome dates must match")

    def optional(value: Decimal | None, field: str) -> Decimal | None:
        return (
            None
            if value is None
            else quantize_publication_field(value, field_name=field)
        )

    return PortfolioDailyPublication(
        as_of_date=period.as_of_date,
        status=outcome.status,
        reason_codes=outcome.reason_codes,
        coverage_status=period.coverage_status,
        valuation_status=period.valuation_status,
        fx_status=period.fx_status,
        nav=optional(period.measured_nav, "nav"),
        external_flow_in=quantize_publication_field(
            period.external_flow_in,
            field_name="external_flow_in",
        ),
        external_flow_out=quantize_publication_field(
            period.external_flow_out,
            field_name="external_flow_out",
        ),
        return_period_start_date=outcome.return_period_start_date,
        return_period_day_count=outcome.return_period_day_count,
        subperiod_twr_published=optional(
            outcome.subperiod_twr_method50,
            "subperiod_twr_published",
        ),
        cumulative_twr_published=optional(
            outcome.cumulative_twr_method50,
            "cumulative_twr_published",
        ),
        drawdown_published=optional(
            outcome.drawdown_method50,
            "drawdown_published",
        ),
    )


@dataclass(frozen=True, slots=True)
class RollupCheckInput:
    kind: RollupKind
    publication_field: str
    parent_value: Decimal
    component_values: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        _enum(self.kind, RollupKind, field_name="kind")
        if self.publication_field not in PUBLICATION_FIELD_SCALES:
            raise PortfolioDailyExactError(
                "publication_field must name an explicit publication scale"
            )
        _decimal(self.parent_value, field_name="parent_value")
        if not isinstance(self.component_values, tuple):
            raise PortfolioDailyExactError("component_values must be a tuple")
        for index, value in enumerate(self.component_values):
            _decimal(value, field_name=f"component_values[{index}]")


@dataclass(frozen=True, slots=True)
class RollupCheckResult:
    kind: RollupKind
    status: RollupStatus
    reason_codes: tuple[PortfolioDailyReasonCode, ...]
    publication_field: str
    quantum: Decimal
    exact_parent_value: Decimal
    exact_component_total: Decimal
    exact_residual: Decimal
    publication_parent_value: Decimal
    publication_component_total: Decimal
    publication_residual: Decimal


def validate_rollup(check: RollupCheckInput) -> RollupCheckResult:
    if not isinstance(check, RollupCheckInput):
        raise PortfolioDailyExactError("check must be a RollupCheckInput")
    scale = PUBLICATION_FIELD_SCALES[check.publication_field]
    quantum = quantum_for_scale(scale=scale)
    exact_total = exact_decimal_sum(check.component_values)
    exact_residual = exact_decimal_subtract(check.parent_value, exact_total)
    published_parent = quantize_publication_field(
        check.parent_value,
        field_name=check.publication_field,
    )
    published_components = tuple(
        quantize_publication_field(value, field_name=check.publication_field)
        for value in check.component_values
    )
    published_total = exact_decimal_sum(published_components)
    published_residual = exact_decimal_subtract(
        published_parent,
        published_total,
    )
    reasons: list[PortfolioDailyReasonCode] = []
    if exact_residual.copy_abs() > quantum:
        reasons.append(PortfolioDailyReasonCode.EXACT_ROLLUP_RESIDUAL_EXCEEDED)
    if published_residual.copy_abs() > quantum:
        reasons.append(PortfolioDailyReasonCode.PUBLICATION_ROLLUP_RESIDUAL_EXCEEDED)
    return RollupCheckResult(
        kind=check.kind,
        status=RollupStatus.FAILED if reasons else RollupStatus.PASSED,
        reason_codes=tuple(reasons),
        publication_field=check.publication_field,
        quantum=quantum,
        exact_parent_value=check.parent_value,
        exact_component_total=exact_total,
        exact_residual=exact_residual,
        publication_parent_value=published_parent,
        publication_component_total=published_total,
        publication_residual=published_residual,
    )


__all__ = [
    "CoverageStatus",
    "DailyCalculationStatus",
    "DailyTwrOutcome",
    "FxStatus",
    "PortfolioDailyExactError",
    "PortfolioDailyInput",
    "PortfolioDailyPublication",
    "PortfolioDailyReasonCode",
    "RollupCheckInput",
    "RollupCheckResult",
    "RollupKind",
    "RollupStatus",
    "TwrAccumulatorState",
    "TwrWindowStatus",
    "ValuationStatus",
    "advance_daily_twr",
    "quantize_portfolio_daily_publication",
    "quantize_publication_field",
    "validate_rollup",
]
