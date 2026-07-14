from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_balance_output,
    portfolio_daily_holding_output,
    portfolio_daily_lot_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.calculations.numeric import quantize_decimal
from portfolio_app.db.session import get_db_session
from portfolio_app.main import app
from portfolio_app.api.routes import accounts as account_routes
from portfolio_ops_calculation_core.state import CalculationRunStatus
from tests.test_portfolio_daily_published_repository import (
    _RecordingExecutor,
    _confirmation,
    _metadata_row,
    _output_row,
)


pytestmark = pytest.mark.no_database


def _client(executor: _RecordingExecutor) -> TestClient:
    app.dependency_overrides[get_db_session] = lambda: executor
    return TestClient(app)


def _assert_select_only(executor: _RecordingExecutor) -> None:
    assert executor.statements
    assert all(statement.is_select for statement in executor.statements)


def _assert_no_json_floats(value: object) -> None:
    if isinstance(value, float):
        raise AssertionError(f"published API emitted JSON float {value!r}")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_json_floats(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_json_floats(item)


def _freshness_metadata(
    *,
    publication_id,
    run_id,
    token: int,
) -> dict[str, object]:
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    metadata.update(
        {
            "current_generation": 4,
            "pending_run_id": uuid4(),
            "pending_run_generation": 4,
            "pending_run_status": CalculationRunStatus.RUNNING,
            "pending_intent_id": uuid4(),
            "pending_intent_generation": 4,
            "pending_intent_status": "pending",
        }
    )
    return metadata


def test_positions_reads_only_current_published_holdings_with_canonical_decimals() -> (
    None
):
    publication_id = uuid4()
    run_id = uuid4()
    token = 71
    metadata = _freshness_metadata(
        publication_id=publication_id,
        run_id=run_id,
        token=token,
    )
    holding = _output_row(
        portfolio_daily_holding_output,
        run_id=run_id,
        token=token,
        account_id="account-1",
        instrument_id="instrument-1",
        currency="USD",
        quantity_exact=Decimal("1.230000"),
        quantity=Decimal("1.230000000000"),
        measured_price=True,
        measured_market_value=True,
        measured_book_pnl=True,
        adopted_price_exact=Decimal("10.5000"),
        price=Decimal("10.500000000000"),
        contract_multiplier_exact=Decimal("1.0000"),
        price_factor_exact=Decimal("1.0000"),
        adopted_fx_rate_exact=Decimal("7.2000"),
        market_value_local_exact=Decimal("12.9150000"),
        market_value_base_exact=Decimal("92.98800000"),
        cost_basis_local=Decimal("11.00000000"),
        cost_basis_base=Decimal("79.20000000"),
        economic_pnl_daily_base=Decimal("0.50000000"),
        portfolio_weight=Decimal("0.125000000000000000"),
        return_contribution=Decimal("0.010000000000000000"),
        valuation_coverage_state="complete",
        book_pnl_coverage_state="complete",
        position_attribution_coverage_state="complete",
        valuation_endpoint_status="fresh",
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [holding],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get("/api/portfolios/portfolio-exact/positions")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    _assert_no_json_floats(payload)
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["publication"]["stale"] is True
    assert payload["publication"]["pending"] is True
    assert payload["publication"]["pending_generation"] == 4
    assert payload["positions"][0]["quantity_exact"] == "1.23"
    assert payload["positions"][0]["adopted_price_exact"] == "10.5"
    assert payload["summary"]["market_value_base_exact"] == "92.988"
    _assert_select_only(executor)
    assert len(executor.statements) == 3
    output_statement = executor.statements[1]
    output_sql = str(output_statement.compile(dialect=postgresql.dialect()))
    assert "portfolio_daily_holding_output" in output_sql
    assert "portfolio_daily_lot_output" not in output_sql
    assert (
        publication_id
        in output_statement.compile(dialect=postgresql.dialect()).params.values()
    )


def test_position_lots_reads_only_published_open_lots_and_never_live_replays() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 72
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    lot = _output_row(
        portfolio_daily_lot_output,
        run_id=run_id,
        token=token,
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
        open_quantity_exact=Decimal("2.5000"),
        open_quantity=Decimal("2.500000000000"),
        measured_base_cost=True,
        acquisition_fx_rate_exact=Decimal("7.2000"),
        cost_basis_local_exact=Decimal("20.0000"),
        cost_basis_local=Decimal("20.00000000"),
        unit_cost_local=Decimal("8.000000000000"),
        unit_cost_local_rounding_residual_exact=Decimal("0.0000"),
        cost_basis_base_exact=Decimal("144.0000"),
        cost_basis_base=Decimal("144.00000000"),
        unit_cost_base=Decimal("57.600000000000"),
        unit_cost_base_rounding_residual_exact=Decimal("0.0000"),
        base_cost_coverage_state="complete",
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [lot],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/position-lots",
            params={"account_id": "account-1", "status": "open"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    _assert_no_json_floats(payload)
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["position_lots"][0]["open_quantity_exact"] == "2.5"
    assert payload["summary"]["cost_basis_base_exact"] == "144"
    _assert_select_only(executor)


@pytest.mark.parametrize("generation_changed", (False, True))
def test_accounts_workspace_reads_exact_financial_state_from_one_publication(
    monkeypatch: pytest.MonkeyPatch,
    generation_changed: bool,
) -> None:
    publication_id = uuid4()
    run_id = uuid4()
    manifest_id = uuid4()
    token = 73
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=manifest_id,
        token=token,
    )
    snapshot = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 14),
        subperiod_twr="0",
        inception_twr="0",
    )
    holding = _output_row(
        portfolio_daily_holding_output,
        run_id=run_id,
        token=token,
        account_id="broker-1",
        instrument_id="instrument-1",
        currency="USD",
        quantity_exact=Decimal("2.500000000000"),
        quantity=Decimal("2.500000000000"),
        measured_price=True,
        measured_market_value=True,
        adopted_price_exact=Decimal("10"),
        price=Decimal("10"),
        market_value_local_exact=Decimal("25"),
        market_value_base_exact=Decimal("180"),
        valuation_coverage_state="complete",
        valuation_endpoint_status="fresh",
    )
    balance = _output_row(
        portfolio_daily_balance_output,
        run_id=run_id,
        token=token,
        account_id="broker-1",
        component_type="pending_payable",
        component_key="transaction-1",
        currency="USD",
        measured_base_amount=True,
        local_amount=Decimal("0.10000000"),
        adopted_fx_rate_exact=Decimal("7.2"),
        fx_rate_to_base=Decimal("7.2"),
        base_amount_exact=Decimal("0.72000000"),
        base_amount=Decimal("0.72000000"),
        coverage_state="complete",
    )
    lot = _output_row(
        portfolio_daily_lot_output,
        run_id=run_id,
        token=token,
        account_id="broker-1",
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
        open_quantity_exact=Decimal("2.500000000000"),
        open_quantity=Decimal("2.500000000000"),
        measured_base_cost=True,
        acquisition_fx_rate_exact=Decimal("7.2"),
        cost_basis_local_exact=Decimal("20"),
        cost_basis_local=Decimal("20"),
        unit_cost_local=Decimal("8"),
        unit_cost_local_rounding_residual_exact=Decimal("0"),
        cost_basis_base_exact=Decimal("144"),
        cost_basis_base=Decimal("144"),
        unit_cost_base=Decimal("57.6"),
        unit_cost_base_rounding_residual_exact=Decimal("0"),
        base_cost_coverage_state="complete",
    )
    confirmation = _confirmation(
        publication_id=publication_id,
        run_id=run_id,
        token=token,
    )
    sealed_account = {
        "manifest_id": manifest_id,
        "run_id": run_id,
        "portfolio_id": "portfolio-exact",
        "account_id": "broker-1",
        "currency": "USD",
    }
    sealed_instrument = {
        "manifest_id": manifest_id,
        "run_id": run_id,
        "portfolio_id": "portfolio-exact",
        "instrument_id": "instrument-1",
        "instrument_name": "Exact Instrument",
        "instrument_type": "equity",
        "currency": "USD",
    }
    confirmed_metadata = dict(metadata)
    if generation_changed:
        confirmed_metadata["current_generation"] = int(
            confirmed_metadata["current_generation"]
        ) + 1
    executor = _RecordingExecutor(
        [
            [metadata],
            [snapshot],
            [holding],
            [balance],
            [lot],
            [confirmation],
            [sealed_account],
            [sealed_instrument],
            [confirmed_metadata],
            [confirmation],
        ]
    )
    account = {
        "account_id": "broker-1",
        "portfolio_id": "portfolio-exact",
        "account_name": "Exact Broker",
        "account_type": "securities_account",
        "currency": "USD",
        "institution": "Custodian",
        "default_settlement_cash_account_id": None,
        "cost_basis_method": "fifo",
        "allowed_instrument_types": ["equity"],
        "opened_at": date(2026, 1, 1),
        "closed_at": None,
        "status": "active",
    }
    new_account_after_publication = {
        "account_id": "cash-new",
        "portfolio_id": "portfolio-exact",
        "account_name": "New Cash Account",
        "account_type": "deposit_account",
        "currency": "CNY",
        "institution": "Bank",
        "default_settlement_cash_account_id": None,
        "cost_basis_method": None,
        "allowed_instrument_types": None,
        "opened_at": date(2026, 7, 14),
        "closed_at": None,
        "status": "active",
    }
    monkeypatch.setattr(
        account_routes,
        "get_portfolio",
        lambda portfolio_id: (
            {"portfolio_id": portfolio_id, "base_currency": "CNY"}
            if portfolio_id == "portfolio-exact"
            else None
        ),
    )
    monkeypatch.setattr(
        account_routes,
        "list_accounts",
        lambda portfolio_id: [account, new_account_after_publication],
    )
    monkeypatch.setattr(
        account_routes,
        "list_transactions",
        lambda portfolio_id, **kwargs: [],
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/accounts/workspace",
            params={"account_id": "broker-1"},
        )
    app.dependency_overrides.clear()

    if generation_changed:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == (
            "publication_context_changed_retry"
        )
        assert response.json()["detail"]["expected_generation"] == 3
        assert response.json()["detail"]["actual_generation"] == 4
        _assert_select_only(executor)
        assert len(executor.statements) == 10
        return

    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_no_json_floats(payload)
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["as_of_date"] == "2026-07-14"
    assert payload["accounts"][0]["account_value_base_exact"] == "179.28"
    assert payload["accounts"][0]["cost_basis_local_exact"] == "20"
    assert payload["balances"][0]["local_amount"] == "0.1"
    assert payload["positions"][0]["quantity_exact"] == "2.5"
    assert payload["positions"][0]["market_value_base_exact"] == "180"
    assert payload["positions"][0]["cost_basis_base_exact"] == "144"
    assert payload["summary"]["valuation_coverage_state"] == "partial"
    new_account_row = next(
        row for row in payload["accounts"] if row["account"]["account_id"] == "cash-new"
    )
    assert new_account_row["valuation_coverage_state"] == "unavailable"
    assert new_account_row["account_value_base_exact"] is None
    assert new_account_row["valuation_reason_codes"] == [
        "account_not_in_current_publication"
    ]
    _assert_select_only(executor)
    assert len(executor.statements) == 10


def _snapshot(
    *,
    run_id,
    token: int,
    as_of_date: date,
    subperiod_twr: str,
    inception_twr: str,
) -> dict[str, object]:
    subperiod = Decimal(subperiod_twr)
    cumulative = Decimal(inception_twr)
    wealth = Decimal("1") + cumulative
    published_subperiod = quantize_decimal(
        subperiod,
        scale=18,
        field_name="test subperiod TWR",
    )
    published_cumulative = quantize_decimal(
        cumulative,
        scale=18,
        field_name="test cumulative TWR",
    )
    published_wealth = quantize_decimal(
        wealth,
        scale=18,
        field_name="test wealth index",
    )
    return _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        as_of_date=as_of_date,
        base_currency="CNY",
        measured_nav=True,
        measured_position_market_value=True,
        measured_book_pnl=True,
        measured_return=True,
        measured_external_flows=True,
        opening_nav=Decimal("100.00000000"),
        closing_nav=Decimal("110.00000000"),
        position_market_value=Decimal("80.00000000"),
        settled_cash=Decimal("30.00000000"),
        pending_receivable=Decimal("0.00000000"),
        pending_payable=Decimal("0.00000000"),
        accrual_receivable=Decimal("0.00000000"),
        accrual_payable=Decimal("0.00000000"),
        external_flow_in=Decimal("0.00000000"),
        external_flow_out=Decimal("0.00000000"),
        economic_pnl=Decimal("10.00000000"),
        subperiod_twr_method50=subperiod,
        subperiod_twr_published=published_subperiod,
        cumulative_twr_method50=cumulative,
        cumulative_twr_published=published_cumulative,
        wealth_index_method50=wealth,
        wealth_index_published=published_wealth,
        peak_wealth_index_method50=wealth,
        peak_wealth_index_published=published_wealth,
        drawdown_method50=Decimal("0"),
        drawdown_published=Decimal("0"),
        wealth_chain_rounding_adjustment_exact=Decimal("0"),
        reliable_anchor_date=date(2026, 7, 1),
        return_period_start_date=as_of_date - timedelta(days=1),
        return_period_end_date=as_of_date,
        return_period_day_count=1,
        calculation_status="calculated",
        return_chain_status="active",
        nav_coverage_state="complete",
        book_pnl_coverage_state="complete",
        return_coverage_state="complete",
        flow_coverage_state="complete",
        position_attribution_coverage_state="complete",
        valuation_endpoint_status="fresh",
    )


