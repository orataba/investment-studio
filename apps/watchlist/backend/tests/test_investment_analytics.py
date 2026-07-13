from __future__ import annotations

from datetime import date, timedelta
import math
import statistics

import pytest
from pydantic import ValidationError

from watchlist_app.api.contracts import InvestmentAnalyticsResponse
from watchlist_app.services.investment_analytics import (
    build_investment_analytics_payload,
)


def _points(values: list[float], dates: list[date] | None = None) -> list[dict[str, object]]:
    observation_dates = dates or _monthly_dates(len(values))
    return [
        {"as_of_date": as_of_date, "value": value}
        for as_of_date, value in zip(observation_dates, values)
    ]


def _monthly_dates(
    count: int,
    *,
    start_year: int = 2025,
    start_month: int = 1,
    day: int = 28,
) -> list[date]:
    start_ordinal = start_year * 12 + start_month - 1
    return [
        date((start_ordinal + offset) // 12, (start_ordinal + offset) % 12 + 1, day)
        for offset in range(count)
    ]


def _values_from_returns(
    returns: list[float],
    *,
    initial_value: float = 100.0,
) -> list[float]:
    values = [initial_value]
    for periodic_return in returns:
        values.append(values[-1] * (1 + periodic_return))
    return values


def _quote_resolution_summary(instrument_id: str) -> dict[str, object]:
    end_date = date(2026, 1, 1)
    consumer_profile = {
        "profile": "periodic_fund_nav",
        "policy_version": "watchlist_quote_consumer.v1",
        "reason_code": "canonical_fund_periodic_publication_window",
        "canonical_instrument_type": "fund",
        "resolver_policy": {
            "policy_version": "canonical_quote_freshness.v1",
            "mode": "calendar_day_carry_forward",
            "max_age_days": 45,
        },
    }
    return {
        "schema_version": "watchlist_quote_resolution_summary.v1",
        "resolution_status": "unavailable",
        "resolver_strategy_version": "canonical_quote_resolver.v1",
        "instrument_id": instrument_id,
        "role": "total_return",
        "metric_family": None,
        "quote_basis": None,
        "currency": "USD",
        "range_mode": "since_inception",
        "start_date": None,
        "end_date": end_date,
        "quote_selection_policy_version": "quote_selection_policy.v1",
        "quote_selection_policy_revision": "test-policy-revision",
        "freshness_policy": consumer_profile["resolver_policy"],
        "quote_series_id": None,
        "start_boundary_observation": None,
        "start_anchor": None,
        "observation_count": 0,
        "adopted_point_count": 0,
        "first_observation_date": None,
        "last_observation_date": None,
        "coverage_status": "unavailable",
        "freshness_status": "missing",
        "ingestion_status": "unknown",
        "reliability_status": "unavailable",
        "reason_codes": ["missing_quote_series"],
        "calculation_dependency": {
            "resolver_strategy_version": "canonical_quote_resolver.v1",
            "freshness_policy_version": "canonical_quote_freshness.v1",
            "freshness_mode": "calendar_day_carry_forward",
            "max_age_days": 45,
            "range_mode": "since_inception",
            "start_date": None,
            "end_date": end_date,
            "quote_selection_policy_version": "quote_selection_policy.v1",
            "quote_selection_policy_revision": "test-policy-revision",
            "quote_series_id": None,
            "revision_count": 0,
            "excluded_revision_count": 0,
            "fingerprint": f"canonical:{instrument_id}",
        },
        "consumer_freshness_profile": consumer_profile,
        "consumer_dependency": {
            "dependency_kind": "watchlist_quote_consumer_dependency",
            "dependency_version": "v1",
            "canonical_dependency_fingerprint": f"canonical:{instrument_id}",
            "consumer_freshness_profile": consumer_profile,
            "fingerprint": f"consumer:{instrument_id}",
        },
    }


def _analytics(
    fund_points: list[dict[str, object]],
    benchmark_points: list[dict[str, object]] | None = None,
    benchmark_instrument_id: str | None = None,
) -> dict[str, object]:
    payload = build_investment_analytics_payload(
        fund_points,
        benchmark_points=benchmark_points,
        benchmark_instrument_id=benchmark_instrument_id,
        rolling_window_months=3,
    )
    payload["valuation_date"] = max(
        (
            point["as_of_date"]
            for point in [*fund_points, *(benchmark_points or [])]
            if isinstance(point.get("as_of_date"), date)
        ),
        default=date(2026, 1, 1),
    )
    payload["quote_resolutions"] = {
        "fund": _quote_resolution_summary("test-fund"),
        "benchmark": None,
    }
    return payload


def _period(payload: dict[str, object], period: str) -> dict[str, object]:
    return next(row for row in payload["periods"] if row["period"] == period)


@pytest.mark.parametrize(
    ("fund_points", "expected_reason", "expected_input_count"),
    [
        ([], "empty_series", 0),
        (_points([100.0]), "insufficient_observations", 1),
    ],
)
def test_empty_and_single_point_series_leave_metrics_unavailable(
    fund_points: list[dict[str, object]],
    expected_reason: str,
    expected_input_count: int,
) -> None:
    payload = _analytics(fund_points)

    assert payload["quality"]["fund_status"] == "unavailable"
    assert payload["quality"]["fund_reason"] == expected_reason
    assert payload["source_input_observation_count"] == expected_input_count
    assert payload["statistics"]["current_drawdown"] is None
    assert _period(payload, "SI")["fund"]["period_return"] is None
    assert _period(payload, "SI")["fund"]["max_drawdown"] is None


def test_missing_benchmark_does_not_create_relative_metrics() -> None:
    payload = _analytics(_points([100.0, 102.0, 103.0, 106.0]))

    assert payload["quality"]["fund_status"] == "available"
    assert payload["quality"]["benchmark_status"] == "not_requested"
    assert payload["benchmark_observation_count"] == 0
    assert _period(payload, "SI")["benchmark"] is None
    assert _period(payload, "SI")["relative"] is None
    assert payload["series"]["benchmark_drawdown"] == []
    assert payload["series"]["rolling_beta"] == []
    assert payload["quality"]["series"]["benchmark_rolling_annualized_volatility"] == {
        "status": "not_requested",
        "reason": "benchmark_not_requested",
        "observation_count": 0,
        "excluded_observation_count": 0,
        "used_window_count": 0,
        "excluded_window_count": 0,
    }
    assert payload["quality"]["series"]["benchmark_rolling_sharpe_ratio"]["status"] == (
        "not_requested"
    )


def test_zero_volatility_is_zero_but_ratios_with_zero_denominator_are_null() -> None:
    payload = _analytics(_points([100.0] * 13))
    since_inception_period = _period(payload, "SI")
    since_inception = since_inception_period["fund"]

    assert since_inception["period_return"] == pytest.approx(0.0)
    assert since_inception["annualized_volatility"] == pytest.approx(0.0)
    assert since_inception["annualized_downside_deviation"] == pytest.approx(0.0)
    assert since_inception["max_drawdown"] == pytest.approx(0.0)
    assert since_inception["sharpe_ratio"] is None
    assert since_inception["sortino_ratio"] is None
    assert since_inception["calmar_ratio"] is None
    assert since_inception_period["quality"]["fund"]["downside_deviation"]["status"] == "available"
    assert since_inception_period["quality"]["fund"]["annualized_volatility"]["status"] == "available"
    assert since_inception_period["quality"]["fund"]["sharpe_ratio"]["reason"] == "zero_return_variance"
    assert since_inception_period["quality"]["fund"]["sortino_ratio"]["reason"] == "zero_downside_deviation"


def test_non_synchronous_benchmark_dates_fail_closed() -> None:
    fund_dates = _monthly_dates(13, day=28)
    benchmark_dates = _monthly_dates(13, day=27)
    payload = _analytics(
        _points(_values_from_returns([0.01, -0.005] * 6), fund_dates),
        _points(_values_from_returns([0.008, 0.003] * 6), benchmark_dates),
        "benchmark",
    )
    since_inception = _period(payload, "SI")

    assert payload["quality"]["benchmark_status"] == "available"
    assert since_inception["benchmark"] is None
    assert since_inception["relative"] is None
    assert since_inception["quality"]["relative"]["alignment"]["status"] == "unavailable"
    assert since_inception["quality"]["relative"]["alignment"]["reason"] == "comparison_boundary_mismatch"
    assert payload["series"]["rolling_beta"] == []
    assert payload["quality"]["series"]["rolling_beta"]["reason"] == "comparison_dates_misaligned"


def test_relative_metrics_require_every_exact_comparison_date() -> None:
    fund_dates = _monthly_dates(13)
    benchmark_dates = [
        point_date
        for index, point_date in enumerate(fund_dates)
        if index != 6
    ]
    payload = _analytics(
        _points(_values_from_returns([0.01, -0.005] * 6), fund_dates),
        _points(
            _values_from_returns([0.008] * (len(benchmark_dates) - 1)),
            benchmark_dates,
        ),
        "benchmark",
    )
    since_inception = _period(payload, "SI")

    assert since_inception["benchmark"] is None
    assert since_inception["relative"] is None
    assert since_inception["quality"]["relative"]["alignment"]["reason"] == "comparison_frequency_mismatch"


def test_relative_quality_separates_excess_return_from_unavailable_risk_metrics() -> None:
    dates = _monthly_dates(4)
    payload = _analytics(
        _points([100, 102, 101, 105], dates),
        _points([100, 101, 102, 103], dates),
        "benchmark",
    )
    since_inception = _period(payload, "SI")

    assert since_inception["relative"]["excess_return"] is not None
    assert since_inception["relative"]["tracking_error"] is None
    assert since_inception["quality"]["relative"]["excess_return"]["status"] == "available"
    assert since_inception["quality"]["relative"]["tracking_error"]["status"] == "unavailable"
    assert since_inception["quality"]["relative"]["tracking_error"]["reason"] == "insufficient_history"


def test_capture_ratios_require_three_observations_in_each_regime() -> None:
    dates = _monthly_dates(13)
    fund_returns = [0.012] * 12
    benchmark_returns = [0.01] * 10 + [-0.005] * 2
    payload = _analytics(
        _points(_values_from_returns(fund_returns), dates),
        _points(_values_from_returns(benchmark_returns), dates),
        "benchmark",
    )
    since_inception = _period(payload, "SI")

    assert since_inception["relative"]["upside_capture"] is not None
    assert since_inception["relative"]["downside_capture"] is None
    assert since_inception["quality"]["relative"]["upside_capture"]["status"] == "available"
    assert since_inception["quality"]["relative"]["downside_capture"]["status"] == "unavailable"
    assert since_inception["quality"]["relative"]["downside_capture"]["reason"] == (
        "insufficient_regime_observations"
    )
    assert payload["methodology"]["sharpe_ratio"].endswith("risk_free_rate_zero")
    assert payload["methodology"]["sortino_ratio"].endswith("mar_zero")


def test_downside_deviation_uses_all_return_observations_in_denominator() -> None:
    periodic_returns = [0.01] * 11 + [-0.02]
    payload = _analytics(_points(_values_from_returns(periodic_returns)))
    since_inception = _period(payload, "SI")

    assert since_inception["fund"]["annualized_downside_deviation"] == pytest.approx(2.0)
    assert since_inception["fund"]["sortino_ratio"] == pytest.approx(4.5)
    assert since_inception["quality"]["fund"]["downside_deviation"]["status"] == "available"
    assert payload["methodology"]["downside_deviation"] == (
        "lower_partial_moment_mar_zero_all_observations"
    )


def test_short_windows_keep_period_return_but_fail_closed_annualized_metrics() -> None:
    short_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(13)]
    payload = _analytics(
        _points(_values_from_returns([0.001, -0.0005] * 6), short_dates)
    )
    since_inception = _period(payload, "SI")

    assert since_inception["fund"]["period_return"] is not None
    assert since_inception["fund"]["annualized_return"] is None
    assert since_inception["fund"]["calmar_ratio"] is None
    assert since_inception["fund"]["annualized_volatility"] is not None
    assert since_inception["quality"]["fund"]["annualized_return"]["reason"] == "insufficient_history"
    assert since_inception["quality"]["fund"]["calmar_ratio"]["reason"] == "insufficient_history"


