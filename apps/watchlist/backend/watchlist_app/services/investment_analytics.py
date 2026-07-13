"""Backend calculation authority for fund and benchmark investment analytics.

The module deliberately fails closed when observations cannot support a
methodologically comparable result.  Consumers receive explicit quality
reasons instead of frontend estimates or silently aligned partial samples.
"""

from __future__ import annotations

import calendar
from bisect import bisect_right
from datetime import date, timedelta
import math
import statistics
from typing import Any

from watchlist_app.services.calculation_frequency import (
    ANNUALIZATION_PERIODS_PER_YEAR,
    infer_observation_frequency,
)


INVESTMENT_ANALYTICS_PERIODS = ("1W", "MTD", "YTD", "1Y", "2Y", "3Y", "5Y", "SI")
INVESTMENT_ANALYTICS_ROLLING_WINDOWS = {1, 3, 6, 12}
MIN_ANNUALIZATION_DAYS = 365
MIN_CALMAR_DAYS = math.ceil(365.25 * 3)
MIN_RISK_RETURN_OBSERVATIONS = 12
MIN_CAPTURE_REGIME_OBSERVATIONS = 3
MIN_MONTHLY_VOLATILITY_RETURNS = MIN_RISK_RETURN_OBSERVATIONS
MAX_ROLLING_CHART_POINTS = 520

FREQUENCY_GAP_BOUNDS_DAYS = {
    "daily": (1, 4),
    "weekly": (4, 10),
    "monthly": (18, 45),
}

