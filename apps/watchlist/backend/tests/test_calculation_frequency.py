from __future__ import annotations

from datetime import date, timedelta

import pytest

from watchlist_app.services.calculation_frequency import (
    build_calculation_frequency_context,
    infer_observation_frequency,
)


@pytest.mark.parametrize("dates", [[], [date(2026, 1, 2)]])
def test_frequency_inference_does_not_default_empty_or_single_point_series_to_daily(
    dates: list[date],
) -> None:
    assert infer_observation_frequency(dates) is None

    context = build_calculation_frequency_context(
        [
            {"as_of_date": observation_date, "value": 100.0}
            for observation_date in dates
        ]
    )

    assert context["profile"]["resolved_frequency"] is None
    assert context["profile"]["inferred_frequency"] is None
    assert context["profile"]["annualization_periods_per_year"] is None
    assert context["profile"]["gap_status"] == "unresolved"
    assert context["profile"]["source_frequency_counts"]["unknown"] == len(dates)


def test_frequency_inference_fails_closed_for_irregular_observation_gaps() -> None:
    gaps = [1, 7, 20, 2, 11, 30]
    dates = [date(2026, 1, 1)]
    for gap in gaps:
        dates.append(dates[-1] + timedelta(days=gap))

    assert infer_observation_frequency(dates) is None

    context = build_calculation_frequency_context(
        [
            {"as_of_date": observation_date, "value": 100.0 + index}
            for index, observation_date in enumerate(dates)
        ]
    )

    assert context["points"] == [
        {"as_of_date": observation_date, "value": 100.0 + index}
        for index, observation_date in enumerate(dates)
    ]
    assert context["profile"]["resolved_frequency"] is None
    assert context["profile"]["annualization_periods_per_year"] is None
    assert context["profile"]["gap_status"] == "unresolved"


@pytest.mark.parametrize(
    ("dates", "expected_frequency", "expected_annualization"),
    [
        (
            [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)],
            "daily",
            252.0,
        ),
        (
            [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19)],
            "weekly",
            52.0,
        ),
        (
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
            "monthly",
            12.0,
        ),
    ],
)
def test_stable_inferred_frequency_uses_fixed_annualization_factor(
    dates: list[date],
    expected_frequency: str,
    expected_annualization: float,
) -> None:
    context = build_calculation_frequency_context(
        [
            {"as_of_date": observation_date, "value": 100.0 + index}
            for index, observation_date in enumerate(dates)
        ]
    )

    assert context["profile"]["resolved_frequency"] == expected_frequency
    assert context["profile"]["inferred_frequency"] == expected_frequency
    assert context["profile"]["annualization_periods_per_year"] == expected_annualization


def test_declared_frequency_remains_separate_from_date_inference() -> None:
    points = [
        {
            "as_of_date": date(2026, 1, 5) + timedelta(days=index),
            "value": 100.0 + index,
            "frequency": "weekly",
        }
        for index in range(3)
    ]

    context = build_calculation_frequency_context(points)

    assert context["profile"]["resolved_frequency"] == "weekly"
    assert context["profile"]["inferred_frequency"] == "daily"
    assert context["profile"]["source_frequency_counts"] == {
        "daily": 0,
        "weekly": 3,
        "monthly": 0,
        "unknown": 0,
    }
    assert context["profile"]["annualization_periods_per_year"] is None
