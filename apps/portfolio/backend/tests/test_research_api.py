from __future__ import annotations

from datetime import date
from math import sqrt

import numpy as np
import pandas as pd
import pytest

from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_charts import _annualized_volatility, _candidate_chart_bases

from portfolio_app.services.research_solver import (
    RiskBudgetProblem,
    TARGET_MEMBER_INSTRUMENT,
    TARGET_MEMBER_NODE,
    ScopeMemberRecord,
    _align_member_series,
    _estimate_covariance,
    _infer_periods_per_year,
    _periodic_nav_series,
    _selected_price_points,
    _series_to_nav,
    _solve_risk_budget_problem,
)

def _create_planning_taxonomy(client, *, root_default_target_dimension: str = "weight") -> tuple[str, dict[str, str]]:
    taxonomy_response = client.post(
        "/api/portfolios/yungu/taxonomies",
        json={
            "name": "Research Planning Axis",
            "taxonomy_type": "custom",
            "primary_assignment_scope": "instrument",
            "planning_enabled": True,
            "budgeting_level": "weight_and_risk_budget",
            "root_default_target_dimension": root_default_target_dimension,
            "purpose": "Research recursive sleeve test",
        },
    )
    assert taxonomy_response.status_code == 200
    taxonomy_id = taxonomy_response.json()["taxonomy_id"]

    node_ids: dict[str, str] = {}
    for payload in [
        {"node_name": "Risk Assets", "node_code": "RISK"},
        {"node_name": "Rates", "node_code": "RATES"},
        {"node_name": "Cash Reserve", "node_code": "CASH"},
    ]:
        node_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
            json=payload,
        )
        assert node_response.status_code == 200
        node_ids[payload["node_name"]] = node_response.json()["taxonomy_node_id"]

    for payload in [
        {
            "node_name": "Defensive Equity",
            "node_code": "DEF",
            "parent_taxonomy_node_id": node_ids["Risk Assets"],
            "default_target_dimension": "weight",
        },
        {
            "node_name": "Hong Kong Beta",
            "node_code": "HKB",
            "parent_taxonomy_node_id": node_ids["Risk Assets"],
            "default_target_dimension": "risk_budget",
        },
    ]:
        node_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/nodes",
            json=payload,
        )
        assert node_response.status_code == 200
        node_ids[payload["node_name"]] = node_response.json()["taxonomy_node_id"]

    for assignment in [
        ("instrument", "equity-us-abbv", "Defensive Equity"),
        ("instrument", "fund-hk-2800", "Hong Kong Beta"),
        ("instrument", "fund-us-agg", "Rates"),
        ("cash_bucket", "cash-usd-main", "Cash Reserve"),
    ]:
        assignment_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/assignments",
            json={
                "target_scope": assignment[0],
                "target_entity_id": assignment[1],
                "taxonomy_node_id": node_ids[assignment[2]],
            },
        )
        assert assignment_response.status_code == 200

    default_response = client.put(
        "/api/portfolios/yungu/taxonomies/default-planning",
        json={"taxonomy_id": taxonomy_id},
    )
    assert default_response.status_code == 200
    return taxonomy_id, node_ids


def _create_target_sets(client, taxonomy_id: str, node_ids: dict[str, str]) -> None:
    for payload in [
        {
            "target_set_type": "saa",
            "name": "Root SAA",
            "effective_from": "2026-03-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.55,
                    "target_risk_share": 0.6,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.3,
                    "target_risk_share": 0.4,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Cash Reserve"],
                    "target_weight": 0.15,
                    "target_risk_share": 0.0,
                },
            ],
        },
        {
            "target_set_type": "taa",
            "name": "Root TAA",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.6,
                    "target_risk_share": 0.62,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.25,
                    "target_risk_share": 0.38,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Cash Reserve"],
                    "target_weight": 0.15,
                    "target_risk_share": 0.0,
                },
            ],
        },
        {
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "target_set_type": "saa",
            "name": "Risk Assets SAA",
            "effective_from": "2026-03-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Defensive Equity"],
                    "target_weight": 0.58,
                    "target_risk_share": 0.52,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Hong Kong Beta"],
                    "target_weight": 0.42,
                    "target_risk_share": 0.48,
                },
            ],
        },
        {
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "target_set_type": "taa",
            "name": "Risk Assets TAA",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": True,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Defensive Equity"],
                    "target_weight": 0.5,
                    "target_risk_share": 0.45,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Hong Kong Beta"],
                    "target_weight": 0.5,
                    "target_risk_share": 0.55,
                },
            ],
        },
    ]:
        target_set_response = client.post(
            f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
            json=payload,
        )
        assert target_set_response.status_code == 200