ANALYTICS_METHODOLOGY = {
    "annualized_return": "geometric_minimum_365_calendar_days",
    "calmar_ratio": "annualized_return_over_max_drawdown_minimum_1096_calendar_days",
    "annualized_risk": "minimum_12_same_frequency_returns",
    "sharpe_ratio": "arithmetic_mean_excess_return_risk_free_rate_zero",
    "downside_deviation": "lower_partial_moment_mar_zero_all_observations",
    "sortino_ratio": "arithmetic_mean_excess_return_mar_zero",
    "relative_alignment": "exact_fund_observation_boundaries",
    "monthly_volatility_annualization": "fixed_same_frequency_annualization_252_52_12",
    "rolling_beta_frequency": "exact_contiguous_calendar_months",
    "capture_ratio": "minimum_3_exact_same_frequency_returns_per_regime",
}


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _validate_nav_points(
    nav_points: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    if not nav_points:
        return [], "empty_series"
    normalized: list[dict[str, Any]] = []
    seen_dates: set[date] = set()
    for point in nav_points:
        point_date = point.get("as_of_date")
        point_value = _safe_float(point.get("value"))
        if not isinstance(point_date, date):
            return [], "invalid_observation_date"
        if point_date in seen_dates:
            return [], "duplicate_observation_date"
        if point_value is None or point_value <= 0:
            return [], "invalid_nav_observation"
        seen_dates.add(point_date)
        normalized.append({**point, "as_of_date": point_date, "value": point_value})
    normalized.sort(key=lambda point: point["as_of_date"])
    if len(normalized) < 2:
        return normalized, "insufficient_observations"
    return normalized, None


def compute_annualized_return(
    latest_value: float,
    base_value: float,
    days: int,
) -> float | None:
    if (
        latest_value <= 0
        or base_value <= 0
        or days < MIN_ANNUALIZATION_DAYS
    ):
        return None
    return (pow(latest_value / base_value, 365.25 / days) - 1) * 100


def compute_calmar_ratio(
    annualized_return: float | None,
    max_drawdown: float | None,
    elapsed_days: int,
) -> float | None:
    if (
        elapsed_days < MIN_CALMAR_DAYS
        or annualized_return is None
        or max_drawdown in {None, 0}
    ):
        return None
    return annualized_return / abs(max_drawdown)



def compute_volatility(nav_points: list[dict[str, Any]]) -> float | None:
    context = _same_frequency_return_context(nav_points)
    returns = [float(point["value"]) for point in context["returns"]]
    if len(returns) < MIN_RISK_RETURN_OBSERVATIONS:
        return None
    periods_per_year = float(context["periods_per_year"])
    return statistics.stdev(returns) * math.sqrt(periods_per_year) * 100


def compute_sharpe(nav_points: list[dict[str, Any]]) -> float | None:
    context = _same_frequency_return_context(nav_points)
    returns = [float(point["value"]) for point in context["returns"]]
    if len(returns) < MIN_RISK_RETURN_OBSERVATIONS:
        return None
    stdev = statistics.stdev(returns)
    periods_per_year = float(context["periods_per_year"])
    if stdev == 0:
        return None
    mean_return = statistics.fmean(returns)
    return (mean_return / stdev) * math.sqrt(periods_per_year)


def _same_frequency_return_context(
    nav_points: list[dict[str, Any]],
) -> dict[str, object]:
    points, reason = _validate_nav_points(nav_points)
    if reason not in {None, "insufficient_observations"} or len(points) < 2:
        return {
            "frequency": None,
            "periods_per_year": None,
            "returns": [],
            "excluded_return_count": 0,
            "reason": reason or "insufficient_history",
        }

    frequency = infer_observation_frequency(
        [point["as_of_date"] for point in points]
    )
    if frequency is None:
        return {
            "frequency": None,
            "periods_per_year": None,
            "returns": [],
            "excluded_return_count": len(points) - 1,
            "reason": "observation_frequency_unresolved",
        }
    minimum_gap, maximum_gap = FREQUENCY_GAP_BOUNDS_DAYS[frequency]
    valid_returns: list[dict[str, object]] = []
    excluded_return_count = 0
    for previous, current in zip(points, points[1:]):
        gap_days = (current["as_of_date"] - previous["as_of_date"]).days
        if gap_days < minimum_gap or gap_days > maximum_gap:
            excluded_return_count += 1
            continue
        valid_returns.append(
            {
                "start_date": previous["as_of_date"],
                "as_of_date": current["as_of_date"],
                "value": current["value"] / previous["value"] - 1,
                "gap_days": gap_days,
            }
        )
    return {
        "frequency": frequency,
        "periods_per_year": ANNUALIZATION_PERIODS_PER_YEAR[frequency],
        "returns": valid_returns,
        "excluded_return_count": excluded_return_count,
        "reason": None,
    }

def compute_downside_deviation(nav_points: list[dict[str, Any]]) -> float | None:
    context = _same_frequency_return_context(nav_points)
    periodic_returns = [float(point["value"]) for point in context["returns"]]
    if len(periodic_returns) < MIN_RISK_RETURN_OBSERVATIONS:
        return None
    periods_per_year = float(context["periods_per_year"])
    downside_variance = (
        sum(min(0.0, value) ** 2 for value in periodic_returns)
        / len(periodic_returns)
    )
    return math.sqrt(max(downside_variance, 0)) * math.sqrt(periods_per_year) * 100


def compute_sortino(nav_points: list[dict[str, Any]]) -> float | None:
    context = _same_frequency_return_context(nav_points)
    periodic_returns = [float(point["value"]) for point in context["returns"]]
    if len(periodic_returns) < MIN_RISK_RETURN_OBSERVATIONS:
        return None
    periods_per_year = float(context["periods_per_year"])
    downside_variance = (
        sum(min(0.0, value) ** 2 for value in periodic_returns)
        / len(periodic_returns)
    )
    downside_deviation = math.sqrt(max(downside_variance, 0))
    if downside_deviation == 0:
        return None
    mean_return = statistics.fmean(periodic_returns)
    return (mean_return / downside_deviation) * math.sqrt(periods_per_year)


def _monthly_close_points(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points, reason = _validate_nav_points(nav_points)
    if reason not in {None, "insufficient_observations"}:
        return []
    monthly: dict[tuple[int, int], dict[str, Any]] = {}
    for point in points:
        monthly[(point["as_of_date"].year, point["as_of_date"].month)] = point
    return [monthly[key] for key in sorted(monthly)]


def _month_ordinal(value: date) -> int:
    return value.year * 12 + value.month - 1


def monthly_return_series(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = _monthly_close_points(nav_points)
    returns: list[dict[str, Any]] = []
    for previous, current in zip(closes, closes[1:]):
        if _month_ordinal(current["as_of_date"]) - _month_ordinal(previous["as_of_date"]) != 1:
            continue
        returns.append({
            "start_date": previous["as_of_date"],
            "as_of_date": current["as_of_date"],
            "value": (current["value"] / previous["value"] - 1) * 100,
        })
    return returns



def trailing_negative_month_count(monthly_returns: list[dict[str, Any]]) -> int:
    count = 0
    for row in reversed(monthly_returns):
        if row["value"] >= 0:
            break
        count += 1
    return count



def _shift_date_months(value: date, months: int) -> date:
    target_month_index = value.month - 1 + months
    target_year = value.year + target_month_index // 12
    target_month = target_month_index % 12 + 1
    target_day = min(value.day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def _sorted_nav_points(nav_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(nav_points, key=lambda point: point["as_of_date"])


def _analytics_window(
    nav_points: list[dict[str, Any]],
    period: str,
    *,
    reference_end_date: date | None,
) -> list[dict[str, Any]]:
    points = _sorted_nav_points(nav_points)
    if not points:
        return []
    end_date = reference_end_date or points[-1]["as_of_date"]
    end_index = bisect_right([point["as_of_date"] for point in points], end_date) - 1
    if end_index < 0:
        return []
    if period == "SI":
        return points[: end_index + 1]

    actual_end_date = points[end_index]["as_of_date"]
    if period == "YTD":
        target_start_date = date(actual_end_date.year, 1, 1)
        start_index = bisect_right(
            [point["as_of_date"] for point in points],
            target_start_date - timedelta(days=1),
        ) - 1
    elif period == "MTD":
        target_start_date = date(actual_end_date.year, actual_end_date.month, 1)
        start_index = bisect_right(
            [point["as_of_date"] for point in points],
            target_start_date - timedelta(days=1),
        ) - 1
    else:
        if period == "1W":
            target_start_date = actual_end_date - timedelta(days=7)
        elif period in {"1Y", "2Y", "3Y", "5Y"}:
            target_start_date = _shift_date_months(
                actual_end_date,
                -12 * int(period[:-1]),
            )
        else:
            raise ValueError(f"Unsupported investment analytics period: {period}")
        start_index = bisect_right(
            [point["as_of_date"] for point in points],
            target_start_date,
        ) - 1

    if start_index < 0 or start_index >= end_index:
        return []
    return points[start_index : end_index + 1]


def _drawdown_stats(nav_points: list[dict[str, Any]]) -> dict[str, object]:
    points, reason = _validate_nav_points(nav_points)
    if reason not in {None, "insufficient_observations"}:
        points = []
    if len(points) < 2:
        return {
            "max_drawdown": None,
            "recovery_days": None,
            "recovery_open": False,
        }

    running_peak_value = points[0]["value"]
    running_peak_index = 0
    worst_drawdown = 0.0
    worst_peak_index = 0
    worst_trough_index: int | None = None
    for index, point in enumerate(points[1:], start=1):
        if point["value"] > running_peak_value:
            running_peak_value = point["value"]
            running_peak_index = index
        drawdown = (
            (point["value"] / running_peak_value - 1) * 100
            if running_peak_value > 0
            else 0.0
        )
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak_index = running_peak_index
            worst_trough_index = index

    if worst_trough_index is None:
        return {
            "max_drawdown": 0.0,
            "recovery_days": 0,
            "recovery_open": False,
        }

    recovery_days: int | None = None
    recovery_open = True
    recovery_target = points[worst_peak_index]["value"]
    for point in points[worst_trough_index + 1 :]:
        if point["value"] >= recovery_target:
            recovery_days = (
                point["as_of_date"] - points[worst_trough_index]["as_of_date"]
            ).days
            recovery_open = False
            break
    return {
        "max_drawdown": worst_drawdown,
        "recovery_days": recovery_days,
        "recovery_open": recovery_open,
    }


def _metric_quality(
    status: str,
    reason: str | None,
    *,
    observation_count: int,
    excluded_observation_count: int = 0,
    used_window_count: int = 0,
    excluded_window_count: int = 0,
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "observation_count": observation_count,
        "excluded_observation_count": excluded_observation_count,
        "used_window_count": used_window_count,
        "excluded_window_count": excluded_window_count,
    }


def _performance_snapshot_quality(
    nav_points: list[dict[str, Any]],
    snapshot: dict[str, object],
) -> dict[str, object]:
    points, reason = _validate_nav_points(nav_points)
    if reason is not None or len(points) < 2:
        unavailable_reason = reason or "insufficient_observations"
        return _unavailable_performance_quality(
            unavailable_reason,
            observation_count=len(points),
        )

    elapsed_days = (points[-1]["as_of_date"] - points[0]["as_of_date"]).days
    window_quality = _metric_quality(
        "available",
        None,
        observation_count=len(points),
    )
    if elapsed_days < MIN_ANNUALIZATION_DAYS:
        annualized_quality = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=len(points),
        )
    elif snapshot["annualized_return"] is None:
        annualized_quality = _metric_quality(
            "unavailable",
            "invalid_annualized_return_inputs",
            observation_count=len(points),
        )
    else:
        annualized_quality = _metric_quality(
            "available",
            None,
            observation_count=len(points),
        )

    risk_context = _same_frequency_return_context(points)
    valid_return_count = len(risk_context["returns"])
    excluded_return_count = int(risk_context["excluded_return_count"])
    if risk_context["reason"] == "observation_frequency_unresolved":
        downside_quality = _metric_quality(
            "unavailable",
            "observation_frequency_unresolved",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    elif valid_return_count < MIN_RISK_RETURN_OBSERVATIONS:
        downside_quality = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    elif risk_context["periods_per_year"] is None:
        downside_quality = _metric_quality(
            "unavailable",
            "invalid_observation_frequency",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    elif excluded_return_count:
        downside_quality = _metric_quality(
            "qualified",
            "off_frequency_observations_excluded",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    else:
        downside_quality = _metric_quality(
            "available",
            None,
            observation_count=valid_return_count,
        )

    if downside_quality["status"] == "unavailable":
        sortino_quality = downside_quality.copy()
    elif snapshot["sortino_ratio"] is None:
        sortino_quality = _metric_quality(
            "unavailable",
            "zero_downside_deviation",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    else:
        sortino_quality = downside_quality.copy()

    volatility_quality = downside_quality.copy()
    if downside_quality["status"] == "unavailable":
        sharpe_quality = downside_quality.copy()
    elif snapshot["sharpe_ratio"] is None:
        sharpe_quality = _metric_quality(
            "unavailable",
            "zero_return_variance",
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
        )
    else:
        sharpe_quality = downside_quality.copy()

    if elapsed_days < MIN_CALMAR_DAYS:
        calmar_quality = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=len(points),
        )
    elif snapshot["calmar_ratio"] is None:
        calmar_quality = _metric_quality(
            "unavailable",
            "zero_or_unavailable_max_drawdown",
            observation_count=len(points),
        )
    else:
        calmar_quality = _metric_quality(
            "available",
            None,
            observation_count=len(points),
        )

    return {
        "window": window_quality,
        "annualized_return": annualized_quality,
        "annualized_volatility": volatility_quality,
        "sharpe_ratio": sharpe_quality,
        "downside_deviation": downside_quality,
        "sortino_ratio": sortino_quality,
        "calmar_ratio": calmar_quality,
    }


def _unavailable_performance_quality(
    reason: str,
    *,
    observation_count: int,
) -> dict[str, object]:
    unavailable = _metric_quality(
        "unavailable",
        reason,
        observation_count=observation_count,
    )
    return {
        "window": unavailable,
        "annualized_return": unavailable.copy(),
        "annualized_volatility": unavailable.copy(),
        "sharpe_ratio": unavailable.copy(),
        "downside_deviation": unavailable.copy(),
        "sortino_ratio": unavailable.copy(),
        "calmar_ratio": unavailable.copy(),
    }


def _performance_metric_snapshot(
    nav_points: list[dict[str, Any]],
) -> tuple[dict[str, object], dict[str, object]]:
    points, reason = _validate_nav_points(nav_points)
    empty = {
        "period_return": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "annualized_downside_deviation": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "max_drawdown": None,
        "recovery_days": None,
        "recovery_open": False,
    }
    if reason is not None or len(points) < 2:
        return empty, _performance_snapshot_quality(points, empty)

    first = points[0]
    latest = points[-1]
    elapsed_days = (latest["as_of_date"] - first["as_of_date"]).days
    period_return = (
        (latest["value"] / first["value"] - 1) * 100
        if first["value"] != 0
        else None
    )
    annualized_return = compute_annualized_return(
        latest["value"],
        first["value"],
        elapsed_days,
    )
    drawdown = _drawdown_stats(points)
    max_drawdown = _safe_float(drawdown["max_drawdown"])
    calmar_ratio = compute_calmar_ratio(
        annualized_return,
        max_drawdown,
        elapsed_days,
    )
    snapshot = {
        "period_return": period_return,
        "annualized_return": annualized_return,
        "annualized_volatility": compute_volatility(points),
        "annualized_downside_deviation": compute_downside_deviation(points),
        "sharpe_ratio": compute_sharpe(points),
        "sortino_ratio": compute_sortino(points),
        "calmar_ratio": calmar_ratio,
        **drawdown,
    }
    return snapshot, _performance_snapshot_quality(points, snapshot)


def _periodic_return_points(nav_points: list[dict[str, Any]]) -> list[dict[str, object]]:
    points, reason = _validate_nav_points(nav_points)
    if reason not in {None, "insufficient_observations"}:
        return []
    returns: list[dict[str, object]] = []
    for previous, current in zip(points, points[1:]):
        returns.append(
            {
                "start_date": previous["as_of_date"],
                "as_of_date": current["as_of_date"],
                "value": current["value"] / previous["value"] - 1,
            }
        )
    return returns


def _sample_covariance(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    return sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    ) / (len(left) - 1)


def _annualized_compound_return(values: list[float], periods_per_year: float) -> float | None:
    if not values or periods_per_year <= 0:
        return None
    cumulative = math.prod(1 + value for value in values)
    if not math.isfinite(cumulative) or cumulative <= 0:
        return None
    return (pow(cumulative, periods_per_year / len(values)) - 1) * 100


def _exact_benchmark_window(
    fund_points: list[dict[str, Any]],
    benchmark_points: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    if len(fund_points) < 2:
        return [], "insufficient_history"
    benchmark_by_date = {
        point["as_of_date"]: point
        for point in benchmark_points
    }
    fund_dates = [point["as_of_date"] for point in fund_points]
    if (
        fund_dates[0] not in benchmark_by_date
        or fund_dates[-1] not in benchmark_by_date
    ):
        return [], "comparison_boundary_mismatch"
    if any(point_date not in benchmark_by_date for point_date in fund_dates[1:-1]):
        return [], "comparison_frequency_mismatch"
    return [benchmark_by_date[point_date] for point_date in fund_dates], None


def _relative_metric_snapshot(
    nav_points: list[dict[str, Any]],
    benchmark_points: list[dict[str, Any]],
) -> dict[str, float | None]:
    empty = {
        "excess_return": None,
        "information_ratio": None,
        "tracking_error": None,
        "beta": None,
        "upside_capture": None,
        "downside_capture": None,
    }
    fund_dates = [point["as_of_date"] for point in nav_points]
    benchmark_dates = [point["as_of_date"] for point in benchmark_points]
    if len(nav_points) < 2 or fund_dates != benchmark_dates:
        return empty

    fund_snapshot, _ = _performance_metric_snapshot(nav_points)
    benchmark_snapshot, _ = _performance_metric_snapshot(benchmark_points)
    fund_period_return = _safe_float(fund_snapshot.get("period_return"))
    benchmark_period_return = _safe_float(benchmark_snapshot.get("period_return"))
    if fund_period_return is not None and benchmark_period_return is not None:
        empty["excess_return"] = fund_period_return - benchmark_period_return
    frequency_context = _same_frequency_return_context(nav_points)
    if (
        len(frequency_context["returns"]) < MIN_RISK_RETURN_OBSERVATIONS
        or int(frequency_context["excluded_return_count"]) > 0
    ):
        return empty

    periods_per_year = float(frequency_context["periods_per_year"])
    fund_returns = [
        current["value"] / previous["value"] - 1
        for previous, current in zip(nav_points, nav_points[1:])
    ]
    benchmark_returns = [
        current["value"] / previous["value"] - 1
        for previous, current in zip(benchmark_points, benchmark_points[1:])
    ]
    active_returns = [
        fund_return - benchmark_return
        for fund_return, benchmark_return in zip(fund_returns, benchmark_returns)
    ]
    active_stdev = statistics.stdev(active_returns)
    tracking_error = active_stdev * math.sqrt(periods_per_year) * 100
    information_ratio = (
        statistics.fmean(active_returns) / active_stdev * math.sqrt(periods_per_year)
        if active_stdev != 0
        else None
    )
    benchmark_stdev = statistics.stdev(benchmark_returns)
    covariance = _sample_covariance(fund_returns, benchmark_returns)
    beta = (
        covariance / (benchmark_stdev**2)
        if covariance is not None and benchmark_stdev != 0
        else None
    )

    up_pairs = [
        (fund_return, benchmark_return)
        for fund_return, benchmark_return in zip(fund_returns, benchmark_returns)
        if benchmark_return > 0
    ]
    down_pairs = [
        (fund_return, benchmark_return)
        for fund_return, benchmark_return in zip(fund_returns, benchmark_returns)
        if benchmark_return < 0
    ]
    upside_benchmark_return = (
        _annualized_compound_return(
            [benchmark_return for _, benchmark_return in up_pairs],
            periods_per_year,
        )
        if len(up_pairs) >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else None
    )
    upside_fund_return = (
        _annualized_compound_return(
            [fund_return for fund_return, _ in up_pairs],
            periods_per_year,
        )
        if len(up_pairs) >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else None
    )
    downside_benchmark_return = (
        _annualized_compound_return(
            [benchmark_return for _, benchmark_return in down_pairs],
            periods_per_year,
        )
        if len(down_pairs) >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else None
    )
    downside_fund_return = (
        _annualized_compound_return(
            [fund_return for fund_return, _ in down_pairs],
            periods_per_year,
        )
        if len(down_pairs) >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else None
    )
    return {
        "excess_return": empty["excess_return"],
        "information_ratio": information_ratio,
        "tracking_error": tracking_error,
        "beta": beta,
        "upside_capture": (
            upside_fund_return / upside_benchmark_return * 100
            if upside_fund_return is not None
            and upside_benchmark_return not in {None, 0}
            else None
        ),
        "downside_capture": (
            downside_fund_return / downside_benchmark_return * 100
            if downside_fund_return is not None
            and downside_benchmark_return not in {None, 0}
            else None
        ),
    }


def _relative_metric_quality(
    fund_points: list[dict[str, Any]],
    benchmark_points: list[dict[str, Any]],
    *,
    alignment_reason: str | None,
) -> dict[str, object]:
    if alignment_reason is not None:
        return _uniform_relative_quality(
            "unavailable",
            alignment_reason,
        )
    if len(fund_points) < 2 or len(benchmark_points) < 2:
        return _uniform_relative_quality(
            "unavailable",
            "insufficient_history",
            observation_count=max(
                min(len(fund_points), len(benchmark_points)) - 1,
                0,
            ),
        )

    frequency_context = _same_frequency_return_context(fund_points)
    return_count = len(frequency_context["returns"])
    excluded_count = int(frequency_context["excluded_return_count"])
    alignment_quality = _metric_quality(
        "available",
        None,
        observation_count=len(fund_points),
    )
    excess_quality = _metric_quality(
        "available",
        None,
        observation_count=1,
    )
    if return_count < MIN_RISK_RETURN_OBSERVATIONS:
        unavailable_risk = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=return_count,
            excluded_observation_count=excluded_count,
        )
        return {
            "alignment": alignment_quality,
            "excess_return": excess_quality,
            "tracking_error": unavailable_risk,
            "information_ratio": unavailable_risk.copy(),
            "beta": unavailable_risk.copy(),
            "upside_capture": unavailable_risk.copy(),
            "downside_capture": unavailable_risk.copy(),
        }
    if excluded_count:
        unavailable_risk = _metric_quality(
            "unavailable",
            "observation_frequency_drift",
            observation_count=return_count,
            excluded_observation_count=excluded_count,
        )
        return {
            "alignment": alignment_quality,
            "excess_return": excess_quality,
            "tracking_error": unavailable_risk,
            "information_ratio": unavailable_risk.copy(),
            "beta": unavailable_risk.copy(),
            "upside_capture": unavailable_risk.copy(),
            "downside_capture": unavailable_risk.copy(),
        }

    fund_returns = [
        current["value"] / previous["value"] - 1
        for previous, current in zip(fund_points, fund_points[1:])
    ]
    benchmark_returns = [
        current["value"] / previous["value"] - 1
        for previous, current in zip(benchmark_points, benchmark_points[1:])
    ]
    active_returns = [
        fund_return - benchmark_return
        for fund_return, benchmark_return in zip(fund_returns, benchmark_returns)
    ]
    active_stdev = statistics.stdev(active_returns)
    benchmark_stdev = statistics.stdev(benchmark_returns)
    available_risk = _metric_quality(
        "available",
        None,
        observation_count=return_count,
    )
    information_ratio_quality = (
        available_risk.copy()
        if active_stdev != 0
        else _metric_quality(
            "unavailable",
            "zero_active_return_variance",
            observation_count=return_count,
        )
    )
    beta_quality = (
        available_risk.copy()
        if benchmark_stdev != 0
        else _metric_quality(
            "unavailable",
            "zero_benchmark_variance",
            observation_count=return_count,
        )
    )
    up_count = sum(value > 0 for value in benchmark_returns)
    down_count = sum(value < 0 for value in benchmark_returns)
    upside_quality = (
        _metric_quality(
            "available",
            None,
            observation_count=up_count,
        )
        if up_count >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else _metric_quality(
            "unavailable",
            "insufficient_regime_observations",
            observation_count=up_count,
        )
    )
    downside_quality = (
        _metric_quality(
            "available",
            None,
            observation_count=down_count,
        )
        if down_count >= MIN_CAPTURE_REGIME_OBSERVATIONS
        else _metric_quality(
            "unavailable",
            "insufficient_regime_observations",
            observation_count=down_count,
        )
    )
    return {
        "alignment": alignment_quality,
        "excess_return": excess_quality,
        "tracking_error": available_risk,
        "information_ratio": information_ratio_quality,
        "beta": beta_quality,
        "upside_capture": upside_quality,
        "downside_capture": downside_quality,
    }


def _uniform_relative_quality(
    status: str,
    reason: str,
    *,
    observation_count: int = 0,
) -> dict[str, object]:
    quality = _metric_quality(
        status,
        reason,
        observation_count=observation_count,
    )
    return {
        "alignment": quality,
        "excess_return": quality.copy(),
        "tracking_error": quality.copy(),
        "information_ratio": quality.copy(),
        "beta": quality.copy(),
        "upside_capture": quality.copy(),
        "downside_capture": quality.copy(),
    }


def _monthly_return_matrix(nav_points: list[dict[str, Any]]) -> list[dict[str, object]]:
    closes = _monthly_close_points(_sorted_nav_points(nav_points))
    close_by_month = {
        (point["as_of_date"].year, point["as_of_date"].month): point
        for point in closes
    }
    rows: dict[int, dict[str, object]] = {}
    latest_close_by_year: dict[int, dict[str, Any]] = {}
    for point in closes:
        point_date = point["as_of_date"]
        latest_close_by_year[point_date.year] = point
        previous_month_date = _shift_date_months(date(point_date.year, point_date.month, 1), -1)
        previous = close_by_month.get((previous_month_date.year, previous_month_date.month))
        if previous is None or previous["value"] == 0:
            continue
        row = rows.setdefault(
            point_date.year,
            {
                "year": str(point_date.year),
                "months": [None] * 12,
                "ytd": None,
            },
        )
        row["months"][point_date.month - 1] = (
            point["value"] / previous["value"] - 1
        ) * 100

    for year, row in rows.items():
        previous_year_close = close_by_month.get((year - 1, 12))
        current_year_close = latest_close_by_year.get(year)
        if (
            previous_year_close is not None
            and current_year_close is not None
            and previous_year_close["value"] != 0
        ):
            row["ytd"] = (
                current_year_close["value"] / previous_year_close["value"] - 1
            ) * 100
    return [rows[year] for year in sorted(rows, reverse=True)]


def _drawdown_series(nav_points: list[dict[str, Any]]) -> list[dict[str, object]]:
    points, reason = _validate_nav_points(nav_points)
    if not points or reason not in {None, "insufficient_observations"}:
        return []
    peak_value = points[0]["value"]
    series: list[dict[str, object]] = []
    for point in points:
        peak_value = max(peak_value, point["value"])
        if peak_value <= 0:
            continue
        series.append(
            {
                "date": point["as_of_date"].isoformat(),
                "value": (point["value"] / peak_value - 1) * 100,
            }
        )
    return series


def _monthly_minimum_series(points: list[dict[str, object]]) -> list[dict[str, object]]:
    monthly: dict[str, dict[str, object]] = {}
    for point in points:
        month = str(point["date"])[:7]
        previous = monthly.get(month)
        if previous is None or float(point["value"]) <= float(previous["value"]):
            monthly[month] = point
    return [monthly[key] for key in sorted(monthly)]


def _monthly_annualized_volatility_series(
    nav_points: list[dict[str, Any]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    points = _sorted_nav_points(nav_points)
    returns = _periodic_return_points(points)
    frequency = infer_observation_frequency(
        [point["as_of_date"] for point in points]
    )
    if frequency is None:
        return [], _metric_quality(
            "unavailable",
            "observation_frequency_unresolved",
            observation_count=0,
            excluded_observation_count=len(returns),
        )
    minimum_gap, maximum_gap = FREQUENCY_GAP_BOUNDS_DAYS[frequency]
    periods_per_year = ANNUALIZATION_PERIODS_PER_YEAR[frequency]
    returns_by_month: dict[tuple[int, int], list[dict[str, object]]] = {}
    for point in returns:
        start_date = point["start_date"]
        point_date = point["as_of_date"]
        if (
            start_date.year != point_date.year
            or start_date.month != point_date.month
        ):
            continue
        returns_by_month.setdefault((point_date.year, point_date.month), []).append(point)
    series: list[dict[str, object]] = []
    used_return_count = 0
    excluded_return_count = 0
    drift_month_count = 0
    eligible_month_count = 0
    for month in sorted(returns_by_month):
        month_returns = returns_by_month[month]
        if len(month_returns) < MIN_MONTHLY_VOLATILITY_RETURNS:
            continue
        eligible_month_count += 1
        valid_month_returns = [
            point
            for point in month_returns
            if minimum_gap
            <= (point["as_of_date"] - point["start_date"]).days
            <= maximum_gap
        ]
        month_excluded_count = len(month_returns) - len(valid_month_returns)
        excluded_return_count += month_excluded_count
        if len(valid_month_returns) < MIN_MONTHLY_VOLATILITY_RETURNS:
            drift_month_count += 1
            continue
        used_return_count += len(valid_month_returns)
        series.append(
            {
                "date": valid_month_returns[-1]["as_of_date"].isoformat(),
                "value": statistics.stdev(
                    float(point["value"]) for point in valid_month_returns
                )
                * math.sqrt(periods_per_year)
                * 100,
            }
        )
    if series and (drift_month_count or excluded_return_count):
        quality = _metric_quality(
            "qualified",
            "observation_frequency_drift_excluded",
            observation_count=used_return_count,
            excluded_observation_count=excluded_return_count,
            used_window_count=len(series),
            excluded_window_count=drift_month_count,
        )
    elif series:
        quality = _metric_quality(
            "available",
            None,
            observation_count=used_return_count,
            used_window_count=len(series),
        )
    elif eligible_month_count:
        quality = _metric_quality(
            "unavailable",
            "observation_frequency_drift",
            observation_count=0,
            excluded_observation_count=excluded_return_count,
            excluded_window_count=drift_month_count,
        )
    else:
        quality = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=0,
        )
    return series, quality


def _rolling_window_points(
    points: list[dict[str, Any]],
    end_index: int,
    window_months: int,
) -> list[dict[str, Any]]:
    if end_index <= 0 or end_index >= len(points):
        return []
    end_point = points[end_index]
    target_start_date = _shift_date_months(end_point["as_of_date"], -window_months)
    start_index = bisect_right(
        [point["as_of_date"] for point in points],
        target_start_date,
    ) - 1
    if start_index < 0 or start_index >= end_index:
        return []
    target_days = (end_point["as_of_date"] - target_start_date).days
    actual_days = (end_point["as_of_date"] - points[start_index]["as_of_date"]).days
    if target_days <= 0 or actual_days <= 0 or actual_days > target_days * 1.35 + 14:
        return []
    return points[start_index : end_index + 1]


def _rolling_risk_series(
    nav_points: list[dict[str, Any]],
    window_months: int,
) -> dict[str, object]:
    points = _sorted_nav_points(nav_points)
    volatility: list[dict[str, object]] = []
    sharpe: list[dict[str, object]] = []
    evaluated_window_count = 0
    valid_return_count = 0
    excluded_return_count = 0
    off_frequency_return_count = 0
    volatility_excluded_window_count = 0
    sharpe_excluded_window_count = 0
    unresolved_frequency_window_count = 0
    zero_variance_window_count = 0
    for end_index in range(1, len(points)):
        window = _rolling_window_points(points, end_index, window_months)
        if not window:
            continue
        evaluated_window_count += 1
        context = _same_frequency_return_context(window)
        returns = [float(point["value"]) for point in context["returns"]]
        valid_return_count += len(returns)
        context_excluded_count = int(context["excluded_return_count"])
        excluded_return_count += context_excluded_count
        if context["frequency"] is None:
            unresolved_frequency_window_count += 1
            volatility_excluded_window_count += 1
            sharpe_excluded_window_count += 1
            continue
        off_frequency_return_count += context_excluded_count
        if (
            len(returns) < MIN_RISK_RETURN_OBSERVATIONS
            or context["periods_per_year"] is None
        ):
            volatility_excluded_window_count += 1
            sharpe_excluded_window_count += 1
            continue
        periods_per_year = float(context["periods_per_year"])
        stdev = statistics.stdev(returns)
        as_of_date = points[end_index]["as_of_date"].isoformat()
        volatility.append(
            {
                "date": as_of_date,
                "value": stdev * math.sqrt(periods_per_year) * 100,
            }
        )
        if stdev != 0:
            sharpe.append(
                {
                    "date": as_of_date,
                    "value": statistics.fmean(returns)
                    / stdev
                    * math.sqrt(periods_per_year),
                }
            )
        else:
            zero_variance_window_count += 1
            sharpe_excluded_window_count += 1

    def _quality(
        series: list[dict[str, object]],
        *,
        excluded_window_count: int,
        zero_variance_count: int = 0,
    ) -> dict[str, object]:
        if series:
            reason = (
                "off_frequency_observations_excluded"
                if off_frequency_return_count
                else "observation_frequency_unresolved_windows_excluded"
                if unresolved_frequency_window_count
                else "zero_return_variance_windows_excluded"
                if zero_variance_count
                else "insufficient_history_windows_excluded"
                if excluded_window_count
                else None
            )
            return _metric_quality(
                "qualified" if reason is not None else "available",
                reason,
                observation_count=valid_return_count,
                excluded_observation_count=excluded_return_count,
                used_window_count=len(series),
                excluded_window_count=excluded_window_count,
            )

        reason = (
            "insufficient_history"
            if evaluated_window_count == 0
            else "observation_frequency_unresolved"
            if unresolved_frequency_window_count == evaluated_window_count
            else "zero_return_variance"
            if zero_variance_count == evaluated_window_count
            else "insufficient_same_frequency_history"
        )
        return _metric_quality(
            "unavailable",
            reason,
            observation_count=valid_return_count,
            excluded_observation_count=excluded_return_count,
            excluded_window_count=excluded_window_count,
        )

    volatility_quality = _quality(
        volatility,
        excluded_window_count=volatility_excluded_window_count,
    )
    sharpe_quality = _quality(
        sharpe,
        excluded_window_count=sharpe_excluded_window_count,
        zero_variance_count=zero_variance_window_count,
    )
    return {
        "annualized_volatility": volatility[-MAX_ROLLING_CHART_POINTS:],
        "sharpe_ratio": sharpe[-MAX_ROLLING_CHART_POINTS:],
        "quality": {
            "annualized_volatility": volatility_quality,
            "sharpe_ratio": sharpe_quality,
        },
    }


def _rolling_beta_series(
    nav_points: list[dict[str, Any]],
    benchmark_points: list[dict[str, Any]],
    window_months: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if window_months < 2:
        return [], _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=0,
        )

    fund_monthly = monthly_return_series(nav_points)
    benchmark_monthly = monthly_return_series(benchmark_points)
    fund_by_month = {
        _month_ordinal(point["as_of_date"]): point
        for point in fund_monthly
    }
    benchmark_by_month = {
        _month_ordinal(point["as_of_date"]): point
        for point in benchmark_monthly
    }
    if not fund_by_month or not benchmark_by_month:
        return [], _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=0,
        )

    first_eligible_month = max(min(fund_by_month), min(benchmark_by_month)) + window_months - 1
    beta: list[dict[str, object]] = []
    gap_excluded_count = 0
    alignment_excluded_count = 0
    zero_variance_count = 0
    for end_month in sorted(fund_by_month):
        if end_month < first_eligible_month:
            continue
        required_months = list(
            range(end_month - window_months + 1, end_month + 1)
        )
        if any(
            month not in fund_by_month or month not in benchmark_by_month
            for month in required_months
        ):
            gap_excluded_count += 1
            continue
        fund_window = [fund_by_month[month] for month in required_months]
        benchmark_window = [benchmark_by_month[month] for month in required_months]
        if any(
            fund_point["start_date"] != benchmark_point["start_date"]
            or fund_point["as_of_date"] != benchmark_point["as_of_date"]
            for fund_point, benchmark_point in zip(fund_window, benchmark_window)
        ):
            alignment_excluded_count += 1
            continue
        fund_returns = [float(point["value"]) / 100 for point in fund_window]
        benchmark_returns = [
            float(point["value"]) / 100 for point in benchmark_window
        ]
        benchmark_stdev = statistics.stdev(benchmark_returns)
        covariance = _sample_covariance(fund_returns, benchmark_returns)
        if covariance is None or benchmark_stdev == 0:
            zero_variance_count += 1
            continue
        beta.append(
            {
                "date": fund_window[-1]["as_of_date"].isoformat(),
                "value": covariance / (benchmark_stdev**2),
            }
        )
    excluded_count = (
        gap_excluded_count
        + alignment_excluded_count
        + zero_variance_count
    )
    reason = (
        "comparison_dates_misaligned"
        if alignment_excluded_count
        else "non_contiguous_monthly_returns"
        if gap_excluded_count
        else "zero_benchmark_variance"
        if zero_variance_count
        else None
    )
    if beta and excluded_count:
        quality = _metric_quality(
            "qualified",
            f"{reason}_excluded",
            observation_count=len(beta),
            excluded_observation_count=excluded_count,
        )
    elif beta:
        quality = _metric_quality(
            "available",
            None,
            observation_count=len(beta),
        )
    elif excluded_count:
        quality = _metric_quality(
            "unavailable",
            reason,
            observation_count=0,
            excluded_observation_count=excluded_count,
        )
    else:
        quality = _metric_quality(
            "unavailable",
            "insufficient_history",
            observation_count=0,
        )
    return beta[-MAX_ROLLING_CHART_POINTS:], quality


def _series_summary(points: list[dict[str, object]]) -> dict[str, float | None]:
    values = [float(point["value"]) for point in points]
    if not values:
        return {
            "latest": None,
            "median": None,
            "percentile": None,
            "maximum": None,
            "minimum": None,
        }
    latest = values[-1]
    return {
        "latest": latest,
        "median": statistics.median(values),
        "percentile": sum(value <= latest for value in values) / len(values) * 100,
        "maximum": max(values),
        "minimum": min(values),
    }


def build_investment_analytics_payload(
    nav_points: list[dict[str, Any]],
    *,
    benchmark_points: list[dict[str, Any]] | None,
    benchmark_instrument_id: str | None,
    rolling_window_months: int,
) -> dict[str, object]:
    if rolling_window_months not in INVESTMENT_ANALYTICS_ROLLING_WINDOWS:
        raise ValueError(
            f"Unsupported rolling window: {rolling_window_months}. "
            f"Expected one of {sorted(INVESTMENT_ANALYTICS_ROLLING_WINDOWS)}."
        )
    fund_input_count = len(nav_points)
    benchmark_input_count = len(benchmark_points or [])
    fund_points, fund_reason = _validate_nav_points(nav_points)
    benchmark, benchmark_reason = _validate_nav_points(benchmark_points or [])
    if benchmark_instrument_id is None:
        benchmark = []
        benchmark_reason = None
    reference_end_date = fund_points[-1]["as_of_date"] if fund_points else None
    periods: list[dict[str, object]] = []
    for period in INVESTMENT_ANALYTICS_PERIODS:
        fund_window = _analytics_window(
            fund_points,
            period,
            reference_end_date=reference_end_date,
        )
        fund_snapshot, fund_snapshot_quality = _performance_metric_snapshot(
            fund_window
        )
        benchmark_snapshot: dict[str, object] | None = None
        benchmark_snapshot_quality: dict[str, object] | None = None
        relative_snapshot: dict[str, float | None] | None = None

        if benchmark_instrument_id is None:
            relative_quality = _uniform_relative_quality(
                "not_requested",
                "benchmark_not_requested",
            )
        elif benchmark_reason is not None:
            benchmark_snapshot_quality = _unavailable_performance_quality(
                benchmark_reason,
                observation_count=0,
            )
            relative_quality = _uniform_relative_quality(
                "unavailable",
                benchmark_reason,
            )
        elif len(fund_window) < 2:
            benchmark_snapshot_quality = _unavailable_performance_quality(
                "insufficient_history",
                observation_count=0,
            )
            relative_quality = _uniform_relative_quality(
                "unavailable",
                "insufficient_history",
            )
        else:
            benchmark_window, alignment_reason = _exact_benchmark_window(
                fund_window,
                benchmark,
            )
            if alignment_reason is not None:
                benchmark_snapshot_quality = _unavailable_performance_quality(
                    alignment_reason,
                    observation_count=0,
                )
            else:
                benchmark_snapshot, benchmark_snapshot_quality = (
                    _performance_metric_snapshot(benchmark_window)
                )
                relative_snapshot = _relative_metric_snapshot(
                    fund_window,
                    benchmark_window,
                )
            relative_quality = _relative_metric_quality(
                fund_window,
                benchmark_window,
                alignment_reason=alignment_reason,
            )
        periods.append(
            {
                "period": period,
                "fund": fund_snapshot,
                "benchmark": benchmark_snapshot,
                "relative": relative_snapshot,
                "quality": {
                    "fund": fund_snapshot_quality,
                    "benchmark": benchmark_snapshot_quality,
                    "relative": relative_quality,
                },
            }
        )

    drawdown = _drawdown_series(fund_points)
    benchmark_drawdown = _drawdown_series(benchmark)
    monthly_drawdown = _monthly_minimum_series(drawdown)
    fund_rolling = _rolling_risk_series(fund_points, rolling_window_months)
    if benchmark_instrument_id is None:
        benchmark_rolling_quality = _metric_quality(
            "not_requested",
            "benchmark_not_requested",
            observation_count=0,
        )
        benchmark_rolling = {
            "annualized_volatility": [],
            "sharpe_ratio": [],
            "quality": {
                "annualized_volatility": benchmark_rolling_quality,
                "sharpe_ratio": benchmark_rolling_quality.copy(),
            },
        }
    elif benchmark_reason is not None:
        benchmark_rolling_quality = _metric_quality(
            "unavailable",
            benchmark_reason,
            observation_count=0,
        )
        benchmark_rolling = {
            "annualized_volatility": [],
            "sharpe_ratio": [],
            "quality": {
                "annualized_volatility": benchmark_rolling_quality,
                "sharpe_ratio": benchmark_rolling_quality.copy(),
            },
        }
    else:
        benchmark_rolling = _rolling_risk_series(
            benchmark,
            rolling_window_months,
        )
    if benchmark_instrument_id is None:
        rolling_beta = []
        rolling_beta_quality = _metric_quality(
            "not_requested",
            "benchmark_not_requested",
            observation_count=0,
        )
    elif benchmark_reason is not None:
        rolling_beta = []
        rolling_beta_quality = _metric_quality(
            "unavailable",
            benchmark_reason,
            observation_count=0,
        )
    else:
        rolling_beta, rolling_beta_quality = _rolling_beta_series(
            fund_points,
            benchmark,
            rolling_window_months,
        )
    monthly_volatility, monthly_volatility_quality = (
        _monthly_annualized_volatility_series(fund_points)
    )
    monthly_returns = monthly_return_series(fund_points)
    return {
        "methodology_version": "canonical-investment-analytics/v2",
        "methodology": ANALYTICS_METHODOLOGY,
        "as_of_date": reference_end_date.isoformat() if reference_end_date else None,
        "benchmark_instrument_id": benchmark_instrument_id,
        "rolling_window_months": rolling_window_months,
        "periods": periods,
        "monthly_return_matrix": _monthly_return_matrix(fund_points),
        "series": {
            "drawdown": drawdown,
            "benchmark_drawdown": benchmark_drawdown,
            "monthly_drawdown": monthly_drawdown[-36:],
            "monthly_annualized_volatility": monthly_volatility[-36:],
            "rolling_annualized_volatility": fund_rolling["annualized_volatility"],
            "benchmark_rolling_annualized_volatility": benchmark_rolling[
                "annualized_volatility"
            ],
            "rolling_sharpe_ratio": fund_rolling["sharpe_ratio"],
            "benchmark_rolling_sharpe_ratio": benchmark_rolling["sharpe_ratio"],
            "rolling_beta": rolling_beta,
        },
        "statistics": {
            "current_drawdown": (
                float(drawdown[-1]["value"])
                if len(fund_points) >= 2 and drawdown
                else None
            ),
            "monthly_return": _series_summary(
                [
                    {
                        "date": point["as_of_date"].isoformat(),
                        "value": point["value"],
                    }
                    for point in monthly_returns
                ]
            ),
            "monthly_drawdown": _series_summary(monthly_drawdown),
            "rolling_annualized_volatility": _series_summary(
                fund_rolling["annualized_volatility"]
            ),
            "rolling_beta": _series_summary(rolling_beta),
            "trailing_negative_month_count": (
                trailing_negative_month_count(monthly_returns)
                if monthly_returns
                else None
            ),
        },
        "source_observation_count": len(fund_points),
        "benchmark_observation_count": len(benchmark),
        "source_input_observation_count": fund_input_count,
        "benchmark_input_observation_count": benchmark_input_count,
        "quality": {
            "fund_status": "available" if fund_reason is None else "unavailable",
            "fund_reason": fund_reason,
            "benchmark_status": (
                "not_requested"
                if benchmark_instrument_id is None
                else "available"
                if benchmark_reason is None
                else "unavailable"
            ),
            "benchmark_reason": benchmark_reason,
            "series": {
                "monthly_annualized_volatility": monthly_volatility_quality,
                "rolling_annualized_volatility": fund_rolling["quality"][
                    "annualized_volatility"
                ],
                "benchmark_rolling_annualized_volatility": benchmark_rolling[
                    "quality"
                ]["annualized_volatility"],
                "rolling_sharpe_ratio": fund_rolling["quality"]["sharpe_ratio"],
                "benchmark_rolling_sharpe_ratio": benchmark_rolling["quality"][
                    "sharpe_ratio"
                ],
                "rolling_beta": rolling_beta_quality,
            },
        },
    }
