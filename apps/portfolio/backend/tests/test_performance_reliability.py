from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from portfolio_app.api.contracts import PerformanceSummary
from portfolio_app.api.routes import performance as performance_routes
from portfolio_app.services.performance import PerformanceDataIntegrityError
from portfolio_app.services.performance_reliability import (
    ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM,
    HISTORY_WINDOW_UNAVAILABLE,
    MIN_ANNUALIZED_RETURN_HISTORY_DAYS,
    build_performance_history_reliability,
)


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


@pytest.mark.parametrize(
    (
        "start_date",
        "end_date",
        "eligible",
        "expected_annualized_twr",
        "expected_irr",
        "expected_mwror",
        "expected_calmar_ratio",
    ),
    [
        pytest.param(
            date(2026, 4, 14),
            date(2026, 4, 15),
            False,
            None,
            None,
            None,
            None,
            id="short-history-withheld",
        ),
        pytest.param(
            date(2025, 4, 15),
            date(2026, 4, 15),
            True,
            0.12,
            0.11,
            0.11,
            0.6,
            id="365-day-boundary-published",
        ),
    ],
)
def test_performance_api_gates_annualized_summary_metrics_by_observed_history(
    client,
    monkeypatch,
    start_date,
    end_date,
    eligible,
    expected_annualized_twr,
    expected_irr,
    expected_mwror,
    expected_calmar_ratio,
) -> None:
    portfolio_id = "performance-history-gate"
    monkeypatch.setattr(
        performance_routes,
        "get_portfolio",
        lambda _portfolio_id: {"portfolio_id": portfolio_id},
    )
    monkeypatch.setattr(
        performance_routes,
        "get_cached_materialized_performance_report",
        lambda *_args, **_kwargs: {
            "base_currency": "USD",
            "valuation_timezone": "UTC",
            "valuation_cutoff_policy": "latest_complete_eod",
            "summary": {
                "start_date": start_date,
                "end_date": end_date,
                "nav_coverage_state": "complete",
                "nav_coverage_reason_codes": [],
                "book_pnl_coverage_state": "complete",
                "book_pnl_coverage_reason_codes": [],
                "twr_state": "linked",
                "twr_reliability_status": "reliable",
                "twr_reliability_reasons": [],
                "snapshot_count": 2,
                "return_observation_count": 2,
                "risk_return_observation_count": 2,
                "cumulative_twr": 0.05,
                "annualized_twr": 0.12,
                "irr": 0.11,
                "mwror": 0.11,
                "max_drawdown": -0.20,
            },
            "daily_series": [],
        },
    )

    response = client.get(f"/api/portfolios/{portfolio_id}/performance")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["cumulative_twr"] == pytest.approx(0.05)
    assert summary["max_drawdown"] == pytest.approx(-0.20)
    for field_name, expected in (
        ("annualized_twr", expected_annualized_twr),
        ("irr", expected_irr),
        ("mwror", expected_mwror),
        ("calmar_ratio", expected_calmar_ratio),
    ):
        if expected is None:
            assert summary[field_name] is None
        else:
            assert summary[field_name] == pytest.approx(expected)
    reliability = summary["history_reliability"]
    assert reliability["start_date"] == start_date.isoformat()
    assert reliability["end_date"] == end_date.isoformat()
    assert reliability["elapsed_days"] == (end_date - start_date).days
    assert reliability["minimum_history_days"] == 365
    assert reliability["annualized_return_eligible"] is eligible
    assert reliability["annualized_return_reason_codes"] == (
        [] if eligible else [ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM]
    )


def test_performance_summary_contract_rejects_short_history_metric_leakage() -> None:
    with pytest.raises(
        ValidationError,
        match="requires null annualized TWR",
    ):
        PerformanceSummary.model_validate(
            {
                "start_date": date(2026, 4, 14),
                "end_date": date(2026, 4, 15),
                "nav_coverage_state": "complete",
                "nav_coverage_reason_codes": [],
                "book_pnl_coverage_state": "complete",
                "book_pnl_coverage_reason_codes": [],
                "twr_state": "linked",
                "twr_reliability_status": "reliable",
                "twr_reliability_reasons": [],
                "history_reliability": {
                    "start_date": date(2026, 4, 14),
                    "end_date": date(2026, 4, 15),
                    "elapsed_days": 1,
                    "calendar_span_days": 2,
                    "minimum_history_days": 365,
                    "annualized_return_eligible": False,
                    "annualized_return_reason_codes": [
                        ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM
                    ],
                    "sample_label": "short history",
                    "annualization_message": "Annualized metrics withheld.",
                },
                "snapshot_count": 2,
                "return_observation_count": 2,
                "risk_return_observation_count": 2,
                "annualized_twr": 0.12,
                "calmar_ratio": None,
            }
        )


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


def test_malformed_summary_facts_raise_data_integrity_error() -> None:
    with pytest.raises(
        PerformanceDataIntegrityError,
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
        PerformanceDataIntegrityError,
        match="snapshot_count must be a non-negative integer",
    ):
        build_performance_history_reliability(malformed)