def test_research_workbench_returns_target_solve_defaults(client):
    response = client.get("/api/portfolios/yungu/research/workbench")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "yungu"
    assert payload["settings"]["planning_taxonomy_id"] is None
    assert payload["settings"]["target_dimension"] == "scope_default"
    assert payload["settings"]["capital_mode"] == "unit_notional"
    assert payload["settings"]["calculation_frequency"] == "auto"
    assert payload["settings"]["missing_return_policy"] == "strict"
    assert payload["calculation_frequency"]["resolved_frequency"] == "daily"
    assert payload["settings"]["gross_exposure"] is None
    assert payload["settings"]["target_volatility"] is None
    assert payload["settings"]["max_gross_exposure"] is None
    assert "run_template" not in payload["settings"]
    assert "target_set_mode" not in payload["settings"]
    assert "rebalance_frequency" not in payload["settings"]
    assert "start_date" not in payload["settings"]
    assert payload["planning_taxonomy_options"] == []
    assert payload["planning_scope_options"] == []
    assert payload["runs"] == []
    assert payload["selected_run"] is None
    assert payload["current_context"]["holdings_count"] == 3
    assert payload["current_context"]["planning_group_count"] == 0
    assert payload["current_context"]["nav"] == payload["current_context"]["summary"]["end_nav"]


def test_research_workbench_reads_canonical_run_top_holdings(client):
    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(
            ResearchRunRecordModel(
                research_run_id="canonical-run",
                portfolio_id="yungu",
                job_type="target_weight_solve",
                status="completed",
                requested_at="2026-04-15T10:00:00Z",
                started_at="2026-04-15T10:00:00Z",
                finished_at="2026-04-15T10:01:00Z",
                as_of_date=date(2026, 4, 15),
                planning_taxonomy_id=None,
                lookback_days=90,
                requested_by=None,
                headline="Canonical run",
                detail_json={
                    "top_holdings": [
                        {
                            "instrument_id": "equity-us-abbv",
                            "instrument_name": "AbbVie Inc",
                            "instrument_type": "equity",
                            "allocation": 0.25,
                            "market_value_base": 100.0,
                            "cost_basis_base": 90.0,
                            "base_currency": "USD",
                            "price": 206.47,
                        }
                    ]
                },
                artifacts_json=[],
                request_payload_json={},
                error_message=None,
            )
        )
        session.commit()

    response = client.get("/api/portfolios/yungu/research/workbench")
    assert response.status_code == 200
    payload = response.json()
    top_holding = payload["runs"][0]["detail"]["top_holdings"][0]
    assert top_holding["instrument_id"] == "equity-us-abbv"
    assert top_holding["instrument_name"] == "AbbVie Inc"
    assert top_holding["instrument_type"] == "equity"
    assert payload["selected_run"]["detail"]["top_holdings"][0]["instrument_id"] == "equity-us-abbv"


def test_research_series_prefers_total_return_nav_for_funds() -> None:
    detail = {
        "instrument_id": "fund-test",
        "instrument_type": "fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav", "official_nav"],
            "total_return": ["total_return_nav", "official_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "1.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-14",
                "value": "1.1200",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-15",
                "value": "1.0100",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "1.1350",
                "currency": "USD",
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 1.12),
        ("2026-04-15", 1.135),
    ]


def test_research_series_uses_only_complete_market_data() -> None:
    detail = {
        "instrument_id": "fund-status-test",
        "instrument_type": "fund",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "chart": ["total_return_nav", "official_nav"],
            "total_return": ["total_return_nav", "official_nav"],
        },
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-14",
                "value": "1.1200",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-15",
                "value": "1.1350",
                "currency": "USD",
                "status": "partial",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [("2026-04-14", 1.12)]


def test_instrument_chart_bases_prefer_total_return_role() -> None:
    detail = {
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "reference": ["official_nav"],
            "total_return": ["total_return_nav", "official_nav"],
        },
    }

    assert _candidate_chart_bases(detail) == ["total_return_nav", "official_nav"]


