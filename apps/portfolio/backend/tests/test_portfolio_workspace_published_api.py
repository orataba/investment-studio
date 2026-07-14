from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from portfolio_app.api.contracts import PortfolioWorkspaceSummaryResponse
from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.api.routes.workspace import _sealed_taxonomy_display_config
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_contribution_output,
    portfolio_daily_holding_output,
    portfolio_daily_lot_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.db.session import get_db_session
from portfolio_app.main import app
from tests.test_portfolio_daily_published_repository import (
    _RecordingExecutor,
    _confirmation,
    _metadata_row,
    _output_row,
)


pytestmark = pytest.mark.no_database


def _assert_no_json_floats(value: object) -> None:
    if isinstance(value, float):
        raise AssertionError(f"published workspace emitted JSON float {value!r}")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_json_floats(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_json_floats(item)


def test_sealed_taxonomy_allows_explicit_empty_snapshot() -> None:
    result = _sealed_taxonomy_display_config(
        {
            "default_planning_taxonomy_id": None,
            "taxonomy": {
                "taxonomies": [],
                "nodes": [],
                "assignments": [],
            },
        },
        portfolio_id="portfolio-etf",
    )

    assert result == {
        "default_planning_taxonomy_id": None,
        "taxonomies": [],
        "taxonomy_nodes": [],
        "taxonomy_assignments": [],
    }


def test_sealed_taxonomy_fails_closed_when_snapshot_is_missing() -> None:
    with pytest.raises(HTTPException) as captured:
        _sealed_taxonomy_display_config(
            {"default_planning_taxonomy_id": None},
            portfolio_id="portfolio-missing-taxonomy",
        )

    assert captured.value.status_code == 503
    assert captured.value.detail["reason"] == "sealed_taxonomy_snapshot_missing"


def test_holdings_workspace_uses_one_publication_exact_lots_and_attribution() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    manifest_id = uuid4()
    token = 91
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=manifest_id,
        token=token,
    )
    snapshot = _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 14),
        base_currency="CNY",
        measured_nav=True,
        measured_position_market_value=True,
        measured_book_pnl=True,
        measured_return=True,
        measured_external_flows=True,
        opening_nav=Decimal("100.00000000"),
        closing_nav=Decimal("110.00000000"),
        position_market_value=Decimal("100.12345679"),
        settled_cash=Decimal("9.87654321"),
        pending_receivable=Decimal("0"),
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        economic_pnl=Decimal("10"),
        subperiod_twr_method50=Decimal("0.1"),
        subperiod_twr_published=Decimal("0.1"),
        cumulative_twr_method50=Decimal("0.1"),
        cumulative_twr_published=Decimal("0.1"),
        wealth_index_method50=Decimal("1.1"),
        wealth_index_published=Decimal("1.1"),
        peak_wealth_index_method50=Decimal("1.1"),
        peak_wealth_index_published=Decimal("1.1"),
        drawdown_method50=Decimal("0"),
        drawdown_published=Decimal("0"),
        wealth_chain_rounding_adjustment_exact=Decimal("0"),
        calculation_status="calculated",
        return_chain_status="active",
        nav_coverage_state="complete",
        book_pnl_coverage_state="complete",
        return_coverage_state="complete",
        flow_coverage_state="complete",
        position_attribution_coverage_state="complete",
        valuation_endpoint_status="fresh",
    )
    holding = _output_row(
        portfolio_daily_holding_output,
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 14),
        account_id="account-1",
        instrument_id="instrument-1",
        currency="USD",
        quantity_exact=Decimal("1.25"),
        quantity=Decimal("1.250000000000"),
        measured_price=True,
        measured_market_value=True,
        adopted_price_exact=Decimal("10.5"),
        price=Decimal("10.500000000000"),
        contract_multiplier_exact=Decimal("1"),
        contract_multiplier=Decimal("1.000000000000"),
        price_factor_exact=Decimal("1"),
        price_factor=Decimal("1.000000000000"),
        adopted_fx_rate_exact=Decimal("7.628453850599"),
        fx_rate_to_base=Decimal("7.628453850599000000"),
        market_value_local_exact=Decimal("13.125"),
        market_value_base_exact=Decimal("100.123456789123"),
        market_value_local=Decimal("13.12500000"),
        market_value_base=Decimal("100.12345679"),
        # Deliberately different: the workspace must use exact published lots.
        cost_basis_local=Decimal("1"),
        cost_basis_base=Decimal("1"),
        portfolio_weight=Decimal("0.910213243537481818"),
        valuation_coverage_state="complete",
        valuation_endpoint_status="fresh",
        book_pnl_coverage_state="unavailable",
        book_pnl_reason_codes=["holding_book_pnl_detail_unavailable"],
        position_attribution_coverage_state="unavailable",
        position_attribution_reason_codes=["position_attribution_inputs_unavailable"],
    )
    lot = _output_row(
        portfolio_daily_lot_output,
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 14),
        account_id="account-1",
        instrument_id="instrument-1",
        lot_id="lot-1",
        source_transaction_id="transaction-1",
        source_revision_id="revision-1",
        source_revision_number=1,
        custody_transaction_id="transaction-1",
        custody_revision_id="revision-1",
        custody_revision_number=1,
        acquisition_date=date(2026, 7, 1),
        currency="USD",
        open_quantity_exact=Decimal("1.25"),
        open_quantity=Decimal("1.250000000000"),
        measured_base_cost=True,
        acquisition_fx_rate_exact=Decimal("7.2"),
        acquisition_fx_rate=Decimal("7.200000000000000000"),
        cost_basis_local_exact=Decimal("11.128257887361"),
        cost_basis_local=Decimal("11.12825789"),
        unit_cost_local=Decimal("8.902606309889"),
        unit_cost_local_rounding_residual_exact=Decimal("0.000000000000"),
        cost_basis_base_exact=Decimal("80.123456789"),
        cost_basis_base=Decimal("80.12345679"),
        unit_cost_base=Decimal("64.098765431200"),
        unit_cost_base_rounding_residual_exact=Decimal("0"),
        local_cost_rounding_adjustment=Decimal("0"),
        base_cost_rounding_adjustment=Decimal("0"),
        base_cost_coverage_state="complete",
    )
    contribution = _output_row(
        portfolio_daily_contribution_output,
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 14),
        axis="instrument",
        group_key="instrument-1",
        group_label="Instrument One",
        measured=True,
        opening_nav_exact=Decimal("99"),
        opening_nav=Decimal("99"),
        closing_nav_exact=Decimal("99.1"),
        closing_nav=Decimal("99.1"),
        external_flow_in_exact=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out_exact=Decimal("0"),
        external_flow_out=Decimal("0"),
        internal_flow_in_exact=Decimal("0"),
        internal_flow_in=Decimal("0"),
        internal_flow_out_exact=Decimal("0"),
        internal_flow_out=Decimal("0"),
        economic_pnl_exact=Decimal("0.1000000000000000001"),
        economic_pnl=Decimal("0.1"),
        contribution_method50=Decimal("0.001000000000000000001"),
        contribution_published=Decimal("0.001"),
        contribution_division_adjustment_exact=Decimal("0"),
        contribution_rounding_adjustment=Decimal("0"),
        coverage_state="complete",
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [snapshot],
            [holding],
            [lot],
            [contribution],
            [_confirmation(publication_id=publication_id, run_id=run_id, token=token)],
            [
                {
                    "portfolio_id": "portfolio-exact",
                    "base_currency": "CNY",
                    "valuation_timezone": "Asia/Shanghai",
                    "operating_profile": "standard_taxonomy",
                    "canonical_config": {
                        "portfolio_name": "Exact Portfolio",
                        "operating_profile": "standard_taxonomy",
                        "default_planning_taxonomy_id": "taxonomy-risk",
                        "taxonomy": {
                            "taxonomies": [
                                {
                                    "taxonomy_id": "taxonomy-risk",
                                    "portfolio_id": "portfolio-exact",
                                    "name": "Risk Sleeves",
                                    "taxonomy_type": "risk_sleeve",
                                    "purpose": "Sealed overview composition",
                                    "primary_assignment_scope": "instrument",
                                    "planning_enabled": True,
                                    "budgeting_level": "leaf",
                                    "root_default_target_dimension": "weight",
                                    "status": "active",
                                    "source_template_ref": None,
                                }
                            ],
                            "nodes": [
                                {
                                    "taxonomy_node_id": "node-growth",
                                    "taxonomy_id": "taxonomy-risk",
                                    "parent_taxonomy_node_id": None,
                                    "node_name": "Growth",
                                    "node_code": "GROWTH",
                                    "sort_order": 1,
                                    "is_terminal": True,
                                    "default_target_dimension": "weight",
                                    "status": "active",
                                }
                            ],
                            "assignments": [
                                {
                                    "assignment_id": "assignment-instrument-1",
                                    "taxonomy_id": "taxonomy-risk",
                                    "target_scope": "instrument",
                                    "target_entity_id": "instrument-1",
                                    "taxonomy_node_id": "node-growth",
                                    "status": "active",
                                }
                            ],
                        },
                    },
                }
            ],
            [
                {
                    "instrument_id": "instrument-1",
                    "canonical_instrument": {
                        "instrument_id": "instrument-1",
                        "instrument_name": "Instrument One",
                        "instrument_type": "fund",
                        "currency": "USD",
                        "identifiers": [],
                    },
                }
            ],
        ]
    )
    app.dependency_overrides[get_db_session] = lambda: executor
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/workspace/holdings",
                params={"portfolio_id": "portfolio-exact"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    _assert_no_json_floats(payload)
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["rows"][0]["market_value_base"] == "100.123456789123"
    assert payload["rows"][0]["cost_basis_base"] == "80.123456789"
    assert payload["rows"][0]["unrealized_pnl_base"] == "20.000000000123"
    assert payload["rows"][0]["day_change_value"] is None
    assert payload["rows"][0]["day_change_value_base"] == "0.1000000000000000001"
    assert (
        payload["rows"][0]["portfolio_return_contribution"] == "0.001000000000000000001"
    )
    assert payload["totals"]["market_value"] == "100.123456789123"
    assert payload["totals"]["cost_basis"] == "80.123456789"
    assert payload["totals"]["unrealized_pnl_base"] == "20.000000000123"
    sealed_taxonomy = payload["sealed_display_config"]["taxonomy"]
    assert sealed_taxonomy["default_planning_taxonomy_id"] == "taxonomy-risk"
    assert sealed_taxonomy["taxonomies"][0]["name"] == "Risk Sleeves"
    assert sealed_taxonomy["taxonomy_nodes"][0]["node_name"] == "Growth"
    assert (
        sealed_taxonomy["taxonomy_assignments"][0]["target_entity_id"] == "instrument-1"
    )
    assert all(statement.is_select for statement in executor.statements)
    assert len(executor.statements) == 8


@pytest.mark.parametrize(
    ("operating_profile", "shows_allocation_lab"),
    [
        ("standard_taxonomy", True),
        ("external_etf_rotation", False),
    ],
)
def test_workspace_summary_sections_follow_operating_profile(
    monkeypatch,
    operating_profile: str,
    shows_allocation_lab: bool,
) -> None:
    as_of_date = date(2026, 7, 14)
    manifest_id = uuid4()
    publication = SimpleNamespace(
        metadata=SimpleNamespace(manifest_id=manifest_id),
        snapshots=(
            SimpleNamespace(
                base_currency="CNY",
                as_of_date=as_of_date,
                measured_nav=True,
                closing_nav=Decimal("110"),
                economic_pnl=Decimal("10"),
                subperiod_twr_method50=Decimal("0.1"),
                subperiod_twr_published=Decimal("0.1"),
                return_period_start_date=date(2026, 7, 13),
                return_period_end_date=as_of_date,
                return_period_day_count=1,
                nav_coverage_state="complete",
                nav_reason_codes=(),
                book_pnl_coverage_state="complete",
                book_pnl_reason_codes=(),
                return_coverage_state="complete",
                return_reason_codes=(),
                valuation_endpoint_status="fresh",
                valuation_reason_codes=(),
            ),
        ),
        holdings=(
            SimpleNamespace(instrument_id="instrument-a"),
            SimpleNamespace(instrument_id="instrument-a"),
        ),
    )
    metadata_payload = {
        "publication_id": str(uuid4()),
        "run_id": str(uuid4()),
        "manifest_id": str(manifest_id),
        "published_fencing_token": 1,
        "published_at": datetime(2026, 7, 14, 10, tzinfo=UTC),
        "calculated_at": datetime(2026, 7, 14, 9, tzinfo=UTC),
        "requested_as_of": as_of_date,
        "effective_as_of": as_of_date,
        "output_range_start": as_of_date,
        "output_range_end": as_of_date,
        "methodology_version": "portfolio-daily-v1",
        "output_schema_version": "portfolio-daily-output-v1",
        "canonical_output_hash": "a" * 64,
        "captured_generation": 1,
        "current_generation": 1,
        "stale": False,
        "pending": False,
        "pending_generation": None,
        "pending_run_id": None,
        "pending_run_status": None,
        "pending_intent_id": None,
        "pending_intent_status": None,
        "coverage_state": "complete",
        "reason_codes": [],
    }
    monkeypatch.setattr(
        workspace_routes,
        "read_published_latest",
        lambda *_args, **_kwargs: publication,
    )
    monkeypatch.setattr(
        workspace_routes,
        "_sealed_portfolio_config",
        lambda *_args, **_kwargs: {
            "portfolio_name": "Profile Portfolio",
            "valuation_timezone": "Asia/Shanghai",
            "operating_profile": operating_profile,
            "default_planning_taxonomy_id": (
                "taxonomy-risk" if operating_profile == "standard_taxonomy" else None
            ),
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "publication_metadata_response",
        lambda _metadata: SimpleNamespace(
            model_dump=lambda **_kwargs: metadata_payload
        ),
    )

    response = workspace_routes.workspace_summary(
        portfolio_id="portfolio-profile",
        session=object(),
    )

    assert isinstance(response, PortfolioWorkspaceSummaryResponse)
    assert response.operating_profile == operating_profile
    assert response.instrument_count == 1
    section_labels = {section.label for section in response.sections}
    assert ("Allocation Lab" in section_labels) is shows_allocation_lab
    _assert_no_json_floats(response.model_dump(mode="json"))
