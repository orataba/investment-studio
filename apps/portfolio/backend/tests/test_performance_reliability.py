from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services.performance_reliability import (
    ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM,
    HISTORY_WINDOW_UNAVAILABLE,
    MIN_ANNUALIZED_RETURN_HISTORY_DAYS,
    PerformanceReliabilityError,
    build_performance_history_reliability,
)


pytestmark = pytest.mark.no_database


def _summary(
    *,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, object]:
    return {
        "start_date": start_date,
        "end_date": end_date,
        "snapshot_count": 101,
        "return_observation_count": 101,
        "risk_return_observation_count": 68,
    }


def test_short_history_is_explicitly_ineligible() -> None:
    reliability = build_performance_history_reliability(
        _summary(
            start_date=date(2026, 3, 31),
            end_date=date(2026, 7, 9),
        )
    )

    assert MIN_ANNUALIZED_RETURN_HISTORY_DAYS == 365
    assert reliability["elapsed_days"] == 100
    assert reliability["calendar_span_days"] == 101
    assert reliability["annualized_return_eligible"] is False
    assert reliability["annualized_return_reason_codes"] == [
        ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM
    ]
    assert "101 snapshots" in str(reliability["sample_label"])
    assert "68 risk observations" in str(reliability["sample_label"])
    assert "Period TWR remains the primary return" in str(
        reliability["annualization_message"]
    )


def test_full_year_history_is_eligible_at_the_exact_boundary() -> None:
    reliability = build_performance_history_reliability(
        _summary(
            start_date=date(2025, 7, 9),
            end_date=date(2026, 7, 9),
        )
    )

    assert reliability["elapsed_days"] == 365
    assert reliability["annualized_return_eligible"] is True
    assert reliability["annualized_return_reason_codes"] == []
    assert reliability["annualization_message"] is None


def test_missing_history_window_fails_closed_with_a_reason() -> None:
    reliability = build_performance_history_reliability(
        _summary(start_date=None, end_date=None)
    )

    assert reliability["elapsed_days"] is None
    assert reliability["calendar_span_days"] is None
    assert reliability["annualized_return_eligible"] is False
    assert reliability["annualized_return_reason_codes"] == [
        HISTORY_WINDOW_UNAVAILABLE
    ]
    assert str(reliability["sample_label"]).startswith(
        "Observed period unavailable"
    )


def test_malformed_summary_facts_raise_reliability_error() -> None:
    with pytest.raises(
        PerformanceReliabilityError,
        match="end_date must be on or after start_date",
    ):
        build_performance_history_reliability(
            _summary(
                start_date=date(2026, 7, 10),
                end_date=date(2026, 7, 9),
            )
        )

    malformed = _summary(
        start_date=date(2026, 7, 9),
        end_date=date(2026, 7, 10),
    )
    malformed["snapshot_count"] = True
    with pytest.raises(
        PerformanceReliabilityError,
        match="snapshot_count must be a non-negative integer",
    ):
        build_performance_history_reliability(malformed)
