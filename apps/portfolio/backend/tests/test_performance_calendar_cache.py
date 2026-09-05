from __future__ import annotations

from copy import deepcopy
from datetime import date

from portfolio_app.services import performance, workspace_cache


def test_return_calendar_reuses_the_bounded_cached_performance_window(
    client,
    monkeypatch,
) -> None:
    snapshots_response = client.get("/api/portfolios/investment-studio/snapshots/daily")
    assert snapshots_response.status_code == 200

    performance_response = client.get("/api/portfolios/investment-studio/performance")
    assert performance_response.status_code == 200

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("calendar must reuse the cached materialized Performance window")

    monkeypatch.setattr(performance, "build_daily_portfolio_snapshots", fail_rebuild)
    monkeypatch.setattr(workspace_cache, "build_materialized_performance_report", fail_rebuild)

    calendar_response = client.get(
        "/api/portfolios/investment-studio/performance/calendar?frequency=monthly"
    )
    assert calendar_response.status_code == 200
    weekly_response = client.get(
        "/api/portfolios/investment-studio/performance/calendar?frequency=weekly"
    )
    assert weekly_response.status_code == 200

    performance_summary = performance_response.json()["summary"]
    calendar_summary = calendar_response.json()["summary"]
    assert calendar_summary["end_date"] == performance_summary["effective_end_date"]
    assert weekly_response.json()["summary"]["end_date"] == performance_summary["effective_end_date"]


def test_calendar_bucket_rollup_is_result_equivalent_and_does_not_hide_partial_coverage(
    monkeypatch,
) -> None:
    source_report = {
        "portfolio_id": "calendar-equivalence-test",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "summary": {
            "requested_start_date": date(2026, 1, 1),
            "requested_end_date": date(2026, 1, 3),
            "effective_start_date": date(2026, 1, 1),
            "effective_end_date": date(2026, 1, 2),
            "as_of_clamp_reason": "requested_end_after_latest_reliable_endpoint",
        },
        "daily_series": [
            {
                "as_of_date": date(2026, 1, 1),
                "coverage_state": "complete",
                "valuation_coverage_state": "complete",
                "return_coverage_state": "complete",
                "beginning_nav": 100.0,
                "ending_nav": 100.0,
                "external_cash_in": 0.0,
                "external_cash_out": 0.0,
                "net_external_inflow": 0.0,
                "absolute_change": 0.0,
                "delta": 0.0,
                "daily_twr": 0.0,
            },
            {
                "as_of_date": date(2026, 1, 2),
                "coverage_state": "partial",
                "valuation_coverage_state": "complete",
                "return_coverage_state": "unavailable",
                "beginning_nav": 100.0,
                "ending_nav": 101.0,
                "external_cash_in": 0.0,
                "external_cash_out": 0.0,
                "net_external_inflow": 0.0,
                "absolute_change": None,
                "delta": None,
                "daily_twr": None,
            },
        ],
    }
    original_report = deepcopy(source_report)
    monkeypatch.setattr(
        performance,
        "build_portfolio_performance_report",
        lambda *_args, **_kwargs: source_report,
    )

    direct_build_result = performance.build_return_calendar_report(
        {"portfolio_id": "calendar-equivalence-test"},
        [],
        [],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        frequency="monthly",
    )
    cached_rollup_result = performance.build_return_calendar_report_from_performance_report(
        source_report,
        frequency="monthly",
    )

    assert cached_rollup_result == direct_build_result
    assert cached_rollup_result["summary"]["end_date"] == date(2026, 1, 2)
    assert cached_rollup_result["buckets"][0]["coverage_state"] == "partial"
    assert cached_rollup_result["buckets"][0]["cumulative_twr"] is None
    assert cached_rollup_result["buckets"][0]["absolute_change"] is None
    assert cached_rollup_result["buckets"][0]["delta"] is None
    assert source_report == original_report