def test_instrument_trend_volatility_uses_quote_observation_density() -> None:
    points = [
        {"date": date(2026, 1, 1), "value": 100.0},
        {"date": date(2026, 1, 8), "value": 102.0},
        {"date": date(2026, 1, 15), "value": 99.0},
    ]
    returns = [0.02, 99.0 / 102.0 - 1.0]
    mean_return = sum(returns) / len(returns)
    sample_stddev = sqrt(sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1))
    periods_per_year = 2 / 14 * 365.25

    assert _annualized_volatility(points) == pytest.approx(sample_stddev * sqrt(periods_per_year))


def test_instrument_trend_volatility_can_use_weekly_risk_basis() -> None:
    points = [
        {"date": date(2026, 1, 1), "value": 100.0},
        {"date": date(2026, 1, 2), "value": 101.0},
        {"date": date(2026, 1, 5), "value": 104.0},
        {"date": date(2026, 1, 9), "value": 106.0},
        {"date": date(2026, 1, 12), "value": 102.0},
        {"date": date(2026, 1, 16), "value": 103.0},
    ]
    returns = [106.0 / 101.0 - 1.0, 103.0 / 106.0 - 1.0]
    mean_return = sum(returns) / len(returns)
    sample_stddev = sqrt(sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1))
    periods_per_year = 2 / 14 * 365.25

    assert _annualized_volatility(
        points,
        calculation_frequency="weekly",
        final_date=date(2026, 1, 16),
    ) == pytest.approx(sample_stddev * sqrt(periods_per_year))


def test_research_series_prefers_adjusted_close_for_equities() -> None:
    detail = {
        "instrument_id": "equity-test",
        "instrument_type": "equity",
        "currency": "USD",
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "chart": ["adjusted_close", "close"],
            "total_return": ["adjusted_close", "close"],
        },
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-14",
                "value": "100.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-14",
                "value": "108.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-15",
                "value": "102.0000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-04-15",
                "value": "110.5000",
                "currency": "USD",
                "status": "complete",
            },
        ],
    }

    points = _selected_price_points(detail, end_date=date(2026, 4, 15))

    assert [(item[0].isoformat(), item[1]) for item in points] == [
        ("2026-04-14", 108.0),
        ("2026-04-15", 110.5),
    ]


def test_research_daily_alignment_does_not_span_missing_dates() -> None:
    members = [
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-a",
            label="Instrument A",
        ),
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-b",
            label="Instrument B",
        ),
    ]
    nav_series_by_member = {
        (TARGET_MEMBER_INSTRUMENT, "instrument-a"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 5): 110.0,
            },
            dtype="float64",
        ),
        (TARGET_MEMBER_INSTRUMENT, "instrument-b"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 2): 102.0,
                date(2026, 1, 5): 101.0,
            },
            dtype="float64",
        ),
    }

    aligned_members, calendar, _warnings = _align_member_series(
        members,
        nav_series_by_member,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        calculation_frequency="daily",
    )

    returns_by_member = {item.member.member_id: item.returns for item in aligned_members}
    assert calendar == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)]
    assert pd.isna(returns_by_member["instrument-a"].loc[date(2026, 1, 2)])
    assert pd.isna(returns_by_member["instrument-a"].loc[date(2026, 1, 5)])
    assert returns_by_member["instrument-b"].loc[date(2026, 1, 2)] == pytest.approx(0.02)
    assert _infer_periods_per_year([date(2026, 1, 2), date(2026, 1, 5)]) == pytest.approx(2 / 6 * 365.25)


def test_research_weekly_alignment_uses_period_end_observations() -> None:
    members = [
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-a",
            label="Instrument A",
        ),
        ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id="instrument-b",
            label="Instrument B",
        ),
    ]
    nav_series_by_member = {
        (TARGET_MEMBER_INSTRUMENT, "instrument-a"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 5): 110.0,
            },
            dtype="float64",
        ),
        (TARGET_MEMBER_INSTRUMENT, "instrument-b"): pd.Series(
            {
                date(2026, 1, 1): 100.0,
                date(2026, 1, 2): 102.0,
                date(2026, 1, 5): 101.0,
            },
            dtype="float64",
        ),
    }

    aligned_members, calendar, _warnings = _align_member_series(
        members,
        nav_series_by_member,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        calculation_frequency="weekly",
    )

    returns_by_member = {item.member.member_id: item.returns for item in aligned_members}
    assert calendar == [date(2026, 1, 2), date(2026, 1, 5)]
    assert returns_by_member["instrument-a"].loc[date(2026, 1, 5)] == pytest.approx(0.1)
    assert returns_by_member["instrument-b"].loc[date(2026, 1, 5)] == pytest.approx(101.0 / 102.0 - 1.0)


