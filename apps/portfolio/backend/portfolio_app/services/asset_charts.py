from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from portfolio_app.services.instrument_registry import get_registry_instrument_detail

SUPPORTED_CHART_RANGE_KEYS: tuple[str, ...] = ("1m", "3m", "6m", "ytd", "1y", "all")


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
        if not isinstance(raw_point, dict):
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


def build_asset_price_chart_from_detail(
    detail: dict[str, object],
    *,
    asset_id: str,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    normalized_range_key = normalize_chart_range_key(range_key)
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

    if not selected_points:
        fallback_points = [
            point
            for points in points_by_basis.values()
            for point in points
            if point["date"] <= as_of_date
        ]
        fallback_points.sort(key=lambda item: item["date_iso"])
        selected_points = fallback_points
        if selected_points:
            selected_basis = str(selected_points[-1].get("quote_basis") or "")

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
        "asset_core": {
            "asset_id": str(detail.get("asset_id") or asset_id),
            "asset_name": str(detail.get("asset_name") or asset_id),
            "asset_type": str(detail.get("asset_type") or "other"),
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


def build_asset_price_chart(
    asset_id: str,
    *,
    as_of_date: date,
    range_key: str | None = None,
    max_points: int | None = None,
) -> dict[str, object] | None:
    detail = get_registry_instrument_detail(asset_id)
    if not isinstance(detail, dict):
        return None
    return build_asset_price_chart_from_detail(
        detail,
        asset_id=asset_id,
        as_of_date=as_of_date,
        range_key=range_key,
        max_points=max_points,
    )


def build_asset_sparkline_from_detail(
    detail: dict[str, object],
    *,
    asset_id: str,
    as_of_date: date,
    max_points: int = 20,
) -> list[dict[str, object]]:
    chart = build_asset_price_chart_from_detail(
        detail,
        asset_id=asset_id,
        as_of_date=as_of_date,
        range_key="6m",
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


def build_asset_sparkline(
    asset_id: str,
    *,
    as_of_date: date,
    max_points: int = 20,
) -> list[dict[str, object]]:
    chart = build_asset_price_chart(
        asset_id,
        as_of_date=as_of_date,
        range_key="6m",
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
