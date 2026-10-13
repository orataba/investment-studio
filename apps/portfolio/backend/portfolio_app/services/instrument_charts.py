from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt

from portfolio_app.services.calculation_frequency import CalculationFrequency
from investment_studio_instrument_core import resolve_quote_return_semantics
from investment_studio_instrument_core.return_series import build_return_intervals, return_interval_unavailable_reason
from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import (
    QuoteSeriesResolution,
    analytical_return_quote_bases,
    analytical_return_metadata,
    market_calendar_sessions,
    quote_policy_bases,
    resolve_quote_series,
)
from portfolio_app.services.risk_basis import observation_coverage_from_dates, observation_source_settings, observation_schedule_verified

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
}
ASSET_RISK_MAX_START_GAP_DAYS: dict[CalculationFrequency, int] = {
    "daily": 10,
}
ASSET_RISK_MAX_TRAILING_STALENESS_DAYS: dict[CalculationFrequency, int] = {
    "daily": 5,
}
ASSET_RISK_MIN_WINDOW_COVERAGE_RATIO = 0.8
DAYS_PER_YEAR = 365.25
TREND_RETURN_WINDOWS: tuple[str, ...] = (
    "1w",
    "1m",
    "3m",
    "6m",
    "mtd",
    "ytd",
    "1y",
)


@dataclass(frozen=True)
class ChartSeriesSelection:
    points: tuple[dict[str, object], ...] = ()
    selected_basis: str | None = None
    coverage: dict[str, object] | None = None
    reason: str = "quote_series_unavailable"
    split_adjusted: bool = False
    return_metadata: dict[str, object] | None = None

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


def _candidate_analytical_return_bases(detail: dict[str, object]) -> list[str]:
    return analytical_return_quote_bases(detail)


def _bind_return_segments(points: list[dict[str, object]], detail: dict[str, object], basis: str) -> None:
    settings = detail.get("source_settings") or {}
    if not any(key in settings for key in ("total_return_segments", "total_return_unavailable_intervals")):
        return
    segment = points[0]["date"].isoformat() if points else None
    previous = None
    for point in points:
        reason = (return_interval_unavailable_reason(previous, point["date"],
                  source_settings=detail.get("source_settings"), quote_basis=basis)
                  if previous is not None else None)
        if reason:
            segment = point["date"].isoformat()
            point["return_break_reason"] = reason
        point["return_segment"] = segment
        previous = point["date"]


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


def _resolve_profile_quote_series(
    detail: dict[str, object],
    *,
    quote_basis: str,
    as_of_date: date,
    resolutions: dict[str, QuoteSeriesResolution],
) -> QuoteSeriesResolution:
    # This dictionary belongs to one instrument and as-of date. Chart and
    # total-return selection keep their own policies, sharing only raw inputs.
    if quote_basis not in resolutions:
        resolutions[quote_basis] = resolve_quote_series(
            detail,
            candidate_bases=[quote_basis],
            end_date=as_of_date,
        )
    return resolutions[quote_basis]


