from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
    infer_observation_frequency,
    selected_observation_dates_from_detail,
)
from portfolio_app.services.instrument_registry import get_registry_instrument_detail


def calculation_frequency_profile_for_instruments(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    end_date: date,
    requested_frequency: object = "auto",
    lookback_days: int = 366,
    detail_loader: Callable[[str], dict[str, object] | None] = get_registry_instrument_detail,
) -> dict[str, object]:
    start_date = end_date - timedelta(days=max(lookback_days, 1))
    normalized_instrument_ids = []
    for raw_instrument_id in instrument_ids:
        instrument_id = str(raw_instrument_id or "").strip()
        if instrument_id and instrument_id not in normalized_instrument_ids:
            normalized_instrument_ids.append(instrument_id)

    source_frequencies: list[CalculationFrequency] = []
    source_frequency_by_instrument: dict[str, CalculationFrequency] = {}
    missing_instrument_ids: list[str] = []
    insufficient_history_instrument_ids: list[str] = []
    for instrument_id in normalized_instrument_ids:
        detail = detail_loader(instrument_id)
        if not isinstance(detail, dict):
            missing_instrument_ids.append(instrument_id)
            continue
        dates = [
            point_date
            for point_date in selected_observation_dates_from_detail(detail, end_date=end_date)
            if start_date <= point_date <= end_date
        ]
        if len(set(dates)) < 2:
            insufficient_history_instrument_ids.append(instrument_id)
            continue
        frequency = infer_observation_frequency(dates)
        source_frequencies.append(frequency)
        source_frequency_by_instrument[instrument_id] = frequency

    profile = calculation_frequency_profile(
        requested_frequency=requested_frequency,
        source_frequencies=source_frequencies,
    )
    profile["instrument_ids"] = normalized_instrument_ids
    profile["source_frequency_by_instrument"] = source_frequency_by_instrument
    profile["requested_instrument_count"] = len(normalized_instrument_ids)
    profile["resolved_instrument_count"] = len(source_frequency_by_instrument)
    profile["missing_instrument_ids"] = missing_instrument_ids
    profile["insufficient_history_instrument_ids"] = (
        insufficient_history_instrument_ids
    )
    if (
        normalized_instrument_ids
        and len(source_frequency_by_instrument) == len(normalized_instrument_ids)
    ):
        profile["coverage_state"] = "complete"
    elif source_frequency_by_instrument:
        profile["coverage_state"] = "partial"
        profile["status_label"] = (
            f"Risk basis partial - {len(source_frequency_by_instrument)}/"
            f"{len(normalized_instrument_ids)} instruments resolved"
        )
    else:
        profile["coverage_state"] = "unavailable"
        profile["status_label"] = (
            "Risk basis unavailable - no active non-cash instruments"
            if not normalized_instrument_ids
            else "Risk basis unavailable - instrument return history missing"
        )
    return profile