def test_calmar_requires_three_year_history() -> None:
    one_year_returns = [0.01, -0.02] + [0.01] * 10
    one_year = _period(
        _analytics(_points(_values_from_returns(one_year_returns))),
        "SI",
    )
    assert one_year["fund"]["annualized_return"] is not None
    assert one_year["fund"]["calmar_ratio"] is None
    assert one_year["quality"]["fund"]["calmar_ratio"]["reason"] == "insufficient_history"

    three_year_returns = ([0.01, -0.02] + [0.01] * 10) * 3 + [0.01]
    three_year = _period(
        _analytics(_points(_values_from_returns(three_year_returns))),
        "SI",
    )
    assert three_year["fund"]["calmar_ratio"] is not None
    assert three_year["quality"]["fund"]["calmar_ratio"]["status"] == "available"


def test_risk_metrics_require_twelve_same_frequency_returns() -> None:
    insufficient = _period(
        _analytics(_points(_values_from_returns([0.01, -0.005] * 5 + [0.01]))),
        "SI",
    )
    assert insufficient["fund"]["annualized_volatility"] is None
    assert insufficient["fund"]["annualized_downside_deviation"] is None
    assert insufficient["fund"]["sharpe_ratio"] is None
    assert insufficient["quality"]["fund"]["downside_deviation"]["reason"] == "insufficient_history"

    dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(7)]
    dates += [date(2026, 1, 14) + timedelta(days=offset) for offset in range(7)]
    qualified = _period(
        _analytics(
            _points(
                _values_from_returns([0.01, -0.005] * 6 + [0.01]),
                dates,
            )
        ),
        "SI",
    )
    assert qualified["fund"]["annualized_volatility"] is not None
    assert qualified["quality"]["fund"]["downside_deviation"]["status"] == "qualified"
    assert qualified["quality"]["fund"]["downside_deviation"]["reason"] == (
        "off_frequency_observations_excluded"
    )