def _select_analytical_return_series(
    detail: dict[str, object],
    *,
    as_of_date: date,
    resolutions: dict[str, QuoteSeriesResolution] | None = None,
) -> ChartSeriesSelection:
    resolutions = {} if resolutions is None else resolutions
    analytical_bases = analytical_return_quote_bases(detail)
    candidate_bases = _candidate_analytical_return_bases(detail)
    if not candidate_bases:
        return ChartSeriesSelection(
            reason=(
                "analytical_return_basis_unavailable"
                if analytical_bases
                else "quote_policy_unavailable"
            ),
            coverage=_empty_trend_coverage(),
        )

    unavailable_reasons: list[str] = []
    for policy_index, quote_basis in enumerate(candidate_bases):
        resolution = _resolve_profile_quote_series(
            detail,
            quote_basis=quote_basis,
            as_of_date=as_of_date,
            resolutions=resolutions,
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
        _bind_return_segments(points, detail, str(resolution.quote_basis or quote_basis))
        observation_coverage = observation_coverage_from_dates(
            [point["date"] for point in points], source_settings=observation_source_settings(detail),
            end_date=as_of_date,
        )
        gap_dates = sorted(observation_coverage["gap_dates"])
        schedule_verified = observation_schedule_verified(observation_coverage)
        for previous, current in zip(points, points[1:]):
            if not schedule_verified:
                current["risk_break_reason"] = "observation_schedule_unverified"
            elif bisect_right(gap_dates, current["date_iso"]) > bisect_right(gap_dates, previous["date_iso"]):
                current["risk_break_reason"] = (
                    "missing_expected_observation" if observation_coverage["gap_detection_basis"].startswith("market_calendar:")
                    else "missing_observation_interval"
                )
        if gap_dates:
            segment = points[0]["date_iso"]
            for point in points:
                if point.get("risk_break_reason") or point.get("return_break_reason"):
                    segment = point["date_iso"]
                point["chart_segment"] = segment
        if len(points) < 2:
            reason = "selected_series_has_single_observation"
        elif not schedule_verified:
            reason = "observation_schedule_unverified"
        elif gap_dates and gap_dates[-1] > points[-1]["date_iso"]:
            reason = "missing_expected_observation"
        elif policy_index > 0:
            reason = "selected_alternate_analytical_return_series"
        else:
            reason = "selected_policy_series"
        return ChartSeriesSelection(
            points=tuple(points),
            selected_basis=str(resolution.quote_basis or quote_basis),
            coverage=_trend_coverage(points, window_end_date=as_of_date),
            reason=reason,
            split_adjusted=False,
            return_metadata={**analytical_return_metadata(detail, quote_basis=resolution.quote_basis,
                                                          currency=resolution.currency),
                             "observation_coverage": observation_coverage},
        )

    blocking_reason = next(
        (
            reason
            for reason in unavailable_reasons
            if reason not in {"quote_series_unavailable", "quote_policy_unavailable"}
        ),
        "analytical_return_series_unavailable",
    )
    return ChartSeriesSelection(
        reason=blocking_reason,
        coverage=_empty_trend_coverage(),
    )


def _select_price_level_series(
    detail: dict[str, object],
    *,
    as_of_date: date,
    resolutions: dict[str, QuoteSeriesResolution] | None = None,
) -> ChartSeriesSelection:
    resolutions = {} if resolutions is None else resolutions
    candidate_bases = quote_policy_bases(detail, ("valuation", "trading", "reference"))
    candidate_bases = [basis for basis in candidate_bases if basis not in {"adjusted_close", "total_return_nav"}]
    if not candidate_bases:
        return ChartSeriesSelection(
            reason="execution_quote_policy_unavailable",
            coverage=_empty_trend_coverage(),
        )
    unavailable_reasons: list[str] = []
    for policy_index, quote_basis in enumerate(candidate_bases):
        resolution = _resolve_profile_quote_series(
            detail,
            quote_basis=quote_basis,
            as_of_date=as_of_date,
            resolutions=resolutions,
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
        return ChartSeriesSelection(
            points=tuple(points),
            selected_basis=str(resolution.quote_basis or quote_basis),
            coverage=_trend_coverage(points),
            reason="selected_raw_valuation_series",
            split_adjusted=False,
        )
    return ChartSeriesSelection(
        reason=(unavailable_reasons or ["execution_price_series_unavailable"])[0],
        coverage=_empty_trend_coverage(),
    )


def _latest_point_on_or_before(
    points: list[dict[str, object]],
    target_date: date,
) -> dict[str, object] | None:
    # Preserve the last matching observation without walking every older point
    # for each current or recent trend boundary.
    for point in reversed(points):
        point_date = point.get("date")
        if isinstance(point_date, date) and point_date <= target_date:
            return point
    return None


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
    if start_point.get("return_segment") != end_point.get("return_segment"):
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


def _period_return_series(
    points: list[dict[str, object]],
) -> tuple[list[dict[str, object]], date | None, date | None]:
    breaks = {point["date"]: point.get("return_break_reason") or point["risk_break_reason"]
              for point in points if point.get("return_break_reason") or point.get("risk_break_reason")}
    returns = build_return_intervals(points, break_dates=breaks)
    for item in returns:
        end = _parse_iso_date(item["date"])
        if end in breaks:
            item["reason"] = breaks[end]
    first = _parse_iso_date(returns[0]["start_date"]) if returns else None
    last = _parse_iso_date(returns[-1]["date"]) if returns else None
    return returns, first, last


def _period_return_observations(points: list[dict[str, object]]) -> tuple[list[float], date | None, date | None]:
    returns, first, last = _period_return_series(points)
    if any(item["status"] != "complete" for item in returns):
        return [], None, None
    return [float(item["value"]) for item in returns], first, last


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
    del calculation_frequency, final_date
    sampled_points = points
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
    del calculation_frequency, final_date
    sampled_points = points
    returns, first_return_start_date, last_return_end_date = _period_return_series(sampled_points)
    if len(returns) < min_return_observations or first_return_start_date is None or last_return_end_date is None:
        return _empty_return_series_payload()
    return {
        "first_return_start_date": first_return_start_date.isoformat(),
        "last_return_end_date": last_return_end_date.isoformat(),
        "return_measure": "simple",
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
    sampled_points = window_points
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
        "last_return_end_date": last_return_end_date.isoformat(),
        "return_measure": "simple",
        "points": returns,
    }


def _max_drawdown(points: list[dict[str, object]]) -> float | None:
    if (len(points) < 2 or len({point.get("return_segment") for point in points}) > 1
            or any(point.get("risk_break_reason") for point in points[1:])):
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
    if (len({point.get("return_segment") for point in points}) > 1
            or any(point.get("risk_break_reason") for point in points[1:])):
        return None
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
    selection = _select_analytical_return_series(detail, as_of_date=as_of_date)
    return _build_instrument_trend_metrics_from_selection(
        selection,
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
        calculation_frequency=calculation_frequency,
        source_settings=observation_source_settings(detail),
    )


def _build_instrument_trend_metrics_from_selection(
    selection: ChartSeriesSelection,
    *,
    as_of_date: date,
    holding_start_date: date | None,
    calculation_frequency: CalculationFrequency,
    source_settings: object,
) -> dict[str, object]:
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
    source_coverage = (selection.return_metadata or {}).get("observation_coverage") or {}
    source_gaps = sorted(source_coverage.get("gap_dates", []))
    holding_coverage = dict(source_coverage)
    holding_points = []
    if holding_start_date:
        # Keep the real preceding NAV as the opening anchor, including the
        # first post-recognition return. A missing observation on/before the
        # recognition date is not a gap in the subsequent holding period.
        holding_points = [dict(point) for point in _points_since(selected_points, start_date=holding_start_date)]
        holding_gaps = [day for day in source_gaps if day > holding_start_date.isoformat()]
        holding_coverage.update(start_date=holding_start_date.isoformat(), gap_dates=holding_gaps)
        for previous, current in zip(holding_points, holding_points[1:]):
            if (current.get("risk_break_reason") in {"missing_expected_observation", "missing_observation_interval"}
                    and bisect_right(source_gaps, current["date_iso"]) > bisect_right(source_gaps, previous["date_iso"])
                    and bisect_right(holding_gaps, current["date_iso"]) == bisect_right(holding_gaps, previous["date_iso"])):
                current.pop("risk_break_reason")

    result = {
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
        "instrument_return_series_all": _full_return_series_payload(
            selected_points,
            end_date=end_date,
            calculation_frequency=calculation_frequency,
            source_settings=source_settings,
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

    for key, payload in result.items():
        if (key.startswith("instrument_return_series_") or key == "instrument_holding_return_series") and isinstance(payload, dict):
            payload.update(selection.return_metadata or {})
            if key == "instrument_holding_return_series" and holding_start_date:
                payload["observation_coverage"] = holding_coverage
    if any(day > end_date.isoformat() for day in source_coverage.get("gap_dates", [])):
        for key in result:
            if key.startswith("instrument_volatility_") or key in {
                "instrument_current_drawdown", "instrument_max_drawdown", "instrument_holding_max_drawdown",
            }:
                result[key] = None
    return result


def _full_return_series_payload(
    selected_points: list[dict[str, object]],
    *,
    end_date: date,
    calculation_frequency: CalculationFrequency,
    source_settings: object,
) -> dict[str, object]:
    return {
        **_return_series_payload(
            selected_points,
            calculation_frequency=calculation_frequency,
            final_date=end_date,
        ),
        "observation_coverage": observation_coverage_from_dates(
            [point["date"] for point in selected_points],
            source_settings=source_settings,
            end_date=end_date,
        ),
    }


def build_instrument_risk_return_series_from_detail(
    detail: dict[str, object],
    *,
    as_of_date: date,
    calculation_frequency: CalculationFrequency = "daily",
) -> dict[str, object]:
    """Use the identical full native risk history without rendering holdings charts."""
    selection = _select_analytical_return_series(detail, as_of_date=as_of_date)
    points = list(selection.points)
    if not points:
        return _empty_return_series_payload()
    last_date = points[-1].get("date")
    return {**_full_return_series_payload(
        points,
        end_date=last_date if isinstance(last_date, date) else as_of_date,
        calculation_frequency=calculation_frequency,
        source_settings=observation_source_settings(detail),
    ), **(selection.return_metadata or {})}


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


def _chart_window_points(
    selection: ChartSeriesSelection,
    *,
    as_of_date: date,
    range_key: str,
) -> list[dict[str, object]]:
    range_start_date = _range_start_date(as_of_date=as_of_date, range_key=range_key)
    visible_points = [
        point
        for point in selection.points
        if range_start_date is None or point["date"] >= range_start_date
    ]
    return visible_points


def _price_display_selection(detail, *, as_of_date, price_mode):
    if price_mode not in {"return", "raw", "front", "back"}:
        raise ValueError("Price mode must be return, raw, front or back.")
    resolutions = {}
    raw = _select_price_level_series(detail, as_of_date=as_of_date, resolutions=resolutions)
    modes = ["raw"] if raw.available else []
    analytical = _select_analytical_return_series(detail, as_of_date=as_of_date, resolutions=resolutions)
    raw_by_date = {point["date"]: point for point in raw.points}
    common = [point for point in analytical.points if point["date"] in raw_by_date
              and point["currency"] == raw_by_date[point["date"]]["currency"]]
    can_adjust = (str(detail.get("instrument_type")) in {"equity", "etf"}
                  and (analytical.return_metadata or {}).get("return_kind") == "total_return"
                  and bool(common))
    can_adjust_front = can_adjust and common[-1]["date"] == raw.points[-1]["date"]
    if can_adjust_front:
        modes.append("front")
    if can_adjust:
        modes.append("back")
    if price_mode == "return":
        return analytical, modes, None
    if price_mode == "raw":
        return raw, modes, None
    if not can_adjust or price_mode == "front" and not can_adjust_front:
        return ChartSeriesSelection(reason="confirmed_total_return_for_price_adjustment_unavailable"), modes, None
    # The anchor belongs to the complete as-of history, never the visible range.
    anchor = common[-1] if price_mode == "front" else common[0]
    anchor_price = raw_by_date[anchor["date"]]["value"]
    factor = float(anchor_price) / float(anchor["value"])
    points = tuple({**point, "value": float(point["value"]) * factor}
                   for point in analytical.points
                   if point.get("return_segment") == anchor.get("return_segment"))
    selection = ChartSeriesSelection(points=points, selected_basis=analytical.selected_basis,
        coverage=_trend_coverage(list(points)), reason="derived_total_return_price_display",
        split_adjusted=True, return_metadata=analytical.return_metadata)
    return selection, modes, anchor["date"]


def build_instrument_price_chart_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
    price_level: bool = False,
    price_mode: str = "return",
) -> dict[str, object] | None:
    normalized_range_key = normalize_chart_range_key(range_key)
    if price_level:
        price_mode = "raw"
    selection, available_modes, anchor_date = _price_display_selection(
        detail, as_of_date=as_of_date, price_mode=price_mode,
    )
    selected_basis = selection.selected_basis
    visible_points = _chart_window_points(
        selection,
        as_of_date=as_of_date,
        range_key=normalized_range_key,
    )

    settings = detail.get("source_settings")
    calendar_name = str(
        (settings.get("market_calendar") if isinstance(settings, dict) else None)
        or detail.get("exchange_code") or ""
    ).strip()
    # Calendar coverage extends through the requested date, including missing
    # prices after the last observation. Never infer closures from price gaps.
    market_sessions = (
        market_calendar_sessions(calendar_name, visible_points[0]["date"], as_of_date)
        if calendar_name and visible_points else None
    )
    # Missing observations interrupt the drawn path, not comparability of
    # confirmed total-return levels within the same economic segment.
    comparable_levels = len({point.get("return_segment") for point in visible_points}) <= 1
    visible_points = _downsample_points(visible_points, max_points=max_points)
    point_values = [float(point["value"]) for point in visible_points]
    first_value = point_values[0] if point_values else None
    last_value = point_values[-1] if point_values else None
    change_value = ((last_value - first_value)
                    if comparable_levels and first_value is not None and last_value is not None else None)
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
            "exchange_code": detail.get("exchange_code"),
            "currency": str(detail.get("currency") or "USD"),
            "identifiers": list(detail.get("identifiers", [])) if isinstance(detail.get("identifiers"), list) else [],
        },
        "as_of_date": as_of_date.isoformat(),
        "range_key": normalized_range_key,
        "series_role": "performance" if price_mode == "return" else "price_level",
        "price_mode": price_mode,
        "available_price_modes": available_modes,
        "adjustment_anchor_date": anchor_date.isoformat() if anchor_date else None,
        "adjustment_method": "total_return_reinvestment" if anchor_date else None,
        "chart_basis": selected_basis,
        "return_semantics": chart_return_semantics(
            detail,
            selected_basis=selected_basis,
        ),
        "return_kind": chart_return_semantics(detail, selected_basis=selected_basis),
        "return_measure": "simple",
        "observation_coverage": (selection.return_metadata or {}).get("observation_coverage"),
        **{key: settings[key] for key in ("total_return_segments", "total_return_unavailable_intervals")
           if isinstance(settings, dict) and key in settings},
        "coverage_state": str((selection.coverage or {}).get("state") or "unavailable"),
        "selection_reason": selection.reason,
        "split_adjusted": selection.split_adjusted,
        "market_session_dates": (
            [session.isoformat() for session in market_sessions]
            if market_sessions is not None else None
        ),
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
                **({"segment_id": point["return_segment"]}
                   if price_mode == "return" and "return_segment" in point else {}),
                **({"chart_segment_id": point["chart_segment"]}
                   if price_mode == "return" and "chart_segment" in point else {}),
            }
            for point in visible_points
        ],
        "summary": {
            "point_count": len(visible_points),
            "change_value": change_value,
            "change_pct": change_pct,
            "high": max(point_values) if comparable_levels and point_values else None,
            "low": min(point_values) if comparable_levels and point_values else None,
        },
    }


