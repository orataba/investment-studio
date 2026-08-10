from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from portfolio_app.services.research_solver import (
    prepare_return_window_for_covariance,
    risk_contribution_shares,
)
from portfolio_app.services.risk_model import enrich_holdings_forward_risk


AS_OF_DATE = date(2026, 7, 24)


def _return_points(scale: float = 1.0) -> list[dict[str, object]]:
    dates = [item.date() for item in pd.bdate_range(start="2026-06-23", end=AS_OF_DATE)]
    return [
        {
            "start_date": dates[index - 1].isoformat(),
            "date": point_date.isoformat(),
            "value": scale * (0.002 if index % 4 == 0 else -0.001 if index % 3 == 0 else 0.0005),
        }
        for index, point_date in enumerate(dates[1:], start=1)
    ]


def _holding(
    instrument_id: str,
    weight: float,
    *,
    currency: str = "CNY",
    points: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "line_id": f"holding:{instrument_id}",
        "instrument_core": {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "fund",
            "currency": currency,
        },
        "quantity": 100,
        "allocation": weight,
        "market_value_base": weight * 1_000_000,
        "risk_eligible": True,
        "risk_budget_eligible": True,
        "instrument_return_series_all": {
            "points": points if points is not None else _return_points(),
        },
    }


def _risk_policy(**overrides: object) -> dict[str, object]:
    return {
        "covariance_model_id": "sample_covariance",
        "lookback_days": 30,
        "calculation_frequency": "daily",
        "missing_return_policy": "strict",
        "contribution_mode": "signed",
        **overrides,
    }


def test_absolute_risk_contribution_keeps_zero_contribution_exactly_zero() -> None:
    covariance = np.asarray([[0.04, 0.0], [0.0, 0.01]], dtype="float64")
    weights = np.asarray([1.0, 0.0], dtype="float64")

    shares = risk_contribution_shares(covariance, weights, contribution_mode="abs")

    assert shares.tolist() == [1.0, 0.0]