def test_rolling_beta_rejects_windows_with_missing_calendar_months() -> None:
    dates = [
        date(2025, 1, 28),
        date(2025, 2, 28),
        date(2025, 3, 28),
        date(2025, 5, 28),
        date(2025, 6, 28),
        date(2025, 7, 28),
    ]
    payload = _analytics(
        _points([100, 101, 99, 103, 104, 102], dates),
        _points([100, 100.5, 101, 102, 101.5, 103], dates),
        "benchmark",
    )

    assert payload["series"]["rolling_beta"] == []
    assert payload["quality"]["series"]["rolling_beta"]["status"] == "unavailable"
    assert payload["quality"]["series"]["rolling_beta"]["reason"] == (
        "non_contiguous_monthly_returns"
    )


def test_monthly_volatility_uses_fixed_factor_not_local_observation_density() -> None:
    periodic_returns = [0.01, -0.005] * 6
    january_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(13)]
    february_dates = [date(2026, 2, 1) + timedelta(days=2 * offset) for offset in range(13)]
    points = _points(_values_from_returns(periodic_returns), january_dates)
    points += _points(
        _values_from_returns(periodic_returns, initial_value=120.0),
        february_dates,
    )

    payload = _analytics(points)
    monthly_volatility = payload["series"]["monthly_annualized_volatility"]
    expected = statistics.stdev(periodic_returns) * math.sqrt(252.0) * 100

    assert len(monthly_volatility) == 2
    assert monthly_volatility[0]["value"] == pytest.approx(expected)
    assert monthly_volatility[1]["value"] == pytest.approx(expected)
    assert payload["methodology"]["monthly_volatility_annualization"] == (
        "fixed_same_frequency_annualization_252_52_12"
    )
    assert payload["quality"]["series"]["monthly_annualized_volatility"]["status"] == "available"


