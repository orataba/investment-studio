from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import sqrt

from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import is_usable_market_data_point

SUPPORTED_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "ytd", "1y", "all")
HOLDINGS_PRICE_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "1y")
ASSET_RISK_WINDOW_DAYS: dict[str, int] = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
}
DAYS_PER_YEAR = 365.25


def empty_instrument_trend_metrics(
    *,
    selected_basis: str | None = None,
    holding_start_date: date | None = None,
) -> dict[str, object]:
    return {
        "instrument_trend_as_of_date": None,
        "instrument_trend_basis": selected_basis,
        "instrument_return_1w": None,
        "instrument_return_mtd": None,
        "instrument_return_ytd": None,
        "instrument_return_1y": None,
        "instrument_volatility_1m": None,
        "instrument_volatility_3m": None,
        "instrument_volatility_6m": None,
        "instrument_volatility_1y": None,
        "instrument_current_drawdown": None,
        "instrument_max_drawdown": None,
        "instrument_holding_max_drawdown": None,
        "instrument_holding_start_date": holding_start_date.isoformat() if holding_start_date else None,
    }


def empty_instrument_holdings_market_profile(
    *,
    holding_start_date: date | None = None,
) -> dict[str, object]:
    return {
        "price_chart_1m": [],
        "price_chart_3m": [],
        "price_chart_6m": [],
        "price_chart_1y": [],
        **empty_instrument_trend_metrics(holding_start_date=holding_start_date),
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


def _normalized_policy_bases(detail: dict[str, object], role: str) -> list[str]:
    policy = detail.get("quote_selection_policy", {})
    if not isinstance(policy, dict):
        return []
    raw_values = policy.get(role)
    if not isinstance(raw_values, list):
        return []
    normalized_values: list[str] = []
    for raw_value in raw_values:
        quote_basis = str(raw_value or "").strip()
        if quote_basis and quote_basis not in normalized_values:
            normalized_values.append(quote_basis)
    return normalized_values


def _basis_points(detail: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    points_by_basis: dict[str, list[dict[str, object]]] = defaultdict(list)
    market_data = detail.get("market_data", [])
    if not isinstance(market_data, list):
        return points_by_basis

    for raw_point in market_data:
        if not is_usable_market_data_point(raw_point):
            continue
        quote_basis = str(raw_point.get("quote_basis") or "").strip()
        point_date = _parse_iso_date(raw_point.get("as_of_date"))
        value = _safe_float(raw_point.get("value"))
        if not quote_basis or point_date is None or value is None:
            continue
        points_by_basis[quote_basis].append(
            {
                "date": point_date,
                "date_iso": point_date.isoformat(),
                "value": value,
                "currency": str(raw_point.get("currency") or detail.get("currency") or "USD"),
                "metric_family": str(raw_point.get("metric_family") or ""),
                "quote_basis": quote_basis,
            }
        )

    for points in points_by_basis.values():
        points.sort(key=lambda item: item["date_iso"])
    return points_by_basis


def _candidate_chart_bases(detail: dict[str, object]) -> list[str]:
    candidate_bases = [
        *_normalized_policy_bases(detail, "total_return"),
        *_normalized_policy_bases(detail, "chart"),
        *_normalized_policy_bases(detail, "valuation"),
        *_normalized_policy_bases(detail, "reference"),
    ]
    normalized: list[str] = []
    for quote_basis in candidate_bases:
        if quote_basis not in normalized:
            normalized.append(quote_basis)
    return normalized


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


def _selected_chart_points(
    detail: dict[str, object],
    *,
    as_of_date: date,
) -> tuple[list[dict[str, object]], str | None]:
    points_by_basis = _basis_points(detail)
    candidate_bases = _candidate_chart_bases(detail)

    selected_points: list[dict[str, object]] = []
    selected_basis: str | None = None
    for quote_basis in candidate_bases:
        eligible_points = [point for point in points_by_basis.get(quote_basis, []) if point["date"] <= as_of_date]
        if eligible_points:
            selected_basis = quote_basis
            selected_points = eligible_points
            break

    if selected_points:
        return selected_points, selected_basis

    return [], None


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
    days: int,
) -> list[dict[str, object]]:
    start_date = as_of_date - timedelta(days=days)
    anchor_point = _latest_point_on_or_before(points, start_date)
    visible_points = [
        point
        for point in points
        if isinstance(point.get("date"), date) and start_date < point["date"] <= as_of_date
    ]
    if anchor_point is not None:
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


def _annualized_volatility(points: list[dict[str, object]]) -> float | None:
    returns, first_return_start_date, last_return_end_date = _period_return_observations(points)
    stddev = _sample_stddev(returns)
    if stddev is None or first_return_start_date is None or last_return_end_date is None:
        return None
    elapsed_days = (last_return_end_date - first_return_start_date).days
    if elapsed_days <= 0:
        return None
    periods_per_year = float(len(returns)) / float(elapsed_days) * DAYS_PER_YEAR
    return stddev * sqrt(periods_per_year)


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


def _period_return(
    points: list[dict[str, object]],
    *,
    end_point: dict[str, object] | None,
    anchor_date: date,
    fallback_start_date: date | None = None,
) -> float | None:
    start_point = _latest_point_on_or_before(points, anchor_date)
    if start_point is None and fallback_start_date is not None:
        start_point = _first_point_on_or_after(points, fallback_start_date)
    return _return_between_points(start_point, end_point)


def build_instrument_trend_metrics_from_detail(
    detail: dict[str, object],
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
) -> dict[str, object]:
    selected_points, selected_basis = _selected_chart_points(detail, as_of_date=as_of_date)
    if not selected_points:
        return empty_instrument_trend_metrics(selected_basis=selected_basis, holding_start_date=holding_start_date)

    end_point = selected_points[-1]
    end_date = end_point.get("date") if isinstance(end_point.get("date"), date) else as_of_date
    month_start = date(end_date.year, end_date.month, 1)
    year_start = date(end_date.year, 1, 1)
    holding_points = _points_since(selected_points, start_date=holding_start_date) if holding_start_date else []

    return {
        "instrument_trend_as_of_date": end_date.isoformat(),
        "instrument_trend_basis": selected_basis,
        "instrument_return_1w": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=end_date - timedelta(days=7),
        ),
        "instrument_return_mtd": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=month_start - timedelta(days=1),
            fallback_start_date=month_start,
        ),
        "instrument_return_ytd": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=year_start - timedelta(days=1),
            fallback_start_date=year_start,
        ),
        "instrument_return_1y": _period_return(
            selected_points,
            end_point=end_point,
            anchor_date=end_date - timedelta(days=365),
        ),
        "instrument_volatility_1m": _annualized_volatility(
            _window_points(selected_points, as_of_date=end_date, days=ASSET_RISK_WINDOW_DAYS["1m"])
        ),
        "instrument_volatility_3m": _annualized_volatility(
            _window_points(selected_points, as_of_date=end_date, days=ASSET_RISK_WINDOW_DAYS["3m"])
        ),
        "instrument_volatility_6m": _annualized_volatility(
            _window_points(selected_points, as_of_date=end_date, days=ASSET_RISK_WINDOW_DAYS["6m"])
        ),
        "instrument_volatility_1y": _annualized_volatility(
            _window_points(selected_points, as_of_date=end_date, days=ASSET_RISK_WINDOW_DAYS["1y"])
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
) -> dict[str, object]:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return empty_instrument_trend_metrics(holding_start_date=holding_start_date)
    return build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
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
    selected_points, selected_basis = _selected_chart_points(detail, as_of_date=as_of_date)

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


def build_instrument_holdings_market_profile(
    instrument_id: str,
    *,
    as_of_date: date,
    holding_start_date: date | None = None,
    max_points: int = 48,
) -> dict[str, object]:
    detail = get_registry_instrument_detail(instrument_id)
    if not isinstance(detail, dict):
        return empty_instrument_holdings_market_profile(holding_start_date=holding_start_date)
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
        ),
    }
