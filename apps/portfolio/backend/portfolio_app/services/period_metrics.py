from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import exp, expm1, fsum, isfinite, log1p, sqrt
from typing import Literal

from portfolio_app.services.calculation_frequency import CalculationFrequency


DAYS_PER_YEAR = 365.25
PORTFOLIO_RISK_CALCULATION_FREQUENCY: CalculationFrequency = "daily"
PORTFOLIO_RISK_MINIMUM_SAMPLE_COUNT = 2
XIRR_MIN_RATE = -0.9999
XIRR_MAX_RATE = 1_000_000.0

XirrSolveStatus = Literal[
    "unique_root",
    "invalid_cash_flows",
    "no_root",
    "multiple_roots_or_non_unique",
]


@dataclass(frozen=True, slots=True)
class XirrSolveResult:
    status: XirrSolveStatus
    rate: float | None = None


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def year_fraction(start_date: date, end_date: date) -> float:
    return max((end_date - start_date).days / DAYS_PER_YEAR, 0.0)


def periods_per_year_from_observations(
    *,
    observation_count: int,
    start_date: date | None,
    end_date: date | None,
) -> float | None:
    if observation_count < 1 or start_date is None or end_date is None:
        return None
    elapsed_days = (end_date - start_date).days
    if elapsed_days <= 0:
        return None
    return float(observation_count) / float(elapsed_days) * DAYS_PER_YEAR


