from __future__ import annotations

from datetime import date, timedelta
from math import sqrt

import pytest

from portfolio_app.services import period_metrics


def _drawdown_snapshots() -> list[dict[str, object]]:
    return [
        {"as_of_date": date(2026, 1, 2), "daily_twr": 0.10},
        {"as_of_date": date(2026, 1, 3), "daily_twr": -0.20},
        {"as_of_date": date(2026, 1, 4), "daily_twr": 0.25},
    ]


def test_period_scalar_metric_golden_contract() -> None:
    assert period_metrics.year_fraction(date(2026, 1, 1), date(2027, 1, 2)) == pytest.approx(
        366 / 365.25
    )
    assert period_metrics.year_fraction(date(2026, 1, 2), date(2026, 1, 1)) == 0.0
    assert period_metrics.periods_per_year_from_observations(
        observation_count=2,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
    ) == pytest.approx(2 / 4 * 365.25)
    assert period_metrics.periods_per_year_from_observations(
        observation_count=1,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    ) is None
    assert period_metrics.sample_stddev([0.01]) is None
    assert period_metrics.sample_stddev([0.01, -0.01]) == pytest.approx(sqrt(0.0002))
    assert period_metrics.downside_deviation([0.02, -0.01, -0.02]) == pytest.approx(
        sqrt(0.0005 / 3)
    )
    assert period_metrics.downside_deviation([0.01, 0.02]) is None


@pytest.mark.parametrize(
    ("return_coverage_state", "sample_count", "periods_per_year", "annualized_volatility", "status", "reason"),
    [
        ("partial", 3, 252.0, 0.2, "unavailable", "return_coverage_incomplete"),
        ("complete", 1, 252.0, 0.2, "insufficient_samples", "insufficient_return_samples"),
        ("complete", 2, None, 0.2, "unavailable", "risk_observation_frequency_unavailable"),
        ("complete", 2, 252.0, None, "unavailable", "risk_metric_calculation_unavailable"),
        ("complete", 2, 252.0, 0.2, "available", None),
    ],
)
def test_portfolio_risk_result_contract_golden(
    return_coverage_state: str,
    sample_count: int,
    periods_per_year: float | None,
    annualized_volatility: float | None,
    status: str,
    reason: str | None,
) -> None:
    result = period_metrics.portfolio_risk_result_contract(
        return_coverage_state=return_coverage_state,
        sample_count=sample_count,
        periods_per_year=periods_per_year,
        annualized_volatility=annualized_volatility,
    )

    assert result == {
        "risk_calculation_frequency": "daily",
        "risk_minimum_sample_count": 2,
        "risk_sample_count": sample_count,
        "risk_result_status": status,
        "risk_unavailable_reason": reason,
    }


def test_drawdown_stats_golden_contract() -> None:
    assert period_metrics.drawdown_stats(
        _drawdown_snapshots(),
        start_anchor_date=date(2026, 1, 1),
    ) == pytest.approx(
        {
            "current_drawdown": 0.0,
            "max_drawdown": -0.20,
            "max_drawdown_days": 1,
            "drawdown_duration_days": 2,
        }
    )
    assert period_metrics.drawdown_stats([]) == {
        "current_drawdown": None,
        "max_drawdown": None,
        "max_drawdown_days": None,
        "drawdown_duration_days": None,
    }


def test_xirr_status_golden_contract() -> None:
    unique_cash_flows = [
        (date(2026, 1, 1), -100.0),
        (date(2027, 1, 1), 110.0),
    ]
    expected_rate = 1.1 ** (period_metrics.DAYS_PER_YEAR / 365.0) - 1.0
    unique = period_metrics.solve_xirr_result(unique_cash_flows)
    assert unique.status == "unique_root"
    assert unique.rate == pytest.approx(expected_rate, abs=1e-10)
    assert period_metrics.xnpv(unique.rate or 0.0, unique_cash_flows) == pytest.approx(0.0, abs=1e-9)
    assert period_metrics.solve_xirr(unique_cash_flows) == pytest.approx(expected_rate, abs=1e-10)

    invalid = period_metrics.solve_xirr_result(
        [(date(2026, 1, 1), -100.0), (date(2026, 1, 1), 110.0)]
    )
    assert (invalid.status, invalid.rate) == ("invalid_cash_flows", None)

    no_root = period_metrics.solve_xirr_result(
        [(date(2026, 1, 1), 100.0), (date(2027, 1, 1), 110.0)]
    )
    assert (no_root.status, no_root.rate) == ("no_root", None)

    multiple = period_metrics.solve_xirr_result(
        [
            (date(2025, 1, 1), -100.0),
            (date(2026, 1, 1), 230.0),
            (date(2027, 1, 1), -132.0),
        ]
    )
    assert (multiple.status, multiple.rate) == ("multiple_roots_or_non_unique", None)
    assert period_metrics.solve_xirr(
        [
            (date(2025, 1, 1), -100.0),
            (date(2026, 1, 1), 230.0),
            (date(2027, 1, 1), -132.0),
        ]
    ) is None