def test_research_weekly_staleness_applies_to_selected_period_observation_only() -> None:
    series = pd.Series(
        {
            date(2026, 1, 5): 100.0,
            date(2026, 1, 9): 101.0,
            date(2026, 1, 12): 102.0,
        },
        dtype="float64",
    )

    periodic = _periodic_nav_series(
        series,
        calculation_frequency="weekly",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 9),
    )

    assert periodic.loc[date(2026, 1, 9)] == pytest.approx(101.0)
    with pytest.raises(ValueError, match="stale observation"):
        _periodic_nav_series(
            pd.Series({date(2026, 1, 12): 102.0}, dtype="float64"),
            calculation_frequency="weekly",
            start_date=date(2026, 1, 12),
            end_date=date(2026, 1, 16),
        )


def test_recursive_child_nav_preserves_first_valid_return_without_filling_internal_gaps() -> None:
    child_returns = pd.Series(
        {
            date(2026, 1, 1): np.nan,
            date(2026, 1, 2): 0.10,
            date(2026, 1, 3): np.nan,
            date(2026, 1, 4): 0.20,
        },
        dtype="float64",
    )
    child_nav = _series_to_nav(child_returns, as_of_date=date(2026, 1, 4))

    assert child_nav.loc[date(2026, 1, 1)] == pytest.approx(1.0)
    assert child_nav.loc[date(2026, 1, 2)] == pytest.approx(1.1)
    assert pd.isna(child_nav.loc[date(2026, 1, 3)])
    assert pd.isna(child_nav.loc[date(2026, 1, 4)])

    aligned_members, _calendar, _warnings = _align_member_series(
        [ScopeMemberRecord(member_type=TARGET_MEMBER_NODE, member_id="child", label="Child")],
        {(TARGET_MEMBER_NODE, "child"): child_nav},
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
        calculation_frequency="daily",
    )

    parent_returns = aligned_members[0].returns
    assert parent_returns.loc[date(2026, 1, 2)] == pytest.approx(0.1)
    assert pd.isna(parent_returns.loc[date(2026, 1, 3)])
    assert pd.isna(parent_returns.loc[date(2026, 1, 4)])


def test_research_covariance_annualizes_complete_aligned_dates() -> None:
    returns = pd.DataFrame(
        {
            "asset_a": [0.01, 0.02, 0.03],
            "asset_b": [0.05, 0.07, 0.04],
        },
        index=[date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)],
        dtype="float64",
    )

    covariance = _estimate_covariance(
        returns,
        model_id="sample_covariance",
        lookback_days=30,
        parameters={"min_observations": 2},
    )

    annualization = _infer_periods_per_year([date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)])
    expected_covariance = returns.cov(ddof=1) * annualization

    assert covariance.loc["asset_a", "asset_a"] == pytest.approx(expected_covariance.loc["asset_a", "asset_a"])
    assert covariance.loc["asset_a", "asset_b"] == pytest.approx(expected_covariance.loc["asset_a", "asset_b"])
    assert covariance.loc["asset_b", "asset_a"] == pytest.approx(expected_covariance.loc["asset_b", "asset_a"])


def test_research_covariance_rejects_partial_missing_rows() -> None:
    returns = pd.DataFrame(
        {
            "instrument_a": [0.01, 0.02, None, None],
            "instrument_b": [None, None, -0.01, 0.03],
        },
        index=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)],
        dtype="float64",
    )

    with pytest.raises(ValueError, match="complete aligned return observations"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 2},
        )


