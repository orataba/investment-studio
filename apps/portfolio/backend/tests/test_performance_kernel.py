from __future__ import annotations

from math import isclose, sqrt
from copy import deepcopy

from portfolio_app.api.routes import performance as performance_routes
from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import ledger, performance, portfolio_store


def _write_store(store: dict[str, object]) -> None:
    portfolio_store.reset_store(store)


def _test_instrument_detail(
    *,
    asset_id: str,
    asset_name: str,
    history: list[tuple[str, str]],
    asset_type: str = "equity",
) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": asset_id.upper(), "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "status": "complete",
            }
            for as_of_date, value in history
        ],
    }


def _daily_snapshot_row_count(portfolio_id: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
            .count()
        )


def _daily_holding_row_count(portfolio_id: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailyHoldingSnapshotModel)
            .filter(PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id)
            .count()
        )


def _daily_contribution_slice_count(portfolio_id: str, axis: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailyContributionSliceModel)
            .filter(
                PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id,
                PortfolioDailyContributionSliceModel.axis == axis,
            )
            .count()
        )


def _minimal_store(
    *,
    portfolio_id: str,
    transactions: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "portfolios": [
            {
                "portfolio_id": portfolio_id,
                "portfolio_name": portfolio_id,
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": max(str(item["trade_date"]) for item in transactions),
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-usd-main",
                "portfolio_id": portfolio_id,
                "account_name": "Main USD Cash",
                "account_type": "deposit_account",
                "currency": "USD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-us-core",
                "portfolio_id": portfolio_id,
                "account_name": "Core Brokerage",
                "account_type": "securities_account",
                "currency": "USD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-usd-main",
                "cost_basis_method": "fifo",
                "allowed_asset_types": ["equity"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": transactions,
    }


def test_seed_portfolio_performance_uses_external_boundary_flows(client):
    snapshots_response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert snapshots_response.status_code == 200
    snapshots_payload = snapshots_response.json()
    assert snapshots_payload["summary"]["latest_complete_as_of_date"] == "2026-04-15"

    by_date = {item["as_of_date"]: item for item in snapshots_payload["snapshots"]}
    assert by_date["2026-02-03"]["external_cash_in"] == 50000.0
    assert by_date["2026-04-08"]["external_cash_in"] == 0.0
    assert by_date["2026-04-08"]["external_cash_out"] == 0.0
    assert by_date["2026-04-11"]["external_cash_out"] == 12000.0
    assert by_date["2026-04-12"]["external_cash_in"] == 0.0
    assert by_date["2026-04-12"]["external_cash_out"] == 0.0

    performance_response = client.get("/api/portfolios/yungu/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    summary = performance_payload["summary"]
    assert summary["latest_complete_as_of_date"] == "2026-04-15"
    assert summary["external_cash_in"] == 50000.0
    assert summary["external_cash_out"] == 12000.0
    assert summary["net_external_inflow"] == 38000.0
    assert summary["cumulative_twr"] is not None
    assert summary["irr"] is not None


def test_performance_endpoints_reuse_materialized_daily_snapshots(client, monkeypatch):
    first_response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert first_response.status_code == 200
    assert _daily_snapshot_row_count("yungu") > 0

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "yungu")
        assert state is not None
        assert state.daily_snapshot_status == "current"

    def fail_dynamic_snapshot_build(*_args, **_kwargs):
        raise AssertionError("materialized daily snapshots should satisfy this read")

    monkeypatch.setattr(performance, "build_daily_portfolio_snapshots", fail_dynamic_snapshot_build)

    second_snapshot_response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert second_snapshot_response.status_code == 200
    performance_response = client.get("/api/portfolios/yungu/performance")
    assert performance_response.status_code == 200


def test_refresh_materializes_holdings_and_contribution_slices(client):
    response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert response.status_code == 200

    assert _daily_snapshot_row_count("yungu") > 0
    assert _daily_holding_row_count("yungu") > 0
    assert _daily_contribution_slice_count("yungu", "instrument") > 0
    assert _daily_contribution_slice_count("yungu", "account") > 0


def test_holdings_and_contribution_endpoints_reuse_materialized_read_models(client, monkeypatch):
    response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert response.status_code == 200

    def fail_live_holdings(*_args, **_kwargs):
        raise AssertionError("materialized holdings should satisfy this read")

    def fail_dynamic_contribution(*_args, **_kwargs):
        raise AssertionError("materialized contribution slices should satisfy this read")

    monkeypatch.setattr(workspace_routes, "build_statement_of_assets_report", fail_live_holdings)
    monkeypatch.setattr(performance_routes, "build_contribution_report", fail_dynamic_contribution)

    holdings_response = client.get("/api/workspace/holdings?portfolio_id=yungu")
    assert holdings_response.status_code == 200
    assert holdings_response.json()["rows"]

    contribution_response = client.get("/api/portfolios/yungu/performance/contribution?axis=instrument")
    assert contribution_response.status_code == 200
    assert contribution_response.json()["daily_slices"]


def test_daily_snapshot_refresh_endpoint_refreshes_impacted_asset_portfolios(client):
    initial_response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert initial_response.status_code == 200

    refresh_response = client.post(
        "/api/portfolios/snapshots/daily/refresh",
        json={
            "asset_ids": ["fund-us-agg"],
            "dirty_from": "2026-04-14",
        },
    )
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["portfolio_ids"] == ["yungu"]
    assert refresh_payload["refreshed"][0]["snapshot_count"] == _daily_snapshot_row_count("yungu")

    empty_refresh_response = client.post(
        "/api/portfolios/snapshots/daily/refresh",
        json={"asset_ids": ["not-held"]},
    )
    assert empty_refresh_response.status_code == 200
    assert empty_refresh_response.json()["portfolio_ids"] == []


def test_daily_twr_neutralizes_external_deposit(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="twr-deposit-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 50.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Capital contribution.",
                "created_at": "2026-01-02T08:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get("/api/portfolios/twr-deposit-test/performance")
    assert response.status_code == 200
    payload = response.json()
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}

    assert by_date["2026-01-02"]["beginning_nav"] == 100.0
    assert by_date["2026-01-02"]["ending_nav"] == 160.0
    assert by_date["2026-01-02"]["external_cash_in"] == 50.0
    assert by_date["2026-01-02"]["daily_twr"] == 0.06666666666666665


def test_explicit_performance_period_uses_beginning_nav_boundary(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-03-31", "100.00"),
            ("2026-04-01", "110.00"),
            ("2026-04-20", "120.00"),
        ],
    )
    instrument_ref = {
        "asset_id": "equity-us-test",
        "asset_name": "Test Equity",
        "asset_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="period-boundary-nav-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-boundary-nav-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-03-31",
                "settlement_date": "2026-03-31",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-03-31T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-boundary-nav-test",
                "transaction_type": "buy",
                "trade_date": "2026-03-31",
                "settlement_date": "2026-03-31",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-03-31T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-04-20"
    _write_store(store)

    response = client.get(
        "/api/portfolios/period-boundary-nav-test/performance?start_date=2026-04-01&end_date=2026-04-20"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    first_point = payload["daily_series"][0]

    assert summary["start_date"] == "2026-04-01"
    assert summary["end_date"] == "2026-04-20"
    assert isclose(summary["start_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 120.0, rel_tol=0.0, abs_tol=1e-12)
    assert first_point["as_of_date"] == "2026-04-01"
    assert isclose(first_point["beginning_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(first_point["ending_nav"], 110.0, rel_tol=0.0, abs_tol=1e-12)

    calculation_response = client.get(
        "/api/portfolios/period-boundary-nav-test/performance/calculation?start_date=2026-04-01&end_date=2026-04-20"
    )
    assert calculation_response.status_code == 200
    calculation_summary = calculation_response.json()["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 120.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_daily_twr_ignores_internal_sale_but_cuts_on_withdrawal(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="twr-internal-sale-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/twr-internal-sale-test/performance")
    assert response.status_code == 200
    payload = response.json()
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}

    assert by_date["2026-01-02"]["external_cash_in"] == 0.0
    assert by_date["2026-01-02"]["external_cash_out"] == 0.0
    assert by_date["2026-01-02"]["daily_twr"] == 0.10000000000000009
    assert by_date["2026-01-03"]["external_cash_out"] == 110.0
    assert by_date["2026-01-03"]["daily_twr"] == 0.0


def test_performance_summary_reports_one_year_twr_annualized_and_irr(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2027-01-01", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="performance-one-year-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-one-year-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-one-year-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2027-01-01"
    _write_store(store)

    response = client.get("/api/portfolios/performance-one-year-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    last_point = payload["daily_series"][-1]
    expected_annualized = (1.1 ** (365.25 / 365.0)) - 1.0

    assert isclose(summary["cumulative_twr"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_twr"], expected_annualized, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["irr"], expected_annualized, rel_tol=0.0, abs_tol=1e-10)
    assert isclose(summary["mwror"], summary["irr"], rel_tol=0.0, abs_tol=1e-12)
    assert summary["external_cash_in"] == 0.0
    assert summary["external_cash_out"] == 0.0
    assert summary["realized_pnl"] == 0.0
    assert summary["unrealized_pnl"] == 10.0
    assert summary["total_pnl"] == 10.0
    assert last_point["cumulative_twr"] == summary["cumulative_twr"]
    assert last_point["unrealized_pnl"] == 10.0


def test_performance_summary_reports_pnl_decomposition_and_risk_metrics(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="performance-pnl-risk-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/performance-pnl-risk-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    expected_daily_returns = [0.10000000000000009]
    expected_mean_daily_return = sum(expected_daily_returns) / len(expected_daily_returns)
    expected_periods_per_year = 1 / 2 * performance.DAYS_PER_YEAR
    expected_annualized_mean = expected_mean_daily_return * expected_periods_per_year

    assert summary["realized_pnl"] == 10.0
    assert summary["unrealized_pnl"] == 0.0
    assert summary["income_cash_amount"] == 0.0
    assert summary["expense_cash_amount"] == 0.0
    assert summary["total_pnl"] == 10.0
    assert summary["return_observation_count"] == 2
    assert summary["risk_return_observation_count"] == 1
    assert isclose(summary["risk_annualization_periods_per_year"], expected_periods_per_year, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["mean_daily_return"], expected_mean_daily_return, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_return_from_daily_mean"], expected_annualized_mean, rel_tol=0.0, abs_tol=1e-12)
    assert summary["annualized_volatility"] is None
    assert summary["annualized_downside_volatility"] is None
    assert summary["sharpe_ratio"] is None
    assert summary["sortino_ratio"] is None
    assert summary["max_drawdown"] == 0.0
    assert summary["max_drawdown_days"] == 0
    assert summary["drawdown_duration_days"] is None
    assert by_date["2026-01-02"]["realized_pnl"] == 10.0
    assert by_date["2026-01-03"]["realized_pnl"] == 10.0
    assert by_date["2026-01-03"]["total_pnl"] == 10.0


def test_risk_metrics_exclude_carry_forward_non_trading_days(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-02", "100.00"),
            ("2026-01-05", "110.00"),
            ("2026-01-06", "105.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="non-trading-risk-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "non-trading-risk-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "non-trading-risk-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-02T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-06"
    _write_store(store)

    response = client.get("/api/portfolios/non-trading-risk-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    risk_returns = [0.10, 105.0 / 110.0 - 1.0]
    mean_return = sum(risk_returns) / len(risk_returns)
    daily_stddev = sqrt(sum((value - mean_return) ** 2 for value in risk_returns) / (len(risk_returns) - 1))
    expected_periods_per_year = 2 / 4 * performance.DAYS_PER_YEAR

    assert by_date["2026-01-03"]["market_observation_count"] == 0
    assert by_date["2026-01-03"]["return_observation_eligible"] is False
    assert by_date["2026-01-04"]["market_observation_count"] == 0
    assert by_date["2026-01-04"]["return_observation_eligible"] is False
    assert by_date["2026-01-05"]["market_observation_count"] == 1
    assert by_date["2026-01-05"]["return_observation_eligible"] is True
    assert by_date["2026-01-06"]["market_observation_count"] == 1
    assert by_date["2026-01-06"]["return_observation_eligible"] is True
    assert summary["return_observation_count"] == 4
    assert summary["risk_return_observation_count"] == 2
    assert isclose(summary["risk_annualization_periods_per_year"], expected_periods_per_year, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["mean_daily_return"], sum(risk_returns) / len(risk_returns), rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_volatility"], daily_stddev * sqrt(expected_periods_per_year), rel_tol=0.0, abs_tol=1e-12)

    window_response = client.get(
        "/api/portfolios/non-trading-risk-test/performance?start_date=2026-01-05&end_date=2026-01-06"
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    assert window_payload["summary"]["start_date"] == "2026-01-05"
    assert window_payload["summary"]["start_nav"] == 100.0
    assert isclose(window_payload["summary"]["cumulative_twr"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_materialized_window_summary_rebases_twr_and_drawdown(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
            ("2026-01-04", "108.90"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="window-rebase-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "window-rebase-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "window-rebase-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-04"
    _write_store(store)

    full_response = client.get("/api/portfolios/window-rebase-test/performance")
    assert full_response.status_code == 200
    assert isclose(full_response.json()["summary"]["cumulative_twr"], 0.089, rel_tol=0.0, abs_tol=1e-12)

    window_response = client.get(
        "/api/portfolios/window-rebase-test/performance?start_date=2026-01-03&end_date=2026-01-04"
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    summary = window_payload["summary"]
    by_date = {item["as_of_date"]: item for item in window_payload["daily_series"]}
    assert isclose(summary["cumulative_twr"], -0.01, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["current_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["max_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-03"]["cumulative_twr"], 0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-04"]["cumulative_twr"], -0.01, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-04"]["drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)


def test_window_drawdown_uses_period_start_anchor_when_first_return_is_loss(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "90.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="drawdown-anchor-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "drawdown-anchor-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "drawdown-anchor-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/drawdown-anchor-test/performance?start_date=2026-01-02&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    assert isclose(summary["current_drawdown"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["max_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert summary["max_drawdown_days"] == 1
    assert summary["drawdown_duration_days"] == 2
    assert isclose(by_date["2026-01-02"]["drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-03"]["drawdown"], 0.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_report_reconciles_initial_delta_transfers_and_final_value(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="period-calculation-bridge-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/period-calculation-bridge-test/performance/calculation")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    lines = {item["key"]: item for item in payload["lines"]}

    assert summary["initial_value"] == 100.0
    assert summary["final_value"] == 0.0
    assert summary["delta"] == 10.0
    assert summary["capital_gains"] == 10.0
    assert summary["realized_capital_gains"] == 10.0
    assert summary["unrealized_capital_gains"] == 0.0
    assert summary["earnings"] == 0.0
    assert summary["fees"] == 0.0
    assert summary["taxes"] == 0.0
    assert summary["deposits"] == 0.0
    assert summary["withdrawals"] == 110.0
    assert summary["net_external_inflow"] == -110.0
    assert "cash_fx_residual_gains" not in summary
    assert lines["performance_neutral_transfers"]["amount"] == -110.0
    assert lines["capital_gains"]["amount"] == 10.0
    assert lines["realized_capital_gains"]["amount"] == 10.0
    assert lines["unrealized_capital_gains"]["amount"] == 0.0
    assert lines["deposits"]["amount"] == 0.0
    assert lines["withdrawals"]["amount"] == 110.0


def test_period_calculation_report_splits_earnings_fees_and_taxes(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="period-calculation-income-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 1000.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend payment.",
                "created_at": "2026-01-02T10:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "fee",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 2.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Account fee.",
                "created_at": "2026-01-02T11:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "tax",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 3.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Tax payment.",
                "created_at": "2026-01-02T12:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/period-calculation-income-test/performance/calculation")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["initial_value"] == 1000.0
    assert summary["final_value"] == 1015.0
    assert summary["delta"] == 15.0
    assert summary["capital_gains"] == 0.0
    assert summary["realized_capital_gains"] == 0.0
    assert summary["earnings"] == 20.0
    assert summary["fees"] == 2.0
    assert summary["taxes"] == 3.0
    assert summary["deposits"] == 0.0
    assert summary["withdrawals"] == 0.0
    assert summary["net_external_inflow"] == 0.0
    assert "cash_fx_residual_gains" not in summary


def test_period_boundary_holdings_report_returns_start_and_end_positions(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/boundary-holdings-test/performance/boundary-holdings")
    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 0
    assert payload["summary"]["start_total_market_value_base"] == 100.0
    assert payload["summary"]["end_total_market_value_base"] == 0.0
    assert payload["start_positions"][0]["asset_id"] == "equity-us-test"
    assert payload["start_positions"][0]["account_ids"] == ["broker-us-core"]
    assert payload["start_positions"][0]["market_value_base"] == 100.0
    assert payload["start_positions"][0]["portfolio_weight"] == 1.0
    assert payload["end_positions"] == []


def test_period_boundary_holdings_can_filter_by_account_group(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-account-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-account-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-account-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/boundary-holdings-account-filter-test/performance/boundary-holdings"
        "?axis=account&group_key=broker-us-core"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "account"
    assert payload["summary"]["group_key"] == "broker-us-core"
    assert payload["summary"]["group_label"] == "Core Brokerage"
    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 1
    assert payload["start_positions"][0]["account_ids"] == ["broker-us-core"]


def test_period_boundary_holdings_can_filter_by_taxonomy_group(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-taxonomy-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-taxonomy-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-taxonomy-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "boundary-holdings-taxonomy-filter-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/boundary-holdings-taxonomy-filter-test/performance/boundary-holdings"
        "?axis=taxonomy&taxonomy_id=tax-sector&group_key=tax-sector-value"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["group_key"] == "tax-sector-value"
    assert payload["summary"]["group_label"] == "Value"
    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 1
    assert payload["start_positions"][0]["asset_id"] == "equity-us-test"


def test_return_calendar_report_rolls_daily_returns_into_monthly_buckets(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-31", "100.00"),
            ("2026-02-01", "110.00"),
            ("2026-03-01", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="return-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "return-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-31",
                "settlement_date": "2026-01-31",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-31T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "return-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-31",
                "settlement_date": "2026-01-31",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-31T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-03-01"
    _write_store(store)

    response = client.get("/api/portfolios/return-calendar-test/performance/calendar?frequency=monthly")
    assert response.status_code == 200
    payload = response.json()
    buckets = {item["bucket_key"]: item for item in payload["buckets"]}

    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 3
    assert buckets["2026-01"]["cumulative_twr"] is None
    assert buckets["2026-02"]["cumulative_twr"] == 0.10000000000000009
    assert buckets["2026-03"]["cumulative_twr"] == 0.10000000000000009
    assert buckets["2026-02"]["start_nav"] == 100.0
    assert buckets["2026-02"]["end_nav"] == 110.0
    assert buckets["2026-03"]["start_nav"] == 110.0
    assert buckets["2026-03"]["end_nav"] == 121.0


def test_instrument_contribution_report_tracks_daily_pnl_and_residual(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get("/api/portfolios/instrument-contribution-test/performance/contribution?axis=instrument")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert isclose(summary["portfolio_arithmetic_return"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "equity-us-test")
    assert isclose(line["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["unrealized_pnl_change"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "equity-us-test"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert slice_by_date["2026-01-01"]["daily_contribution"] is None
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_cost_basis_method_changes_book_split_not_economic_contribution(client, monkeypatch):
    asset_id = "equity-us-cost-method"
    instrument_ref = {
        "asset_id": asset_id,
        "asset_name": "Asset A",
        "asset_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "A", "is_primary": True}],
    }
    instrument_detail = _test_instrument_detail(
        asset_id=asset_id,
        asset_name="Asset A",
        history=[
            ("2026-01-01", "50.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "75.00"),
            ("2026-01-04", "80.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda requested_asset_id: deepcopy(instrument_detail) if requested_asset_id == asset_id else None,
    )
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_detail",
        lambda requested_asset_id: deepcopy(instrument_detail) if requested_asset_id == asset_id else None,
    )
    monkeypatch.setattr(
        workspace_routes,
        "build_asset_sparkline",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    def transaction(
        transaction_id: str,
        transaction_type: str,
        trade_date: str,
        *,
        account_id: str,
        asset_id_value: str | None,
        quantity: float | None,
        price: float | None,
        gross_amount: float,
        note: str,
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "portfolio_id": "",
            "transaction_type": transaction_type,
            "trade_date": trade_date,
            "settlement_date": trade_date,
            "account_id": account_id,
            "settlement_cash_account_id": (
                "cash-usd-main" if transaction_type in {"buy", "sell"} else None
            ),
            "asset_id": asset_id_value,
            "instrument_ref": deepcopy(instrument_ref) if asset_id_value else None,
            "quantity": quantity,
            "price": price,
            "gross_amount": gross_amount,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": note,
            "created_at": f"{trade_date}T09:00:00Z",
        }

    def build_store(portfolio_id: str, cost_basis_method: str, *, final_sale: bool = False) -> dict[str, object]:
        transactions = [
            transaction(
                "txn-0001",
                "opening_balance",
                "2026-01-01",
                account_id="cash-usd-main",
                asset_id_value=None,
                quantity=None,
                price=None,
                gross_amount=750000.0,
                note="Opening cash.",
            ),
            transaction(
                "txn-0002",
                "buy",
                "2026-01-01",
                account_id="broker-us-core",
                asset_id_value=asset_id,
                quantity=5000.0,
                price=50.0,
                gross_amount=250000.0,
                note="Buy 5000 at 50.",
            ),
            transaction(
                "txn-0003",
                "buy",
                "2026-01-02",
                account_id="broker-us-core",
                asset_id_value=asset_id,
                quantity=5000.0,
                price=100.0,
                gross_amount=500000.0,
                note="Buy 5000 at 100.",
            ),
            transaction(
                "txn-0004",
                "sell",
                "2026-01-03",
                account_id="broker-us-core",
                asset_id_value=asset_id,
                quantity=6000.0,
                price=75.0,
                gross_amount=450000.0,
                note="Sell 6000 at 75.",
            ),
        ]
        if final_sale:
            transactions.append(
                transaction(
                    "txn-0005",
                    "sell",
                    "2026-01-04",
                    account_id="broker-us-core",
                    asset_id_value=asset_id,
                    quantity=4000.0,
                    price=80.0,
                    gross_amount=320000.0,
                    note="Sell remaining 4000 at 80.",
                )
            )
        for item in transactions:
            item["portfolio_id"] = portfolio_id
        store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
        store["accounts"][1]["cost_basis_method"] = cost_basis_method
        return store

    def run_case(portfolio_id: str, cost_basis_method: str) -> dict[str, object]:
        _write_store(build_store(portfolio_id, cost_basis_method))

        holdings_response = client.get(f"/api/workspace/holdings?portfolio_id={portfolio_id}")
        assert holdings_response.status_code == 200
        holding = next(row for row in holdings_response.json()["rows"] if row["asset_core"]["asset_id"] == asset_id)

        contribution_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/contribution?axis=instrument"
        )
        assert contribution_response.status_code == 200
        contribution_payload = contribution_response.json()
        line = next(item for item in contribution_payload["lines"] if item["group_key"] == asset_id)
        calculation_groups_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument"
        )
        assert calculation_groups_response.status_code == 200
        calculation_group = next(
            item
            for item in calculation_groups_response.json()["groups"]
            if item["group_key"] == asset_id
        )
        performance_response = client.get(f"/api/portfolios/{portfolio_id}/performance")
        assert performance_response.status_code == 200
        return {
            "holding": holding,
            "line": line,
            "calculation_group": calculation_group,
            "summary": performance_response.json()["summary"],
        }

    fifo = run_case("cost-method-fifo-test", "fifo")
    moving_average = run_case("cost-method-ma-test", "moving_average")

    assert fifo["holding"]["quantity"] == 4000.0
    assert moving_average["holding"]["quantity"] == 4000.0
    assert fifo["holding"]["market_value"] == 300000.0
    assert moving_average["holding"]["market_value"] == 300000.0

    assert fifo["holding"]["cost_basis"] == 400000.0
    assert fifo["line"]["realized_pnl"] == 100000.0
    assert fifo["line"]["unrealized_pnl_change"] == -100000.0
    assert fifo["calculation_group"]["capital_gains"] == 0.0
    assert fifo["calculation_group"]["realized_capital_gains"] == 100000.0
    assert fifo["calculation_group"]["unrealized_pnl_change"] == -100000.0
    assert fifo["calculation_group"]["total_pnl"] == 0.0
    assert fifo["calculation_group"]["average_weight"] is not None
    assert fifo["calculation_group"]["ending_weight"] is not None

    assert moving_average["holding"]["cost_basis"] == 300000.0
    assert moving_average["line"]["realized_pnl"] == 0.0
    assert moving_average["line"]["unrealized_pnl_change"] == 0.0
    assert moving_average["calculation_group"]["capital_gains"] == 0.0
    assert moving_average["calculation_group"]["realized_capital_gains"] == 100000.0
    assert moving_average["calculation_group"]["unrealized_pnl_change"] == -100000.0
    assert moving_average["calculation_group"]["total_pnl"] == 0.0

    assert fifo["line"]["total_pnl"] == 0.0
    assert moving_average["line"]["total_pnl"] == 0.0
    assert isclose(
        fifo["line"]["period_contribution"],
        moving_average["line"]["period_contribution"],
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        fifo["summary"]["cumulative_twr"],
        moving_average["summary"]["cumulative_twr"],
        rel_tol=0.0,
        abs_tol=1e-12,
    )

    _write_store(build_store("cost-method-sold-out-test", "fifo", final_sale=True))
    sold_out_response = client.get(
        "/api/portfolios/cost-method-sold-out-test/performance/calculation/groups?axis=instrument"
    )
    assert sold_out_response.status_code == 200
    sold_out_group = next(
        item
        for item in sold_out_response.json()["groups"]
        if item["group_key"] == asset_id
    )
    assert sold_out_group["final_value"] == 0.0
    assert sold_out_group["capital_gains"] == 20000.0
    assert sold_out_group["realized_capital_gains"] == 20000.0
    assert sold_out_group["unrealized_pnl_change"] == 0.0

    sold_out_summary_response = client.get(
        "/api/portfolios/cost-method-sold-out-test/performance/calculation"
    )
    assert sold_out_summary_response.status_code == 200
    sold_out_summary = sold_out_summary_response.json()["summary"]
    assert sold_out_summary["capital_gains"] == 20000.0
    assert sold_out_summary["realized_capital_gains"] == 20000.0
    assert sold_out_summary["unrealized_capital_gains"] == 0.0


def test_account_contribution_report_tracks_interest_income(client, monkeypatch):
    monkeypatch.setattr(performance, "get_registry_instrument_detail", lambda asset_id: None)
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="account-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "account-contribution-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Interest income.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/account-contribution-test/performance/contribution?axis=account")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "account"
    assert isclose(summary["portfolio_arithmetic_return"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "cash-usd-main")
    assert isclose(line["income_cash_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "cash-usd-main"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_and_calculation_capture_attached_buy_charges(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-fee-test",
        asset_name="Fee Test Equity",
        history=[
            ("2026-01-02", "93.00"),
            ("2026-01-03", "93.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-fee-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-fee-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-fee-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-fee-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-fee-test",
                "instrument_ref": {
                    "asset_id": "equity-us-fee-test",
                    "asset_name": "Fee Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "FEE", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 93.0,
                "gross_amount": 93.0,
                "fees": 5.0,
                "taxes": 2.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with attached charges.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    contribution_response = client.get(
        "/api/portfolios/instrument-contribution-fee-test/performance/contribution?axis=instrument"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    summary = contribution_payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], -0.07, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], -0.07, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in contribution_payload["lines"] if item["group_key"] == "equity-us-fee-test")
    assert isclose(line["expense_cash_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["fee_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["tax_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], -7.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], -0.07, rel_tol=0.0, abs_tol=1e-12)

    contribution_entries_response = client.get(
        "/api/portfolios/instrument-contribution-fee-test/performance/contribution/entries"
        "?axis=instrument&bucket=fee_amount"
    )
    assert contribution_entries_response.status_code == 200
    contribution_entries_payload = contribution_entries_response.json()
    assert contribution_entries_payload["summary"]["entry_count"] == 0
    assert isclose(contribution_entries_payload["summary"]["total_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert contribution_entries_payload["entries"] == []

    calculation_response = client.get("/api/portfolios/instrument-contribution-fee-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["capital_gains"], -7.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["fees"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["taxes"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary


def test_instrument_contribution_report_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-filter-test/performance/contribution"
        "?axis=instrument&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["group_count"] == 1
    assert summary["slice_count"] == 2
    assert summary["observation_count"] == 1
    assert isclose(summary["portfolio_arithmetic_return"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], -0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["lines"]) == 1
    line = payload["lines"][0]
    assert line["group_key"] == "equity-us-alpha"
    assert isclose(line["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_bucket_drilldown_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-drilldown-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-drilldown-filter-test/performance/contribution/drilldown"
        "?axis=instrument&bucket=contribution&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "contribution"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["group_count"] == 1
    assert isclose(summary["total_amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["group_key"] == "equity-us-alpha"
    assert isclose(group["amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_can_filter_income_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-entries-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-02T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-02T12:01:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-entries-filter-test/performance/contribution/entries"
        "?axis=instrument&bucket=income_cash_amount&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "income_cash_amount"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["group_key"] == "equity-us-alpha"
    assert entry["component_kind"] == "gross_amount"
    assert isclose(entry["base_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_report_surfaces_asset_currency_gains(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    fund_detail = {
        "asset_id": "fund-hk-contribution-test",
        "asset_name": "HK Contribution Fund",
        "asset_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKCONFUND", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-contribution-test": fund_detail,
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "instrument-asset-fx-contribution-test",
                "portfolio_name": "instrument-asset-fx-contribution-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "instrument-asset-fx-contribution-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": "fifo",
                "allowed_asset_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-asset-fx-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "asset_id": "fund-hk-contribution-test",
                "instrument_ref": {
                    "asset_id": "fund-hk-contribution-test",
                    "asset_name": "HK Contribution Fund",
                    "asset_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HK fund.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-asset-fx-contribution-test/performance/contribution?axis=instrument"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "fund-hk-contribution-test")
    assert isclose(line["asset_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["unrealized_pnl_change"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "fund-hk-contribution-test"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["asset_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["unrealized_pnl_change"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_bucket_drilldown_surfaces_asset_currency_gains(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    fund_detail = {
        "asset_id": "fund-hk-contribution-drilldown-test",
        "asset_name": "HK Drilldown Fund",
        "asset_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKDRILL", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-contribution-drilldown-test": fund_detail,
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "instrument-asset-fx-contribution-drilldown-test",
                "portfolio_name": "instrument-asset-fx-contribution-drilldown-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "instrument-asset-fx-contribution-drilldown-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": "fifo",
                "allowed_asset_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-asset-fx-contribution-drilldown-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "asset_id": "fund-hk-contribution-drilldown-test",
                "instrument_ref": {
                    "asset_id": "fund-hk-contribution-drilldown-test",
                    "asset_name": "HK Drilldown Fund",
                    "asset_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HK fund.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-asset-fx-contribution-drilldown-test/performance/contribution/drilldown"
        "?axis=instrument&bucket=asset_currency_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert isclose(payload["summary"]["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["group_key"] == "fund-hk-contribution-drilldown-test"
    assert isclose(group["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_support_realized_pnl_realizations(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-realized-entry",
        asset_name="Realized Entry Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-realized-entry" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-realized-entry-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-realized-entry",
                "instrument_ref": {
                    "asset_id": "equity-us-realized-entry",
                    "asset_name": "Realized Entry Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "REAL", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy realized equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-realized-entry",
                "instrument_ref": {
                    "asset_id": "equity-us-realized-entry",
                    "asset_name": "Realized Entry Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "REAL", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell realized equity.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-realized-entry-test/performance/contribution/entries"
        "?axis=instrument&bucket=realized_pnl"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "realization"
    assert entry["transaction_type"] == "sell"
    assert entry["group_key"] == "equity-us-realized-entry"
    assert isclose(entry["base_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_account_contribution_report_tracks_cash_currency_gains(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(fx_detail) if asset_id == "fx-usd-hkd" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "account-cash-fx-contribution-test",
                "portfolio_name": "account-cash-fx-contribution-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "account-cash-fx-contribution-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-cash-fx-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get("/api/portfolios/account-cash-fx-contribution-test/performance/contribution?axis=account")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "cash-hkd-main")
    assert isclose(line["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "cash-hkd-main"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_catalog_route_returns_taxonomy_tree_and_assignments(client):
    store = _minimal_store(
        portfolio_id="taxonomy-catalog-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-catalog-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    )
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-catalog-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get("/api/portfolios/taxonomy-catalog-test/taxonomies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == "taxonomy-catalog-test"
    assert len(payload["taxonomies"]) == 1
    assert payload["taxonomies"][0]["taxonomy_id"] == "tax-sector"
    assert len(payload["taxonomy_nodes"]) == 1
    assert payload["taxonomy_nodes"][0]["node_name"] == "Value"
    assert len(payload["taxonomy_assignments"]) == 1
    assert payload["taxonomy_assignments"][0]["target_entity_id"] == "equity-us-test"


def test_taxonomy_contribution_report_regroups_instrument_slices_by_effective_assignment(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="taxonomy-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-contribution-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-contribution-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-contribution-test/performance/contribution"
        "?axis=taxonomy&taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert isclose(summary["portfolio_arithmetic_return"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    lines = {item["group_key"]: item for item in payload["lines"]}
    assert isclose(lines["tax-sector-value"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-value"]["period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-value"]["end_value_base"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-growth"]["total_pnl"], 11.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-growth"]["period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-growth"]["end_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)

    slices = {(item["as_of_date"], item["group_key"]): item for item in payload["daily_slices"]}
    assert isclose(slices[("2026-01-02", "tax-sector-value")]["daily_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        slices[("2026-01-03", "tax-sector-growth")]["daily_contribution"],
        0.1,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def test_taxonomy_contribution_and_entries_support_cash_bucket_scope(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-cash-bucket",
        asset_name="Cash Bucket Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-cash-bucket" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="taxonomy-cash-bucket-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": None,
                "asset_id": "equity-us-cash-bucket",
                "instrument_ref": {
                    "asset_id": "equity-us-cash-bucket",
                    "asset_name": "Cash Bucket Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "CASH", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open equity position.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Interest income.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-liquidity",
            "portfolio_id": "taxonomy-cash-bucket-test",
            "name": "Liquidity",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "cash_bucket",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-liquidity-core",
            "taxonomy_id": "tax-liquidity",
            "parent_taxonomy_node_id": None,
            "node_name": "Core Cash",
            "node_code": "CORE_CASH",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-liquidity",
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": "tax-liquidity-core",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    contribution_response = client.get(
        "/api/portfolios/taxonomy-cash-bucket-test/performance/contribution"
        "?axis=taxonomy&taxonomy_id=tax-liquidity"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    summary = contribution_payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-liquidity"
    assert isclose(summary["portfolio_arithmetic_return"], 0.075, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.025, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(contribution_payload["lines"]) == 1
    line = contribution_payload["lines"][0]
    assert line["group_key"] == "tax-liquidity-core"
    assert isclose(line["income_cash_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.025, rel_tol=0.0, abs_tol=1e-12)

    entries_response = client.get(
        "/api/portfolios/taxonomy-cash-bucket-test/performance/contribution/entries"
        "?axis=taxonomy&taxonomy_id=tax-liquidity&bucket=income_cash_amount"
    )
    assert entries_response.status_code == 200
    entries_payload = entries_response.json()
    assert entries_payload["summary"]["entry_count"] == 1
    assert isclose(entries_payload["summary"]["total_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    entry = entries_payload["entries"][0]
    assert entry["group_key"] == "tax-liquidity-core"
    assert entry["component_kind"] == "gross_amount"
    assert entry["transaction_id"] == "txn-0003"
    assert isclose(entry["base_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-filter-test/performance/contribution/calendar"
        "?axis=instrument&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["observation_count"] == 1
    assert isclose(summary["total_bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["group_label"] == "Alpha Equity"
    assert isclose(bucket["bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_bucket_drilldown_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-drilldown-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-drilldown-filter-test/performance/contribution/calendar/drilldown"
        "?axis=instrument&frequency=monthly&bucket=contribution&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "contribution"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["observation_count"] == 1
    assert isclose(summary["total_amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert isclose(bucket["amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_calendar_can_filter_income_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-15", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-15", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-entries-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-15T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-15T12:01:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-15"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-entries-calendar-filter-test/performance/contribution/entries/calendar"
        "?axis=instrument&bucket=income_cash_amount&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "income_cash_amount"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["contribution_bucket"] == "income_cash_amount"
    assert isclose(bucket["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_rolls_daily_slices_into_monthly_bucket(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-test/performance/contribution/calendar"
        "?axis=instrument&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["frequency"] == "monthly"
    assert summary["bucket_count"] == 1
    assert isclose(summary["total_bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["bucket_key"] == "2026-01"
    assert bucket["group_key"] == "equity-us-test"
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_contribution_calendar_keeps_reassignment_from_creating_false_bucket_pnl(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="taxonomy-contribution-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-contribution-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-contribution-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-contribution-calendar-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-contribution-calendar-test/performance/contribution/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert summary["frequency"] == "monthly"
    assert isclose(summary["total_bucket_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    buckets = {item["group_key"]: item for item in payload["buckets"]}
    assert isclose(buckets["tax-sector-value"]["bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets["tax-sector-value"]["ending_value_base"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets["tax-sector-growth"]["bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets["tax-sector-growth"]["ending_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_calendar_rolls_monthly_instrument_bridge(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-instrument-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-instrument-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-instrument-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-instrument-test/performance/calculation/groups/calendar"
        "?axis=instrument&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "instrument"
    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 1
    assert isclose(payload["summary"]["total_delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["bucket_key"] == "2026-01"
    assert bucket["group_key"] == "equity-us-test"
    assert isclose(bucket["initial_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_support_asset_type_axis(client, monkeypatch):
    equity_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
        asset_type="equity",
    )
    fund_detail = _test_instrument_detail(
        asset_id="fund-us-test",
        asset_name="Test Fund",
        history=[
            ("2026-01-01", "200.00"),
            ("2026-01-02", "190.00"),
        ],
        asset_type="fund",
    )
    instrument_details = {
        "equity-us-test": equity_detail,
        "fund-us-test": fund_detail,
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    portfolio_id = "calculation-groups-asset-type-test"
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "asset_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 300.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": "Opening cash.",
            "created_at": "2026-01-01T09:00:00Z",
        },
    ]
    for index, (asset_id, asset_name, asset_type, amount) in enumerate(
        [
            ("equity-us-test", "Test Equity", "equity", 100.0),
            ("fund-us-test", "Test Fund", "fund", 200.0),
        ],
        start=2,
    ):
        transactions.append(
            {
                "transaction_id": f"txn-{index:04d}",
                "portfolio_id": portfolio_id,
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": asset_id,
                "instrument_ref": {
                    "asset_id": asset_id,
                    "asset_name": asset_name,
                    "asset_type": asset_type,
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": asset_id.upper(), "is_primary": True}],
                },
                "quantity": 1.0,
                "price": amount,
                "gross_amount": amount,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": f"Buy {asset_name}.",
                "created_at": f"2026-01-01T09:{index}0:00Z",
            }
        )

    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=asset_type")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "asset_type"
    groups = {item["group_key"]: item for item in payload["groups"]}

    assert groups["equity"]["group_label"] == "Equity"
    assert isclose(groups["equity"]["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity"]["unrealized_pnl_change"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert groups["fund"]["group_label"] == "Fund"
    assert isclose(groups["fund"]["final_value"], 190.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["fund"]["total_pnl"], -10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["fund"]["unrealized_pnl_change"], -10.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_calendar_supports_monthly_taxonomy_bridge(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-groups-calendar-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-taxonomy-test/performance/calculation/groups/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["frequency"] == "monthly"
    assert isclose(payload["summary"]["total_delta"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "tax-sector-value"
    assert isclose(bucket["initial_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["delta"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-filter-test/performance/calculation/groups/calendar"
        "?axis=instrument&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["group_key"] == "equity-us-alpha"
    assert payload["summary"]["group_label"] == "Alpha Equity"
    assert payload["summary"]["bucket_count"] == 1
    assert payload["summary"]["group_count"] == 1
    assert payload["summary"]["observation_count"] == 1
    assert isclose(payload["summary"]["total_delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert isclose(bucket["bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_boundary_groups_report_uses_effective_assignment_on_start_and_end_dates(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="taxonomy-boundary-groups-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-boundary-groups-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-boundary-groups-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-boundary-groups-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-boundary-groups-test/performance/boundary-groups?taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["start_group_count"] == 1
    assert payload["summary"]["end_group_count"] == 1
    assert payload["start_groups"][0]["group_key"] == "tax-sector-value"
    assert payload["end_groups"][0]["group_key"] == "tax-sector-growth"
    assert isclose(payload["start_groups"][0]["market_value_base"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["end_groups"][0]["market_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_calculation_groups_report_surfaces_residual_reclassification_delta(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="taxonomy-calculation-groups-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-calculation-groups-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-calculation-groups-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-calculation-groups-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-calculation-groups-test/performance/calculation/groups"
        "?axis=taxonomy&taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert isclose(summary["total_initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_delta"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_residual_delta"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    groups = {item["group_key"]: item for item in payload["groups"]}
    assert isclose(groups["tax-sector-value"]["delta"], -100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-value"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-value"]["residual_delta"], -110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["delta"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["total_pnl"], 11.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["residual_delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_drilldown_returns_instrument_capital_gains(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-drilldown-instrument-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-drilldown-instrument-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-drilldown-instrument-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-drilldown-instrument-test/performance/calculation/drilldown"
        "?axis=instrument&bucket=capital_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "instrument"
    assert payload["summary"]["bucket"] == "capital_gains"
    assert isclose(payload["summary"]["total_amount"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["group_key"] == "equity-us-test"
    assert isclose(group["amount"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_drilldown_taxonomy_exposes_reclassification_residual(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-drilldown-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-drilldown-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-drilldown-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-drilldown-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-drilldown-taxonomy-test/performance/calculation/drilldown"
        "?axis=taxonomy&taxonomy_id=tax-sector&bucket=residual_delta"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["bucket"] == "residual_delta"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert isclose(payload["summary"]["total_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    groups = {item["group_key"]: item for item in payload["groups"]}
    assert isclose(groups["tax-sector-value"]["amount"], -110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["amount"], 110.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_extract_attached_tax_bucket_by_instrument(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-tax-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 3.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend with withholding tax.",
                "created_at": "2026-01-02T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-tax-test/performance/calculation/entries"
        "?axis=instrument&bucket=taxes"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "taxes"
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 3.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "transaction"
    assert entry["component_kind"] == "attached_tax"
    assert entry["transaction_id"] == "txn-0003"
    assert entry["group_key"] == "equity-us-test"
    assert isclose(entry["base_amount"], 3.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_return_realized_gain_realizations(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-realized-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-realized-test/performance/calculation/entries"
        "?axis=instrument&bucket=realized_capital_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "realized_capital_gains"
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "realization"
    assert entry["component_kind"] == "realization"
    assert entry["transaction_id"] == "txn-0003"
    assert entry["group_key"] == "equity-us-test"
    assert isclose(entry["base_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_rolls_up_monthly_earnings(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-02-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-earnings-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "January dividend.",
                "created_at": "2026-01-02T12:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "dividend",
                "trade_date": "2026-02-03",
                "settlement_date": "2026-02-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "February dividend.",
                "created_at": "2026-02-03T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-02-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-earnings-test/performance/calculation/entries/calendar"
        "?axis=instrument&bucket=earnings&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "earnings"
    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 2
    assert payload["summary"]["entry_count"] == 2
    assert isclose(payload["summary"]["total_amount"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    buckets = {(item["bucket_key"], item["group_key"]): item for item in payload["buckets"]}
    assert isclose(buckets[("2026-01", "equity-us-test")]["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets[("2026-02", "equity-us-test")]["total_amount"], 30.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_rolls_up_taxonomy_taxes(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-test",
        asset_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-15", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-test",
                "instrument_ref": {
                    "asset_id": "equity-us-test",
                    "asset_name": "Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 4.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend with tax.",
                "created_at": "2026-01-15T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-15"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-entries-calendar-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-income",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Income",
            "node_code": "INCOME",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-income",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-taxonomy-test/performance/calculation/entries/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&bucket=taxes&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["bucket"] == "taxes"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["bucket_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "tax-sector-income"
    assert isclose(bucket["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            asset_id="equity-us-alpha",
            asset_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-20", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            asset_id="equity-us-beta",
            asset_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-20", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-alpha",
                "instrument_ref": {
                    "asset_id": "equity-us-alpha",
                    "asset_name": "Alpha Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 10.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-15T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-20",
                "settlement_date": "2026-01-20",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-beta",
                "instrument_ref": {
                    "asset_id": "equity-us-beta",
                    "asset_name": "Beta Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 15.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-20T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-20"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-filter-test/performance/calculation/entries/calendar"
        "?axis=instrument&bucket=earnings&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["group_key"] == "equity-us-alpha"
    assert payload["summary"]["group_label"] == "Alpha Equity"
    assert payload["summary"]["bucket_count"] == 1
    assert payload["summary"]["group_count"] == 1
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["group_label"] == "Alpha Equity"
    assert isclose(bucket["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_cash_currency_gains_flow_through_performance_and_calculation(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(fx_detail) if asset_id == "fx-usd-hkd" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "cash-fx-test",
                "portfolio_name": "cash-fx-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "cash-fx-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "cash-fx-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/cash-fx-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["cumulative_twr"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    calculation_response = client.get("/api/portfolios/cash-fx-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary
    assert "cash_fx_residual_gains" not in calculation_summary

    line_by_key = {item["key"]: item for item in calculation_payload["lines"]}
    assert isclose(line_by_key["cash_currency_gains"]["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in line_by_key


def test_asset_currency_gains_flow_through_performance_and_calculation(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    hk_fund_detail = {
        "asset_id": "fund-hk-test",
        "asset_name": "HK Fund",
        "asset_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKFUND", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-test": hk_fund_detail,
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "asset-fx-test",
                "portfolio_name": "asset-fx-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "asset-fx-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "asset-fx-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-hkd-main",
                "cost_basis_method": "fifo",
                "allowed_asset_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "asset-fx-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "asset_id": "fund-hk-test",
                "instrument_ref": {
                    "asset_id": "fund-hk-test",
                    "asset_name": "HK Fund",
                    "asset_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HK fund position.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/asset-fx-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["asset_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["unrealized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["cumulative_twr"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    calculation_response = client.get("/api/portfolios/asset-fx-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["capital_gains"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["asset_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary

    line_by_key = {item["key"]: item for item in calculation_payload["lines"]}
    assert isclose(line_by_key["asset_currency_gains"]["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)


def test_foreign_currency_income_and_realized_pnl_are_reported_in_base_currency(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    hk_equity_detail = {
        "asset_id": "equity-hk-income-test",
        "asset_name": "HK Income Equity",
        "asset_type": "equity",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKINC", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "82.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "equity-hk-income-test": hk_equity_detail,
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(details.get(asset_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "foreign-income-realized-test",
                "portfolio_name": "foreign-income-realized-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "foreign-income-realized-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "foreign-income-realized-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-hkd-main",
                "cost_basis_method": "fifo",
                "allowed_asset_types": ["equity"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "asset_id": "equity-hk-income-test",
                "instrument_ref": {
                    "asset_id": "equity-hk-income-test",
                    "asset_name": "HK Income Equity",
                    "asset_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HK equity position.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": "cash-hkd-main",
                "asset_id": "equity-hk-income-test",
                "instrument_ref": {
                    "asset_id": "equity-hk-income-test",
                    "asset_name": "HK Income Equity",
                    "asset_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 75.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "entitlement_date": "2026-01-02",
                "note": "HK dividend.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": "cash-hkd-main",
                "asset_id": "equity-hk-income-test",
                "instrument_ref": {
                    "asset_id": "equity-hk-income-test",
                    "asset_name": "HK Income Equity",
                    "asset_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 82.0,
                "gross_amount": 820.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell HK equity.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/foreign-income-realized-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["income_cash_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["realized_pnl"], 40.0 / 7.5, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], (75.0 + 40.0) / 7.5, rel_tol=0.0, abs_tol=1e-12)


def test_foreign_currency_external_flow_is_converted_before_daily_twr(client, monkeypatch):
    fx_detail = {
        "asset_id": "fx-usd-hkd",
        "asset_name": "USD/HKD",
        "asset_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(fx_detail) if asset_id == "fx-usd-hkd" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.50,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "asset_id": "fx-usd-hkd",
                    "source_asset_ids": ["fx-usd-hkd"],
                    "status": "complete",
                }
            ],
        },
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "foreign-external-flow-test",
                "portfolio_name": "foreign-external-flow-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "foreign-external-flow-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_asset_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "foreign-external-flow-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "foreign-external-flow-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 750.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "HKD deposit.",
                "created_at": "2026-01-02T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    snapshots_response = client.get("/api/portfolios/foreign-external-flow-test/snapshots/daily")
    assert snapshots_response.status_code == 200
    payload = snapshots_response.json()
    by_date = {item["as_of_date"]: item for item in payload["snapshots"]}
    day_two = by_date["2026-01-02"]
    assert isclose(day_two["external_cash_in"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["absolute_change"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["daily_twr"], 0.02, rel_tol=0.0, abs_tol=1e-12)


def test_performance_summary_custom_period_uses_period_deltas(client, monkeypatch):
    monkeypatch.setattr(performance, "get_registry_instrument_detail", lambda asset_id: None)
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="performance-period-delta-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Day-two interest.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 7.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Day-three interest.",
                "created_at": "2026-01-03T09:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/performance-period-delta-test/performance"
        "?start_date=2026-01-02&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["start_date"] == "2026-01-02"
    assert summary["end_date"] == "2026-01-03"
    assert isclose(summary["start_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 112.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["absolute_change"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["delta"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["income_cash_amount"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["realized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["unrealized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["expense_cash_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_in"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_out"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 12.0, rel_tol=0.0, abs_tol=1e-12)


def test_performance_summary_ignores_flows_before_first_complete_snapshot(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-partial-anchor",
        asset_name="Partial Anchor Equity",
        history=[
            ("2026-01-02", "150.00"),
            ("2026-01-03", "165.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-partial-anchor" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="performance-partial-anchor-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 50.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Capital contribution before first complete snapshot.",
                "created_at": "2026-01-01T09:05:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-partial-anchor",
                "instrument_ref": {
                    "asset_id": "equity-us-partial-anchor",
                    "asset_name": "Partial Anchor Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "PARTIAL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 150.0,
                "gross_amount": 150.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy before first priced day.",
                "created_at": "2026-01-01T09:10:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/performance-partial-anchor-test/performance"
        "?start_date=2026-01-01&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["start_date"] == "2026-01-02"
    assert summary["end_date"] == "2026-01-03"
    assert summary["latest_complete_as_of_date"] == "2026-01-03"
    assert isclose(summary["start_nav"], 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 165.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["absolute_change"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_in"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_out"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["delta"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["unrealized_pnl"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 15.0, rel_tol=0.0, abs_tol=1e-12)


def test_daily_snapshots_keep_nav_constant_until_security_cash_settles(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-settlement-test",
        asset_name="Settlement Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-settlement-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="settlement-replay-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "settlement-replay-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "settlement-replay-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-settlement-test",
                "instrument_ref": {
                    "asset_id": "equity-us-settlement-test",
                    "asset_name": "Settlement Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "SETL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with delayed settlement.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get("/api/portfolios/settlement-replay-test/snapshots/daily")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["latest_complete_as_of_date"] == "2026-01-03"

    by_date = {item["as_of_date"]: item for item in payload["snapshots"]}
    assert by_date["2026-01-01"]["cash_balance"] == 100.0
    assert by_date["2026-01-02"]["cash_balance"] == 100.0
    assert by_date["2026-01-03"]["cash_balance"] == 0.0
    assert by_date["2026-01-01"]["ending_nav"] == 100.0
    assert by_date["2026-01-02"]["ending_nav"] == 100.0
    assert by_date["2026-01-03"]["ending_nav"] == 100.0


def test_account_contribution_carries_pending_settlement_in_ending_values(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        asset_id="equity-us-account-settlement-test",
        asset_name="Account Settlement Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda asset_id: deepcopy(instrument_detail) if asset_id == "equity-us-account-settlement-test" else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )

    store = _minimal_store(
        portfolio_id="account-contribution-settlement-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-contribution-settlement-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "asset_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "account-contribution-settlement-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "asset_id": "equity-us-account-settlement-test",
                "instrument_ref": {
                    "asset_id": "equity-us-account-settlement-test",
                    "asset_name": "Account Settlement Test Equity",
                    "asset_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "ACTSETL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with delayed settlement.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/account-contribution-settlement-test/performance/contribution?axis=account"
    )
    assert response.status_code == 200
    payload = response.json()
    slices = [
        item
        for item in payload["daily_slices"]
        if item["as_of_date"] == "2026-01-01"
    ]
    by_group = {item["group_key"]: item for item in slices}

    assert isclose(by_group["cash-usd-main"]["ending_value_base"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_group["broker-us-core"]["ending_value_base"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        sum((item["ending_value_base"] or 0.0) for item in slices),
        100.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        sum((item["ending_weight"] or 0.0) for item in slices),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