def annualization_periods_per_year_from_dates(
    date_keys: list[date],
    *,
    observation_count: int | None = None,
    start_date: date | None = None,
) -> float | None:
    sorted_dates = sorted(set(item for item in date_keys if isinstance(item, date)))
    resolved_observation_count = observation_count if observation_count is not None else len(sorted_dates)
    if resolved_observation_count < 1 or len(sorted_dates) < 2:
        return None
    if start_date is not None:
        elapsed_days = (sorted_dates[-1] - start_date).days
        return (
            float(resolved_observation_count) / float(elapsed_days) * DAYS_PER_YEAR
            if elapsed_days > 0
            else None
        )
    elapsed_days = (sorted_dates[-1] - sorted_dates[0]).days
    if elapsed_days < 0:
        return None
    gaps = sorted(
        (sorted_dates[index] - sorted_dates[index - 1]).days
        for index in range(1, len(sorted_dates))
        if (sorted_dates[index] - sorted_dates[index - 1]).days > 0
    )
    median_gap = gaps[len(gaps) // 2] if gaps else 1
    observation_span_days = elapsed_days + median_gap
    return (
        float(resolved_observation_count) / float(observation_span_days) * DAYS_PER_YEAR
        if observation_span_days > 0
        else None
    )


def sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance < 0:
        return None
    return sqrt(variance)


def downside_deviation(
    values: list[float],
    *,
    minimum_acceptable_return: float = 0.0,
) -> float | None:
    if not values:
        return None
    downside_squares = [
        min(0.0, value - minimum_acceptable_return) ** 2
        for value in values
    ]
    if not any(square > 0 for square in downside_squares):
        return None
    return sqrt(sum(downside_squares) / len(downside_squares))


def portfolio_risk_result_contract(
    *,
    return_coverage_state: str,
    sample_count: int,
    periods_per_year: float | None,
    annualized_volatility: float | None,
) -> dict[str, object]:
    result_status = "available"
    unavailable_reason = None
    if return_coverage_state != "complete":
        result_status = "unavailable"
        unavailable_reason = "return_coverage_incomplete"
    elif sample_count < PORTFOLIO_RISK_MINIMUM_SAMPLE_COUNT:
        result_status = "insufficient_samples"
        unavailable_reason = "insufficient_return_samples"
    elif periods_per_year is None:
        result_status = "unavailable"
        unavailable_reason = "risk_observation_frequency_unavailable"
    elif annualized_volatility is None:
        result_status = "unavailable"
        unavailable_reason = "risk_metric_calculation_unavailable"

    return {
        "risk_calculation_frequency": PORTFOLIO_RISK_CALCULATION_FREQUENCY,
        "risk_minimum_sample_count": PORTFOLIO_RISK_MINIMUM_SAMPLE_COUNT,
        "risk_sample_count": sample_count,
        "risk_result_status": result_status,
        "risk_unavailable_reason": unavailable_reason,
    }


def drawdown_stats(
    snapshots: list[dict[str, object]],
    *,
    start_anchor_date: date | None = None,
    return_field: str = "daily_twr",
) -> dict[str, int | float | None]:
    growth_points: list[tuple[date, float]] = []
    growth_index = 1.0
    for snapshot in snapshots:
        daily_twr = _safe_float(snapshot.get(return_field))
        snapshot_date = snapshot.get("as_of_date")
        if daily_twr is None or not isfinite(daily_twr) or not isinstance(snapshot_date, date):
            continue
        growth_index *= 1.0 + daily_twr
        growth_points.append((snapshot_date, growth_index))
    if not growth_points:
        return {
            "current_drawdown": None,
            "max_drawdown": None,
            "max_drawdown_days": None,
            "drawdown_duration_days": None,
        }

    running_peak = 1.0
    running_peak_date = start_anchor_date or growth_points[0][0]
    current_drawdown = 0.0
    max_drawdown = 0.0
    max_drawdown_peak_date = running_peak_date
    max_drawdown_trough_date = running_peak_date
    max_drawdown_peak_growth = running_peak
    recovery_index = None

    for point_date, growth_index in growth_points:
        if growth_index >= running_peak - 1e-12:
            running_peak = growth_index
            running_peak_date = point_date
        drawdown = (growth_index / running_peak) - 1.0 if running_peak > 1e-12 else None
        if drawdown is not None:
            current_drawdown = drawdown
        if drawdown is not None and drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_peak_date = running_peak_date
            max_drawdown_trough_date = point_date
            max_drawdown_peak_growth = running_peak
            recovery_index = None

    if max_drawdown_peak_date != max_drawdown_trough_date:
        for point_date, growth_index in growth_points:
            if point_date <= max_drawdown_trough_date:
                continue
            if growth_index >= max_drawdown_peak_growth - 1e-12:
                recovery_index = point_date
                break

    return {
        "current_drawdown": current_drawdown,
        "max_drawdown": max_drawdown,
        "max_drawdown_days": (max_drawdown_trough_date - max_drawdown_peak_date).days
        if max_drawdown_peak_date != max_drawdown_trough_date
        else 0,
        "drawdown_duration_days": (recovery_index - max_drawdown_peak_date).days
        if recovery_index is not None and max_drawdown_peak_date != max_drawdown_trough_date
        else None,
    }


def xnpv(rate: float, cash_flows: list[tuple[date, float]]) -> float:
    if rate <= -0.999999999:
        return float("inf")
    start_date = cash_flows[0][0]
    total = 0.0
    for cash_flow_date, amount in cash_flows:
        years = year_fraction(start_date, cash_flow_date)
        total += amount / ((1.0 + rate) ** years)
    return total


def _discounted_cash_flow_roots(
    terms: list[tuple[float, float]], low: float, high: float,
) -> list[float]:
    """Isolate all admissible roots in log(1+r), including turning-point roots.

    Sign variations bound the number of roots; they do not prove multiplicity.
    After removing the positive exponential factor, the derivative has one
    fewer term. Its roots partition the original function into monotone pieces,
    so bisection cannot skip a pair of IRRs between arbitrary grid points.
    """
    scale = max(abs(amount) for _, amount in terms)
    first_time = terms[0][0]
    terms = [(time - first_time, amount / scale) for time, amount in terms]
    variations = sum(a * b < 0 for (_, a), (_, b) in zip(terms, terms[1:]))
    if not variations:
        return []

    def value(x: float) -> float:
        exponents = [-time * x for time, _ in terms]
        shift = max(exponents)
        weighted = [amount * exp(exponent - shift)
                    for (_, amount), exponent in zip(terms, exponents)]
        return fsum(weighted) / fsum(abs(amount) for amount in weighted)

    critical = []
    if variations > 1:
        critical = _discounted_cash_flow_roots(
            [(time, -time * amount) for time, amount in terms[1:]], low, high,
        )
    boundaries = sorted(set([low, *critical, high]))
    values = [value(x) for x in boundaries]
    roots = [x for x, y in zip(boundaries, values) if abs(y) <= 1e-12]
    for left, right, left_value, right_value in zip(
        boundaries, boundaries[1:], values, values[1:],
    ):
        if abs(left_value) <= 1e-12 or abs(right_value) <= 1e-12 or left_value * right_value >= 0:
            continue
        for _ in range(128):
            middle = (left + right) / 2
            middle_value = value(middle)
            if abs(middle_value) <= 1e-13 or right - left <= 1e-13:
                break
            if left_value * middle_value < 0:
                right = middle
            else:
                left, left_value = middle, middle_value
        roots.append(middle)
    return sorted(roots)


def solve_xirr_result(cash_flows: list[tuple[date, float]]) -> XirrSolveResult:
    cash_flows_by_date: dict[date, float] = defaultdict(float)
    for cash_flow_date, amount in cash_flows:
        normalized_amount = _safe_float(amount)
        if (
            type(cash_flow_date) is not date
            or normalized_amount is None
            or not isfinite(normalized_amount)
        ):
            return XirrSolveResult(status="invalid_cash_flows")
        cash_flows_by_date[cash_flow_date] += normalized_amount
    if any(not isfinite(amount) for amount in cash_flows_by_date.values()):
        return XirrSolveResult(status="invalid_cash_flows")
    normalized_cash_flows = [
        (cash_flow_date, amount)
        for cash_flow_date, amount in sorted(cash_flows_by_date.items())
        if abs(amount) > 1e-12
    ]
    if (
        len(normalized_cash_flows) < 2
        or normalized_cash_flows[0][0] >= normalized_cash_flows[-1][0]
    ):
        return XirrSolveResult(status="invalid_cash_flows")
    has_positive = any(amount > 0 for _, amount in normalized_cash_flows)
    has_negative = any(amount < 0 for _, amount in normalized_cash_flows)
    if not has_positive or not has_negative:
        return XirrSolveResult(status="no_root")

    start_date = normalized_cash_flows[0][0]
    roots = _discounted_cash_flow_roots([
        (year_fraction(start_date, cash_flow_date), amount)
        for cash_flow_date, amount in normalized_cash_flows
    ], log1p(XIRR_MIN_RATE), log1p(XIRR_MAX_RATE))
    if not roots:
        return XirrSolveResult(status="no_root")
    if len(roots) > 1:
        return XirrSolveResult(status="multiple_roots_or_non_unique")
    return XirrSolveResult(status="unique_root", rate=expm1(roots[0]))


def solve_xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    result = solve_xirr_result(cash_flows)
    return result.rate if result.status == "unique_root" else None
