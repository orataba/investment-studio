from __future__ import annotations

from datetime import date
from typing import Literal

from portfolio_app.services.market_data import (
    analytical_return_quote_bases,
    resolve_quote_series,
)

CalculationFrequency = Literal["daily"]


def calculation_frequency_profile(
    *,
    instrument_count: int,
) -> dict[str, object]:
    return {
        "requested_frequency": "daily",
        "resolved_frequency": "daily",
        "default_frequency": "daily",
        "source_frequency_counts": {"daily": instrument_count},
        "options": [
            {
                "frequency": "daily",
                "label": "Daily",
                "available": True,
                "reason": None,
            }
        ],
        "status_label": "Daily risk basis",
    }


def period_end_date(observation_date: date, frequency: CalculationFrequency, *, final_date: date | None = None) -> date:
    del frequency
    period_end = observation_date
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
        candidate_bases=analytical_return_quote_bases(detail),
        end_date=end_date,
    )
    if not resolution.available:
        return []
    return [
        point_date
        for point in resolution.points
        if isinstance((point_date := point.get("as_of_date")), date)
    ]
