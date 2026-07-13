from __future__ import annotations

from datetime import date, timedelta

from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
    infer_observation_frequency,
)
from portfolio_app.services.instrument_charts import (
    CanonicalInstrumentMarketData,
    canonical_series_failure_reasons,
    canonical_series_points,
    lock_instrument_market_data,
)


def _normalized_instrument_ids(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> list[str]:
    return sorted(
        {
            normalized
            for raw_instrument_id in instrument_ids
            if (normalized := str(raw_instrument_id or "").strip())
        }
    )


def calculation_frequency_profile_for_instruments(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    end_date: date,
    requested_frequency: object = "auto",
    lookback_days: int = 366,
    market_data: CanonicalInstrumentMarketData | None = None,
) -> dict[str, object]:
    """Resolve risk frequency strictly from canonical total-return observations."""

    normalized_instrument_ids = _normalized_instrument_ids(instrument_ids)
    locked_market_data = market_data or lock_instrument_market_data(
        normalized_instrument_ids,
        as_of_date=end_date,
        roles=("total_return",),
    )
    if locked_market_data.as_of_date != end_date:
        raise ValueError("Risk basis requires a canonical lock at the requested end date.")
    if "total_return" not in locked_market_data.windows_by_role:
        raise ValueError("Risk basis requires a locked canonical total_return role.")

    start_date = end_date - timedelta(days=max(lookback_days, 1))
    source_frequencies: list[CalculationFrequency] = []
    source_frequency_by_instrument: dict[str, CalculationFrequency] = {}
    unavailable_reason_by_instrument: dict[str, list[str]] = {}
    quote_dependencies: list[dict[str, object]] = []
    for instrument_id in normalized_instrument_ids:
        dates = sorted(
            {
                point_date
                for point in canonical_series_points(
                    locked_market_data,
                    instrument_id=instrument_id,
                    role="total_return",
                )
                if isinstance((point_date := point.get("date")), date)
                and start_date <= point_date <= end_date
            }
        )
        window = locked_market_data.windows_by_role["total_return"].get(
            instrument_id
        )
        if window is not None:
            quote_dependencies.append(
                window.calculation_dependency.model_dump(mode="json")
            )
        if len(dates) < 2:
            reasons = canonical_series_failure_reasons(
                locked_market_data,
                instrument_id=instrument_id,
                role="total_return",
            )
            if not reasons:
                reasons = ["insufficient_frequency_observations"]
            unavailable_reason_by_instrument[instrument_id] = reasons
            continue
        frequency = infer_observation_frequency(dates)
        source_frequencies.append(frequency)
        source_frequency_by_instrument[instrument_id] = frequency

    profile = calculation_frequency_profile(
        requested_frequency=requested_frequency,
        source_frequencies=source_frequencies,
    )
    unavailable_ids = sorted(unavailable_reason_by_instrument)
    coverage_status = "complete" if not unavailable_ids else "incomplete"
    if unavailable_ids:
        profile["status_label"] = (
            "Risk basis unavailable - canonical total-return coverage is incomplete"
        )
    profile.update(
        {
            "coverage_status": coverage_status,
            "market_data_role": "total_return",
            "market_data_as_of_date": end_date.isoformat(),
            "instrument_ids": normalized_instrument_ids,
            "source_frequency_by_instrument": source_frequency_by_instrument,
            "unavailable_instrument_ids": unavailable_ids,
            "unavailable_reason_by_instrument": unavailable_reason_by_instrument,
            "quote_dependencies": quote_dependencies,
        }
    )
    return profile


__all__ = ["calculation_frequency_profile_for_instruments"]