def test_research_covariance_complete_case_drop_uses_only_complete_rows() -> None:
    dates = [date(2026, 1, day) for day in range(1, 12)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 12)],
            "instrument_b": [0.002 * day for day in range(1, 12)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[date(2026, 1, 5), "instrument_b"] = np.nan

    covariance = _estimate_covariance(
        returns,
        model_id="sample_covariance",
        lookback_days=30,
        parameters={"min_observations": 5},
        missing_return_policy="complete_case_drop",
        calculation_frequency="daily",
        as_of_date=date(2026, 1, 11),
    )

    complete = returns.dropna(how="any")
    expected_covariance = complete.cov(ddof=1) * _infer_periods_per_year(list(complete.index))
    assert covariance.loc["instrument_a", "instrument_a"] == pytest.approx(
        expected_covariance.loc["instrument_a", "instrument_a"]
    )
    assert covariance.loc["instrument_a", "instrument_b"] == pytest.approx(
        expected_covariance.loc["instrument_a", "instrument_b"]
    )


def test_research_covariance_complete_case_drop_rejects_excessive_missing_rows() -> None:
    dates = [date(2026, 1, day) for day in range(1, 11)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 11)],
            "instrument_b": [0.002 * day for day in range(1, 11)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[[date(2026, 1, 4), date(2026, 1, 8)], "instrument_b"] = np.nan

    with pytest.raises(ValueError, match="exceeding"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 5},
            missing_return_policy="complete_case_drop",
            calculation_frequency="daily",
            as_of_date=date(2026, 1, 10),
        )


def test_research_covariance_complete_case_drop_rejects_stale_latest_complete_row() -> None:
    dates = [date(2026, 1, day) for day in range(1, 21)]
    returns = pd.DataFrame(
        {
            "instrument_a": [0.001 * day for day in range(1, 21)],
            "instrument_b": [0.002 * day for day in range(1, 21)],
        },
        index=dates,
        dtype="float64",
    )
    returns.loc[date(2026, 1, 20), "instrument_b"] = np.nan

    with pytest.raises(ValueError, match="latest complete return observation"):
        _estimate_covariance(
            returns,
            model_id="sample_covariance",
            lookback_days=30,
            parameters={"min_observations": 5},
            missing_return_policy="complete_case_drop",
            calculation_frequency="daily",
            as_of_date=date(2026, 1, 31),
        )


def test_research_risk_budget_solver_matches_tight_tolerance() -> None:
    problem = RiskBudgetProblem(
        bucket_ids=["asset_a", "asset_b", "asset_c"],
        covariance=np.array(
            [
                [0.04, 0.01, 0.002],
                [0.01, 0.01, 0.001],
                [0.002, 0.001, 0.0225],
            ],
            dtype="float64",
        ),
        target_risk_shares=np.array([0.4, 0.35, 0.25], dtype="float64"),
        lower_bounds=np.zeros(3, dtype="float64"),
        upper_bounds=np.ones(3, dtype="float64"),
        reference_weights=np.array([0.33, 0.34, 0.33], dtype="float64"),
    )

    solution = _solve_risk_budget_problem(problem)

    assert solution.max_abs_share_gap <= 1e-4
    assert solution.weights.sum() == pytest.approx(1.0)


