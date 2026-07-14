from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import sqrt

import numpy as np
import pandas as pd

from portfolio_app.services.allocation_research import (
    _allocation_instrument_ids,
    _build_taxonomy_state,
    _portfolio_market_data_manifest_from_state,
    _solve_current_target_weights_from_state,
    _state_with_locked_portfolio_market_data,
    _top_sleeve_for_member,
)
from portfolio_app.services.allocation_solver import (
    AllocationResearchState,
    TARGET_MEMBER_INSTRUMENT,
    _safe_float,
)
from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.services.performance_reliability import build_performance_history_reliability
from portfolio_app.services.portfolio_market_data import (
    PortfolioMarketDataError,
    _build_instrument_nav_series,
    _parse_iso_date,
)
from portfolio_app.services.risk_math import (
    COVARIANCE_FREQUENCY_PARAMETERS,
    DEFAULT_MISSING_RETURN_POLICY,
    _infer_periods_per_year,
    _periodic_nav_series,
    _risk_window_months,
)


POLICY_REPLAY_METHODOLOGY_WARNINGS: tuple[str, ...] = (
    "Policy Replay applies the currently configured taxonomy membership and target policy across the full historical simulation; it is not a point-in-time reconstruction of past classifications or mandates.",
    "Policy Replay cash residual earns a 0% return, and simulated returns exclude transaction costs, taxes, slippage, and implementation delay.",
)


POLICY_REPLAY_METRICS_METHOD_VERSION = (
    "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
)


def _normalize_policy_replay_rebalance_frequency(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"1w", "1m", "3m"} else "1m"


def _rebalance_schedule(*, start_date: date, end_date: date, frequency: str) -> list[date]:
    normalized_frequency = _normalize_policy_replay_rebalance_frequency(frequency)
    if normalized_frequency == "1w":
        dates: list[date] = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current = current + timedelta(days=7)
        return dates

    month_step = 3 if normalized_frequency == "3m" else 1
    first_month = date(start_date.year, start_date.month, 1)
    if first_month < start_date:
        first_month = (pd.Timestamp(first_month) + pd.DateOffset(months=1)).date()
    dates: list[date] = []
    current = first_month
    while current <= end_date:
        dates.append(current)
        current = (pd.Timestamp(current) + pd.DateOffset(months=month_step)).date()
    if not dates and start_date <= end_date:
        return [start_date]
    return dates