def test_monthly_volatility_excludes_frequency_drift_with_quality_reason() -> None:
    january_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(7)]
    january_dates += [date(2026, 1, 14) + timedelta(days=offset) for offset in range(7)]
    february_dates = [date(2026, 2, 1) + timedelta(days=offset) for offset in range(13)]
    january_points = _points(
        _values_from_returns([0.01, -0.005] * 6 + [0.01]),
        january_dates,
    )
    february_points = _points(
        _values_from_returns([0.01, -0.005] * 6, initial_value=120.0),
        february_dates,
    )

    qualified_january = _analytics(january_points)
    january_quality = qualified_january["quality"]["series"][
        "monthly_annualized_volatility"
    ]
    assert len(qualified_january["series"]["monthly_annualized_volatility"]) == 1
    assert january_quality["status"] == "qualified"
    assert january_quality["reason"] == "observation_frequency_drift_excluded"
    assert january_quality["observation_count"] == 12
    assert january_quality["excluded_observation_count"] == 1
    assert january_quality["used_window_count"] == 1
    assert january_quality["excluded_window_count"] == 0

    qualified = _analytics(january_points + february_points)
    assert len(qualified["series"]["monthly_annualized_volatility"]) == 2
    assert qualified["quality"]["series"]["monthly_annualized_volatility"]["status"] == "qualified"
    assert qualified["quality"]["series"]["monthly_annualized_volatility"]["reason"] == (
        "observation_frequency_drift_excluded"
    )


