from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from portfolio_app.services.calculation_frequency import (
    calculation_frequency_profile,
    selected_observation_dates_from_detail,
)
from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.market_data import market_calendar_sessions as _market_calendar_sessions


_EXPECTED_MAX_GAP_DAYS = 4


def _source_schedule(
    source_settings: object,
) -> tuple[str | None, bool]:
    if not isinstance(source_settings, dict):
        return None, False
    event_driven = (
        str(source_settings.get("expected_frequency") or "").strip().lower()
        == "event_driven"
    )
    market_calendar = str(source_settings.get("market_calendar") or "").strip() or None
    return market_calendar, event_driven


def _missing_observation_dates(
    dates: list[date],
    *,
    market_calendar: str | None,
) -> tuple[list[date], str]:
    ordered_dates = sorted(set(dates))
    if len(ordered_dates) < 2:
        return [], "insufficient_history"
    if market_calendar:
        sessions = _market_calendar_sessions(
            market_calendar,
            ordered_dates[0],
            ordered_dates[-1],
        )
        if sessions is not None:
            actual_dates = set(ordered_dates)
            return (
                [
                    session_date
                    for session_date in sessions
                    if session_date not in actual_dates
                ],
                f"market_calendar:{market_calendar}",
            )
    missing_dates: list[date] = []
    for index in range(1, len(ordered_dates)):
        previous_date = ordered_dates[index - 1]
        point_date = ordered_dates[index]
        if (point_date - previous_date).days > _EXPECTED_MAX_GAP_DAYS:
            missing_dates.append(point_date)
    return missing_dates, "calendar_day_threshold"


def observation_coverage_from_dates(
    dates: list[date], *, source_settings: object,
) -> dict[str, object]:
    """Describe the exact selected history, without clipping or sampling gaps.

    This metadata is evaluated against each requested risk window downstream.
    Dates outside that window are historical diagnostics, not a global veto.
    """
    ordered_dates = sorted(set(dates))
    calendar, event_driven = _source_schedule(source_settings)
    if event_driven:
        missing_dates, basis = [], "event_driven"
    else:
        missing_dates, basis = _missing_observation_dates(
            ordered_dates, market_calendar=calendar,
        )
    return {
        "start_date": ordered_dates[0].isoformat() if ordered_dates else None,
        "end_date": ordered_dates[-1].isoformat() if ordered_dates else None,
        "gap_dates": [item.isoformat() for item in missing_dates],
        "gap_detection_basis": basis,
    }


def calculation_frequency_profile_for_instruments(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    end_date: date,
    lookback_days: int = 366,
    detail_loader: Callable[[str], dict[str, object] | None] = get_registry_instrument_detail,
) -> dict[str, object]:
    normalized_instrument_ids = _normalized_instrument_ids(instrument_ids)
    observation_dates_by_instrument: dict[str, list[date]] = {}
    source_settings_by_instrument: dict[str, object] = {}
    for instrument_id in normalized_instrument_ids:
        detail = detail_loader(instrument_id)
        if isinstance(detail, dict):
            observation_dates_by_instrument[instrument_id] = (
                selected_observation_dates_from_detail(detail, end_date=end_date)
            )
            source_settings_by_instrument[instrument_id] = detail.get("source_settings")
    return calculation_frequency_profile_from_observation_dates(
        normalized_instrument_ids,
        observation_dates_by_instrument=observation_dates_by_instrument,
        source_settings_by_instrument=source_settings_by_instrument,
        end_date=end_date,
        lookback_days=lookback_days,
    )


def _normalized_instrument_ids(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> list[str]:
    normalized_instrument_ids = []
    for raw_instrument_id in instrument_ids:
        instrument_id = str(raw_instrument_id or "").strip()
        if instrument_id and instrument_id not in normalized_instrument_ids:
            normalized_instrument_ids.append(instrument_id)
    return normalized_instrument_ids


def calculation_frequency_profile_from_observation_dates(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    observation_dates_by_instrument: dict[str, list[date]],
    source_settings_by_instrument: dict[str, object],
    end_date: date,
    lookback_days: int = 366,
) -> dict[str, object]:
    """Build risk coverage from resolved dates; absent instruments stay missing."""
    start_date = end_date - timedelta(days=max(lookback_days, 1))
    normalized_instrument_ids = _normalized_instrument_ids(instrument_ids)

    source_frequency_by_instrument: dict[str, str] = {}
    missing_instrument_ids: list[str] = []
    insufficient_history_instrument_ids: list[str] = []
    gap_instrument_ids: list[str] = []
    gap_details: list[dict[str, object]] = []
    for instrument_id in normalized_instrument_ids:
        if instrument_id not in observation_dates_by_instrument:
            missing_instrument_ids.append(instrument_id)
            continue
        dates = [
            point_date
            for point_date in observation_dates_by_instrument[instrument_id]
            if start_date <= point_date <= end_date
        ]
        if len(set(dates)) < 2:
            insufficient_history_instrument_ids.append(instrument_id)
            continue
        market_calendar, event_driven = _source_schedule(
            source_settings_by_instrument.get(instrument_id)
        )
        source_frequency_by_instrument[instrument_id] = "daily"
        if event_driven:
            continue
        missing_dates, detection_basis = _missing_observation_dates(
            dates,
            market_calendar=market_calendar,
        )
        if missing_dates:
            gap_instrument_ids.append(instrument_id)
            gap_details.append(
                {
                    "instrument_id": instrument_id,
                    "gap_count": len(missing_dates),
                    "gap_detection_basis": detection_basis,
                    "gap_date_sample": [
                        point_date.isoformat()
                        for point_date in (
                            missing_dates
                            if len(missing_dates) <= 20
                            else [*missing_dates[:10], *missing_dates[-10:]]
                        )
                    ],
                }
            )

    profile = calculation_frequency_profile(
        instrument_count=len(source_frequency_by_instrument),
    )
    profile["instrument_ids"] = normalized_instrument_ids
    profile["window_start_date"] = start_date.isoformat()
    profile["window_end_date"] = end_date.isoformat()
    profile["source_frequency_by_instrument"] = source_frequency_by_instrument
    profile["requested_instrument_count"] = len(normalized_instrument_ids)
    profile["resolved_instrument_count"] = len(source_frequency_by_instrument)
    profile["missing_instrument_ids"] = missing_instrument_ids
    profile["insufficient_history_instrument_ids"] = (
        insufficient_history_instrument_ids
    )
    profile["gap_instrument_ids"] = gap_instrument_ids
    profile["gap_count"] = sum(
        int(detail.get("gap_count") or 0)
        for detail in gap_details
    )
    profile["gap_details"] = gap_details
    if (
        normalized_instrument_ids
        and len(source_frequency_by_instrument) == len(normalized_instrument_ids)
        and not gap_instrument_ids
    ):
        profile["coverage_state"] = "complete"
    elif source_frequency_by_instrument:
        profile["coverage_state"] = "partial"
        profile["status_label"] = (
            f"Risk basis partial - {len(gap_instrument_ids)} instrument(s) have "
            f"observation gaps"
            if gap_instrument_ids
            else (
                f"Risk basis partial - {len(source_frequency_by_instrument)}/"
                f"{len(normalized_instrument_ids)} instruments resolved"
            )
        )
    else:
        profile["coverage_state"] = "unavailable"
        profile["status_label"] = (
            "Risk basis unavailable - no active non-cash instruments"
            if not normalized_instrument_ids
            else "Risk basis unavailable - instrument return history missing"
        )
    return profile