def _nav_returns(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    cleaned = series.sort_index().astype("float64")
    return cleaned.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _compound_return(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return total - 1.0


def _build_policy_replay_metrics(points: list[dict[str, object]], returns: dict[str, float]) -> dict[str, object]:
    dates: list[date] = []
    for item in points:
        parsed_date = _parse_iso_date(item.get("date"))
        if parsed_date is not None:
            dates.append(parsed_date)
    raw_history_reliability = build_performance_history_reliability(
        {
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "snapshot_count": len(dates),
            "return_observation_count": len(returns),
            "risk_return_observation_count": len(returns),
        }
    )
    history_reliability = {
        **raw_history_reliability,
        "start_date": (
            raw_history_reliability["start_date"].isoformat()
            if isinstance(raw_history_reliability.get("start_date"), date)
            else None
        ),
        "end_date": (
            raw_history_reliability["end_date"].isoformat()
            if isinstance(raw_history_reliability.get("end_date"), date)
            else None
        ),
    }
    if len(points) < 2 or not returns:
        return {
            "method_version": POLICY_REPLAY_METRICS_METHOD_VERSION,
            "history_reliability": history_reliability,
            "start_date": points[0]["date"] if points else None,
            "end_date": points[-1]["date"] if points else None,
            "period_return": None,
            "ytd_return": None,
            "annualized_return": None,
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "max_drawdown_start_date": None,
            "max_drawdown_end_date": None,
            "max_drawdown_days": None,
            "max_drawdown_recovery_date": None,
            "max_drawdown_recovery_days": None,
            "current_drawdown": None,
            "calmar_ratio": None,
        }
    start_value = _safe_float(points[0].get("value")) or 1.0
    end_value = _safe_float(points[-1].get("value")) or start_value
    return_dates = [_parse_iso_date(date_key) for date_key in returns.keys()]
    periods_per_year = _infer_periods_per_year([item for item in return_dates if item is not None])
    period_count = max(len(returns), 1)
    period_return = end_value / start_value - 1.0 if abs(start_value) > 1e-12 else None
    end_date = dates[-1] if dates else None
    ytd_return_values = [
        float(value)
        for date_key, value in sorted(returns.items())
        if (parsed_date := _parse_iso_date(date_key)) is not None
        and end_date is not None
        and parsed_date.year == end_date.year
    ]
    ytd_return = _compound_return(ytd_return_values)
    geometric_annualized_return = (
        (end_value / start_value) ** (periods_per_year / period_count) - 1.0
        if period_return is not None and end_value > 0 and start_value > 0
        else None
    )
    annualized_return = (
        geometric_annualized_return
        if history_reliability["annualized_return_eligible"]
        else None
    )
    return_values = np.asarray(list(returns.values()), dtype="float64")
    annualized_volatility = (
        float(np.nanstd(return_values, ddof=1) * sqrt(periods_per_year)) if len(return_values) > 1 else None
    )
    arithmetic_annualized_return = (
        float(np.mean(return_values) * periods_per_year)
        if len(return_values) > 0 and np.all(np.isfinite(return_values))
        else None
    )
    sharpe_ratio = (
        float(arithmetic_annualized_return / annualized_volatility)
        if arithmetic_annualized_return is not None
        and annualized_volatility is not None
        and annualized_volatility > 1e-12
        else None
    )

    high_value = -np.inf
    high_date: date | None = None
    max_drawdown = 0.0
    max_start: date | None = None
    max_end: date | None = None
    recovery_date: date | None = None
    target_recovery_value: float | None = None
    for point in points:
        point_date = _parse_iso_date(point.get("date"))
        point_value = _safe_float(point.get("value"))
        if point_date is None or point_value is None:
            continue
        if point_value > high_value:
            high_value = point_value
            high_date = point_date
        drawdown = point_value / high_value - 1.0 if high_value > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_start = high_date
            max_end = point_date
            recovery_date = None
            target_recovery_value = high_value
        if recovery_date is None and max_end is not None and point_date > max_end and target_recovery_value is not None:
            if point_value >= target_recovery_value:
                recovery_date = point_date
    max_drawdown_days = (max_end - max_start).days if max_start is not None and max_end is not None else None
    recovery_days = (recovery_date - max_end).days if recovery_date is not None and max_end is not None else None
    current_drawdown = end_value / high_value - 1.0 if high_value > 0 and end_value is not None else None
    calmar_ratio = (
        float(annualized_return / abs(max_drawdown))
        if annualized_return is not None and max_drawdown is not None and max_drawdown < -1e-12
        else None
    )
    return {
        "method_version": POLICY_REPLAY_METRICS_METHOD_VERSION,
        "history_reliability": history_reliability,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "period_return": period_return,
        "ytd_return": ytd_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "max_drawdown_start_date": max_start.isoformat() if max_start else None,
        "max_drawdown_end_date": max_end.isoformat() if max_end else None,
        "max_drawdown_days": max_drawdown_days,
        "max_drawdown_recovery_date": recovery_date.isoformat() if recovery_date else None,
        "max_drawdown_recovery_days": recovery_days,
        "current_drawdown": current_drawdown,
        "calmar_ratio": calmar_ratio,
    }


def _is_rebalance_data_gap_error(error: ValueError) -> bool:
    message = str(error)
    return (
        "requires at least" in message
        or "return observations" in message
        or "complete aligned return observations" in message
    )


def _instrument_label(state: AllocationResearchState, instrument_id: str) -> str:
    if state.market_data is not None:
        return state.market_data.instrument_name_by_id.get(instrument_id, instrument_id)
    return instrument_id


def _normalized_policy_replay_points(points: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized_points: list[dict[str, object]] = []
    for point in points:
        point_date = _parse_iso_date(point.get("date"))
        point_value = _safe_float(point.get("value"))
        if point_date is None or point_value is None:
            continue
        normalized_points.append({"date": point_date.isoformat(), "value": float(point_value)})
    normalized_points.sort(key=lambda item: str(item.get("date") or ""))
    return normalized_points


def _policy_replay_return_map_from_points(points: list[dict[str, object]]) -> dict[str, float]:
    normalized_points = _normalized_policy_replay_points(points)
    if len(normalized_points) < 2:
        return {}
    returns: dict[str, float] = {}
    previous_value = _safe_float(normalized_points[0].get("value"))
    for point in normalized_points[1:]:
        point_value = _safe_float(point.get("value"))
        date_key = str(point.get("date") or "")
        if previous_value is None or point_value is None or previous_value <= 0.0:
            previous_value = point_value
            continue
        returns[date_key] = point_value / previous_value - 1.0
        previous_value = point_value
    return returns


def _resolved_policy_replay_calculation_frequency(solution: dict[str, object], requested_frequency: object) -> CalculationFrequency:
    profile = solution.get("calculation_frequency")
    if isinstance(profile, dict):
        resolved = str(profile.get("resolved_frequency") or "").strip().lower()
        if resolved in COVARIANCE_FREQUENCY_PARAMETERS:
            return resolved  # type: ignore[return-value]
    requested = str(requested_frequency or "").strip().lower()
    if requested in COVARIANCE_FREQUENCY_PARAMETERS:
        return requested  # type: ignore[return-value]
    return "daily"


def _build_policy_replay_sampled_nav_by_instrument(
    nav_by_instrument: dict[str, pd.Series],
    *,
    calculation_frequency: CalculationFrequency,
    end_date: date,
) -> dict[str, pd.Series]:
    sampled: dict[str, pd.Series] = {}
    for instrument_id, series in nav_by_instrument.items():
        sampled_series = _periodic_nav_series(
            series,
            calculation_frequency=calculation_frequency,
            start_date=date(1900, 1, 1),
            end_date=end_date,
        )
        if not sampled_series.empty:
            sampled[instrument_id] = sampled_series
    return sampled


def _common_return_dates(returns_by_instrument: dict[str, pd.Series]) -> list[date]:
    return_sets = [set(series.index.tolist()) for series in returns_by_instrument.values() if not series.empty]
    if not return_sets:
        return []
    common_dates = set.intersection(*return_sets)
    return sorted(item for item in common_dates if isinstance(item, date))


def _policy_replay_rebalance_dates(
    *,
    start_date: date,
    end_date: date,
    frequency: str,
    calculation_frequency: CalculationFrequency,
    returns_by_instrument: dict[str, pd.Series],
) -> list[date]:
    if frequency == "1w" and calculation_frequency != "daily":
        return [item for item in _common_return_dates(returns_by_instrument) if start_date <= item <= end_date]
    return _rebalance_schedule(start_date=start_date, end_date=end_date, frequency=frequency)


def _latest_series_value_on_or_before(series: pd.Series, point_date: date) -> float | None:
    if series.empty:
        return None
    eligible = series.loc[series.index <= point_date]
    if eligible.empty:
        return None
    value = _safe_float(eligible.iloc[-1])
    if value is None or value <= 0.0:
        return None
    return float(value)


def _build_sampled_benchmark_points(
    benchmark_nav: pd.Series,
    normalized_points: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, float]]:
    benchmark_points: list[dict[str, object]] = []
    benchmark_return_map: dict[str, float] = {}
    if not normalized_points or benchmark_nav.empty:
        return benchmark_points, benchmark_return_map

    benchmark_nav_value = 1.0
    previous_benchmark_value: float | None = None
    for point in normalized_points:
        date_key = str(point.get("date") or "")
        point_date = _parse_iso_date(date_key)
        if point_date is None:
            continue
        benchmark_value = _latest_series_value_on_or_before(benchmark_nav, point_date)
        if benchmark_value is None:
            continue
        if previous_benchmark_value is None:
            benchmark_points.append({"date": date_key, "value": benchmark_nav_value})
            previous_benchmark_value = benchmark_value
            continue
        benchmark_return = benchmark_value / previous_benchmark_value - 1.0
        benchmark_return_map[date_key] = benchmark_return
        benchmark_nav_value *= 1.0 + benchmark_return
        benchmark_points.append({"date": date_key, "value": benchmark_nav_value})
        previous_benchmark_value = benchmark_value

    return benchmark_points, benchmark_return_map


def _build_policy_replay_benchmark_comparison_from_state(
    state: AllocationResearchState,
    *,
    benchmark_instrument_id: str | None,
    portfolio_points: list[dict[str, object]],
    portfolio_returns: dict[str, float] | None = None,
) -> dict[str, object]:
    normalized_benchmark_id = str(benchmark_instrument_id or "").strip()
    if not normalized_benchmark_id:
        return {"policy_replay_benchmark": None, "policy_replay_relative_metrics": None}

    normalized_points = _normalized_policy_replay_points(portfolio_points)
    portfolio_return_map = portfolio_returns or _policy_replay_return_map_from_points(normalized_points)
    benchmark_label = _instrument_label(state, normalized_benchmark_id)
    benchmark_warnings: list[str] = []
    try:
        benchmark_nav, benchmark_warnings = _build_instrument_nav_series(
            state,
            instrument_id=normalized_benchmark_id,
            start_date=date(1900, 1, 1),
            end_date=state.as_of_date,
            warn_on_start_clip=False,
        )
    except PortfolioMarketDataError:
        raise
    except ValueError as error:
        benchmark_warnings.append(str(error))
        benchmark_nav = pd.Series(dtype="float64")

    benchmark_points, benchmark_return_map = _build_sampled_benchmark_points(benchmark_nav, normalized_points)

    benchmark_payload = {
        "instrument_id": normalized_benchmark_id,
        "label": benchmark_label or normalized_benchmark_id,
        "points": benchmark_points,
        "metrics": _build_policy_replay_metrics(benchmark_points, benchmark_return_map),
        "warnings": benchmark_warnings,
    }

    relative_metrics = None
    common_return_dates = sorted(set(portfolio_return_map).intersection(benchmark_return_map))
    if common_return_dates:
        active_points: list[dict[str, object]] = []
        active_return_map: dict[str, float] = {}
        active_nav_value = 1.0
        if normalized_points:
            active_points.append({"date": normalized_points[0]["date"], "value": active_nav_value})
        for date_key in common_return_dates:
            active_return = float(portfolio_return_map[date_key]) - float(benchmark_return_map[date_key])
            active_return_map[date_key] = active_return
            active_nav_value *= 1.0 + active_return
            active_points.append({"date": date_key, "value": active_nav_value})
        active_metrics = _build_policy_replay_metrics(active_points, active_return_map)
        portfolio_common = [float(portfolio_return_map[date_key]) for date_key in common_return_dates]
        benchmark_common = [float(benchmark_return_map[date_key]) for date_key in common_return_dates]
        portfolio_period_return = _compound_return(portfolio_common)
        benchmark_period_return = _compound_return(benchmark_common)
        tracking_error = _safe_float(active_metrics.get("annualized_volatility"))
        parsed_dates = [_parse_iso_date(item) for item in common_return_dates]
        periods_per_year = _infer_periods_per_year([item for item in parsed_dates if item is not None])
        information_ratio = (
            float((np.mean(list(active_return_map.values())) * periods_per_year) / tracking_error)
            if tracking_error is not None and tracking_error > 1e-12
            else None
        )
        relative_metrics = {
            **active_metrics,
            "excess_return": (
                portfolio_period_return - benchmark_period_return
                if portfolio_period_return is not None and benchmark_period_return is not None
                else None
            ),
            "tracking_error": tracking_error,
            "information_ratio": information_ratio,
        }

    return {"policy_replay_benchmark": benchmark_payload, "policy_replay_relative_metrics": relative_metrics}


def build_allocation_research_policy_replay_benchmark_comparison(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    as_of_date: date,
    benchmark_instrument_id: str,
    portfolio_points: list[dict[str, object]],
) -> dict[str, object]:
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    state = _state_with_locked_portfolio_market_data(
        state,
        instrument_ids=[benchmark_instrument_id],
        start_date=date(1900, 1, 1),
        end_date=as_of_date,
    )
    return _build_policy_replay_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=portfolio_points,
    )


def _active_policy_replay_leaf_ids(solution: dict[str, object]) -> list[str]:
    instrument_ids = [
        str(row.get("member_id") or "")
        for row in list(solution.get("leaf_targets") or [])
        if str(row.get("member_type") or "") == TARGET_MEMBER_INSTRUMENT
        and abs(float(_safe_float(row.get("target_weight")) or 0.0)) > 1e-12
    ]
    return list(dict.fromkeys(item for item in instrument_ids if item))


def build_current_target_policy_replay(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: str = "auto",
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    risk_model_config: dict[str, object] | None = None,
    rebalance_frequency: str = "1m",
    benchmark_instrument_id: str | None = None,
    current_solution: dict[str, object] | None = None,
) -> dict[str, object]:
    frequency = _normalize_policy_replay_rebalance_frequency(rebalance_frequency)
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
        top_sleeve_weight_bounds=top_sleeve_weight_bounds,
    )
    state = _state_with_locked_portfolio_market_data(
        state,
        instrument_ids=_allocation_instrument_ids(
            state,
            scope_node_id=comparator_taxonomy_node_id,
            additional_instrument_ids=[benchmark_instrument_id or ""],
        ),
        start_date=date(1900, 1, 1),
        end_date=as_of_date,
    )
    solution = current_solution or _solve_current_target_weights_from_state(
        state,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
        missing_return_policy=missing_return_policy,
        risk_model_config=risk_model_config,
    )
    active_leaf_ids = _active_policy_replay_leaf_ids(solution)
    warnings: list[str] = list(POLICY_REPLAY_METHODOLOGY_WARNINGS)
    if not active_leaf_ids:
        empty_policy_replay = {
            "rebalance_frequency": frequency,
            "common_history_start_date": None,
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_policy_replay_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        "Policy Replay requires at least one solved instrument.",
                    ]
                )
            ),
        }
        return {
            "policy_replay": empty_policy_replay,
            "policy_replay_benchmark": None,
            "policy_replay_relative_metrics": None,
            "market_data_dependencies": _portfolio_market_data_manifest_from_state(state),
        }

    nav_by_instrument: dict[str, pd.Series] = {}
    for instrument_id in active_leaf_ids:
        try:
            nav_series, instrument_warnings = _build_instrument_nav_series(
                state,
                instrument_id=instrument_id,
                start_date=date(1900, 1, 1),
                end_date=as_of_date,
                warn_on_start_clip=False,
            )
        except PortfolioMarketDataError:
            raise
        except ValueError as error:
            warnings.append(f"{instrument_id} excluded from Policy Replay: {error}")
            continue
        if nav_series.empty:
            continue
        nav_by_instrument[instrument_id] = nav_series
        warnings.extend(instrument_warnings)

    policy_replay_calculation_frequency = _resolved_policy_replay_calculation_frequency(solution, calculation_frequency)
    sampled_nav_by_instrument = _build_policy_replay_sampled_nav_by_instrument(
        nav_by_instrument,
        calculation_frequency=policy_replay_calculation_frequency,
        end_date=as_of_date,
    )
    portfolio_first_dates = [series.index[0] for series in sampled_nav_by_instrument.values() if not series.empty]

    if not sampled_nav_by_instrument or not portfolio_first_dates:
        empty_policy_replay = {
            "rebalance_frequency": frequency,
            "common_history_start_date": None,
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_policy_replay_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(dict.fromkeys(warnings or ["Policy Replay has no usable instrument history."])),
        }
        return {
            "policy_replay": empty_policy_replay,
            "policy_replay_benchmark": None,
            "policy_replay_relative_metrics": None,
            "market_data_dependencies": _portfolio_market_data_manifest_from_state(state),
        }

    common_start = max(portfolio_first_dates)
    earliest_start_date = (
        pd.Timestamp(common_start) + pd.DateOffset(months=_risk_window_months(lookback_days))
    ).date()
    if earliest_start_date > as_of_date:
        empty_policy_replay = {
            "rebalance_frequency": frequency,
            "common_history_start_date": common_start.isoformat(),
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_policy_replay_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        "Policy Replay requires portfolio member common history at least as long as the selected risk window.",
                    ]
                )
            ),
        }
        return {
            "policy_replay": empty_policy_replay,
            "policy_replay_benchmark": None,
            "policy_replay_relative_metrics": None,
            "market_data_dependencies": _portfolio_market_data_manifest_from_state(state),
        }
    returns_by_instrument = {instrument_id: _nav_returns(nav) for instrument_id, nav in sampled_nav_by_instrument.items()}
    rebal_dates = _policy_replay_rebalance_dates(
        start_date=earliest_start_date,
        end_date=as_of_date,
        frequency=frequency,
        calculation_frequency=policy_replay_calculation_frequency,
        returns_by_instrument=returns_by_instrument,
    )
    if not rebal_dates:
        rebal_dates = [earliest_start_date]

    portfolio_nav = 1.0
    points: list[dict[str, object]] = []
    portfolio_returns: dict[str, float] = {}
    contribution_accumulator: dict[str, float] = defaultdict(float)
    weight_points: list[dict[str, object]] = []
    contribution_points: list[dict[str, object]] = []
    last_period_weights: dict[str, float] | None = None
    last_top_by_instrument: dict[str, tuple[str | None, str]] = {}

    def sleeve_weight_point(
        date_key: str,
        weights_by_instrument: dict[str, float],
        top_lookup: dict[str, tuple[str | None, str]],
    ) -> dict[str, object]:
        top_weight_by_key: dict[str, dict[str, object]] = {}
        for instrument_id, weight in weights_by_instrument.items():
            if abs(weight) <= 1e-12:
                continue
            top_id, top_label = top_lookup.get(instrument_id, (None, "Unassigned"))
            top_key = top_id or "__unassigned__"
            sleeve = top_weight_by_key.setdefault(
                top_key,
                {"top_sleeve_id": top_id, "top_sleeve_label": top_label, "value": 0.0},
            )
            sleeve["value"] = float(sleeve["value"] or 0.0) + float(weight)
        return {
            "date": date_key,
            "sleeves": sorted(
                top_weight_by_key.values(),
                key=lambda item: abs(_safe_float(item.get("value")) or 0.0),
                reverse=True,
            ),
        }

    for index, rebalance_date in enumerate(rebal_dates):
        period_end = rebal_dates[index + 1] if index + 1 < len(rebal_dates) else as_of_date
        try:
            period_solution = _solve_current_target_weights_from_state(
                replace(
                    state,
                    as_of_date=rebalance_date,
                    current_valuation_cache={},
                ),
                comparator_taxonomy_node_id=comparator_taxonomy_node_id,
                as_of_date=rebalance_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
                target_dimension=target_dimension,
                capital_mode=capital_mode,
                gross_exposure=gross_exposure,
                target_volatility=target_volatility,
                max_gross_exposure=max_gross_exposure,
                missing_return_policy=missing_return_policy,
                risk_model_config=risk_model_config,
                include_actuals=False,
            )
        except PortfolioMarketDataError:
            raise
        except ValueError as error:
            if not _is_rebalance_data_gap_error(error):
                raise ValueError(f"{rebalance_date.isoformat()} rebalance failed: {error}") from error
            if not points:
                warnings.append(
                    f"{rebalance_date.isoformat()} rebalance skipped during Policy Replay warm-up: {error}"
                )
                continue
            if last_period_weights is None:
                raise ValueError(f"{rebalance_date.isoformat()} rebalance failed: {error}") from error
            warnings.append(
                f"{rebalance_date.isoformat()} rebalance skipped; previous weights carried forward: {error}"
            )
            period_weights = dict(last_period_weights)
            top_by_instrument = dict(last_top_by_instrument)
            weight_points.append(
                sleeve_weight_point(rebalance_date.isoformat(), period_weights, top_by_instrument)
            )
        else:
            if not points:
                points.append({"date": rebalance_date.isoformat(), "value": portfolio_nav})

            leaf_weights = {
                str(row.get("member_id") or ""): _safe_float(row.get("target_weight")) or 0.0
                for row in list(period_solution.get("leaf_targets") or [])
                if str(row.get("member_type") or "") == TARGET_MEMBER_INSTRUMENT
            }
            top_by_instrument = {}
            for row in list(period_solution.get("leaf_targets") or []):
                if str(row.get("member_type") or "") != TARGET_MEMBER_INSTRUMENT:
                    continue
                instrument_id = str(row.get("member_id") or "")
                top_id, top_label, _path = _top_sleeve_for_member(
                    state,
                    member_type=TARGET_MEMBER_INSTRUMENT,
                    member_id=instrument_id,
                )
                top_by_instrument[instrument_id] = (top_id, top_label)
            period_weights = {
                instrument_id: float(weight)
                for instrument_id, weight in leaf_weights.items()
                if instrument_id in returns_by_instrument
            }
            weight_points.append(
                sleeve_weight_point(rebalance_date.isoformat(), period_weights, top_by_instrument)
            )

        active_instruments = [
            instrument_id
            for instrument_id, weight in period_weights.items()
            if abs(weight) > 1e-12 and instrument_id in returns_by_instrument
        ]
        candidate_dates = sorted(
            {
                return_date
                for instrument_id in active_instruments
                for return_date in returns_by_instrument.get(instrument_id, pd.Series(dtype="float64")).index
                if rebalance_date < return_date <= period_end
            }
        )
        for return_date in candidate_dates:
            if any(return_date not in returns_by_instrument[instrument_id].index for instrument_id in active_instruments):
                continue
            sleeve_contribution: dict[str, dict[str, object]] = {}
            portfolio_return = 0.0
            for instrument_id in active_instruments:
                instrument_return = _safe_float(returns_by_instrument[instrument_id].get(return_date))
                if instrument_return is None:
                    portfolio_return = np.nan
                    break
                weighted_return = float(period_weights[instrument_id]) * instrument_return
                portfolio_return += weighted_return
                top_id, top_label = top_by_instrument.get(instrument_id, (None, "Unassigned"))
                top_key = top_id or "__unassigned__"
                sleeve = sleeve_contribution.setdefault(
                    top_key,
                    {"top_sleeve_id": top_id, "top_sleeve_label": top_label, "value": 0.0},
                )
                sleeve["value"] = float(sleeve["value"] or 0.0) + weighted_return
            if not np.isfinite(portfolio_return):
                continue
            date_key = return_date.isoformat()
            portfolio_returns[date_key] = float(portfolio_return)
            period_growth = 1.0 + float(portfolio_return)
            if period_growth <= 0.0:
                raise ValueError(
                    f"{return_date.isoformat()} Policy Replay portfolio NAV became "
                    "non-positive."
                )
            for instrument_id in active_instruments:
                instrument_return = _safe_float(returns_by_instrument[instrument_id].get(return_date)) or 0.0
                period_weights[instrument_id] = (
                    float(period_weights[instrument_id]) * (1.0 + instrument_return) / period_growth
                )
            portfolio_nav *= period_growth
            points.append({"date": date_key, "value": portfolio_nav})
            weight_points.append(sleeve_weight_point(date_key, period_weights, top_by_instrument))
            sleeves_for_date = []
            for top_key, sleeve in sleeve_contribution.items():
                contribution_accumulator[top_key] += float(sleeve.get("value") or 0.0)
                sleeves_for_date.append(
                    {
                        "top_sleeve_id": sleeve.get("top_sleeve_id"),
                        "top_sleeve_label": sleeve.get("top_sleeve_label"),
                        "value": contribution_accumulator[top_key],
                    }
                )
            contribution_points.append({"date": date_key, "sleeves": sleeves_for_date})
        last_period_weights = dict(period_weights)
        last_top_by_instrument = dict(top_by_instrument)

    comparison_payload = _build_policy_replay_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=points,
        portfolio_returns=portfolio_returns,
    )

    policy_replay = {
        "rebalance_frequency": frequency,
        "common_history_start_date": common_start.isoformat(),
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else as_of_date.isoformat(),
        "lookback_days": lookback_days,
        "points": points,
        "metrics": _build_policy_replay_metrics(points, portfolio_returns),
        "top_sleeve_weight_points": weight_points,
        "top_sleeve_contribution_points": contribution_points,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return {
        "policy_replay": policy_replay,
        "policy_replay_benchmark": comparison_payload.get("policy_replay_benchmark"),
        "policy_replay_relative_metrics": comparison_payload.get("policy_replay_relative_metrics"),
        "market_data_dependencies": _portfolio_market_data_manifest_from_state(state),
    }