def test_research_run_creates_current_target_weight_outputs(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 7,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Research regression test",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["planning_taxonomy_id"] == taxonomy_id
    assert settings_payload["comparator_taxonomy_node_id"] == node_ids["Risk Assets"]
    assert settings_payload["target_dimension"] == "scope_default"

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert len(workbench_payload["planning_taxonomy_options"]) == 1
    assert any(item["label"] == "Top Level" for item in workbench_payload["planning_scope_options"])
    assert any(item["label"] == "Risk Assets" for item in workbench_payload["planning_scope_options"])
    assert any(item["group_label"] == "Cash Reserve" for item in workbench_payload["current_context"]["planning_groups"])

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()

    run_payload = run_response.json()
    assert run_payload["status"] == "completed"
    assert run_payload["planning_taxonomy_id"] == taxonomy_id
    assert "run_template" not in run_payload
    assert run_payload["artifact_count"] == 12
    assert run_payload["detail"]["selected_scope"]["taxonomy_node_id"] == node_ids["Risk Assets"]
    assert run_payload["detail"]["selected_scope"]["label"] == "Risk Assets"
    assert "backtest_metrics" not in run_payload["detail"]
    assert "backtest_curve" not in run_payload["detail"]
    assert "weight_schedule" not in run_payload["detail"]
    assert len(run_payload["detail"]["member_targets"]) == 2
    assert len(run_payload["detail"]["leaf_targets"]) == 2
    member_targets_by_label = {item["label"]: item for item in run_payload["detail"]["member_targets"]}
    assert member_targets_by_label["Defensive Equity"]["configured_risk_share"] == pytest.approx(0.45)
    assert member_targets_by_label["Hong Kong Beta"]["configured_risk_share"] == pytest.approx(0.55)
    leaf_targets_by_member = {item["member_id"]: item for item in run_payload["detail"]["leaf_targets"]}
    assert leaf_targets_by_member["equity-us-abbv"]["configured_risk_share"] is None
    assert leaf_targets_by_member["fund-hk-2800"]["configured_risk_share"] == pytest.approx(1.0)
    assert len(run_payload["detail"]["target_assumptions"]) >= 1
    assert len(run_payload["detail"]["target_rows"]) == 2
    assert any(item["label"] == "Defensive Equity" for item in run_payload["detail"]["member_targets"])
    assert any(item["label"] == "Hong Kong Beta" for item in run_payload["detail"]["member_targets"])
    assert all(item["source_label"] in {"TAA", "SAA", "Single Member"} for item in run_payload["detail"]["target_rows"])
    assert run_payload["detail"]["solve_event"]["as_of_date"] == "2026-04-15"
    assert len(run_payload["detail"]["target_weight_gaps"]) >= 1
    signal_labels = {item["label"] for item in run_payload["detail"]["signals"]}
    assert "Scope Default" in signal_labels
    assert "Solver" in signal_labels
    assert "Missing Returns" in signal_labels
    assert "Estimated Volatility" in signal_labels
    reference_tape_artifact = next(item for item in run_payload["artifacts"] if item["artifact_id"] == "reference_tape")
    assert reference_tape_artifact["label"] == "Reference Tape CSV"
    assert reference_tape_artifact["path"].endswith("/reference_tape.csv")
    assert any(item["artifact_id"] == "target_weights" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "leaf_targets" for item in run_payload["artifacts"])
    assert all(item["artifact_id"] != "backtest_curve" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "solve_event" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "scope_solve_events" for item in run_payload["artifacts"])
    assert any(item["artifact_id"] == "target_weight_gaps" for item in run_payload["artifacts"])
    assert all(item["label"] != "Daily NAV CSV" for item in run_payload["artifacts"])

    report_artifact = next(item for item in run_payload["artifacts"] if item["artifact_id"] == "report")
    artifact_response = client.get(
        "/api/portfolios/yungu/research/artifacts/content",
        params={"path": report_artifact["path"]},
    )
    assert artifact_response.status_code == 200
    artifact_payload = artifact_response.json()
    assert artifact_payload["preview_kind"] == "text"
    assert "# Research Run" in artifact_payload["content"]
    assert "Current Target Weights" in artifact_payload["content"]

    selected_workbench_response = client.get(
        "/api/portfolios/yungu/research/workbench",
        params={"selected_run_id": run_payload["research_run_id"]},
    )
    assert selected_workbench_response.status_code == 200
    selected_workbench_payload = selected_workbench_response.json()
    assert selected_workbench_payload["selected_run"]["research_run_id"] == run_payload["research_run_id"]


def test_research_target_solve_actuals_include_pending_security_settlement(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 7,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
            "notes": "Delayed settlement actuals regression",
        },
    )
    assert settings_response.status_code == 200

    baseline_workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert baseline_workbench_response.status_code == 200
    baseline_cash_value = next(
        item["end_value_base"]
        for item in baseline_workbench_response.json()["current_context"]["planning_groups"]
        if item["group_label"] == "Cash Reserve"
    )

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-16",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 206.47,
            "gross_amount": 206.47,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 200, run_response.json()
    run_payload = run_response.json()
    cash_row = next(
        item
        for item in run_payload["detail"]["target_rows"]
        if item["label"] == "Cash Reserve"
    )
    assert cash_row["current_value_base"] == pytest.approx(baseline_cash_value - 206.47)