def build_instrument_price_chart(
    instrument_id: str,
    *,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
    price_level: bool = False,
    price_mode: str = "return",
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
        price_level=price_level,
        price_mode=price_mode,
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


def build_instrument_holdings_market_profile_from_detail(
    detail: dict[str, object],
    *,
    instrument_id: str,
    as_of_date: date,
    holding_start_date: date | None = None,
    max_points: int = 48,
    calculation_frequency: CalculationFrequency = "daily",
    include_details: bool = True,
) -> dict[str, object]:
    resolutions: dict[str, QuoteSeriesResolution] = {}
    chart_selection = _select_price_level_series(
        detail, as_of_date=as_of_date, resolutions=resolutions,
    )
    trend_selection = _select_analytical_return_series(
        detail, as_of_date=as_of_date, resolutions=resolutions,
    )
    # Holdings exposes only chart points. Full chart metadata and calendar
    # sessions belong to the separate price-chart response.
    charts = {
        f"price_chart_{range_key}": (
            [
                {"date": point["date_iso"], "value": float(point["value"])}
                for point in _downsample_points(
                    _chart_window_points(
                        chart_selection, as_of_date=as_of_date, range_key=range_key,
                    ),
                    max_points=max_points,
                )
            ]
            if include_details or range_key == "6m"
            else []
        )
        for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
    }
    return {
        **charts,
        **_build_instrument_trend_metrics_from_selection(
            trend_selection,
            as_of_date=as_of_date,
            holding_start_date=holding_start_date,
            calculation_frequency=calculation_frequency,
            source_settings=observation_source_settings(detail),
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