@pytest.mark.parametrize(
    ("cash_flows", "expected_status"),
    [
        (
            [(date(2026, 1, 1), -100.0), (date(2027, 1, 1), 110.0)],
            "unique_root",
        ),
        (
            [(date(2026, 1, 1), -100.0), (date(2026, 1, 1), 110.0)],
            "invalid_cash_flows",
        ),
        (
            [(date(2026, 1, 1), 100.0), (date(2027, 1, 1), 110.0)],
            "no_root",
        ),
        (
            [
                (date(2025, 1, 1), -100.0),
                (date(2026, 1, 1), 230.0),
                (date(2027, 1, 1), -132.0),
            ],
            "multiple_roots_or_non_unique",
        ),
    ],
)
def test_xirr_statuses_match_expected_contract(
    cash_flows: list[tuple[date, float]],
    expected_status: str,
) -> None:
    result = period_metrics.solve_xirr_result(cash_flows)

    assert result.status == expected_status
    assert period_metrics.solve_xirr(cash_flows) == result.rate


@pytest.mark.parametrize(
    ("amounts", "expected_status", "expected_q"),
    [
        pytest.param([100.0, -110.0], "unique_root", 1.1, id="positive-then-negative"),
        # q^2 - 1.10005q + .000055: the second root .00005 is below the rate domain.
        pytest.param([100.0, -110.005, 0.0055], "unique_root", 1.1, id="one-admissible-root"),
        # -100(q - 1)^2 - 1 never vanishes, despite two sign changes.
        pytest.param([-100.0, 200.0, -101.0], "no_root", None, id="mixed-sign-no-root"),
        # -100(q - 1.1)^2 touches zero without changing sign.
        pytest.param([-100.0, 220.0, -121.0], "unique_root", 1.1, id="tangent-root"),
        # -100(q - 1.1)(q^2 - .5q + 1): the quadratic has no real roots.
        pytest.param([-100.0, 160.0, -155.0, 110.0], "unique_root", 1.1, id="three-sign-changes-one-root"),
        # -100(q - 1.1)^2(q - 1.2) has two distinct positive roots.
        pytest.param(
            [-100.0, 340.0, -385.0, 145.2],
            "multiple_roots_or_non_unique",
            None,
            id="tangent-and-simple-roots",
        ),
    ],
)
def test_xirr_matches_independent_cash_flow_polynomials(
    amounts: list[float], expected_status: str, expected_q: float | None,
) -> None:
    # For equal 365-day spacing, multiply XNPV by q^degree where
    # q=(1+r)^(365/365.25). The comments above factor that ordinary polynomial.
    cash_flows = [
        (date(2025, 1, 1) + timedelta(days=365 * index), amount)
        for index, amount in enumerate(amounts)
    ]
    result = period_metrics.solve_xirr_result(cash_flows)

    assert result.status == expected_status
    if expected_q is None:
        assert result.rate is None
    else:
        expected_rate = expected_q ** (365.25 / 365.0) - 1.0
        assert result.rate == pytest.approx(expected_rate, abs=1e-9)
        assert period_metrics.xnpv(result.rate, cash_flows) == pytest.approx(0.0, abs=1e-8)


def test_xirr_tangent_at_supported_lower_rate_boundary_is_one_root() -> None:
    minimum_rate = -0.9999
    q = (1.0 + minimum_rate) ** (365.0 / 365.25)
    # -100(q_observed - q)^2 has one distinct root exactly at the lower boundary.
    cash_flows = [
        (date(2025, 1, 1), -100.0),
        (date(2026, 1, 1), 200.0 * q),
        (date(2027, 1, 1), -100.0 * q * q),
    ]

    result = period_metrics.solve_xirr_result(cash_flows)

    assert result.status == "unique_root"
    assert result.rate == pytest.approx(minimum_rate, abs=1e-12)
