"""Pure multi-period reporting over one Portfolio Daily publication.

The module accepts no database session and imports no mutable portfolio facts.
All financial arithmetic, including descriptive statistics, remains Decimal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import Literal

from portfolio_app.calculations.numeric import (
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    RATIO_SCALE,
    CalculationNumericError,
    calculation_context,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_add,
    method_decimal_divide,
    method_decimal_multiply,
    method_decimal_subtract,
    quantize_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyContribution,
    PortfolioDailySnapshot,
)


ZERO = Decimal("0")
ONE = Decimal("1")
ANNUALIZATION_DAYS = Decimal("365")
MINIMUM_ANNUALIZATION_DAYS = 365
XIRR_BISECTION_ITERATIONS = 256
XIRR_MAX_BRACKET_EXPANSIONS = 256
FRONGELLO_METHOD_VERSION = "frongello-forward.v2.decimal50-deterministic-group-balance"
XIRR_METHOD_VERSION = "xirr.v1.actual-365.decimal50.unique-sign-change"
STATISTICS_METHOD_VERSION_MONTHLY = (
    "calendar-period.v1.monthly.ppy-12.vol-sample-n-minus-1."
    "downside-target-zero-n.decimal50"
)
STATISTICS_METHOD_VERSION_WEEKLY = (
    "calendar-period.v1.weekly.ppy-52.vol-sample-n-minus-1."
    "downside-target-zero-n.decimal50"
)

ReportingStatus = Literal["ready", "unavailable"]
CalendarFrequency = Literal["monthly", "weekly"]
CalendarCoverageState = Literal["complete", "partial", "unavailable"]
AttributionAxis = Literal["portfolio", "account", "instrument", "currency", "taxonomy"]


class PublishedReportingIntegrityError(ValueError):
    """A supposedly closed immutable publication violates reporting invariants."""


class ReportingMethodDomainError(PublishedReportingIntegrityError):
    """A derived reporting value cannot be represented in the method domain."""


def _require_calendar_frequency(value: str) -> CalendarFrequency:
    if value not in {"monthly", "weekly"}:
        raise PublishedReportingIntegrityError(
            f"unsupported reporting calendar frequency {value!r}"
        )
    return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class RebasedWealthPoint:
    as_of_date: date
    wealth_index_method50: Decimal
    peak_wealth_index_method50: Decimal
    drawdown_method50: Decimal
    wealth_chain_rounding_adjustment_exact: Decimal | None
    return_chain_status: str


@dataclass(frozen=True, slots=True)
class DecimalReportingMetric:
    method50: Decimal
    published: Decimal
    rounding_adjustment_exact: Decimal


@dataclass(frozen=True, slots=True)
class CalendarPeriodStatistics:
    status: ReportingStatus
    method_version: str
    reason_codes: tuple[str, ...]
    frequency: CalendarFrequency
    observation_count: int
    excluded_partial_bucket_count: int
    excluded_unavailable_bucket_count: int
    periods_per_year: DecimalReportingMetric | None
    mean_period_return: DecimalReportingMetric | None
    annualized_arithmetic_mean: DecimalReportingMetric | None
    annualized_volatility: DecimalReportingMetric | None
    annualized_downside_deviation: DecimalReportingMetric | None


@dataclass(frozen=True, slots=True)
class XirrResult:
    status: ReportingStatus
    method_version: str
    reason_codes: tuple[str, ...]
    cash_flow_count: int
    annualized_headline_eligible: bool
    rate: DecimalReportingMetric | None
    xnpv_residual_exact: Decimal | None


@dataclass(frozen=True, slots=True)
class SelectedRangePerformance:
    status: ReportingStatus
    reason_codes: tuple[str, ...]
    selected_start_date: date | None
    selected_end_date: date | None
    effective_return_start_date: date | None
    effective_return_end_date: date | None
    elapsed_days: int | None
    observation_count: int
    cumulative_twr_method50: Decimal | None
    annualized_twr: DecimalReportingMetric | None
    current_drawdown_method50: Decimal | None
    max_drawdown_method50: Decimal | None
    wealth_points: tuple[RebasedWealthPoint, ...]
    active_snapshots: tuple[PortfolioDailySnapshot, ...]
    statistics: CalendarPeriodStatistics
    xirr: XirrResult


@dataclass(frozen=True, slots=True)
class LinkedAttributionGroup:
    axis: AttributionAxis
    group_key: str
    group_label: str
    opening_nav_exact: Decimal
    closing_nav_exact: Decimal
    external_flow_in_exact: Decimal
    external_flow_out_exact: Decimal
    internal_flow_in_exact: Decimal
    internal_flow_out_exact: Decimal
    economic_pnl_exact: Decimal
    linked_contribution_method50: Decimal
    linking_adjustment_exact: Decimal
    linked_contribution_effective: Decimal
    closure_residual_exact: Decimal


@dataclass(frozen=True, slots=True)
class LinkedAttribution:
    status: ReportingStatus
    method_version: str
    reason_codes: tuple[str, ...]
    axis: AttributionAxis
    effective_start_date: date | None
    effective_end_date: date | None
    observation_count: int
    cumulative_twr_method50: Decimal | None
    total_linked_contribution_effective: Decimal | None
    total_linking_adjustment_exact: Decimal | None
    closure_residual_exact: Decimal | None
    groups: tuple[LinkedAttributionGroup, ...]


@dataclass(frozen=True, slots=True)
class ReturnCalendarBucketResult:
    bucket_key: str
    frequency: CalendarFrequency
    calendar_start_date: date
    calendar_end_date: date
    coverage_state: CalendarCoverageState
    coverage_reason_codes: tuple[str, ...]
    performance: SelectedRangePerformance


@dataclass(frozen=True, slots=True)
class AttributionCalendarBucketResult:
    bucket_key: str
    frequency: CalendarFrequency
    calendar_start_date: date
    calendar_end_date: date
    coverage_state: CalendarCoverageState
    coverage_reason_codes: tuple[str, ...]
    attribution: LinkedAttribution


def _ordered_reasons(*reason_groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for group in reason_groups for reason in group))


def _method_divide(numerator: Decimal, denominator: Decimal, *, label: str) -> Decimal:
    if denominator == ZERO:
        raise PublishedReportingIntegrityError(f"{label} denominator is zero")
    try:
        value = method_decimal_divide(numerator, denominator)
    except (
        CalculationNumericError,
        DivisionByZero,
        InvalidOperation,
        Overflow,
    ) as error:
        raise PublishedReportingIntegrityError(f"{label} division failed") from error
    if not value.is_finite():
        raise PublishedReportingIntegrityError(f"{label} produced a non-finite value")
    return _bounded_method(value, label=label)


def _bounded_method(value: Decimal, *, label: str) -> Decimal:
    try:
        return require_method_decimal(
            value,
            field_name=label,
        )
    except CalculationNumericError as error:
        raise ReportingMethodDomainError(
            f"{label} exceeds the bounded method50 domain"
        ) from error


def _bounded_adjustment(value: Decimal, *, label: str) -> Decimal:
    try:
        return require_exact_numeric_typmod(
            value,
            precision=METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
            scale=METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
            field_name=label,
        )
    except CalculationNumericError as error:
        raise ReportingMethodDomainError(
            f"{label} exceeds the bounded adjustment domain"
        ) from error


def _method_sum(values: tuple[Decimal, ...]) -> Decimal:
    total = ZERO
    for value in values:
        total = _bounded_method(
            method_decimal_add(total, value),
            label="method50 ordered sum",
        )
    return total


def _reporting_metric(
    method50: Decimal,
    *,
    field_name: str,
) -> DecimalReportingMetric:
    method50 = _bounded_method(method50, label=field_name)
    published = quantize_decimal(
        method50,
        scale=RATIO_SCALE,
        field_name=field_name,
    )
    return DecimalReportingMetric(
        method50=method50,
        published=published,
        rounding_adjustment_exact=_bounded_adjustment(
            exact_decimal_subtract(published, method50),
            label=f"{field_name}.rounding_adjustment_exact",
        ),
    )


def _validate_snapshot_order(
    publication: CurrentPortfolioDailyPublication,
) -> tuple[PortfolioDailySnapshot, ...]:
    snapshots = publication.snapshots
    dates = tuple(row.as_of_date for row in snapshots)
    if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
        raise PublishedReportingIntegrityError(
            "published snapshots must be unique and strictly date ordered"
        )
    if any(
        not publication.requested_range_start
        <= snapshot.as_of_date
        <= publication.requested_range_end
        for snapshot in snapshots
    ):
        raise PublishedReportingIntegrityError(
            "published snapshot lies outside the selected range"
        )
    _validate_published_snapshot_chain(snapshots)
    return snapshots


def validate_published_snapshot_chain(
    publication: CurrentPortfolioDailyPublication,
) -> None:
    """Validate persisted method50/published return-chain evidence."""

    _validate_snapshot_order(publication)


def _validate_method_publication_pair(
    snapshot: PortfolioDailySnapshot,
    *,
    method_field: str,
    published_field: str,
) -> None:
    method50 = getattr(snapshot, method_field)
    published = getattr(snapshot, published_field)
    if (method50 is None) != (published is None):
        raise PublishedReportingIntegrityError(
            f"{method_field}/{published_field} nullability is inconsistent"
        )
    if method50 is not None:
        _bounded_method(method50, label=method_field)
        if published != quantize_decimal(
            method50,
            scale=RATIO_SCALE,
            field_name=published_field,
        ):
            raise PublishedReportingIntegrityError(
                f"{published_field} does not equal HALF_EVEN({method_field}, 18)"
            )


def _validate_snapshot_method_identity(snapshot: PortfolioDailySnapshot) -> None:
    chain = (
        snapshot.cumulative_twr_method50,
        snapshot.wealth_index_method50,
        snapshot.peak_wealth_index_method50,
        snapshot.drawdown_method50,
    )
    if all(value is None for value in chain):
        return
    if any(value is None for value in chain):
        raise PublishedReportingIntegrityError(
            "published method50 wealth chain is only partially populated"
        )
    cumulative, wealth, peak, drawdown = chain
    assert cumulative is not None
    assert wealth is not None
    assert peak is not None
    assert drawdown is not None
    if wealth < ZERO or peak <= ZERO or peak < wealth:
        raise PublishedReportingIntegrityError(
            "published method50 wealth/peak ordering is invalid"
        )
    if cumulative != method_decimal_subtract(wealth, ONE):
        raise PublishedReportingIntegrityError(
            "published cumulative TWR does not close to method50 wealth"
        )
    expected_drawdown = _method_divide(
        method_decimal_subtract(wealth, peak),
        peak,
        label="published snapshot drawdown",
    )
    if drawdown != expected_drawdown:
        raise PublishedReportingIntegrityError(
            "published drawdown does not close to method50 wealth/peak"
        )


def _validate_published_snapshot_chain(
    snapshots: tuple[PortfolioDailySnapshot, ...],
) -> None:
    pairs = (
        ("subperiod_twr_method50", "subperiod_twr_published"),
        ("cumulative_twr_method50", "cumulative_twr_published"),
        ("wealth_index_method50", "wealth_index_published"),
        ("peak_wealth_index_method50", "peak_wealth_index_published"),
        ("drawdown_method50", "drawdown_published"),
    )
    previous: PortfolioDailySnapshot | None = None
    for snapshot in snapshots:
        for method_field, published_field in pairs:
            _validate_method_publication_pair(
                snapshot,
                method_field=method_field,
                published_field=published_field,
            )
        _validate_snapshot_method_identity(snapshot)
        status = snapshot.return_chain_status
        chain = (
            snapshot.cumulative_twr_method50,
            snapshot.wealth_index_method50,
            snapshot.peak_wealth_index_method50,
            snapshot.drawdown_method50,
        )
        if status == "broken":
            if (
                snapshot.subperiod_twr_method50 is not None
                or any(value is not None for value in chain)
                or snapshot.wealth_chain_rounding_adjustment_exact is not None
            ):
                raise PublishedReportingIntegrityError(
                    "broken snapshot retained method50 return-chain values"
                )
        elif status == "reanchor":
            if (
                snapshot.subperiod_twr_method50 is not None
                or chain != (ZERO, ONE, ONE, ZERO)
                or snapshot.wealth_chain_rounding_adjustment_exact is not None
            ):
                raise PublishedReportingIntegrityError(
                    "reanchor snapshot violates method50 reset semantics"
                )
        elif status == "no_new_valuation":
            if (
                snapshot.subperiod_twr_method50 is not None
                or snapshot.wealth_chain_rounding_adjustment_exact is not None
            ):
                raise PublishedReportingIntegrityError(
                    "no-new-valuation snapshot advanced the method50 chain"
                )
            if previous is not None:
                previous_chain = (
                    previous.cumulative_twr_method50,
                    previous.wealth_index_method50,
                    previous.peak_wealth_index_method50,
                    previous.drawdown_method50,
                )
                if chain != previous_chain:
                    raise PublishedReportingIntegrityError(
                        "no-new-valuation snapshot changed method50 chain state"
                    )
        elif status == "active":
            if (
                snapshot.subperiod_twr_method50 is None
                or any(value is None for value in chain)
                or snapshot.wealth_chain_rounding_adjustment_exact is None
            ):
                raise PublishedReportingIntegrityError(
                    "active snapshot lacks method50 return-chain evidence"
                )
            if previous is not None:
                if previous.wealth_index_method50 == ZERO:
                    raise PublishedReportingIntegrityError(
                        "active snapshot cannot continue a zero-wealth return chain"
                    )
                if (
                    previous.wealth_index_method50 is None
                    or previous.peak_wealth_index_method50 is None
                ):
                    raise PublishedReportingIntegrityError(
                        "active snapshot follows a boundary without an anchor"
                    )
                factor = method_decimal_add(
                    ONE,
                    snapshot.subperiod_twr_method50,
                )
                expected_wealth = method_decimal_multiply(
                    previous.wealth_index_method50,
                    factor,
                )
                raw_wealth = exact_decimal_product(
                    previous.wealth_index_method50,
                    factor,
                )
                expected_adjustment = exact_decimal_subtract(
                    expected_wealth,
                    raw_wealth,
                )
                if (
                    snapshot.wealth_index_method50 != expected_wealth
                    or snapshot.wealth_chain_rounding_adjustment_exact
                    != expected_adjustment
                    or snapshot.peak_wealth_index_method50
                    != max(previous.peak_wealth_index_method50, expected_wealth)
                ):
                    raise PublishedReportingIntegrityError(
                        "published snapshot method50 wealth recurrence is inconsistent"
                    )
        else:
            raise PublishedReportingIntegrityError(
                f"unsupported published return_chain_status {status!r}"
            )
        previous = snapshot


def _unavailable_statistics(
    reason: str,
    *,
    frequency: CalendarFrequency,
    observation_count: int,
    excluded_partial_bucket_count: int = 0,
    excluded_unavailable_bucket_count: int = 0,
) -> CalendarPeriodStatistics:
    return CalendarPeriodStatistics(
        status="unavailable",
        method_version=_statistics_method_version(frequency),
        reason_codes=(reason,),
        frequency=frequency,
        observation_count=observation_count,
        excluded_partial_bucket_count=excluded_partial_bucket_count,
        excluded_unavailable_bucket_count=excluded_unavailable_bucket_count,
        periods_per_year=None,
        mean_period_return=None,
        annualized_arithmetic_mean=None,
        annualized_volatility=None,
        annualized_downside_deviation=None,
    )


def _statistics_method_version(frequency: CalendarFrequency) -> str:
    if frequency == "monthly":
        return STATISTICS_METHOD_VERSION_MONTHLY
    return STATISTICS_METHOD_VERSION_WEEKLY


def _ready_calendar_period_statistics(
    values: tuple[Decimal, ...],
    *,
    frequency: CalendarFrequency,
    excluded_partial_bucket_count: int,
    excluded_unavailable_bucket_count: int,
) -> CalendarPeriodStatistics:
    periods_per_year = Decimal("12") if frequency == "monthly" else Decimal("52")
    count = len(values)
    mean = _method_divide(
        _method_sum(values),
        Decimal(count),
        label="calendar-period arithmetic mean",
    )
    sample_variance = _method_divide(
        _method_sum(
            tuple(
                method_decimal_multiply(
                    method_decimal_subtract(value, mean),
                    method_decimal_subtract(value, mean),
                )
                for value in values
            )
        ),
        Decimal(count - 1),
        label="calendar-period sample variance",
    )
    downside_variance = _method_divide(
        _method_sum(
            tuple(
                method_decimal_multiply(min(value, ZERO), min(value, ZERO))
                for value in values
            )
        ),
        Decimal(count),
        label="calendar-period downside variance",
    )
    try:
        with calculation_context() as context:
            annualized_volatility = context.sqrt(
                method_decimal_multiply(sample_variance, periods_per_year)
            )
            annualized_downside = context.sqrt(
                method_decimal_multiply(downside_variance, periods_per_year)
            )
    except (InvalidOperation, Overflow) as error:
        raise ReportingMethodDomainError(
            "calendar-period Decimal square root exceeds the method domain"
        ) from error
    return CalendarPeriodStatistics(
        status="ready",
        method_version=_statistics_method_version(frequency),
        reason_codes=(),
        frequency=frequency,
        observation_count=count,
        excluded_partial_bucket_count=excluded_partial_bucket_count,
        excluded_unavailable_bucket_count=excluded_unavailable_bucket_count,
        periods_per_year=_reporting_metric(
            periods_per_year,
            field_name="statistics.periods_per_year",
        ),
        mean_period_return=_reporting_metric(
            mean,
            field_name="statistics.mean_period_return",
        ),
        annualized_arithmetic_mean=_reporting_metric(
            method_decimal_multiply(mean, periods_per_year),
            field_name="statistics.annualized_arithmetic_mean",
        ),
        annualized_volatility=_reporting_metric(
            annualized_volatility,
            field_name="statistics.annualized_volatility",
        ),
        annualized_downside_deviation=_reporting_metric(
            annualized_downside,
            field_name="statistics.annualized_downside_deviation",
        ),
    )


def _calendar_period_statistics(
    publication: CurrentPortfolioDailyPublication,
    *,
    frequency: CalendarFrequency,
    return_buckets: tuple[ReturnCalendarBucketResult, ...] | None = None,
) -> CalendarPeriodStatistics:
    frequency = _require_calendar_frequency(frequency)
    if return_buckets is not None and any(
        bucket.frequency != frequency for bucket in return_buckets
    ):
        raise PublishedReportingIntegrityError(
            "supplied return calendar frequency does not match statistics frequency"
        )
    buckets = (
        return_buckets
        if return_buckets is not None
        else build_return_calendar(publication, frequency=frequency)
    )
    observations = tuple(
        bucket
        for bucket in buckets
        if bucket.coverage_state == "complete" and bucket.performance.status == "ready"
    )
    partial_count = sum(bucket.coverage_state == "partial" for bucket in buckets)
    unavailable_count = sum(
        bucket.coverage_state == "unavailable" for bucket in buckets
    )
    if len(observations) < 2:
        return _unavailable_statistics(
            "risk_statistics_minimum_two_complete_calendar_buckets",
            frequency=frequency,
            observation_count=len(observations),
            excluded_partial_bucket_count=partial_count,
            excluded_unavailable_bucket_count=unavailable_count,
        )
    values = tuple(
        bucket.performance.cumulative_twr_method50 for bucket in observations
    )
    if any(value is None for value in values):
        raise PublishedReportingIntegrityError(
            "ready complete calendar bucket lacks a method50 return"
        )
    resolved_values = tuple(value for value in values if value is not None)
    count = len(resolved_values)
    try:
        return _ready_calendar_period_statistics(
            resolved_values,
            frequency=frequency,
            excluded_partial_bucket_count=partial_count,
            excluded_unavailable_bucket_count=unavailable_count,
        )
    except ReportingMethodDomainError:
        return _unavailable_statistics(
            "risk_statistics_numeric_domain_unavailable",
            frequency=frequency,
            observation_count=count,
            excluded_partial_bucket_count=partial_count,
            excluded_unavailable_bucket_count=unavailable_count,
        )


def _annualized_twr(
    period_wealth: Decimal,
    elapsed_days: int,
) -> DecimalReportingMetric | None:
    if elapsed_days < MINIMUM_ANNUALIZATION_DAYS:
        return None
    period_wealth = _bounded_method(
        period_wealth,
        label="annualized TWR period wealth",
    )
    if period_wealth <= ZERO:
        return None
    try:
        with calculation_context() as context:
            exponent = method_decimal_divide(
                ANNUALIZATION_DAYS,
                Decimal(elapsed_days),
            )
            annualized = method_decimal_subtract(
                context.power(period_wealth, exponent),
                ONE,
            )
    except (DivisionByZero, InvalidOperation, Overflow) as error:
        raise PublishedReportingIntegrityError("annualized TWR failed") from error
    return _reporting_metric(
        annualized,
        field_name="annualized_twr_method50",
    )


def _xnpv_at_q(cash_flows: tuple[tuple[int, Decimal], ...], q: Decimal) -> Decimal:
    terms: list[Decimal] = []
    try:
        with calculation_context() as context:
            for elapsed_days, amount in cash_flows:
                factor = ONE if elapsed_days == 0 else context.power(q, elapsed_days)
                terms.append(exact_decimal_product(amount, factor))
    except (InvalidOperation, Overflow) as error:
        raise PublishedReportingIntegrityError(
            "XIRR polynomial evaluation failed"
        ) from error
    return exact_decimal_sum(tuple(terms))


def _sign(value: Decimal) -> int:
    if value > ZERO:
        return 1
    if value < ZERO:
        return -1
    return 0


def _build_xirr(
    publication: CurrentPortfolioDailyPublication,
    active: tuple[PortfolioDailySnapshot, ...],
    *,
    effective_start: date,
    effective_end: date,
) -> XirrResult:
    active_dates = {row.as_of_date for row in active}
    portfolio_rows = tuple(
        row
        for row in publication.contributions
        if row.axis == "portfolio" and row.as_of_date in active_dates
    )
    by_date: dict[date, PortfolioDailyContribution] = {}
    for row in portfolio_rows:
        if row.as_of_date in by_date:
            raise PublishedReportingIntegrityError(
                "XIRR requires one portfolio contribution row per return period"
            )
        by_date[row.as_of_date] = row
    if any(row.as_of_date not in by_date for row in active):
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_exact_portfolio_cash_flow_rows_unavailable",),
            cash_flow_count=0,
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )
    if any(
        not row.measured
        or row.opening_nav_exact is None
        or row.closing_nav_exact is None
        or row.external_flow_in_exact is None
        or row.external_flow_out_exact is None
        for row in portfolio_rows
    ):
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_external_flow_history_unavailable",),
            cash_flow_count=0,
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )

    first_row = by_date[active[0].as_of_date]
    last_row = by_date[active[-1].as_of_date]
    assert first_row.opening_nav_exact is not None
    assert last_row.closing_nav_exact is not None
    cash_flows_by_date: dict[date, list[Decimal]] = defaultdict(list)
    cash_flows_by_date[effective_start].append(
        exact_decimal_negate(first_row.opening_nav_exact)
    )
    for snapshot in active:
        row = by_date[snapshot.as_of_date]
        assert row.external_flow_in_exact is not None
        assert row.external_flow_out_exact is not None
        cash_flows_by_date[row.as_of_date].extend(
            (
                exact_decimal_negate(row.external_flow_in_exact),
                row.external_flow_out_exact,
            )
        )
    cash_flows_by_date[effective_end].append(last_row.closing_nav_exact)
    dated = tuple(
        (cash_date, exact_decimal_sum(tuple(amounts)))
        for cash_date, amounts in sorted(cash_flows_by_date.items())
        if exact_decimal_sum(tuple(amounts)) != ZERO
    )
    if len(dated) < 2:
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_insufficient_nonzero_cash_flows",),
            cash_flow_count=len(dated),
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )
    signs = tuple(_sign(amount) for _, amount in dated)
    sign_changes = sum(left != right for left, right in zip(signs, signs[1:]))
    if sign_changes != 1:
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_unique_root_not_provable",),
            cash_flow_count=len(dated),
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )

    origin = dated[0][0]
    polynomial = tuple(
        ((cash_date - origin).days, amount) for cash_date, amount in dated
    )
    low = ZERO
    low_value = _xnpv_at_q(polynomial, low)
    high = ONE
    high_value = _xnpv_at_q(polynomial, high)
    expansions = 0
    while (
        _sign(low_value) == _sign(high_value)
        and expansions < XIRR_MAX_BRACKET_EXPANSIONS
    ):
        try:
            high = _bounded_method(
                method_decimal_multiply(high, Decimal("2")),
                label="XIRR bracket",
            )
        except ReportingMethodDomainError:
            return XirrResult(
                status="unavailable",
                method_version=XIRR_METHOD_VERSION,
                reason_codes=("xirr_root_outside_method_domain",),
                cash_flow_count=len(dated),
                annualized_headline_eligible=False,
                rate=None,
                xnpv_residual_exact=None,
            )
        try:
            high_value = _xnpv_at_q(polynomial, high)
        except PublishedReportingIntegrityError:
            return XirrResult(
                status="unavailable",
                method_version=XIRR_METHOD_VERSION,
                reason_codes=("xirr_root_outside_method_domain",),
                cash_flow_count=len(dated),
                annualized_headline_eligible=False,
                rate=None,
                xnpv_residual_exact=None,
            )
        expansions += 1
    if _sign(low_value) == _sign(high_value):
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_root_not_bracketed",),
            cash_flow_count=len(dated),
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )
    q: Decimal | None = None
    residual: Decimal | None = None
    if low_value == ZERO:
        q, residual = low, low_value
    elif high_value == ZERO:
        q, residual = high, high_value
    else:
        for _ in range(XIRR_BISECTION_ITERATIONS):
            try:
                midpoint = _method_divide(
                    method_decimal_add(low, high),
                    Decimal("2"),
                    label="XIRR bisection midpoint",
                )
            except ReportingMethodDomainError:
                return XirrResult(
                    status="unavailable",
                    method_version=XIRR_METHOD_VERSION,
                    reason_codes=("xirr_root_outside_method_domain",),
                    cash_flow_count=len(dated),
                    annualized_headline_eligible=False,
                    rate=None,
                    xnpv_residual_exact=None,
                )
            if midpoint == low or midpoint == high:
                q, residual = min(
                    ((low, low_value), (high, high_value)),
                    key=lambda candidate: (
                        candidate[1].copy_abs(),
                        candidate[0],
                    ),
                )
                break
            try:
                midpoint_value = _xnpv_at_q(polynomial, midpoint)
            except PublishedReportingIntegrityError:
                return XirrResult(
                    status="unavailable",
                    method_version=XIRR_METHOD_VERSION,
                    reason_codes=("xirr_root_outside_method_domain",),
                    cash_flow_count=len(dated),
                    annualized_headline_eligible=False,
                    rate=None,
                    xnpv_residual_exact=None,
                )
            if midpoint_value == ZERO:
                q, residual = midpoint, midpoint_value
                break
            if _sign(midpoint_value) == _sign(low_value):
                low, low_value = midpoint, midpoint_value
            else:
                high, high_value = midpoint, midpoint_value
    if q is None or residual is None:
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_method50_lattice_not_resolved",),
            cash_flow_count=len(dated),
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )

    try:
        with calculation_context() as context:
            rate = method_decimal_subtract(context.power(q, -365), ONE)
        rate_metric = _reporting_metric(rate, field_name="xirr_rate_method50")
    except (
        DivisionByZero,
        InvalidOperation,
        Overflow,
        ReportingMethodDomainError,
    ):
        return XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_rate_outside_method_domain",),
            cash_flow_count=len(dated),
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )
    headline_eligible = (effective_end - effective_start).days >= 365
    return XirrResult(
        status="ready",
        method_version=XIRR_METHOD_VERSION,
        reason_codes=(
            ()
            if headline_eligible
            else ("xirr_annualized_headline_requires_at_least_365_elapsed_days",)
        ),
        cash_flow_count=len(dated),
        annualized_headline_eligible=headline_eligible,
        rate=rate_metric,
        xnpv_residual_exact=residual,
    )


def _build_rebased_wealth_points(
    snapshots: tuple[PortfolioDailySnapshot, ...],
    active: tuple[PortfolioDailySnapshot, ...],
) -> tuple[Decimal, Decimal, tuple[RebasedWealthPoint, ...]]:
    factors = tuple(
        method_decimal_add(ONE, row.subperiod_twr_method50)
        for row in active
        if row.subperiod_twr_method50 is not None
    )
    if any(factor < ZERO for factor in factors):
        raise PublishedReportingIntegrityError(
            "a subperiod TWR factor cannot be negative"
        )
    wealth = ONE
    peak = ONE
    points: list[RebasedWealthPoint] = []
    active_by_date = {row.as_of_date: row for row in active}
    for snapshot in snapshots:
        wealth_adjustment: Decimal | None = None
        if snapshot.as_of_date in active_by_date:
            row = active_by_date[snapshot.as_of_date]
            assert row.subperiod_twr_method50 is not None
            factor = _bounded_method(
                method_decimal_add(ONE, row.subperiod_twr_method50),
                label="selected-range return factor",
            )
            raw_wealth = exact_decimal_product(wealth, factor)
            wealth = _bounded_method(
                method_decimal_multiply(wealth, factor),
                label="selected-range wealth",
            )
            wealth_adjustment = _bounded_adjustment(
                exact_decimal_subtract(wealth, raw_wealth),
                label="selected-range wealth chain adjustment",
            )
            peak = max(peak, wealth)
        drawdown = _method_divide(
            method_decimal_subtract(wealth, peak),
            peak,
            label="selected-range drawdown",
        )
        points.append(
            RebasedWealthPoint(
                snapshot.as_of_date,
                wealth,
                peak,
                drawdown,
                wealth_adjustment,
                snapshot.return_chain_status,
            )
        )
    cumulative = _bounded_method(
        method_decimal_subtract(wealth, ONE),
        label="selected-range cumulative TWR",
    )
    return wealth, cumulative, tuple(points)


def build_selected_range_performance(
    publication: CurrentPortfolioDailyPublication,
    *,
    include_secondary_metrics: bool = True,
    statistics_frequency: CalendarFrequency = "monthly",
    return_buckets: tuple[ReturnCalendarBucketResult, ...] | None = None,
) -> SelectedRangePerformance:
    statistics_frequency = _require_calendar_frequency(statistics_frequency)
    snapshots = _validate_snapshot_order(publication)
    selected_start = snapshots[0].as_of_date if snapshots else None
    selected_end = snapshots[-1].as_of_date if snapshots else None
    empty_stats = _unavailable_statistics(
        "risk_statistics_return_chain_unavailable",
        frequency=statistics_frequency,
        observation_count=0,
    )
    empty_xirr = XirrResult(
        status="unavailable",
        method_version=XIRR_METHOD_VERSION,
        reason_codes=("xirr_return_chain_unavailable",),
        cash_flow_count=0,
        annualized_headline_eligible=False,
        rate=None,
        xnpv_residual_exact=None,
    )
    if not snapshots:
        return SelectedRangePerformance(
            "unavailable",
            ("selected_range_has_no_snapshots",),
            None,
            None,
            None,
            None,
            None,
            0,
            None,
            None,
            None,
            (),
            (),
            empty_stats,
            empty_xirr,
        )

    active: list[PortfolioDailySnapshot] = []
    saw_reanchor = False
    for snapshot in snapshots:
        status = snapshot.return_chain_status
        if status == "no_new_valuation":
            continue
        if status == "reanchor":
            if active or saw_reanchor:
                return _unavailable_performance(
                    snapshots,
                    reason="selected_range_contains_multiple_return_chains",
                    statistics_frequency=statistics_frequency,
                )
            saw_reanchor = True
            continue
        if status != "active":
            return _unavailable_performance(
                snapshots,
                reason="selected_range_return_chain_broken",
                statistics_frequency=statistics_frequency,
            )
        if (
            not snapshot.measured_return
            or snapshot.subperiod_twr_method50 is None
            or snapshot.return_period_start_date is None
            or snapshot.return_period_end_date is None
            or snapshot.return_period_day_count is None
            or snapshot.return_coverage_state != "complete"
        ):
            return _unavailable_performance(
                snapshots,
                reason="selected_range_subperiod_return_unavailable",
                statistics_frequency=statistics_frequency,
            )
        if (
            snapshot.return_period_end_date != snapshot.as_of_date
            or snapshot.return_period_start_date >= snapshot.return_period_end_date
            or snapshot.return_period_day_count
            != (
                snapshot.return_period_end_date - snapshot.return_period_start_date
            ).days
        ):
            raise PublishedReportingIntegrityError(
                "active snapshot return-period boundary is inconsistent"
            )
        if (
            active
            and snapshot.return_period_start_date != active[-1].return_period_end_date
        ):
            return _unavailable_performance(
                snapshots,
                reason="selected_range_return_periods_not_contiguous",
                statistics_frequency=statistics_frequency,
            )
        active.append(snapshot)

    if not active:
        return _unavailable_performance(
            snapshots,
            reason="selected_range_has_no_return_observations",
            statistics_frequency=statistics_frequency,
        )

    try:
        wealth, cumulative, points = _build_rebased_wealth_points(
            snapshots,
            tuple(active),
        )
    except ReportingMethodDomainError:
        return _unavailable_performance(
            snapshots,
            reason="selected_range_method_domain_unavailable",
            statistics_frequency=statistics_frequency,
        )
    effective_start = active[0].return_period_start_date
    effective_end = active[-1].return_period_end_date
    assert effective_start is not None and effective_end is not None
    elapsed_days = (effective_end - effective_start).days
    annualized = (
        _annualized_twr(wealth, elapsed_days) if include_secondary_metrics else None
    )
    reasons: tuple[str, ...] = ()
    if include_secondary_metrics and elapsed_days < MINIMUM_ANNUALIZATION_DAYS:
        reasons = ("annualized_twr_requires_at_least_365_elapsed_days",)
    elif include_secondary_metrics and annualized is None:
        reasons = ("annualized_twr_non_positive_wealth",)
    statistics = (
        _calendar_period_statistics(
            publication,
            frequency=statistics_frequency,
            return_buckets=return_buckets,
        )
        if include_secondary_metrics
        else _unavailable_statistics(
            "risk_statistics_not_requested",
            frequency=statistics_frequency,
            observation_count=len(active),
        )
    )
    xirr = (
        _build_xirr(
            publication,
            tuple(active),
            effective_start=effective_start,
            effective_end=effective_end,
        )
        if include_secondary_metrics
        else XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_not_requested",),
            cash_flow_count=0,
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        )
    )
    return SelectedRangePerformance(
        "ready",
        (
            _ordered_reasons(reasons, statistics.reason_codes, xirr.reason_codes)
            if include_secondary_metrics
            else ()
        ),
        selected_start,
        selected_end,
        effective_start,
        effective_end,
        elapsed_days,
        len(active),
        cumulative,
        annualized,
        points[-1].drawdown_method50,
        min(point.drawdown_method50 for point in points),
        points,
        tuple(active),
        statistics,
        xirr,
    )


def _unavailable_performance(
    snapshots: tuple[PortfolioDailySnapshot, ...],
    *,
    reason: str,
    statistics_frequency: CalendarFrequency = "monthly",
) -> SelectedRangePerformance:
    return SelectedRangePerformance(
        "unavailable",
        (reason,),
        snapshots[0].as_of_date if snapshots else None,
        snapshots[-1].as_of_date if snapshots else None,
        None,
        None,
        None,
        0,
        None,
        None,
        None,
        None,
        (),
        (),
        _unavailable_statistics(
            "risk_statistics_return_chain_unavailable",
            frequency=statistics_frequency,
            observation_count=0,
        ),
        XirrResult(
            status="unavailable",
            method_version=XIRR_METHOD_VERSION,
            reason_codes=("xirr_return_chain_unavailable",),
            cash_flow_count=0,
            annualized_headline_eligible=False,
            rate=None,
            xnpv_residual_exact=None,
        ),
    )


def build_linked_attribution(
    publication: CurrentPortfolioDailyPublication,
    performance: SelectedRangePerformance,
    *,
    axis: AttributionAxis,
    group_key: str | None = None,
) -> LinkedAttribution:
    if performance.status != "ready":
        return LinkedAttribution(
            status="unavailable",
            method_version=FRONGELLO_METHOD_VERSION,
            reason_codes=performance.reason_codes,
            axis=axis,
            effective_start_date=None,
            effective_end_date=None,
            observation_count=0,
            cumulative_twr_method50=None,
            total_linked_contribution_effective=None,
            total_linking_adjustment_exact=None,
            closure_residual_exact=None,
            groups=(),
        )
    active = performance.active_snapshots
    active_dates = {row.as_of_date for row in active}
    selected_rows = tuple(
        row
        for row in publication.contributions
        if row.axis == axis and row.as_of_date in active_dates
    )
    for row in selected_rows:
        if row.contribution_method50 is not None:
            _bounded_method(
                row.contribution_method50,
                label=(
                    f"published contribution {axis}/{row.group_key}/{row.as_of_date}"
                ),
            )
    by_date: dict[date, list[PortfolioDailyContribution]] = defaultdict(list)
    for row in selected_rows:
        by_date[row.as_of_date].append(row)
    if any(not by_date[row.as_of_date] for row in active):
        return _unavailable_attribution(
            axis,
            performance,
            "attribution_axis_not_published_for_every_return_period",
        )
    if any(
        not row.measured
        or row.coverage_state != "complete"
        or row.contribution_method50 is None
        or row.opening_nav_exact is None
        or row.closing_nav_exact is None
        or row.external_flow_in_exact is None
        or row.external_flow_out_exact is None
        or row.internal_flow_in_exact is None
        or row.internal_flow_out_exact is None
        or row.economic_pnl_exact is None
        or row.closure_residual_exact != ZERO
        for row in selected_rows
    ):
        return _unavailable_attribution(
            axis,
            performance,
            "attribution_axis_contains_unmeasured_group",
        )

    seen: set[tuple[date, str]] = set()
    for row in selected_rows:
        identity = (row.as_of_date, row.group_key)
        if identity in seen:
            raise PublishedReportingIntegrityError(
                "published contribution group identity is duplicated"
            )
        seen.add(identity)
    keys = sorted({row.group_key for row in selected_rows})
    rows_by_key_date = {(row.group_key, row.as_of_date): row for row in selected_rows}
    for key in keys:
        previous_closing: Decimal | None = None
        for snapshot in active:
            row = rows_by_key_date.get((key, snapshot.as_of_date))
            opening = row.opening_nav_exact if row is not None else ZERO
            closing = row.closing_nav_exact if row is not None else ZERO
            assert opening is not None and closing is not None
            if previous_closing is not None and opening != previous_closing:
                raise PublishedReportingIntegrityError(
                    f"{axis}/{key} opening NAV does not equal the prior active "
                    "period closing NAV"
                )
            previous_closing = closing
    active_by_date = {row.as_of_date: row for row in active}
    for as_of, rows in by_date.items():
        daily_total = exact_decimal_sum(
            tuple(
                exact_decimal_sum(
                    (
                        row.contribution_method50,
                        row.contribution_division_adjustment_exact,
                    )
                )
                for row in rows
                if row.contribution_method50 is not None
            )
        )
        expected = active_by_date[as_of].subperiod_twr_method50
        if expected is None or daily_total != expected:
            raise PublishedReportingIntegrityError(
                f"{axis} effective daily contributions do not close to method50 TWR"
            )

    expected_twr = performance.cumulative_twr_method50
    if expected_twr is None:
        raise PublishedReportingIntegrityError(
            "ready performance lacks cumulative method50 TWR"
        )
    try:
        linked_by_group = {key: ZERO for key in keys}
        for snapshot in active:
            assert snapshot.subperiod_twr_method50 is not None
            factor = _bounded_method(
                method_decimal_add(ONE, snapshot.subperiod_twr_method50),
                label="Frongello return factor",
            )
            rows_for_date = {row.group_key: row for row in by_date[snapshot.as_of_date]}
            for key in keys:
                row = rows_for_date.get(key)
                effective_contribution = (
                    ZERO
                    if row is None
                    else exact_decimal_sum(
                        (
                            row.contribution_method50,
                            row.contribution_division_adjustment_exact,
                        )
                    )
                )
                linked_by_group[key] = _bounded_method(
                    method_decimal_add(
                        method_decimal_multiply(linked_by_group[key], factor),
                        effective_contribution,
                    ),
                    label=f"Frongello linked contribution {axis}/{key}",
                )

        unadjusted_total = exact_decimal_sum(
            tuple(linked_by_group[key] for key in keys)
        )
        total_linking_adjustment = _bounded_adjustment(
            exact_decimal_subtract(expected_twr, unadjusted_total),
            label=f"Frongello total linking adjustment {axis}",
        )
        adjustment_key = max(
            sorted(keys),
            key=lambda key: linked_by_group[key].copy_abs(),
        )
    except ReportingMethodDomainError:
        return _unavailable_attribution(
            axis,
            performance,
            "attribution_method_domain_unavailable",
        )

    labels: dict[str, str] = {}
    for row in selected_rows:
        existing = labels.setdefault(row.group_key, row.group_label)
        if existing != row.group_label:
            raise PublishedReportingIntegrityError(
                "published contribution group label changed inside selected range"
            )
    first_date = active[0].as_of_date
    last_date = active[-1].as_of_date
    groups: list[LinkedAttributionGroup] = []
    for key in keys:
        opening_row = rows_by_key_date.get((key, first_date))
        closing_row = rows_by_key_date.get((key, last_date))
        opening = opening_row.opening_nav_exact if opening_row is not None else ZERO
        closing = closing_row.closing_nav_exact if closing_row is not None else ZERO
        assert opening is not None and closing is not None
        group_rows = tuple(row for row in selected_rows if row.group_key == key)
        flow_in = exact_decimal_sum(
            tuple(
                row.external_flow_in_exact
                for row in group_rows
                if row.external_flow_in_exact is not None
            )
        )
        flow_out = exact_decimal_sum(
            tuple(
                row.external_flow_out_exact
                for row in group_rows
                if row.external_flow_out_exact is not None
            )
        )
        internal_in = exact_decimal_sum(
            tuple(
                row.internal_flow_in_exact
                for row in group_rows
                if row.internal_flow_in_exact is not None
            )
        )
        internal_out = exact_decimal_sum(
            tuple(
                row.internal_flow_out_exact
                for row in group_rows
                if row.internal_flow_out_exact is not None
            )
        )
        pnl = exact_decimal_sum(
            tuple(
                row.economic_pnl_exact
                for row in group_rows
                if row.economic_pnl_exact is not None
            )
        )
        residual = exact_decimal_subtract(
            exact_decimal_sum((closing, flow_out, internal_out)),
            exact_decimal_sum((opening, flow_in, internal_in, pnl)),
        )
        if residual != ZERO:
            raise PublishedReportingIntegrityError(
                f"{axis}/{key} selected-range bridge does not close exactly"
            )
        unadjusted_linked = linked_by_group[key]
        linking_adjustment = total_linking_adjustment if key == adjustment_key else ZERO
        linked = exact_decimal_sum((unadjusted_linked, linking_adjustment))
        groups.append(
            LinkedAttributionGroup(
                axis=axis,
                group_key=key,
                group_label=labels[key],
                opening_nav_exact=opening,
                closing_nav_exact=closing,
                external_flow_in_exact=flow_in,
                external_flow_out_exact=flow_out,
                internal_flow_in_exact=internal_in,
                internal_flow_out_exact=internal_out,
                economic_pnl_exact=pnl,
                linked_contribution_method50=unadjusted_linked,
                linking_adjustment_exact=linking_adjustment,
                linked_contribution_effective=linked,
                closure_residual_exact=residual,
            )
        )
    total = exact_decimal_sum(
        tuple(row.linked_contribution_effective for row in groups)
    )
    if total != expected_twr:
        raise PublishedReportingIntegrityError(
            f"{axis} balanced Frongello contributions do not close to method50 TWR"
        )
    visible = tuple(
        row for row in groups if group_key is None or row.group_key == group_key
    )
    return LinkedAttribution(
        status="ready",
        method_version=FRONGELLO_METHOD_VERSION,
        reason_codes=(),
        axis=axis,
        effective_start_date=performance.effective_return_start_date,
        effective_end_date=performance.effective_return_end_date,
        observation_count=len(active),
        cumulative_twr_method50=expected_twr,
        total_linked_contribution_effective=total,
        total_linking_adjustment_exact=total_linking_adjustment,
        closure_residual_exact=exact_decimal_subtract(total, expected_twr),
        groups=visible,
    )


def _unavailable_attribution(
    axis: AttributionAxis,
    performance: SelectedRangePerformance,
    reason: str,
) -> LinkedAttribution:
    return LinkedAttribution(
        status="unavailable",
        method_version=FRONGELLO_METHOD_VERSION,
        reason_codes=(reason,),
        axis=axis,
        effective_start_date=performance.effective_return_start_date,
        effective_end_date=performance.effective_return_end_date,
        observation_count=performance.observation_count,
        cumulative_twr_method50=performance.cumulative_twr_method50,
        total_linked_contribution_effective=None,
        total_linking_adjustment_exact=None,
        closure_residual_exact=None,
        groups=(),
    )


def _calendar_key(value: date, frequency: CalendarFrequency) -> str:
    if frequency == "monthly":
        return value.strftime("%Y-%m")
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _calendar_bounds(value: date, frequency: CalendarFrequency) -> tuple[date, date]:
    if frequency == "weekly":
        start = value - timedelta(days=value.weekday())
        return start, start + timedelta(days=6)
    start = date(value.year, value.month, 1)
    next_month = (
        date(value.year + 1, 1, 1)
        if value.month == 12
        else date(value.year, value.month + 1, 1)
    )
    return start, next_month - timedelta(days=1)


def _calendar_bucket_coverage(
    publication: CurrentPortfolioDailyPublication,
    *,
    calendar_start: date,
    calendar_end: date,
    performance: SelectedRangePerformance,
) -> tuple[CalendarCoverageState, tuple[str, ...]]:
    if performance.status != "ready":
        return "unavailable", ("calendar_bucket_return_unavailable",)
    effective_start = performance.effective_return_start_date
    effective_end = performance.effective_return_end_date
    if effective_start is None or effective_end is None:
        raise PublishedReportingIntegrityError(
            "ready calendar bucket lacks effective return boundaries"
        )

    # Calendar returns are open-left/closed-right.  A bucket beginning on day D
    # therefore needs a reliable D-1 EOD opening anchor, not merely an endpoint
    # somewhere inside the requested calendar range.  A sparse valuation
    # subperiod may begin before D-1 and is still owned wholly by its endpoint
    # bucket.  At the close, qualified no-new-valuation points may carry the
    # last fresh endpoint through a weekend or other normal calendar gap.
    required_opening_boundary = calendar_start - timedelta(days=1)
    reasons: list[str] = []
    if publication.requested_range_start > calendar_start:
        reasons.append("calendar_bucket_requested_start_after_calendar_start")
    elif effective_start > required_opening_boundary:
        reasons.append(
            "calendar_bucket_return_start_after_calendar_opening_boundary"
        )
    if publication.requested_range_end < calendar_end:
        reasons.append("calendar_bucket_requested_end_before_calendar_end")
    elif effective_end < calendar_end:
        trailing_points = tuple(
            point
            for point in performance.wealth_points
            if effective_end < point.as_of_date <= calendar_end
        )
        closes_with_qualified_carry = bool(trailing_points) and (
            trailing_points[-1].as_of_date == calendar_end
            and all(
                point.return_chain_status == "no_new_valuation"
                for point in trailing_points
            )
        )
        if not closes_with_qualified_carry:
            reasons.append(
                "calendar_bucket_return_end_before_calendar_closing_boundary"
            )
    if reasons:
        return "partial", tuple(reasons)
    return "complete", ()


def build_return_calendar(
    publication: CurrentPortfolioDailyPublication,
    *,
    frequency: CalendarFrequency,
) -> tuple[ReturnCalendarBucketResult, ...]:
    frequency = _require_calendar_frequency(frequency)
    snapshots = _validate_snapshot_order(publication)
    grouped: dict[str, list[PortfolioDailySnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[_calendar_key(snapshot.as_of_date, frequency)].append(snapshot)
    buckets: list[ReturnCalendarBucketResult] = []
    for bucket_key in sorted(grouped):
        bucket_snapshots = tuple(grouped[bucket_key])
        first_date = bucket_snapshots[0].as_of_date
        calendar_start, calendar_end = _calendar_bounds(first_date, frequency)
        bucket_publication = CurrentPortfolioDailyPublication(
            metadata=publication.metadata,
            requested_range_start=bucket_snapshots[0].as_of_date,
            requested_range_end=bucket_snapshots[-1].as_of_date,
            snapshots=bucket_snapshots,
            holdings=(),
            balances=(),
            lots=(),
            lot_dispositions=(),
            contributions=tuple(
                row
                for row in publication.contributions
                if row.as_of_date in {item.as_of_date for item in bucket_snapshots}
            ),
        )
        performance = build_selected_range_performance(
            bucket_publication,
            include_secondary_metrics=False,
            statistics_frequency=frequency,
        )
        coverage_state, coverage_reasons = _calendar_bucket_coverage(
            publication,
            calendar_start=calendar_start,
            calendar_end=calendar_end,
            performance=performance,
        )
        buckets.append(
            ReturnCalendarBucketResult(
                bucket_key=bucket_key,
                frequency=frequency,
                calendar_start_date=calendar_start,
                calendar_end_date=calendar_end,
                coverage_state=coverage_state,
                coverage_reason_codes=coverage_reasons,
                performance=performance,
            )
        )
    return tuple(buckets)


def build_attribution_calendar(
    publication: CurrentPortfolioDailyPublication,
    *,
    frequency: CalendarFrequency,
    axis: AttributionAxis,
    group_key: str | None = None,
    return_buckets: tuple[ReturnCalendarBucketResult, ...] | None = None,
) -> tuple[AttributionCalendarBucketResult, ...]:
    frequency = _require_calendar_frequency(frequency)
    if return_buckets is not None and any(
        bucket.frequency != frequency for bucket in return_buckets
    ):
        raise PublishedReportingIntegrityError(
            "supplied return calendar frequency does not match attribution frequency"
        )
    resolved_return_buckets = (
        return_buckets
        if return_buckets is not None
        else build_return_calendar(publication, frequency=frequency)
    )
    results: list[AttributionCalendarBucketResult] = []
    for bucket in resolved_return_buckets:
        bucket_dates = {
            row.as_of_date
            for row in publication.snapshots
            if _calendar_key(row.as_of_date, frequency) == bucket.bucket_key
        }
        bucket_publication = CurrentPortfolioDailyPublication(
            metadata=publication.metadata,
            requested_range_start=min(bucket_dates),
            requested_range_end=max(bucket_dates),
            snapshots=tuple(
                row for row in publication.snapshots if row.as_of_date in bucket_dates
            ),
            holdings=(),
            balances=(),
            lots=(),
            lot_dispositions=(),
            contributions=tuple(
                row
                for row in publication.contributions
                if row.as_of_date in bucket_dates
            ),
        )
        attribution = build_linked_attribution(
            bucket_publication,
            bucket.performance,
            axis=axis,
            group_key=group_key,
        )
        results.append(
            AttributionCalendarBucketResult(
                bucket_key=bucket.bucket_key,
                frequency=frequency,
                calendar_start_date=bucket.calendar_start_date,
                calendar_end_date=bucket.calendar_end_date,
                coverage_state=bucket.coverage_state,
                coverage_reason_codes=bucket.coverage_reason_codes,
                attribution=attribution,
            )
        )
    return tuple(results)


__all__ = [
    "AttributionAxis",
    "AttributionCalendarBucketResult",
    "CalendarCoverageState",
    "CalendarFrequency",
    "CalendarPeriodStatistics",
    "FRONGELLO_METHOD_VERSION",
    "LinkedAttribution",
    "LinkedAttributionGroup",
    "PublishedReportingIntegrityError",
    "RebasedWealthPoint",
    "ReturnCalendarBucketResult",
    "SelectedRangePerformance",
    "STATISTICS_METHOD_VERSION_MONTHLY",
    "STATISTICS_METHOD_VERSION_WEEKLY",
    "XIRR_METHOD_VERSION",
    "XirrResult",
    "build_attribution_calendar",
    "build_linked_attribution",
    "build_return_calendar",
    "build_selected_range_performance",
    "validate_published_snapshot_chain",
]
