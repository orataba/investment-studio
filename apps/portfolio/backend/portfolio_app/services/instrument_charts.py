from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from math import sqrt

from portfolio_app.services.calculation_frequency import CalculationFrequency, period_end_date
from portfolio_ops_instrument_core import resolve_quote_return_semantics
from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import (
    analytical_return_quote_bases,
    resolve_quote_series,
)

SUPPORTED_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "ytd", "1y", "all")
HOLDINGS_PRICE_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "1y")
ASSET_RISK_WINDOW_MONTHS: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "6m": 6,
    "1y": 12,
}
ASSET_RISK_MIN_RETURN_OBSERVATIONS: dict[CalculationFrequency, dict[str, int]] = {
    "daily": {
        "1m": 10,
        "3m": 30,
        "6m": 60,
        "1y": 120,
    },
    "weekly": {
        "1m": 3,
        "3m": 6,
        "6m": 12,
        "1y": 24,
    },
    "monthly": {
        "1m": 2,
        "3m": 2,
        "6m": 4,
        "1y": 6,
    },
}
ASSET_RISK_MAX_START_GAP_DAYS: dict[CalculationFrequency, int] = {
    "daily": 10,
    "weekly": 21,
    "monthly": 45,
}
ASSET_RISK_MAX_TRAILING_STALENESS_DAYS: dict[CalculationFrequency, int] = {
    "daily": 5,
    "weekly": 14,
    "monthly": 62,
}
ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO = 0.8
DAYS_PER_YEAR = 365.25
RAW_SPLIT_SENSITIVE_BASES = frozenset({"close", "last"})
TREND_RETURN_WINDOWS: tuple[str, ...] = (
    "1w",
    "1m",
    "3m",
    "6m",
    "mtd",
    "ytd",
    "1y",
)
SUPPORTED_SPLIT_FRACTION_TREATMENTS = frozenset({"exact", "truncate", "round_half_up"})


@dataclass(frozen=True)
class ChartSeriesSelection:
    points: tuple[dict[str, object], ...] = ()
    selected_basis: str | None = None
    coverage: dict[str, object] | None = None
    reason: str = "quote_series_unavailable"
    split_adjusted: bool = False

    @property
    def available(self) -> bool:
        return bool(self.points)


def chart_return_semantics(
    detail: dict[str, object],
    *,
    selected_basis: str | None,
) -> str:
    """Resolve return semantics independently from the provider's field name.

    A total-return index is commonly delivered as a ``close`` series.  The
    explicit index source contract may therefore confirm that close is a total
    return, while raw close for an ordinary price index remains price return.
    """

    source_settings = detail.get("source_settings")
    return resolve_quote_return_semantics(
        instrument_type=detail.get("instrument_type"),
        quote_basis=selected_basis,
        source_settings=source_settings if isinstance(source_settings, dict) else None,
    )


def _empty_trend_coverage() -> dict[str, object]:
    return {
        "state": "unavailable",
        "observation_count": 0,
        "start_date": None,
        "end_date": None,
        "available_return_windows": [],
    }