def test_irregular_frequency_fails_closed_across_snapshot_and_series_risk_metrics() -> None:
    dates = [date(2025, 1, 1)]
    for gap in [1, 7, 20, 2, 11, 30] * 6:
        dates.append(dates[-1] + timedelta(days=gap))
    periodic_returns = [0.01 if index % 2 == 0 else -0.005 for index in range(len(dates) - 1)]

    payload = _analytics(_points(_values_from_returns(periodic_returns), dates))
    since_inception = _period(payload, "SI")

    assert since_inception["fund"]["annualized_volatility"] is None
    assert since_inception["fund"]["sharpe_ratio"] is None
    assert since_inception["quality"]["fund"]["annualized_volatility"]["reason"] == (
        "observation_frequency_unresolved"
    )
    assert payload["series"]["monthly_annualized_volatility"] == []
    assert payload["quality"]["series"]["monthly_annualized_volatility"]["reason"] == (
        "observation_frequency_unresolved"
    )
    assert payload["series"]["rolling_annualized_volatility"] == []
    assert payload["quality"]["series"]["rolling_annualized_volatility"]["reason"] == (
        "observation_frequency_unresolved"
    )
    assert payload["quality"]["series"]["rolling_annualized_volatility"][
        "excluded_window_count"
    ] > 0


def test_rolling_risk_quality_reports_off_frequency_observations_and_window_counts() -> None:
    dates = [
        date(2025, 1, 1) + timedelta(days=offset)
        for offset in range(180)
        if offset not in range(110, 116)
    ]
    periodic_returns = [0.002 if index % 2 == 0 else -0.001 for index in range(len(dates) - 1)]

    payload = _analytics(_points(_values_from_returns(periodic_returns), dates))
    volatility_quality = payload["quality"]["series"]["rolling_annualized_volatility"]
    sharpe_quality = payload["quality"]["series"]["rolling_sharpe_ratio"]

    assert payload["series"]["rolling_annualized_volatility"]
    assert payload["series"]["rolling_sharpe_ratio"]
    assert volatility_quality["status"] == "qualified"
    assert volatility_quality["reason"] == "off_frequency_observations_excluded"
    assert volatility_quality["excluded_observation_count"] > 0
    assert volatility_quality["used_window_count"] == len(
        payload["series"]["rolling_annualized_volatility"]
    )
    assert sharpe_quality["status"] == "qualified"
    assert sharpe_quality["excluded_observation_count"] > 0
    assert sharpe_quality["used_window_count"] == len(payload["series"]["rolling_sharpe_ratio"])


def test_rolling_sharpe_zero_variance_windows_are_explicitly_unavailable() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(180)]
    payload = _analytics(_points([100.0] * len(dates), dates))

    volatility_quality = payload["quality"]["series"]["rolling_annualized_volatility"]
    sharpe_quality = payload["quality"]["series"]["rolling_sharpe_ratio"]

    assert payload["series"]["rolling_annualized_volatility"]
    assert volatility_quality["status"] == "available"
    assert volatility_quality["used_window_count"] > 0
    assert payload["series"]["rolling_sharpe_ratio"] == []
    assert sharpe_quality["status"] == "unavailable"
    assert sharpe_quality["reason"] == "zero_return_variance"
    assert sharpe_quality["used_window_count"] == 0
    assert sharpe_quality["excluded_window_count"] > 0


