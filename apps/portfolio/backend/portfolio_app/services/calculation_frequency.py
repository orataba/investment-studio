from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, timedelta
from typing import Literal

from portfolio_app.services.market_data import quote_policy_bases, resolve_quote_series

CalculationFrequency = Literal["daily", "weekly", "monthly"]
RequestedCalculationFrequency = Literal["auto", "daily", "weekly", "monthly"]

CALCULATION_FREQUENCIES: tuple[CalculationFrequency, ...] = ("daily", "weekly", "monthly")
CALCULATION_FREQUENCY_LABELS: dict[CalculationFrequency, str] = {
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
}
_FREQUENCY_RANK: dict[CalculationFrequency, int] = {"daily": 0, "weekly": 1, "monthly": 2}


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
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def normalize_requested_frequency(value: object) -> RequestedCalculationFrequency:
    normalized = str(value or "auto").strip().lower().replace("-", "_")
    if normalized in {"auto", "daily", "weekly", "monthly"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("Calculation frequency must be auto, daily, weekly, or monthly.")


def infer_observation_frequency(dates: list[date]) -> CalculationFrequency:
    ordered_dates = sorted(set(item for item in dates if isinstance(item, date)))
    if len(ordered_dates) < 2:
        return "daily"
    gaps = [
        (ordered_dates[index] - ordered_dates[index - 1]).days
        for index in range(1, len(ordered_dates))
        if (ordered_dates[index] - ordered_dates[index - 1]).days > 0
    ]
    if not gaps:
        return "daily"

    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    daily_like_count = len([gap for gap in gaps if gap <= 3])
    weekly_like_count = len([gap for gap in gaps if 4 <= gap <= 10])
    monthly_like_count = len([gap for gap in gaps if 18 <= gap <= 45])

    if len(gaps) < 5:
        if daily_like_count:
            return "daily"
        if weekly_like_count == len(gaps):
            return "weekly"
        if monthly_like_count == len(gaps):
            return "monthly"
        return "daily"

    if median_gap <= 3:
        return "daily"
    if median_gap <= 10:
        return "weekly"
    if daily_like_count and monthly_like_count < len(gaps) * 0.6:
        return "daily"
    return "monthly"


def default_calculation_frequency(source_frequencies: list[CalculationFrequency]) -> CalculationFrequency:
    if not source_frequencies:
        return "daily"
    return max(source_frequencies, key=lambda item: _FREQUENCY_RANK[item])


def available_calculation_frequencies(source_frequencies: list[CalculationFrequency]) -> list[CalculationFrequency]:
    default_frequency = default_calculation_frequency(source_frequencies)
    minimum_rank = _FREQUENCY_RANK[default_frequency]
    return [item for item in CALCULATION_FREQUENCIES if _FREQUENCY_RANK[item] >= minimum_rank]


def resolve_calculation_frequency(
    requested_frequency: object,
    source_frequencies: list[CalculationFrequency],
) -> CalculationFrequency:
    requested = normalize_requested_frequency(requested_frequency)
    if requested == "auto":
        return default_calculation_frequency(source_frequencies)
    available = set(available_calculation_frequencies(source_frequencies))
    if requested not in available:
        default_frequency = default_calculation_frequency(source_frequencies)
        raise ValueError(
            f"{CALCULATION_FREQUENCY_LABELS[requested]} calculation is unavailable because the selected data "
            f"requires at least {CALCULATION_FREQUENCY_LABELS[default_frequency].lower()} alignment."
        )
    return requested


def calculation_frequency_profile(
    *,
    requested_frequency: object,
    source_frequencies: list[CalculationFrequency],
) -> dict[str, object]:
    requested = normalize_requested_frequency(requested_frequency)
    default_frequency = default_calculation_frequency(source_frequencies)
    available = set(available_calculation_frequencies(source_frequencies))
    resolved = resolve_calculation_frequency(requested, source_frequencies)
    counts = Counter(source_frequencies)
    mixed_sources = len([count for count in counts.values() if count > 0]) > 1
    mix_label = (
        "mixed daily/weekly data"
        if counts.get("daily", 0) and counts.get("weekly", 0) and not counts.get("monthly", 0)
        else "mixed frequencies"
        if mixed_sources
        else f"{CALCULATION_FREQUENCY_LABELS[default_frequency].lower()} data"
    )
    options = []
    for frequency in CALCULATION_FREQUENCIES:
        available_flag = frequency in available
        reason = None
        if not available_flag:
            reason = (
                f"Unavailable because the selected universe includes "
                f"{CALCULATION_FREQUENCY_LABELS[default_frequency].lower()} data."
            )
        options.append(
            {
                "frequency": frequency,
                "label": CALCULATION_FREQUENCY_LABELS[frequency],
                "available": available_flag,
                "reason": reason,
            }
        )
    return {
        "requested_frequency": requested,
        "resolved_frequency": resolved,
        "default_frequency": default_frequency,
        "source_frequency_counts": {frequency: int(counts.get(frequency, 0)) for frequency in CALCULATION_FREQUENCIES},
        "options": options,
        "status_label": f"{CALCULATION_FREQUENCY_LABELS[resolved]} risk basis - {mix_label}",
    }


def period_end_date(observation_date: date, frequency: CalculationFrequency, *, final_date: date | None = None) -> date:
    if frequency == "daily":
        period_end = observation_date
    elif frequency == "weekly":
        weekday = observation_date.weekday()
        period_end = observation_date + timedelta(days=4 - weekday)
        if weekday > 4:
            period_end = observation_date - timedelta(days=weekday - 4)
    else:
        period_end = date(
            observation_date.year,
            observation_date.month,
            monthrange(observation_date.year, observation_date.month)[1],
        )
    if final_date is not None and period_end > final_date:
        return final_date
    return period_end


def selected_observation_dates_from_detail(
    detail: dict[str, object],
    *,
    end_date: date,
) -> list[date]:
    resolution = resolve_quote_series(
        detail,
        candidate_bases=quote_policy_bases(
            detail,
            ("total_return", "chart", "valuation", "reference"),
        ),
        end_date=end_date,
    )
    if not resolution.available:
        return []
    return [
        point_date
        for point in resolution.points
        if isinstance((point_date := point.get("as_of_date")), date)
    ]