def empty_instrument_trend_metrics(
    *,
    selected_basis: str | None = None,
    coverage: dict[str, object] | None = None,
    reason: str = "quote_series_unavailable",
    split_adjusted: bool = False,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    return {
        "instrument_trend_as_of_date": None,
        "instrument_trend_basis": selected_basis,
        "instrument_trend_coverage": coverage or _empty_trend_coverage(),
        "instrument_trend_reason": reason,
        "instrument_trend_split_adjusted": split_adjusted,
        "instrument_risk_frequency": calculation_frequency,
        "instrument_return_1w": None,
        "instrument_return_1m": None,
        "instrument_return_3m": None,
        "instrument_return_6m": None,
        "instrument_return_mtd": None,
        "instrument_return_ytd": None,
        "instrument_return_1y": None,
        "instrument_volatility_1m": None,
        "instrument_volatility_3m": None,
        "instrument_volatility_6m": None,
        "instrument_volatility_1y": None,
        "instrument_return_series_1m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_3m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_6m": {"first_return_start_date": None, "points": []},
        "instrument_return_series_1y": {"first_return_start_date": None, "points": []},
        "instrument_return_series_all": {"first_return_start_date": None, "points": []},
        "instrument_holding_return_series": {"first_return_start_date": None, "points": []},
        "instrument_current_drawdown": None,
        "instrument_max_drawdown": None,
        "instrument_holding_max_drawdown": None,
        "instrument_holding_start_date": holding_start_date.isoformat() if holding_start_date else None,
    }


def empty_instrument_holdings_market_profile(
    *,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    return {
        "price_chart_1m": [],
        "price_chart_3m": [],
        "price_chart_6m": [],
        "price_chart_1y": [],
        **empty_instrument_trend_metrics(
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        ),
    }


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return date.fromisoformat(normalized[:10])
        except ValueError:
            return None
    return None


def normalize_chart_range_key(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SUPPORTED_CHART_RANGE_KEYS else "6m"


def _range_start_date(*, as_of_date: date, range_key: str) -> date | None:
    if range_key == "1m":
        return as_of_date - timedelta(days=31)
    if range_key == "3m":
        return as_of_date - timedelta(days=92)
    if range_key == "6m":
        return as_of_date - timedelta(days=183)
    if range_key == "1y":
        return as_of_date - timedelta(days=366)
    if range_key == "ytd":
        return date(as_of_date.year, 1, 1)
    return None


def _candidate_chart_bases(detail: dict[str, object]) -> list[str]:
    return analytical_return_quote_bases(detail)


def _candidate_total_return_bases(detail: dict[str, object]) -> list[str]:
    return [
        quote_basis
        for quote_basis in analytical_return_quote_bases(detail)
        if chart_return_semantics(detail, selected_basis=quote_basis) == "total_return"
    ]


def _downsample_points(points: list[dict[str, object]], max_points: int | None) -> list[dict[str, object]]:
    if max_points is None or max_points <= 0 or len(points) <= max_points:
        return points
    if max_points == 1:
        return [points[-1]]

    sampled_indices: set[int] = {0, len(points) - 1}
    for sample_index in range(1, max_points - 1):
        raw_index = round(sample_index * (len(points) - 1) / (max_points - 1))
        sampled_indices.add(raw_index)
    return [points[index] for index in sorted(sampled_indices)]


def _shift_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    target_year, target_month_index = divmod(month_index, 12)
    target_month = target_month_index + 1
    target_day = min(value.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def _return_window_names(
    points: list[dict[str, object]],
    *,
    window_end_date: date | None = None,
) -> list[str]:
    if len(points) < 2:
        return []
    end_date = window_end_date or points[-1].get("date")
    if not isinstance(end_date, date):
        return []
    windows: list[str] = []
    for window_name in TREND_RETURN_WINDOWS:
        if (
            _named_period_return(
                points,
                end_date=end_date,
                window_name=window_name,
            )
            is not None
        ):
            windows.append(window_name)
    return windows


def _trend_coverage(
    points: list[dict[str, object]],
    *,
    window_end_date: date | None = None,
) -> dict[str, object]:
    if not points:
        return _empty_trend_coverage()
    windows = _return_window_names(points, window_end_date=window_end_date)
    start_date = points[0].get("date")
    end_date = points[-1].get("date")
    return {
        "state": "complete" if len(windows) == len(TREND_RETURN_WINDOWS) else "partial",
        "observation_count": len(points),
        "start_date": start_date.isoformat() if isinstance(start_date, date) else None,
        "end_date": end_date.isoformat() if isinstance(end_date, date) else None,
        "available_return_windows": windows,
    }


def _split_adjusted_raw_points(
    detail: dict[str, object],
    points: list[dict[str, object]],
    *,
    quote_basis: str,
) -> tuple[list[dict[str, object]], bool, str | None]:
    if quote_basis not in RAW_SPLIT_SENSITIVE_BASES or len(points) < 2:
        return points, False, None
    first_date = points[0].get("date")
    last_date = points[-1].get("date")
    if not isinstance(first_date, date) or not isinstance(last_date, date):
        return [], False, "raw_price_split_effective_date_invalid"

    raw_actions = detail.get("corporate_actions")
    actions = (
        [event for event in raw_actions if isinstance(event, dict)]
        if isinstance(raw_actions, list)
        else []
    )
    crossing_actions: list[tuple[date, dict[str, object]]] = []
    for event in actions:
        if str(event.get("action_type") or "").strip().lower() != "share_split":
            continue
        if str(event.get("status") or "").strip().lower() == "cancelled":
            continue
        effective_date = _parse_iso_date(event.get("effective_date"))
        if effective_date is None:
            return [], False, "raw_price_split_effective_date_invalid"
        if first_date < effective_date <= last_date:
            crossing_actions.append((effective_date, event))
    if not crossing_actions:
        return points, False, None

    resolved_actions: list[tuple[date, Decimal]] = []
    for effective_date, event in sorted(crossing_actions, key=lambda item: item[0]):
        if str(event.get("status") or "").strip().lower() != "confirmed":
            return [], False, "raw_price_split_evidence_unconfirmed"
        try:
            new_units = Decimal(str(event.get("new_units") or ""))
            old_units = Decimal(str(event.get("old_units") or ""))
        except (InvalidOperation, ValueError):
            return [], False, "raw_price_split_ratio_invalid"
        if (
            not new_units.is_finite()
            or not old_units.is_finite()
            or new_units <= 0
            or old_units <= 0
            or new_units == old_units
        ):
            return [], False, "raw_price_split_ratio_invalid"
        fraction_treatment = str(event.get("quantity_rounding") or "").strip().lower()
        if fraction_treatment not in SUPPORTED_SPLIT_FRACTION_TREATMENTS:
            return [], False, "raw_price_split_fraction_treatment_insufficient"
        resolved_actions.append((effective_date, old_units / new_units))

    adjusted_points: list[dict[str, object]] = []
    for point in points:
        point_date = point.get("date")
        value = _safe_float(point.get("value"))
        if not isinstance(point_date, date) or value is None:
            return [], False, "invalid_quote_observation"
        adjustment_factor = Decimal("1")
        for effective_date, inverse_ratio in resolved_actions:
            if point_date < effective_date:
                adjustment_factor *= inverse_ratio
        adjusted_point = dict(point)
        adjusted_point["source_value"] = value
        adjusted_point["split_adjustment_factor"] = float(adjustment_factor)
        adjusted_point["value"] = value * float(adjustment_factor)
        adjusted_points.append(adjusted_point)
    return adjusted_points, True, None


def _selection_rank(
    points: list[dict[str, object]],
    *,
    policy_index: int,
) -> tuple[int, int, int, int, int, int]:
    windows = _return_window_names(points)
    start_date = points[0].get("date") if points else None
    end_date = points[-1].get("date") if points else None
    span_days = (
        (end_date - start_date).days
        if isinstance(start_date, date) and isinstance(end_date, date)
        else 0
    )
    end_ordinal = end_date.toordinal() if isinstance(end_date, date) else 0
    return (
        len(windows),
        int(len(points) >= 2),
        -policy_index,
        span_days,
        len(points),
        end_ordinal,
    )


def _select_chart_series(
    detail: dict[str, object],
    *,
    as_of_date: date,
) -> ChartSeriesSelection:
    candidate_bases = _candidate_chart_bases(detail)
    if not candidate_bases:
        return ChartSeriesSelection(
            reason="quote_policy_unavailable",
            coverage=_empty_trend_coverage(),
        )

    candidates: list[
        tuple[tuple[int, int, int, int, int, int], int, str, list[dict[str, object]], bool]
    ] = []
    unavailable_reasons: list[str] = []
    withheld_reasons: list[str] = []
    for policy_index, quote_basis in enumerate(candidate_bases):
        resolution = resolve_quote_series(
            detail,
            candidate_bases=[quote_basis],
            end_date=as_of_date,
        )
        if not resolution.available:
            if resolution.unavailable_reason:
                unavailable_reasons.append(resolution.unavailable_reason)
            continue
        points = [
            {
                "date": point["as_of_date"],
                "date_iso": point["as_of_date"].isoformat(),
                "value": point["value"],
                "currency": point["currency"],
                "metric_family": point["metric_family"],
                "quote_basis": point["quote_basis"],
                "price_unit": point.get("price_unit"),
                "price_scale": point.get("price_scale"),
            }
            for point in resolution.points
            if isinstance(point.get("as_of_date"), date)
        ]
        comparable_points, split_adjusted, split_error = _split_adjusted_raw_points(
            detail,
            points,
            quote_basis=str(resolution.quote_basis or quote_basis),
        )
        if split_error is not None:
            withheld_reasons.append(split_error)
            continue
        candidates.append(
            (
                _selection_rank(comparable_points, policy_index=policy_index),
                policy_index,
                str(resolution.quote_basis or quote_basis),
                comparable_points,
                split_adjusted,
            )
        )

    if not candidates:
        return ChartSeriesSelection(
            reason=(withheld_reasons or unavailable_reasons or ["quote_series_unavailable"])[0],
            coverage=_empty_trend_coverage(),
        )
    _, policy_index, selected_basis, selected_points, split_adjusted = max(
        candidates,
        key=lambda item: item[0],
    )
    if split_adjusted:
        reason = "selected_split_adjusted_raw_price_series"
    elif policy_index > 0:
        reason = "selected_more_complete_alternate_series"
    elif len(selected_points) < 2:
        reason = "selected_series_has_single_observation"
    else:
        reason = "selected_policy_series"
    return ChartSeriesSelection(
        points=tuple(selected_points),
        selected_basis=selected_basis,
        coverage=_trend_coverage(selected_points),
        reason=reason,
        split_adjusted=split_adjusted,
    )


def _select_total_return_series(
    detail: dict[str, object],
    *,
    as_of_date: date,
) -> ChartSeriesSelection:
    analytical_bases = analytical_return_quote_bases(detail)
    candidate_bases = _candidate_total_return_bases(detail)
    if not candidate_bases:
        return ChartSeriesSelection(
            reason=(
                "total_return_basis_unavailable"
                if analytical_bases
                else "quote_policy_unavailable"
            ),
            coverage=_empty_trend_coverage(),
        )

    unavailable_reasons: list[str] = []
    for policy_index, quote_basis in enumerate(candidate_bases):
        resolution = resolve_quote_series(
            detail,
            candidate_bases=[quote_basis],
            end_date=as_of_date,
        )
        if not resolution.available:
            if resolution.unavailable_reason:
                unavailable_reasons.append(resolution.unavailable_reason)
            continue
        points = [
            {
                "date": point["as_of_date"],
                "date_iso": point["as_of_date"].isoformat(),
                "value": point["value"],
                "currency": point["currency"],
                "metric_family": point["metric_family"],
                "quote_basis": point["quote_basis"],
                "price_unit": point.get("price_unit"),
                "price_scale": point.get("price_scale"),
            }
            for point in resolution.points
            if isinstance(point.get("as_of_date"), date)
        ]
        if len(points) < 2:
            reason = "selected_series_has_single_observation"
        elif policy_index > 0:
            reason = "selected_alternate_total_return_series"
        else:
            reason = "selected_policy_series"
        return ChartSeriesSelection(
            points=tuple(points),
            selected_basis=str(resolution.quote_basis or quote_basis),
            coverage=_trend_coverage(points, window_end_date=as_of_date),
            reason=reason,
            split_adjusted=False,
        )

    blocking_reason = next(
        (
            reason
            for reason in unavailable_reasons
            if reason not in {"quote_series_unavailable", "quote_policy_unavailable"}
        ),
        "total_return_series_unavailable",
    )
    return ChartSeriesSelection(
        reason=blocking_reason,
        coverage=_empty_trend_coverage(),
    )


def _latest_point_on_or_before(
    points: list[dict[str, object]],
    target_date: date,
) -> dict[str, object] | None:
    latest: dict[str, object] | None = None
    for point in points:
        point_date = point.get("date")
        if isinstance(point_date, date) and point_date <= target_date:
            latest = point
    return latest


def _first_point_on_or_after(
    points: list[dict[str, object]],
    target_date: date,
) -> dict[str, object] | None:
    for point in points:
        point_date = point.get("date")
        if isinstance(point_date, date) and point_date >= target_date:
            return point
    return None


def _return_between_points(
    start_point: dict[str, object] | None,
    end_point: dict[str, object] | None,
) -> float | None:
    if start_point is None or end_point is None:
        return None
    start_date = start_point.get("date")
    end_date = end_point.get("date")
    if isinstance(start_date, date) and isinstance(end_date, date) and start_date >= end_date:
        return None
    start_value = _safe_float(start_point.get("value"))
    end_value = _safe_float(end_point.get("value"))
    if start_value is None or end_value is None or abs(start_value) <= 1e-12:
        return None
    return end_value / start_value - 1


def _window_points(
    points: list[dict[str, object]],
    *,
    as_of_date: date,
    start_date: date,
    max_anchor_gap_days: int | None = None,
) -> list[dict[str, object]]:
    anchor_point = _latest_point_on_or_before(points, start_date)
    visible_points = [
        point
        for point in points
        if isinstance(point.get("date"), date) and start_date < point["date"] <= as_of_date
    ]
    if anchor_point is not None:
        anchor_date = anchor_point.get("date")
        if (
            max_anchor_gap_days is None
            or not isinstance(anchor_date, date)
            or (start_date - anchor_date).days <= max_anchor_gap_days
        ):
            visible_points = [anchor_point, *visible_points]
    return visible_points


def _points_since(
    points: list[dict[str, object]],
    *,
    start_date: date | None,
) -> list[dict[str, object]]:
    if start_date is None:
        return points
    anchor_point = _latest_point_on_or_before(points, start_date) or _first_point_on_or_after(points, start_date)
    if anchor_point is None:
        return []
    anchor_date = anchor_point.get("date")
    if not isinstance(anchor_date, date):
        return []
    return [point for point in points if isinstance(point.get("date"), date) and point["date"] >= anchor_date]


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) * (value - mean) for value in values) / (len(values) - 1)
    return sqrt(max(variance, 0.0))


def _period_return_observations(points: list[dict[str, object]]) -> tuple[list[float], date | None, date | None]:
    returns: list[float] = []
    first_return_start_date: date | None = None
    last_return_end_date: date | None = None
    previous_date: date | None = None
    previous_value: float | None = None
    for point in points:
        point_date = point.get("date")
        value = _safe_float(point.get("value"))
        if value is None or not isinstance(point_date, date):
            continue
        if previous_value is not None and previous_value > 1e-12 and previous_date is not None and previous_date < point_date:
            returns.append(value / previous_value - 1)
            if first_return_start_date is None:
                first_return_start_date = previous_date
            last_return_end_date = point_date
        previous_date = point_date
        previous_value = value
    return returns, first_return_start_date, last_return_end_date


def _period_return_series(
    points: list[dict[str, object]],
) -> tuple[list[dict[str, object]], date | None, date | None]:
    returns: list[dict[str, object]] = []
    first_return_start_date: date | None = None
    last_return_end_date: date | None = None
    previous_date: date | None = None
    previous_value: float | None = None
    for point in points:
        point_date = point.get("date")
        value = _safe_float(point.get("value"))
        if value is None or not isinstance(point_date, date):
            continue
        if previous_value is not None and previous_value > 1e-12 and previous_date is not None and previous_date < point_date:
            returns.append(
                {
                    "start_date": previous_date.isoformat(),
                    "date": point_date.isoformat(),
                    "value": value / previous_value - 1,
                }
            )
            if first_return_start_date is None:
                first_return_start_date = previous_date
            last_return_end_date = point_date
        previous_date = point_date
        previous_value = value
    return returns, first_return_start_date, last_return_end_date


def _points_for_calculation_frequency(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    final_date: date | None,
) -> list[dict[str, object]]:
    if calculation_frequency == "daily":
        return points

    sampled_by_period: dict[date, dict[str, object]] = {}
    for point in points:
        point_date = point.get("date")
        if not isinstance(point_date, date):
            continue
        target_date = period_end_date(point_date, calculation_frequency, final_date=final_date)
        current_point = sampled_by_period.get(target_date)
        current_date = current_point.get("date") if isinstance(current_point, dict) else None
        if not isinstance(current_date, date) or point_date >= current_date:
            sampled_by_period[target_date] = point
    return [sampled_by_period[target_date] for target_date in sorted(sampled_by_period)]


def _annualized_volatility(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency = "daily",
    final_date: date | None = None,
    min_return_observations: int = 2,
    required_start_date: date | None = None,
    max_start_gap_days: int | None = None,
    min_elapsed_days: int | None = None,
    required_end_date: date | None = None,
    max_trailing_staleness_days: int | None = None,
) -> float | None:
    sampled_points = _points_for_calculation_frequency(
        points,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_observations(sampled_points)
    if len(returns) < min_return_observations:
        return None
    stddev = _sample_stddev(returns)
    if stddev is None or first_return_start_date is None or last_return_end_date is None:
        return None
    if required_start_date is not None and max_start_gap_days is not None:
        start_gap_days = abs((first_return_start_date - required_start_date).days)
        if start_gap_days > max_start_gap_days:
            return None
    if required_end_date is not None and max_trailing_staleness_days is not None:
        trailing_staleness_days = (required_end_date - last_return_end_date).days
        if (
            trailing_staleness_days < 0
            or trailing_staleness_days > max_trailing_staleness_days
        ):
            return None
    elapsed_days = (last_return_end_date - first_return_start_date).days
    if elapsed_days <= 0:
        return None
    if min_elapsed_days is not None and elapsed_days < min_elapsed_days:
        return None
    periods_per_year = float(len(returns)) / float(elapsed_days) * DAYS_PER_YEAR
    return stddev * sqrt(periods_per_year)


def _annualized_window_volatility(
    points: list[dict[str, object]],
    *,
    range_key: str,
    as_of_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> float | None:
    window_start_date = _shift_calendar_months(
        as_of_date,
        -ASSET_RISK_WINDOW_MONTHS[range_key],
    )
    window_days = (as_of_date - window_start_date).days
    max_start_gap_days = ASSET_RISK_MAX_START_GAP_DAYS[calculation_frequency]
    window_points = _window_points(
        points,
        as_of_date=as_of_date,
        start_date=window_start_date,
        max_anchor_gap_days=max_start_gap_days,
    )
    return _annualized_volatility(
        window_points,
        calculation_frequency=calculation_frequency,
        final_date=as_of_date,
        min_return_observations=ASSET_RISK_MIN_RETURN_OBSERVATIONS[calculation_frequency][range_key],
        required_start_date=window_start_date,
        max_start_gap_days=max_start_gap_days,
        min_elapsed_days=int(window_days * ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO),
        required_end_date=as_of_date,
        max_trailing_staleness_days=ASSET_RISK_MAX_TRAILING_STALENESS_DAYS[
            calculation_frequency
        ],
    )


def _empty_return_series_payload() -> dict[str, object]:
    return {"first_return_start_date": None, "points": []}


def _return_series_payload(
    points: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency = "daily",
    final_date: date | None = None,
    min_return_observations: int = 2,
) -> dict[str, object]:
    sampled_points = _points_for_calculation_frequency(
        points,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_series(sampled_points)
    if len(returns) < min_return_observations or first_return_start_date is None or last_return_end_date is None:
        return _empty_return_series_payload()
    return {
        "first_return_start_date": first_return_start_date.isoformat(),
        "points": returns,
    }


def _window_return_series_payload(
    points: list[dict[str, object]],
    *,
    range_key: str,
    as_of_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    window_start_date = _shift_calendar_months(
        as_of_date,
        -ASSET_RISK_WINDOW_MONTHS[range_key],
    )
    window_days = (as_of_date - window_start_date).days
    max_start_gap_days = ASSET_RISK_MAX_START_GAP_DAYS[calculation_frequency]
    window_points = _window_points(
        points,
        as_of_date=as_of_date,
        start_date=window_start_date,
        max_anchor_gap_days=max_start_gap_days,
    )
    sampled_points = _points_for_calculation_frequency(
        window_points,
        calculation_frequency=calculation_frequency,
        final_date=as_of_date,
    )
    returns, first_return_start_date, last_return_end_date = _period_return_series(sampled_points)
    if len(returns) < ASSET_RISK_MIN_RETURN_OBSERVATIONS[calculation_frequency][range_key]:
        return _empty_return_series_payload()
    if first_return_start_date is None or last_return_end_date is None:
        return _empty_return_series_payload()
    trailing_staleness_days = (as_of_date - last_return_end_date).days
    if (
        trailing_staleness_days < 0
        or trailing_staleness_days
        > ASSET_RISK_MAX_TRAILING_STALENESS_DAYS[calculation_frequency]
    ):
        return _empty_return_series_payload()
    start_gap_days = abs((first_return_start_date - window_start_date).days)
    if start_gap_days > max_start_gap_days:
        return _empty_return_series_payload()
    elapsed_days = (last_return_end_date - first_return_start_date).days
    if elapsed_days <= 0 or elapsed_days < int(window_days * ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO):
        return _empty_return_series_payload()
    return {
        "first_return_start_date": first_return_start_date.isoformat(),
        "points": returns,
    }


def _max_drawdown(points: list[dict[str, object]]) -> float | None:
    if len(points) < 2:
        return None
    peak: float | None = None
    max_drawdown = 0.0
    has_value = False
    for point in points:
        value = _safe_float(point.get("value"))
        if value is None:
            continue
        has_value = True
        peak = value if peak is None else max(peak, value)
        if peak > 1e-12:
            max_drawdown = min(max_drawdown, value / peak - 1)
    return max_drawdown if has_value else None


def _current_drawdown(points: list[dict[str, object]]) -> float | None:
    values = [_safe_float(point.get("value")) for point in points]
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        return None
    end_value = valid_values[-1]
    peak_value = max(valid_values)
    return end_value / peak_value - 1 if peak_value > 1e-12 else None


def _named_period_return(
    points: list[dict[str, object]],
    *,
    end_date: date,
    window_name: str,
) -> float | None:
    normalized_window = window_name.strip().lower()
    if normalized_window == "1w":
        anchor_date = end_date - timedelta(days=7)
    elif normalized_window == "1m":
        anchor_date = _shift_calendar_months(end_date, -1)
    elif normalized_window == "3m":
        anchor_date = _shift_calendar_months(end_date, -3)
    elif normalized_window == "6m":
        anchor_date = _shift_calendar_months(end_date, -6)
    elif normalized_window == "mtd":
        anchor_date = date(end_date.year, end_date.month, 1) - timedelta(days=1)
    elif normalized_window == "ytd":
        anchor_date = date(end_date.year, 1, 1) - timedelta(days=1)
    elif normalized_window == "1y":
        anchor_date = _shift_calendar_months(end_date, -12)
    else:
        raise ValueError(f'Unsupported instrument return window "{window_name}".')
    start_point = _latest_point_on_or_before(points, anchor_date)
    end_point = points[-1] if points else None
    return _return_between_points(start_point, end_point)


def build_instrument_trend_metrics_from_detail(
    detail: dict[str, object],
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    selection = _select_total_return_series(detail, as_of_date=as_of_date)
    selected_points = list(selection.points)
    selected_basis = selection.selected_basis
    if not selected_points:
        return empty_instrument_trend_metrics(
            selected_basis=selected_basis,
            coverage=selection.coverage,
            reason=selection.reason,
            split_adjusted=selection.split_adjusted,
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        )

    end_point = selected_points[-1]
    end_date = end_point.get("date") if isinstance(end_point.get("date"), date) else as_of_date
    holding_points = _points_since(selected_points, start_date=holding_start_date) if holding_start_date else []

    return {
        "instrument_trend_as_of_date": end_date.isoformat(),
        "instrument_trend_basis": selected_basis,
        "instrument_trend_coverage": selection.coverage or _trend_coverage(selected_points),
        "instrument_trend_reason": selection.reason,
        "instrument_trend_split_adjusted": selection.split_adjusted,
        "instrument_risk_frequency": calculation_frequency,
        "instrument_return_1w": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="1w",
        ),
        "instrument_return_1m": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="1m",
        ),
        "instrument_return_3m": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="3m",
        ),
        "instrument_return_6m": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="6m",
        ),
        "instrument_return_mtd": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="mtd",
        ),
        "instrument_return_ytd": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="ytd",
        ),
        "instrument_return_1y": _named_period_return(
            selected_points,
            end_date=as_of_date,
            window_name="1y",
        ),
        "instrument_volatility_1m": _annualized_window_volatility(
            selected_points,
            range_key="1m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_3m": _annualized_window_volatility(
            selected_points,
            range_key="3m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_6m": _annualized_window_volatility(
            selected_points,
            range_key="6m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_volatility_1y": _annualized_window_volatility(
            selected_points,
            range_key="1y",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_1m": _window_return_series_payload(
            selected_points,
            range_key="1m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_3m": _window_return_series_payload(
            selected_points,
            range_key="3m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_6m": _window_return_series_payload(
            selected_points,
            range_key="6m",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_1y": _window_return_series_payload(
            selected_points,
            range_key="1y",
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
        ),
        "instrument_return_series_all": _return_series_payload(
            selected_points,
            calculation_frequency=calculation_frequency,
            final_date=end_date,
        ),
        "instrument_holding_return_series": (
            _return_series_payload(
                holding_points,
                calculation_frequency=calculation_frequency,
                final_date=end_date,
            )
            if holding_start_date
            else _empty_return_series_payload()
        ),
        "instrument_current_drawdown": _current_drawdown(selected_points),
        "instrument_max_drawdown": _max_drawdown(selected_points),
        "instrument_holding_max_drawdown": _max_drawdown(holding_points) if holding_start_date else None,
        "instrument_holding_start_date": holding_start_date.isoformat() if holding_start_date else None,
    }


def build_instrument_trend_metrics(
    instrument_id: str,
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return empty_instrument_trend_metrics(
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        )
    return build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
        calculation_frequency=calculation_frequency,
    )


def build_instrument_price_chart_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    normalized_range_key = normalize_chart_range_key(range_key)
    selection = _select_chart_series(detail, as_of_date=as_of_date)
    selected_points = list(selection.points)
    selected_basis = selection.selected_basis

    range_start_date = _range_start_date(as_of_date=as_of_date, range_key=normalized_range_key)
    visible_points = (
        [
            point
            for point in selected_points
            if range_start_date is None or point["date"] >= range_start_date
        ]
        if selected_points
        else []
    )
    if not visible_points and selected_points:
        visible_points = [selected_points[-1]]

    visible_points = _downsample_points(visible_points, max_points=max_points)
    point_values = [float(point["value"]) for point in visible_points]
    first_value = point_values[0] if point_values else None
    last_value = point_values[-1] if point_values else None
    change_value = (last_value - first_value) if first_value is not None and last_value is not None else None
    change_pct = (
        (change_value / first_value)
        if change_value is not None and first_value is not None and abs(first_value) > 1e-9
        else None
    )

    return {
        "instrument_core": {
            "instrument_id": str(detail.get("instrument_id") or instrument_id),
            "instrument_name": str(detail.get("instrument_name") or instrument_id),
            "instrument_type": str(detail.get("instrument_type") or "other"),
            "currency": str(detail.get("currency") or "USD"),
            "identifiers": list(detail.get("identifiers", [])) if isinstance(detail.get("identifiers"), list) else [],
        },
        "as_of_date": as_of_date.isoformat(),
        "range_key": normalized_range_key,
        "chart_basis": selected_basis,
        "return_semantics": chart_return_semantics(
            detail,
            selected_basis=selected_basis,
        ),
        "coverage_state": str((selection.coverage or {}).get("state") or "unavailable"),
        "selection_reason": selection.reason,
        "split_adjusted": selection.split_adjusted,
        "metric_family": (
            str(visible_points[-1].get("metric_family") or "")
            if visible_points
            else None
        ),
        "currency": (
            str(visible_points[-1].get("currency") or detail.get("currency") or "USD")
            if visible_points
            else str(detail.get("currency") or "USD")
        ),
        "points": [
            {
                "date": point["date_iso"],
                "value": float(point["value"]),
            }
            for point in visible_points
        ],
        "summary": {
            "point_count": len(visible_points),
            "change_value": change_value,
            "change_pct": change_pct,
            "high": max(point_values) if point_values else None,
            "low": min(point_values) if point_values else None,
        },
    }


def build_instrument_price_chart(
    instrument_id: str,
    *,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return None
    return build_instrument_price_chart_from_detail(
        detail,
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        range_key=range_key,
        max_points=max_points,
    )


def build_instrument_sparkline_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
    range_key: str | None = "6m",
    max_points: int = 48,
) -> list[dict[str, object]]:
    chart = build_instrument_price_chart_from_detail(
        detail,
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        range_key=range_key,
        max_points=max_points,
    )
    if not isinstance(chart, dict):
        return []
    points = chart.get("points", [])
    if not isinstance(points, list):
        return []
    return [
        {
            "date": str(point.get("date") or ""),
            "value": float(point.get("value")),
        }
        for point in points
        if isinstance(point, dict) and _safe_float(point.get("value")) is not None
    ]


def build_instrument_sparkline(
    instrument_id: str,
    *,
    as_of_date: date,
    range_key: str | None = "6m",
    max_points: int = 48,
) -> list[dict[str, object]]:
    chart = build_instrument_price_chart(
        instrument_id,
        as_of_date=as_of_date,
        range_key=range_key,
        max_points=max_points,
    )
    if not isinstance(chart, dict):
        return []
    points = chart.get("points", [])
    if not isinstance(points, list):
        return []
    return [
        {
            "date": str(point.get("date") or ""),
            "value": float(point.get("value")),
        }
        for point in points
        if isinstance(point, dict) and _safe_float(point.get("value")) is not None
    ]


def _chart_points_payload(chart: dict[str, object] | None) -> list[dict[str, object]]:
    points = chart.get("points", []) if isinstance(chart, dict) else []
    if not isinstance(points, list):
        return []
    return [
        {
            "date": str(point.get("date") or ""),
            "value": float(point.get("value")),
        }
        for point in points
        if isinstance(point, dict) and _safe_float(point.get("value")) is not None
    ]


def build_instrument_holdings_market_profile_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
    holding_start_date: date | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    charts = {
        f"price_chart_{range_key}": _chart_points_payload(
            build_instrument_price_chart_from_detail(
                detail,
                instrument_id=instrument_id,
                as_of_date=as_of_date,
                range_key=range_key,
                max_points=max_points,
            )
        )
        for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
    }
    return {
        **charts,
        **build_instrument_trend_metrics_from_detail(
            detail,
            as_of_date=as_of_date,
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        ),
    }


def build_instrument_holdings_market_profile(
    instrument_id: str,
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return empty_instrument_holdings_market_profile(
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
        )
    return build_instrument_holdings_market_profile_from_detail(
        detail,
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
        max_points=max_points,
        calculation_frequency=calculation_frequency,
    )