def test_non_inception_performance_range_links_exact_subperiods_not_inception_chain() -> (
    None
):
    publication_id = uuid4()
    run_id = uuid4()
    token = 73
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    rows = [
        _snapshot(
            run_id=run_id,
            token=token,
            as_of_date=date(2026, 7, 10),
            subperiod_twr="0.10",
            inception_twr="0.50",
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of_date=date(2026, 7, 11),
            subperiod_twr="0.20",
            inception_twr="0.80",
        ),
    ]
    executor = _RecordingExecutor(
        [
            [metadata],
            rows,
            [],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/performance/report",
            params={"start_date": "2026-07-10", "end_date": "2026-07-11"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    _assert_no_json_floats(payload)
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["performance"]["cumulative_twr"]["method50"] == "0.32"
    assert payload["performance"]["cumulative_twr"]["method50"] != "0.8"
    assert payload["performance"]["return_chain_status"] == "linked"
    assert payload["performance"]["current_drawdown"]["method50"] == "0"
    assert payload["performance"]["max_drawdown"]["method50"] == "0"
    assert payload["daily_series"][0]["subperiod_twr_method50"] == "0.1"
    _assert_select_only(executor)


def test_daily_snapshots_reads_the_same_fenced_publication_without_materializing() -> (
    None
):
    publication_id = uuid4()
    run_id = uuid4()
    token = 75
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    row = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 10),
        subperiod_twr="0.125000",
        inception_twr="0.625000",
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [row],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/snapshots/daily",
            params={"start_date": "2026-07-10", "end_date": "2026-07-10"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["publication"]["publication_id"] == str(publication_id)
    assert payload["snapshots"][0]["subperiod_twr_method50"] == "0.125"
    assert payload["snapshots"][0]["cumulative_twr_method50"] == "0.625"
    assert payload["summary"]["snapshot_count"] == 1
    _assert_select_only(executor)
    output_sql = str(executor.statements[1].compile(dialect=postgresql.dialect()))
    assert "portfolio_daily_snapshot_output" in output_sql
    assert "portfolio_daily_holding_output" not in output_sql


def test_no_new_valuation_is_skipped_and_effective_return_boundary_is_disclosed() -> (
    None
):
    publication_id = uuid4()
    run_id = uuid4()
    token = 76
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    carried = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 10),
        subperiod_twr="0.10",
        inception_twr="0.50",
    )
    carried.update(
        {
            "measured_return": False,
            "subperiod_twr_method50": None,
            "subperiod_twr_published": None,
            "wealth_chain_rounding_adjustment_exact": None,
            "return_period_start_date": None,
            "return_period_end_date": None,
            "return_period_day_count": None,
            "calculation_status": "no_new_valuation",
            "return_chain_status": "no_new_valuation",
            "valuation_endpoint_status": "carry_forward",
        }
    )
    fresh = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 11),
        subperiod_twr="0.20",
        inception_twr="0.80",
    )
    fresh["return_period_start_date"] = date(2026, 7, 8)
    fresh["return_period_day_count"] = 3
    executor = _RecordingExecutor(
        [
            [metadata],
            [carried, fresh],
            [],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/performance/report",
            params={"start_date": "2026-07-10", "end_date": "2026-07-11"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    summary = response.json()["performance"]
    assert summary["cumulative_twr"]["method50"] == "0.2"
    assert summary["effective_return_start_date"] == "2026-07-08"
    assert summary["effective_return_end_date"] == "2026-07-11"


def test_broken_selected_performance_range_fails_closed() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 74
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    row = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 10),
        subperiod_twr="0.10",
        inception_twr="0.50",
    )
    row.update(
        {
            "measured_return": False,
            "subperiod_twr_method50": None,
            "subperiod_twr_published": None,
            "cumulative_twr_method50": None,
            "cumulative_twr_published": None,
            "wealth_index_method50": None,
            "wealth_index_published": None,
            "peak_wealth_index_method50": None,
            "peak_wealth_index_published": None,
            "drawdown_method50": None,
            "drawdown_published": None,
            "wealth_chain_rounding_adjustment_exact": None,
            "calculation_status": "broken",
            "return_chain_status": "broken",
            "return_coverage_state": "unavailable",
            "return_reason_codes": ["gap_external_flow"],
        }
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [row],
            [],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/performance/report",
            params={"start_date": "2026-07-10", "end_date": "2026-07-10"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    summary = response.json()["performance"]
    assert summary["cumulative_twr"] is None
    assert summary["return_chain_status"] == "unavailable"
    assert "selected_range_return_chain_broken" in summary["reason_codes"]


def test_missing_publication_returns_structured_calculation_not_ready() -> None:
    executor = _RecordingExecutor([[]])
    with _client(executor) as client:
        response = client.get("/api/portfolios/portfolio-exact/snapshots/daily")
    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "calculation_not_ready",
        "portfolio_id": "portfolio-exact",
        "start_date": None,
        "end_date": None,
        "reason": "current_publication_missing",
    }
    _assert_select_only(executor)


def test_legacy_performance_endpoints_are_not_routable() -> None:
    executor = _RecordingExecutor([])
    paths = (
        "/api/portfolios/portfolio-exact/performance",
        "/api/portfolios/portfolio-exact/performance/calendar",
        "/api/portfolios/portfolio-exact/performance/contribution",
        "/api/portfolios/portfolio-exact/performance/contribution/calendar",
    )

    with _client(executor) as client:
        statuses = [client.get(path).status_code for path in paths]
    app.dependency_overrides.clear()

    assert statuses == [404, 404, 404, 404]
    assert executor.statements == []


def test_corrupt_published_wealth_recurrence_fails_closed_with_503() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 79
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    first = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 10),
        subperiod_twr="0.10",
        inception_twr="0.50",
    )
    second = _snapshot(
        run_id=run_id,
        token=token,
        as_of_date=date(2026, 7, 11),
        subperiod_twr="0.20",
        inception_twr="0.80",
    )
    second["wealth_chain_rounding_adjustment_exact"] = Decimal("1E-50")
    executor = _RecordingExecutor(
        [
            [metadata],
            [first, second],
            [
                _confirmation(
                    publication_id=publication_id,
                    run_id=run_id,
                    token=token,
                )
            ],
        ]
    )

    with _client(executor) as client:
        response = client.get(
            "/api/portfolios/portfolio-exact/snapshots/daily",
            params={"start_date": "2026-07-10", "end_date": "2026-07-11"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ("published_reporting_integrity_error")
    assert "wealth recurrence" in response.json()["detail"]["reason"]
    _assert_select_only(executor)