def test_forward_risk_uses_one_canonical_leaf_model_and_reports_coverage() -> None:
    workspace = {
        "base_currency": "CNY",
        "rows": [
            _holding("alpha", 0.6, points=_return_points(1.0)),
            _holding("beta", 0.4, points=_return_points(-0.7)),
        ],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "ok"
    coverage = result["forward_risk"]["coverage"]
    assert coverage == {
        "policy": "strict",
        "window_start_date": "2026-06-24",
        "window_end_date": "2026-07-24",
        "return_interval": "(2026-06-24 EOD, 2026-07-24 EOD]",
        "rows_before": 22,
        "rows_after": 22,
        "complete_row_count": 22,
        "missing_row_count": 0,
        "missing_row_fraction": 0.0,
        "missing_rows": [],
        "latest_complete_date": "2026-07-24",
        "trailing_staleness_days": 0,
    }
    rows = result["rows"]
    assert all(row["forward_risk_status"] == "ok" for row in rows)
    assert sum(row["forward_risk_share"] for row in rows) == pytest.approx(1.0)
    assert sum(row["forward_contribution_to_variance"] for row in rows) == pytest.approx(
        result["forward_risk"]["portfolio_variance"]
    )


def test_forward_risk_normalizes_inside_eligible_sleeve_and_discloses_exclusion() -> None:
    eligible = _holding("equity", 0.4)
    ineligible = {
        **_holding("fcn", 0.6),
        "holding_kind": "position",
        "risk_eligible": False,
        "risk_budget_eligible": False,
        "market_value_base": 600_000.0,
    }
    ineligible["instrument_core"] = {
        **ineligible["instrument_core"],
        "instrument_type": "fcn",
    }
    workspace = {
        "base_currency": "CNY",
        "rows": [eligible, ineligible],
        "analytics_scope_summary": {
            "scope_name": "Modeled Market Sleeve",
            "modeled_net_exposure": 400_000.0,
            "modeled_gross_exposure": 400_000.0,
            "excluded_carrying_value": 600_000.0,
            "excluded_liability": 0.0,
            "cash_unallocated_exposure": 0.0,
            "coverage_ratio": 0.4,
            "excluded_rows": [
                {
                    "line_id": "holding:fcn",
                    "instrument_id": "fcn",
                    "exposure_base": 600_000.0,
                    "exclusion_reason": "Event-valued position.",
                }
            ],
        },
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "ok"
    assert result["forward_risk"]["modeled_weight_basis"] == "eligible_gross_exposure"
    assert result["forward_risk"]["coverage_ratio"] == pytest.approx(0.4)
    assert result["forward_risk"]["excluded_carrying_value"] == pytest.approx(600_000.0)
    assert result["forward_risk"]["excluded_rows"][0]["instrument_id"] == "fcn"
    assert result["rows"][0]["forward_risk_status"] == "ok"
    assert result["rows"][0]["forward_risk_share"] == pytest.approx(1.0)
    assert result["rows"][1]["forward_risk_status"] == "modeled_zero"
    assert result["rows"][1]["forward_risk_share"] == pytest.approx(0.0)
    assert result["rows"][1]["forward_contribution_to_variance"] == pytest.approx(0.0)
    assert result["rows"][1]["forward_annualized_volatility"] == pytest.approx(0.0)


def test_forward_risk_is_unavailable_when_every_exposure_is_policy_excluded() -> None:
    excluded = {
        **_holding("fcn", 1.0),
        "risk_eligible": False,
        "risk_budget_eligible": False,
    }
    excluded["instrument_core"] = {
        **excluded["instrument_core"],
        "instrument_type": "fcn",
    }

    result = enrich_holdings_forward_risk(
        {"base_currency": "CNY", "rows": [excluded]},
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "unavailable"
    assert result["forward_risk"]["errors"] == [
        "Modeled sleeve risk requires at least one eligible risky holding."
    ]
    assert result["rows"][0]["forward_risk_status"] == "modeled_zero"
    assert result["rows"][0]["forward_risk_share"] == pytest.approx(0.0)


def test_forward_risk_models_only_base_currency_monetary_rows_as_zero() -> None:
    def monetary_row(
        line_id: str,
        *,
        currency: str,
        holding_kind: str,
    ) -> dict[str, object]:
        return {
            "line_id": line_id,
            "holding_kind": holding_kind,
            "instrument_core": {
                "instrument_id": line_id,
                "instrument_name": line_id,
                "instrument_type": "cash",
                "currency": currency,
            },
            "market_value_base": 100_000.0,
            "risk_eligible": False,
            "risk_budget_eligible": False,
        }

    result = enrich_holdings_forward_risk(
        {
            "base_currency": "CNY",
            "rows": [
                _holding("equity", 1.0),
                monetary_row("cash:CNY", currency="CNY", holding_kind="settled_cash"),
                monetary_row(
                    "pending:CNY",
                    currency="CNY",
                    holding_kind="settlement_receivable",
                ),
                monetary_row(
                    "pending:USD",
                    currency="USD",
                    holding_kind="settlement_receivable",
                ),
            ],
        },
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "ok"
    rows = {row["line_id"]: row for row in result["rows"]}
    for line_id in ("cash:CNY", "pending:CNY"):
        assert rows[line_id]["forward_risk_status"] == "modeled_zero"
        assert rows[line_id]["forward_risk_share"] == pytest.approx(0.0)
        assert rows[line_id]["forward_annualized_volatility"] == pytest.approx(0.0)
    assert rows["pending:USD"]["forward_risk_status"] == "pending_settlement"
    assert rows["pending:USD"]["forward_risk_share"] is None


def test_forward_risk_fails_closed_when_one_member_has_an_internal_period_gap() -> None:
    beta_points = _return_points(-0.7)
    beta_points[10] = {
        **beta_points[10],
        "start_date": (date.fromisoformat(str(beta_points[10]["start_date"])) - timedelta(days=1)).isoformat(),
    }
    workspace = {
        "base_currency": "CNY",
        "rows": [
            _holding("alpha", 0.6, points=_return_points(1.0)),
            _holding("beta", 0.4, points=beta_points),
        ],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "unavailable"
    assert "return series is not contiguous" in result["forward_risk"]["errors"][0]
    assert all(row["forward_risk_status"] == "unavailable" for row in result["rows"])


def test_forward_risk_fails_closed_for_a_shared_internal_period_gap() -> None:
    alpha_points = _return_points(1.0)
    beta_points = _return_points(-0.7)
    for points in (alpha_points, beta_points):
        points[10] = {
            **points[10],
            "start_date": (
                date.fromisoformat(str(points[10]["start_date"])) - timedelta(days=1)
            ).isoformat(),
        }
    workspace = {
        "base_currency": "CNY",
        "rows": [
            _holding("alpha", 0.6, points=alpha_points),
            _holding("beta", 0.4, points=beta_points),
        ],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "unavailable"
    assert "return series is not contiguous" in result["forward_risk"]["errors"][0]
    assert all(row["forward_risk_status"] == "unavailable" for row in result["rows"])


def test_forward_risk_fails_closed_for_non_base_currency_without_fx_total_returns() -> None:
    workspace = {
        "base_currency": "CNY",
        "rows": [_holding("foreign", 1.0, currency="USD")],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "unavailable"
    assert result["forward_risk"]["errors"] == [
        "Forward RC requires base-currency total returns; foreign is USD while the portfolio base currency is CNY."
    ]
    assert result["rows"][0]["forward_risk_status"] == "unavailable"


def test_forward_risk_reports_one_portfolio_level_error_when_base_currency_is_missing() -> None:
    workspace = {
        "base_currency": "",
        "rows": [
            _holding("alpha", 0.6),
            _holding("beta", 0.4),
        ],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "unavailable"
    assert result["forward_risk"]["errors"] == ["Forward RC requires the portfolio base currency."]
    assert all(row["forward_risk_status"] == "unavailable" for row in result["rows"])


def test_forward_risk_uses_scoped_series_instead_of_global_risk_basis_coverage() -> None:
    workspace = {
        "base_currency": "CNY",
        "risk_basis": {
            "coverage_state": "partial",
            "status_label": "Risk basis partial - 2 instrument(s) have observation gaps",
        },
        "rows": [
            _holding("alpha", 0.6),
            _holding("beta", 0.4),
        ],
    }

    result = enrich_holdings_forward_risk(
        workspace,
        as_of_date=AS_OF_DATE,
        calculation_frequency="daily",
        risk_policy=_risk_policy(),
    )

    assert result["forward_risk"]["status"] == "ok"
    assert result["forward_risk"]["errors"] == []
    assert all(row["forward_risk_status"] == "ok" for row in result["rows"])


def test_covariance_window_is_anchored_to_requested_as_of_date() -> None:
    dates = [item.date() for item in pd.date_range(start="2026-06-20", end=AS_OF_DATE)]
    returns = pd.DataFrame({"alpha": np.linspace(-0.01, 0.01, len(dates))}, index=dates)

    coverage = prepare_return_window_for_covariance(
        returns,
        lookback_days=30,
        min_observations=2,
        label="Forward risk contribution",
        missing_return_policy="strict",
        calculation_frequency="daily",
        as_of_date=AS_OF_DATE,
    )

    assert min(coverage.returns.index) == date(2026, 6, 25)
    assert max(coverage.returns.index) == AS_OF_DATE


def test_calendar_month_risk_window_uses_an_exclusive_eod_start_boundary() -> None:
    as_of_date = date(2026, 7, 27)
    dates = [item.date() for item in pd.bdate_range(start="2026-06-24", end=as_of_date)]
    returns = pd.DataFrame({"alpha": np.linspace(-0.01, 0.01, len(dates))}, index=dates)

    coverage = prepare_return_window_for_covariance(
        returns,
        lookback_days=30,
        min_observations=2,
        label="Forward risk contribution",
        missing_return_policy="strict",
        calculation_frequency="daily",
        as_of_date=as_of_date,
    )

    # One calendar month before 2026-07-27 is the Saturday 2026-06-27.
    # No return may end on that EOD boundary; the first XSHG return ends on
    # Monday and starts from the preceding valid Friday close.
    assert min(coverage.returns.index) == date(2026, 6, 29)
    assert max(coverage.returns.index) == as_of_date


def test_strict_covariance_policy_rejects_stale_latest_complete_observation() -> None:
    stale_end = date(2026, 7, 17)
    dates = [item.date() for item in pd.bdate_range(start="2026-06-24", end=stale_end)]
    returns = pd.DataFrame({"alpha": np.linspace(-0.01, 0.01, len(dates))}, index=dates)

    with pytest.raises(ValueError, match="strict latest complete return observation"):
        prepare_return_window_for_covariance(
            returns,
            lookback_days=30,
            min_observations=15,
            label="Forward risk contribution",
            missing_return_policy="strict",
            calculation_frequency="daily",
            as_of_date=AS_OF_DATE,
        )
