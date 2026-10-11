from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from typing import Any, Literal


RETURN_WINDOW_POLICY_VERSION = "return-window/v2"
AnchorMode = Literal["on_or_before", "strictly_before"]


@dataclass(frozen=True)
class ReturnWindow:
    requested_start_date: date
    requested_end_date: date
    anchor_mode: AnchorMode
    anchor_date: date
    end_date: date
    points: tuple[dict[str, Any], ...]

    @property
    def elapsed_days(self) -> int:
        return max((self.end_date - self.anchor_date).days, 0)


@dataclass(frozen=True)
class ReturnWindowSpec:
    requested_start_date: date
    requested_end_date: date
    anchor_mode: AnchorMode


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    target_year, target_month_index = divmod(month_index, 12)
    target_month = target_month_index + 1
    target_day = min(value.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def _actual_year_fraction(start_date: date, end_date: date) -> float:
    if end_date < start_date:
        return -_actual_year_fraction(end_date, start_date)

    def anniversary(years: int) -> date:
        return _shift_months(start_date, years * 12)

    whole_years = max(end_date.year - start_date.year, 0)
    while whole_years > 0 and anniversary(whole_years) > end_date:
        whole_years -= 1
    current_anniversary = anniversary(whole_years)
    if current_anniversary == end_date:
        return float(whole_years)
    next_anniversary = anniversary(whole_years + 1)
    return float(whole_years) + (
        (end_date - current_anniversary).days
        / (next_anniversary - current_anniversary).days
    )


def named_return_window_spec(window: str, as_of_date: date) -> ReturnWindowSpec:
    normalized = window.strip().upper()
    if normalized == "1D":
        start = as_of_date - timedelta(days=1)
        mode: AnchorMode = "on_or_before"
    elif normalized == "1W":
        start = as_of_date - timedelta(days=7)
        mode = "on_or_before"
    elif normalized == "1M":
        start = _shift_months(as_of_date, -1)
        mode = "on_or_before"
    elif normalized == "3M":
        start = _shift_months(as_of_date, -3)
        mode = "on_or_before"
    elif normalized == "6M":
        start = _shift_months(as_of_date, -6)
        mode = "on_or_before"
    elif normalized in {"1Y", "2Y", "3Y", "5Y", "10Y"}:
        start = _shift_months(as_of_date, -12 * int(normalized[:-1]))
        mode = "on_or_before"
    elif normalized == "MTD":
        start = date(as_of_date.year, as_of_date.month, 1)
        mode = "strictly_before"
    elif normalized == "YTD":
        start = date(as_of_date.year, 1, 1)
        mode = "strictly_before"
    else:
        raise ValueError(f'Unsupported return window "{window}".')
    return ReturnWindowSpec(
        requested_start_date=start,
        requested_end_date=as_of_date,
        anchor_mode=mode,
    )


def same_return_segment(points) -> bool:
    """A reset of the return-chain level is not an economic return."""
    return bool(points) and len({point.get("segment_id") for point in points}) == 1


def confirmed_return_points(series: dict[str, Any]) -> list[dict[str, Any]]:
    """Read retained analytical points without losing their economic boundaries.

    Missing segment identities on a declared segmented series cannot establish
    continuity. Invalid observations fail closed instead of disappearing before
    interval calculations and joining their former neighbours.
    """
    metadata = series.get("metadata") or {}
    if metadata.get("return_kind") not in {"total_return", "price_return"} or metadata.get("return_series_status") not in {"ready", "complete", "partial"}:
        return []
    result = []
    seen = set()
    for point in series.get("points") or []:
        try:
            day = date.fromisoformat(point["date"])
            value = float(point["value"])
        except (TypeError, ValueError, KeyError):
            return []
        if day in seen or isinstance(point["value"], bool) or not isfinite(value) or value <= 0:
            return []
        if metadata.get("return_segment_breaks") and not point.get("segment_id"):
            return []
        seen.add(day)
        result.append({**point, "date": day.isoformat(), "as_of_date": day, "value": value})
    return sorted(result, key=lambda point: point["date"])


def return_sample_limitation(series: dict[str, Any], points: list[dict[str, Any]]) -> str | None:
    """Require retained interval evidence for the exact observations consumed.

    A full-history gap counter cannot certify a later window. Conversely, an
    intersection of observed dates must not turn a missing session into a valid
    multi-day return. Call this on each unthinned source path before alignment.
    """
    if len(points) < 2:
        return "区间内不足两个有效观察值"
    if not same_return_segment(points):
        return "请求区间跨越待确认的收益链断点"
    frequency = series.get("frequency") or {}
    resolved = frequency.get("resolved_frequency")
    if resolved not in {"daily", "weekly", "monthly", "event_driven"}:
        return "收益序列频率尚未确认"
    if resolved == "daily" and not str(frequency.get("gap_detection_basis") or "").startswith("market_calendar:"):
        return "缺少已确认的交易日历，不能验证日度观察值完整性"

    def day(point):
        value = point.get("as_of_date") or point.get("date")
        return value.isoformat() if isinstance(value, date) else value

    if resolved in {"weekly", "monthly"}:
        dates = [date.fromisoformat(day(point)) for point in points]
        periods = [value.year * 12 + value.month if resolved == "monthly"
                   else (value.toordinal() - value.weekday()) // 7 for value in dates]
        if any(after - before > 1 for before, after in zip(periods, periods[1:])):
            return "计算区间缺少整期披露观察值，未跨缺口比较"

    expected = {(day(before), day(after)) for before, after in zip(points, points[1:])}
    evidence = {pair: [] for pair in expected}
    for interval in series.get("intervals") or []:
        pair = (interval.get("start_date"), interval.get("date"))
        if pair in evidence:
            evidence[pair].append(interval.get("status"))
    if any(not statuses for statuses in evidence.values()):
        return "计算区间缺少完整的收益区间证据，不能确认观察值连续性"
    if any(statuses != ["complete"] for statuses in evidence.values()):
        return "计算区间存在缺失观察值或未确认的收益区间"
    return None


def resolve_return_window(
    points: list[dict[str, Any]],
    *,
    requested_start_date: date,
    requested_end_date: date,
    anchor_mode: AnchorMode = "on_or_before",
) -> ReturnWindow | None:
    if requested_start_date > requested_end_date:
        return None

    normalized_by_date: dict[date, dict[str, Any]] = {}
    for raw_point in points:
        point_date = raw_point.get("as_of_date")
        raw_value = raw_point.get("value")
        if not isinstance(point_date, date) or isinstance(raw_value, bool):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not isfinite(value) or value <= 0:
            continue
        normalized_by_date[point_date] = {**raw_point, "value": value}

    ordered = [normalized_by_date[key] for key in sorted(normalized_by_date)]
    end_index = next(
        (
            index
            for index in range(len(ordered) - 1, -1, -1)
            if ordered[index]["as_of_date"] <= requested_end_date
        ),
        -1,
    )
    if end_index < 0:
        return None

    def is_anchor_candidate(point: dict[str, Any]) -> bool:
        point_date = point["as_of_date"]
        if anchor_mode == "strictly_before":
            return point_date < requested_start_date
        return point_date <= requested_start_date

    anchor_index = next(
        (
            index
            for index in range(end_index, -1, -1)
            if is_anchor_candidate(ordered[index])
        ),
        -1,
    )
    if anchor_index < 0 or anchor_index >= end_index:
        return None

    window_points = tuple(ordered[anchor_index : end_index + 1])
    # Separately rebased total-return segments are not one economic path.
    if not same_return_segment(window_points):
        return None
    return ReturnWindow(
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        anchor_mode=anchor_mode,
        anchor_date=window_points[0]["as_of_date"],
        end_date=window_points[-1]["as_of_date"],
        points=window_points,
    )


def period_return_percent(window: ReturnWindow) -> float:
    first_value = float(window.points[0]["value"])
    last_value = float(window.points[-1]["value"])
    return (last_value / first_value - 1) * 100


def annualized_return_percent(window: ReturnWindow) -> float | None:
    years = _actual_year_fraction(window.anchor_date, window.end_date)
    if window.elapsed_days <= 0 or years < 1:
        return None
    first_value = float(window.points[0]["value"])
    last_value = float(window.points[-1]["value"])
    return (pow(last_value / first_value, 1 / years) - 1) * 100


def normalized_return_points(window: ReturnWindow) -> list[dict[str, object]]:
    first_value = float(window.points[0]["value"])
    return [
        {
            "date": point["as_of_date"].isoformat(),
            "value": (float(point["value"]) / first_value - 1) * 100,
        }
        for point in window.points
    ]


def return_window_metadata(window: ReturnWindow) -> dict[str, object]:
    return {
        "policy_version": RETURN_WINDOW_POLICY_VERSION,
        "anchor_mode": window.anchor_mode,
        "requested_start_date": window.requested_start_date.isoformat(),
        "requested_end_date": window.requested_end_date.isoformat(),
        "anchor_date": window.anchor_date.isoformat(),
        "end_date": window.end_date.isoformat(),
    }