def test_research_scope_default_respects_taxonomy_root_default_dimension(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 7,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    top_level_scope = next(
        item for item in workbench_response.json()["planning_scope_options"] if item["taxonomy_node_id"] is None
    )
    assert top_level_scope["default_target_dimension"] == "risk_budget"


def test_research_scope_default_requires_configured_dimension_target_set(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    target_set_response = client.post(
        f"/api/portfolios/yungu/taxonomies/{taxonomy_id}/target-sets",
        json={
            "target_set_type": "taa",
            "name": "Root Weight Only",
            "effective_from": "2026-04-01",
            "weight_enabled": True,
            "risk_budget_enabled": False,
            "lines": [
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Risk Assets"],
                    "target_weight": 0.7,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Rates"],
                    "target_weight": 0.2,
                },
                {
                    "target_member_type": "taxonomy_node",
                    "target_member_id": node_ids["Cash Reserve"],
                    "target_weight": 0.1,
                },
            ],
        },
    )
    assert target_set_response.status_code == 200
    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "has no active complete" in run_response.json()["detail"]
    assert "target set" in run_response.json()["detail"]


def test_deleting_selected_research_taxonomy_clears_settings(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": node_ids["Risk Assets"],
            "as_of_date": "2026-04-15",
            "lookback_days": 90,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    delete_response = client.delete(f"/api/portfolios/yungu/taxonomies/{taxonomy_id}")
    assert delete_response.status_code == 200

    workbench_response = client.get("/api/portfolios/yungu/research/workbench")
    assert workbench_response.status_code == 200
    workbench_payload = workbench_response.json()
    assert workbench_payload["default_planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["planning_taxonomy_id"] is None
    assert workbench_payload["settings"]["comparator_taxonomy_node_id"] is None


def test_research_settings_preserve_frozen_nodes_when_field_is_omitted(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client)
    payload = {
        "planning_taxonomy_id": taxonomy_id,
        "comparator_taxonomy_node_id": None,
        "as_of_date": "2026-04-15",
        "lookback_days": 90,
        "missing_return_policy": "complete_case_drop",
        "target_dimension": "scope_default",
        "capital_mode": "unit_notional",
        "frozen_taxonomy_node_ids": [node_ids["Risk Assets"]],
    }
    initial_response = client.put("/api/portfolios/yungu/research/settings", json=payload)
    assert initial_response.status_code == 200
    assert initial_response.json()["frozen_taxonomy_node_ids"] == [node_ids["Risk Assets"]]
    assert initial_response.json()["missing_return_policy"] == "complete_case_drop"

    omitted_payload = dict(payload)
    omitted_payload.pop("frozen_taxonomy_node_ids")
    omitted_payload["notes"] = "Preserve frozen sleeves"
    omitted_response = client.put("/api/portfolios/yungu/research/settings", json=omitted_payload)
    assert omitted_response.status_code == 200
    assert omitted_response.json()["frozen_taxonomy_node_ids"] == [node_ids["Risk Assets"]]

    clear_payload = dict(omitted_payload)
    clear_payload["frozen_taxonomy_node_ids"] = []
    clear_response = client.put("/api/portfolios/yungu/research/settings", json=clear_payload)
    assert clear_response.status_code == 200
    assert clear_response.json()["frozen_taxonomy_node_ids"] == []


def test_research_target_volatility_rejects_unaligned_risk_history(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "weight",
            "capital_mode": "target_volatility",
            "target_volatility": 0.0001,
            "max_gross_exposure": 1.0,
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["capital_mode"] == "target_volatility"
    assert settings_payload["target_volatility"] == pytest.approx(0.0001)
    assert settings_payload["max_gross_exposure"] == pytest.approx(1.0)

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]


def test_research_target_volatility_rejects_missing_child_sleeve_history(client):
    taxonomy_id, node_ids = _create_planning_taxonomy(client, root_default_target_dimension="weight")
    _create_target_sets(client, taxonomy_id, node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "weight",
            "capital_mode": "target_volatility",
            "target_volatility": 1.0,
            "max_gross_exposure": 1.0,
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]


def test_research_run_rejects_insufficient_history_for_unaligned_sparse_window(client):
    taxonomy_id, _node_ids = _create_planning_taxonomy(client, root_default_target_dimension="risk_budget")
    _create_target_sets(client, taxonomy_id, _node_ids)

    settings_response = client.put(
        "/api/portfolios/yungu/research/settings",
        json={
            "planning_taxonomy_id": taxonomy_id,
            "comparator_taxonomy_node_id": None,
            "as_of_date": "2026-04-15",
            "lookback_days": 180,
            "target_dimension": "scope_default",
            "capital_mode": "unit_notional",
        },
    )
    assert settings_response.status_code == 200

    run_response = client.post(
        "/api/portfolios/yungu/research/runs",
        json={"requested_by": "pytest"},
    )
    assert run_response.status_code == 400, run_response.json()
    assert "does not have enough history for aligned research dates" in run_response.json()["detail"]