def test_fund_and_benchmark_rolling_quality_are_independent() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(180)]
    fund_returns = [0.002 if index % 2 == 0 else -0.001 for index in range(len(dates) - 1)]
    benchmark_returns = [0.0015 if index % 3 else -0.0005 for index in range(len(dates) - 1)]

    payload = _analytics(
        _points(_values_from_returns(fund_returns), dates),
        _points(_values_from_returns(benchmark_returns), dates),
        "benchmark",
    )

    assert payload["series"]["rolling_annualized_volatility"]
    assert payload["series"]["benchmark_rolling_annualized_volatility"]
    assert payload["quality"]["series"]["rolling_annualized_volatility"]["status"] == (
        "available"
    )
    assert payload["quality"]["series"]["benchmark_rolling_annualized_volatility"][
        "status"
    ] == "available"
    assert payload["quality"]["series"]["rolling_annualized_volatility"] is not payload[
        "quality"
    ]["series"]["benchmark_rolling_annualized_volatility"]


@pytest.mark.parametrize(
    ("fund_points", "expected_reason"),
    [
        (_points([100.0, 0.0, 101.0]), "invalid_nav_observation"),
        (_points([100.0, -1.0, 101.0]), "invalid_nav_observation"),
        (_points([100.0, float("nan"), 101.0]), "invalid_nav_observation"),
        (
            _points(
                [100.0, 101.0, 102.0],
                [date(2026, 1, 31), date(2026, 1, 31), date(2026, 2, 28)],
            ),
            "duplicate_observation_date",
        ),
    ],
)
def test_invalid_nav_series_fail_closed(
    fund_points: list[dict[str, object]],
    expected_reason: str,
) -> None:
    payload = _analytics(fund_points)
    since_inception = _period(payload, "SI")["fund"]

    assert payload["quality"]["fund_status"] == "unavailable"
    assert payload["quality"]["fund_reason"] == expected_reason
    assert payload["source_observation_count"] == 0
    assert payload["series"]["drawdown"] == []
    assert since_inception["period_return"] is None
    assert since_inception["annualized_return"] is None
    assert since_inception["annualized_volatility"] is None
    assert since_inception["max_drawdown"] is None


def test_invalid_benchmark_fails_closed_without_suppressing_fund_metrics() -> None:
    payload = _analytics(
        _points([100.0, 102.0, 103.0, 105.0]),
        _points([100.0, 0.0, 101.0, 103.0]),
        "invalid-benchmark",
    )

    assert payload["quality"]["fund_status"] == "available"
    assert payload["quality"]["benchmark_status"] == "unavailable"
    assert payload["quality"]["benchmark_reason"] == "invalid_nav_observation"
    assert _period(payload, "SI")["fund"]["period_return"] is not None
    assert _period(payload, "SI")["benchmark"] is None
    assert _period(payload, "SI")["relative"] is None
    assert payload["series"]["benchmark_rolling_annualized_volatility"] == []
    assert payload["quality"]["series"]["benchmark_rolling_annualized_volatility"][
        "reason"
    ] == "invalid_nav_observation"
    assert payload["series"]["benchmark_rolling_sharpe_ratio"] == []
    assert payload["quality"]["series"]["benchmark_rolling_sharpe_ratio"]["reason"] == (
        "invalid_nav_observation"
    )


def test_pydantic_contract_rejects_non_finite_analytics_values() -> None:
    payload = _analytics(_points(_values_from_returns([0.01, -0.005] * 6)))
    _period(payload, "SI")["fund"]["period_return"] = float("inf")

    with pytest.raises(ValidationError):
        InvestmentAnalyticsResponse.model_validate(payload)


def test_pydantic_contract_rejects_rolling_series_quality_mismatch() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(180)]
    periodic_returns = [0.002 if index % 2 == 0 else -0.001 for index in range(len(dates) - 1)]
    payload = _analytics(_points(_values_from_returns(periodic_returns), dates))
    payload["quality"]["series"]["rolling_annualized_volatility"] = {
        "status": "unavailable",
        "reason": "forced_test_mismatch",
        "observation_count": 0,
        "excluded_observation_count": 0,
        "used_window_count": 0,
        "excluded_window_count": 0,
    }

    with pytest.raises(ValidationError, match="rolling_annualized_volatility conflicts"):
        InvestmentAnalyticsResponse.model_validate(payload)
