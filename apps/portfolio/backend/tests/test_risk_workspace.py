from __future__ import annotations

from contextlib import nullcontext
from datetime import date

import numpy as np
import pandas as pd

from portfolio_app.api.contracts import RiskWorkspaceErrorRecord, RiskWorkspaceResponse
from portfolio_app.services import risk_workspace
from portfolio_app.services.research_solver import ResearchMarketDataError
from portfolio_app.services.risk_model import portfolio_risk_model_snapshot


def _nav_series(pattern: str) -> pd.Series:
    dates = pd.bdate_range("2026-01-02", "2026-06-30").date
    index = np.arange(len(dates), dtype="float64")
    if pattern == "a":
        returns = 0.0004 + 0.006 * np.sin(index / 5.0)
    else:
        returns = 0.0002 + 0.004 * np.cos(index / 7.0) - 0.002 * np.sin(index / 3.0)
    values = 100.0 * np.cumprod(1.0 + returns)
    return pd.Series(values, index=pd.Index(dates, dtype="object"), dtype="float64")


def _install_workspace_facts(monkeypatch, *, include_second_assignment: bool = True):
    monkeypatch.setattr(
        risk_workspace,
        "get_portfolio",
        lambda _portfolio_id: {
            "portfolio_id": "portfolio-1",
            "portfolio_name": "Portfolio 1",
            "base_currency": "CNY",
            "default_planning_taxonomy_id": "tax-1",
            "risk_policy_json": {
                "covariance_model_id": "sample_covariance",
                "lookback_days": 90,
                "calculation_frequency": "daily",
                "missing_return_policy": "strict",
                "contribution_mode": "signed",
            },
        },
    )
    monkeypatch.setattr(risk_workspace, "list_accounts", lambda _portfolio_id: [])
    monkeypatch.setattr(risk_workspace, "list_transactions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        risk_workspace,
        "build_holdings_report",
        lambda *_args, **_kwargs: {
            "positions": [
                {
                    "instrument_id": "instrument-a",
                    "instrument_ref": {"instrument_name": "Instrument A", "instrument_type": "fund"},
                    "market_value_base": 600.0,
                    "portfolio_weight": 0.6,
                },
                {
                    "instrument_id": "instrument-b",
                    "instrument_ref": {"instrument_name": "Instrument B", "instrument_type": "fund"},
                    "market_value_base": 400.0,
                    "portfolio_weight": 0.4,
                },
            ]
        },
    )
    monkeypatch.setattr(
        risk_workspace,
        "build_account_workspace",
        lambda *_args, **_kwargs: {"accounts": []},
    )
    monkeypatch.setattr(
        risk_workspace,
        "list_portfolio_instrument_universe",
        lambda _portfolio_id: [
            {
                "instrument_id": "instrument-a",
                "instrument_ref": {"instrument_name": "Instrument A", "instrument_type": "fund"},
                "status": "active",
            },
            {
                "instrument_id": "instrument-b",
                "instrument_ref": {"instrument_name": "Instrument B", "instrument_type": "fund"},
                "status": "active",
            },
        ],
    )
    monkeypatch.setattr(
        risk_workspace,
        "list_taxonomies",
        lambda _portfolio_id: [
            {
                "taxonomy_id": "tax-1",
                "name": "Asset Allocation",
                "planning_enabled": True,
                "primary_assignment_scope": "instrument",
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        risk_workspace,
        "list_taxonomy_nodes",
        lambda *_args, **_kwargs: [
            {
                "taxonomy_node_id": "node-a",
                "taxonomy_id": "tax-1",
                "parent_taxonomy_node_id": None,
                "node_name": "Growth",
                "status": "active",
            },
            {
                "taxonomy_node_id": "node-b",
                "taxonomy_id": "tax-1",
                "parent_taxonomy_node_id": None,
                "node_name": "Defensive",
                "status": "active",
            },
        ],
    )
    assignments = [
        {
            "assignment_id": "assignment-a",
            "taxonomy_id": "tax-1",
            "target_scope": "instrument",
            "target_entity_id": "instrument-a",
            "taxonomy_node_id": "node-a",
            "status": "active",
        }
    ]
    if include_second_assignment:
        assignments.append(
            {
                "assignment_id": "assignment-b",
                "taxonomy_id": "tax-1",
                "target_scope": "instrument",
                "target_entity_id": "instrument-b",
                "taxonomy_node_id": "node-b",
                "status": "active",
            }
        )
    monkeypatch.setattr(risk_workspace, "list_taxonomy_assignments", lambda *_args, **_kwargs: assignments)
    monkeypatch.setattr(
        risk_workspace,
        "list_target_sets",
        lambda *_args, **_kwargs: [
            {
                "target_set_id": "saa-1",
                "taxonomy_id": "tax-1",
                "comparator_taxonomy_node_id": None,
                "target_set_type": "saa",
                "name": "SAA",
                "weight_enabled": True,
                "risk_budget_enabled": True,
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        risk_workspace,
        "list_target_set_lines",
        lambda *_args, **_kwargs: [
            {
                "target_line_id": "line-a",
                "target_set_id": "saa-1",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-a",
                "target_weight": 0.5,
                "target_risk_share": 0.5,
            },
            {
                "target_line_id": "line-b",
                "target_set_id": "saa-1",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-b",
                "target_weight": 0.5,
                "target_risk_share": 0.5,
            },
        ],
    )

    fake_session = object()
    fake_context = object()
    monkeypatch.setattr(risk_workspace, "get_session_factory", lambda: lambda: nullcontext(fake_session))
    lock_calls = []

    def fake_lock(session, **kwargs):
        lock_calls.append((session, kwargs))
        return fake_context

    monkeypatch.setattr(risk_workspace, "lock_research_market_data_in_session", fake_lock)

    def fake_nav(context, *, instrument_id, **_kwargs):
        assert context is fake_context
        return _nav_series("a" if instrument_id == "instrument-a" else "b"), []

    monkeypatch.setattr(risk_workspace, "build_canonical_total_return_nav_series", fake_nav)
    monkeypatch.setattr(
        risk_workspace,
        "research_market_data_manifest",
        lambda context: {
            "policy_version": "test-policy",
            "base_currency": "CNY",
            "start_date": "1900-01-01",
            "end_date": "2026-06-30",
            "instrument_ids": ["instrument-a", "instrument-b"],
            "quote_dependencies": [],
            "fx_dependencies": [],
        },
    )
    return fake_session, lock_calls


def test_risk_workspace_is_backend_authoritative_and_contract_valid(monkeypatch):
    fake_session, lock_calls = _install_workspace_facts(monkeypatch)

    payload = risk_workspace.build_risk_workspace(
        "portfolio-1",
        as_of_date=date(2026, 6, 30),
        rolling_lookback_days=30,
        matrix_lookback_days=30,
    )
    response = RiskWorkspaceResponse.model_validate(payload)

    assert response.status == "ready"
    assert response.rolling.status == "ready"
    assert response.rolling.portfolio_volatility_points
    assert response.matrix.status == "ready"
    assert len(response.matrix.groups) == 2
    assert response.risk_contribution.status == "ready"
    assert len(response.risk_contribution.rows) == 2
    assert response.drift.status == "ready"
    assert len(response.drift.weight_rows) == 2
    assert response.coverage.market_data_role == "total_return"
    assert response.calculation_lineage["engine_version"] == risk_workspace.RISK_WORKSPACE_ENGINE_VERSION
    assert len(lock_calls) == 1
    assert lock_calls[0][0] is fake_session
    assert lock_calls[0][1]["instrument_ids"] == ["instrument-a", "instrument-b"]


def test_risk_workspace_fails_taxonomy_sections_closed_without_assignment(monkeypatch):
    _install_workspace_facts(monkeypatch, include_second_assignment=False)

    response = RiskWorkspaceResponse.model_validate(
        risk_workspace.build_risk_workspace(
            "portfolio-1",
            as_of_date=date(2026, 6, 30),
            rolling_lookback_days=30,
            matrix_lookback_days=30,
        )
    )

    assert response.status == "partial"
    assert response.rolling.status == "ready"
    assert response.matrix.status == "ready"
    assert response.risk_contribution.status == "unavailable"
    assert response.drift.status == "unavailable"
    assert any("instrument-b" in error.message for error in response.risk_contribution.errors)


def test_risk_workspace_rejects_future_matrix_boundary():
    try:
        risk_workspace.build_risk_workspace(
            "portfolio-1",
            as_of_date=date(2026, 6, 30),
            rolling_lookback_days=30,
            matrix_lookback_days=30,
            matrix_as_of_date=date(2026, 7, 1),
        )
    except risk_workspace.RiskWorkspaceRequestError as error:
        assert "cannot be later" in str(error)
    else:  # pragma: no cover - explicit request boundary guard
        raise AssertionError("Expected RiskWorkspaceRequestError")


def test_risk_workspace_api_requires_explicit_as_of_and_returns_lineage(client):
    missing_boundary = client.get("/api/portfolios/portfolio-ops/risk/workspace")
    assert missing_boundary.status_code == 422

    response = client.get(
        "/api/portfolios/portfolio-ops/risk/workspace",
        params={
            "as_of_date": "2026-04-15",
            "rolling_lookback_days": 30,
            "matrix_lookback_days": 30,
            "matrix_scope_node_id": risk_workspace.ALL_INSTRUMENTS_SCOPE,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["as_of_date"] == "2026-04-15"
    assert payload["coverage"]["market_data_role"] == "total_return"
    assert payload["calculation_lineage"]["engine_version"] == risk_workspace.RISK_WORKSPACE_ENGINE_VERSION
    assert payload["data_lineage"]["policy_version"]


def test_explicit_matrix_date_survives_deterministic_option_sampling():
    dates = pd.bdate_range("2023-01-02", periods=600).date
    index = np.arange(len(dates), dtype="float64")
    frame = pd.DataFrame(
        {
            "a": 0.0005 + 0.004 * np.sin(index / 9.0),
            "b": 0.0002 + 0.003 * np.cos(index / 11.0),
        },
        index=pd.Index(dates, dtype="object"),
    )
    policy = portfolio_risk_model_snapshot(
        {
            "covariance_model_id": "sample_covariance",
            "lookback_days": 30,
            "calculation_frequency": "daily",
            "missing_return_policy": "strict",
            "contribution_mode": "signed",
        },
        calculation_frequency="daily",
    )
    sampled_dates = risk_workspace._calculable_matrix_dates(
        frame,
        calculation_frequency="daily",
        risk_policy=policy,
    )
    requested_date = next(
        item
        for item in dates[60:-1]
        if item not in set(sampled_dates)
    )

    section = risk_workspace._build_matrix_section(
        frame=frame,
        metadata=[
            {"key": "a", "label": "A", "weight": 0.6},
            {"key": "b", "label": "B", "weight": 0.4},
        ],
        requested_as_of_date=requested_date,
        calculation_frequency="daily",
        risk_policy=policy,
        scope=risk_workspace.ALL_INSTRUMENTS_SCOPE,
    )

    assert section["status"] == "ready"
    assert section["as_of_date"] == requested_date.isoformat()
    assert requested_date.isoformat() in section["available_as_of_dates"]
    assert len(section["available_as_of_dates"]) <= risk_workspace.MAX_MATRIX_AS_OF_OPTIONS


def test_canonical_market_data_error_keeps_auditable_dependency():
    dependency = {
        "policy_version": "canonical-total-return-v1",
        "instrument_id": "instrument-a",
        "quote_revision_id": "revision-1",
    }
    payload = risk_workspace._exception_error(
        ResearchMarketDataError(
            "Canonical total-return dependency is unavailable.",
            reason_codes=["missing_quote_series"],
            dependency=dependency,
        )
    )

    response = RiskWorkspaceErrorRecord.model_validate(payload)

    assert response.reason_codes == ["missing_quote_series"]
    assert response.dependency == dependency
