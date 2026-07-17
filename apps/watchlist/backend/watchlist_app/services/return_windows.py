from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from typing import Any, Literal


RETURN_WINDOW_POLICY_VERSION = "return-window/v1"
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
    if window.elapsed_days <= 0:
        return None
    first_value = float(window.points[0]["value"])
    last_value = float(window.points[-1]["value"])
    return (pow(last_value / first_value, 365.25 / window.elapsed_days) - 1) * 100


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
