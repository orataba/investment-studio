from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from tests.store_fixture import TEST_PORTFOLIO_STORE

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, portfolio_store
from portfolio_app.services.daily_snapshots import refresh_portfolio_daily_snapshots
from portfolio_app.services.ledger import _build_position_state, build_position_lots, derive_ledger_postings


def test_store_reset_rejects_legacy_asset_references():
    store = deepcopy(TEST_PORTFOLIO_STORE)
    store["transactions"][2]["asset_id"] = store["transactions"][2].pop("instrument_id")
    with pytest.raises(ValueError, match="legacy asset_id"):
        portfolio_store.reset_store(store)

    store = deepcopy(TEST_PORTFOLIO_STORE)
    store["transactions"][2]["instrument_ref"] = {
        "asset_id": "equity-us-abbv",
        "asset_name": "AbbVie Inc",
        "asset_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    with pytest.raises(ValueError, match="legacy asset reference fields"):
        portfolio_store.reset_store(store)


def test_portfolio_instruments_endpoint_reads_shared_registry_via_portfolio_backend(client):
    response = client.get("/api/portfolios/yungu/instruments")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "yungu"
    assert {item["instrument_core"]["instrument_id"] for item in payload["instruments"]} >= {
        "equity-us-abbv",
        "fund-us-agg",
        "fund-hk-2800",
    }
    abbv = next(
        item for item in payload["instruments"] if item["instrument_core"]["instrument_id"] == "equity-us-abbv"
    )
    assert abbv["instrument_core"]["identifiers"][0]["identifier_value"] == "ABBV"
    assert abbv["coverage_state"] == "complete"


def test_transaction_write_refreshes_materialized_daily_snapshots(client):
    baseline_response = client.get("/api/portfolios/yungu/snapshots/daily")
    assert baseline_response.status_code == 200

    created_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "currency": "USD",
        },
    )
    assert created_response.status_code == 200

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "yungu")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.dirty_from is None
        assert state.refreshed_to == date(2026, 4, 16)
        latest_snapshot = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "yungu")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert latest_snapshot is not None
        assert latest_snapshot.as_of_date == date(2026, 4, 16)


def test_transaction_update_marks_daily_snapshots_dirty_from_old_trade_date():
    refresh_portfolio_daily_snapshots("yungu")
    existing = portfolio_store.get_transaction("yungu", "txn-0002")
    assert existing is not None

    updated = portfolio_store.update_transaction(
        "yungu",
        "txn-0002",
        transaction_type=str(existing["transaction_type"]),
        trade_date=date(2026, 4, 20),
        trade_time=existing.get("trade_time"),
        settlement_date=date(2026, 4, 20),
        entitlement_date=None,
        acquisition_date=None,
        account_id=str(existing["account_id"]),
        settlement_cash_account_id=None,
        instrument_id=None,
        instrument_ref=None,
        quantity=None,
        price=None,
        gross_amount=float(existing["gross_amount"]),
        counter_amount=None,
        fx_rate=None,
        fees=float(existing["fees"]),
        taxes=float(existing["taxes"]),
        currency=str(existing["currency"]),
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=str(existing.get("note") or ""),
        created_at=str(existing.get("created_at") or ""),
    )
    assert updated is not None

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "yungu")
        assert state is not None
        assert state.daily_snapshot_status == "stale"
        assert state.dirty_from == date(2026, 2, 3)


def test_daily_snapshot_refresh_replays_when_data_changes_mid_refresh(monkeypatch):
    original_builder = daily_snapshots.performance.build_daily_portfolio_snapshots
    build_calls = {"count": 0}

    def build_with_mid_refresh_update(*args, **kwargs):
        build_calls["count"] += 1
        if build_calls["count"] == 1:
            portfolio_store.create_transaction(
                portfolio_id="yungu",
                transaction_type="deposit",
                trade_date=date(2026, 4, 18),
                trade_time=None,
                settlement_date=date(2026, 4, 18),
                entitlement_date=None,
                acquisition_date=None,
                account_id="cash-usd-main",
                settlement_cash_account_id=None,
                instrument_id=None,
                instrument_ref=None,
                quantity=None,
                price=None,
                gross_amount=1234.0,
                counter_amount=None,
                fx_rate=None,
                fees=0.0,
                taxes=0.0,
                currency="USD",
                transfer_scope=None,
                transfer_object_type=None,
                transfer_group_id=None,
                counterparty_account_id=None,
                note="mid-refresh data change",
            )
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(
        daily_snapshots.performance,
        "build_daily_portfolio_snapshots",
        build_with_mid_refresh_update,
    )

    result = daily_snapshots.refresh_portfolio_daily_snapshots("yungu")

    assert build_calls["count"] == 2
    assert result is not None
    assert result["refreshed_to"] == date(2026, 4, 18)
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "yungu")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.dirty_from is None
        assert state.refreshed_to == date(2026, 4, 18)
        latest_snapshot = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "yungu")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert latest_snapshot is not None
        assert latest_snapshot.as_of_date == date(2026, 4, 18)


def test_securities_account_defaults_to_fifo_when_cost_basis_omitted(client):
    response = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "FIFO Default Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "allowed_instrument_types": ["fund"],
            "opened_at": "2026-04-22",
            "status": "active",
        },
    )

    assert response.status_code == 200
    assert response.json()["cost_basis_method"] == "fifo"


def test_account_cost_method_can_be_updated_before_instrument_history(client):
    created_response = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Cost Method Editable Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-22",
            "status": "active",
        },
    )
    assert created_response.status_code == 200
    account = created_response.json()

    updated_response = client.patch(
        f"/api/portfolios/yungu/accounts/{account['account_id']}",
        json={
            "account_name": "Cost Method Editable Account",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "moving_average",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-22",
            "status": "active",
        },
    )

    assert updated_response.status_code == 200
    assert updated_response.json()["cost_basis_method"] == "moving_average"


def test_account_cost_method_change_restates_instrument_history(client):
    created_response = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Cost Method Restatement Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    )
    assert created_response.status_code == 200
    account = created_response.json()

    for trade_date, transaction_type, quantity, price in [
        ("2026-04-10", "buy", 5000.0, 50.0),
        ("2026-04-11", "buy", 5000.0, 100.0),
        ("2026-04-12", "sell", 6000.0, 75.0),
    ]:
        transaction_response = client.post(
            "/api/portfolios/yungu/transactions",
            json={
                "transaction_type": transaction_type,
                "trade_date": trade_date,
                "account_id": account["account_id"],
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": quantity,
                "price": price,
                "gross_amount": quantity * price,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
            },
        )
        assert transaction_response.status_code == 200

    fifo_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert fifo_lots_response.status_code == 200
    fifo_lots = fifo_lots_response.json()["position_lots"]
    assert sum(lot["remaining_cost_basis"] for lot in fifo_lots) == pytest.approx(400000.0)
    assert sum(lot["realized_cost_basis"] for lot in fifo_lots) == pytest.approx(350000.0)
    assert sum(lot["realized_pnl"] for lot in fifo_lots) == pytest.approx(100000.0)

    updated_response = client.patch(
        f"/api/portfolios/yungu/accounts/{account['account_id']}",
        json={
            "account_name": "Cost Method Restatement Account",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "moving_average",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["cost_basis_method"] == "moving_average"

    restated_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert restated_lots_response.status_code == 200
    restated_lots = restated_lots_response.json()["position_lots"]
    assert len(restated_lots) == 1
    assert sum(lot["remaining_cost_basis"] for lot in restated_lots) == pytest.approx(300000.0)
    assert sum(lot["realized_cost_basis"] for lot in restated_lots) == pytest.approx(450000.0)
    assert sum(lot["realized_pnl"] for lot in restated_lots) == pytest.approx(0.0)


def test_transaction_fact_can_be_updated_and_deleted(client):
    created_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "currency": "USD",
            "note": "Initial note",
        },
    )
    assert created_response.status_code == 200
    transaction_id = created_response.json()["transaction_id"]

    updated_response = client.put(
        f"/api/portfolios/yungu/transactions/{transaction_id}",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-17",
            "account_id": "cash-usd-main",
            "gross_amount": 1250.0,
            "currency": "USD",
            "note": "Corrected note",
        },
    )
    assert updated_response.status_code == 200
    updated_payload = updated_response.json()
    assert updated_payload["transaction_id"] == transaction_id
    assert updated_payload["trade_date"] == "2026-04-17"
    assert updated_payload["gross_amount"] == pytest.approx(1250.0)
    assert updated_payload["note"] == "Corrected note"

    deleted_response = client.delete(f"/api/portfolios/yungu/transactions/{transaction_id}")
    assert deleted_response.status_code == 200
    deleted_payload = deleted_response.json()
    assert deleted_payload["deleted_count"] == 1
    assert deleted_payload["deleted_transaction_ids"] == [transaction_id]

    listing_response = client.get("/api/portfolios/yungu/transactions")
    assert listing_response.status_code == 200
    assert transaction_id not in {
        item["transaction_id"] for item in listing_response.json()["transactions"]
    }


def test_deleting_transfer_leg_removes_entire_pair(client):
    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-16",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": 250.0,
            "note": "Sweep",
        },
    )
    assert transfer_response.status_code == 200
    transfer_payload = transfer_response.json()
    delete_target = transfer_payload["transactions"][0]["transaction_id"]

    deleted_response = client.delete(f"/api/portfolios/yungu/transactions/{delete_target}")
    assert deleted_response.status_code == 200
    deleted_payload = deleted_response.json()
    assert deleted_payload["deleted_count"] == 2
    assert deleted_payload["transfer_group_id"] == transfer_payload["transfer_group_id"]


def test_create_transactions_rolls_back_whole_batch_on_later_failure(client):
    from portfolio_app.services.portfolio_store import create_transactions, list_transactions

    before_ids = {item["transaction_id"] for item in list_transactions("yungu")}

    with pytest.raises(KeyError, match="gross_amount"):
        create_transactions(
            portfolio_id="yungu",
            records=[
                {
                    "transaction_type": "deposit",
                    "trade_date": date(2026, 4, 20),
                    "trade_time": None,
                    "settlement_date": date(2026, 4, 20),
                    "entitlement_date": None,
                    "acquisition_date": None,
                    "account_id": "cash-usd-main",
                    "settlement_cash_account_id": None,
                    "instrument_id": None,
                    "instrument_ref": None,
                    "quantity": None,
                    "price": None,
                    "gross_amount": 100.0,
                    "counter_amount": None,
                    "fx_rate": None,
                    "fees": 0.0,
                    "taxes": 0.0,
                    "currency": "USD",
                    "transfer_scope": None,
                    "transfer_object_type": None,
                    "transfer_group_id": None,
                    "counterparty_account_id": None,
                    "note": "batch rollback sentinel",
                    "created_at": "2026-04-20T00:00:00Z",
                },
                {
                    "transaction_type": "deposit",
                    "trade_date": date(2026, 4, 20),
                    "trade_time": None,
                    "settlement_date": date(2026, 4, 20),
                    "account_id": "cash-usd-main",
                    "fees": 0.0,
                    "taxes": 0.0,
                    "currency": "USD",
                },
            ],
        )

    after = list_transactions("yungu")
    assert {item["transaction_id"] for item in after} == before_ids
    assert all(item["note"] != "batch rollback sentinel" for item in after)


def test_rejects_cross_currency_security_facts(client):
    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-hkd-main",
            "instrument_id": "fund-hk-2800",
            "quantity": 100.0,
            "price": 21.0,
            "gross_amount": 2100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "HKD",
        },
    )
    assert buy_response.status_code == 400
    assert "Securities account currency must match instrument currency" in buy_response.json()["detail"]

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-15",
            "from_account_id": "broker-us-core",
            "to_account_id": "broker-hk-core",
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
        },
    )
    assert transfer_response.status_code == 400
    assert "same currency as the instrument" in transfer_response.json()["detail"]


def test_holdings_and_account_workspace_use_base_currency_valuation(client):
    holdings_response = client.get("/api/workspace/holdings", params={"portfolio_id": "yungu"})
    assert holdings_response.status_code == 200
    holdings = holdings_response.json()
    assert holdings["base_currency"] == "USD"

    fx_to_usd = {"USD": 1.0, "HKD": 1 / 7.8, "CNY": 1 / 7.2}
    expected_total_market_value = sum(
        (row["market_value"] or 0.0) * fx_to_usd[row["instrument_core"]["currency"]]
        for row in holdings["rows"]
        if row["market_value"] is not None
    )
    assert holdings["totals"]["market_value"] == pytest.approx(expected_total_market_value)

    hkd_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "fund-hk-2800")
    expected_total_nav = holdings["totals"]["nav"]
    assert expected_total_nav != pytest.approx(expected_total_market_value)
    expected_hkd_allocation = (hkd_row["market_value"] * fx_to_usd["HKD"]) / expected_total_nav
    assert hkd_row["allocation"] == pytest.approx(expected_hkd_allocation)
    assert holdings["totals"]["allocation"] == pytest.approx(expected_total_market_value / expected_total_nav)

    accounts_response = client.get("/api/portfolios/yungu/accounts/workspace")
    assert accounts_response.status_code == 200
    accounts_workspace = accounts_response.json()
    hk_account = next(
        row for row in accounts_workspace["accounts"] if row["account"]["account_id"] == "broker-hk-core"
    )
    assert hk_account["position_market_value_currency"] == "USD"
    assert hk_account["position_market_value"] == pytest.approx(hkd_row["market_value"] * fx_to_usd["HKD"])


def test_holdings_workspace_replays_requested_as_of_date(client):
    response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "yungu", "as_of_date": "2026-04-02"},
    )
    assert response.status_code == 200
    holdings = response.json()
    assert holdings["as_of_date"] == "2026-04-02"

    abbv_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "equity-us-abbv")
    assert abbv_row["quantity"] == pytest.approx(1000.0)
    assert abbv_row["last_price"] == pytest.approx(210.20)

    agg_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "fund-us-agg")
    assert agg_row["quantity"] == pytest.approx(304.236)
    assert agg_row["last_price"] == pytest.approx(97.62)

    hkd_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "fund-hk-2800")
    assert hkd_row["last_price"] == pytest.approx(21.05)
    assert hkd_row["market_value"] == pytest.approx(105250.0)
    assert hkd_row["market_value_base"] == pytest.approx(105250.0 / 7.82)


def test_accounts_workspace_defers_security_cash_until_settlement_date(client):
    baseline_summary_response = client.get("/api/workspace/summary", params={"portfolio_id": "yungu"})
    assert baseline_summary_response.status_code == 200
    baseline_nav = baseline_summary_response.json()["nav"]

    baseline_response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"as_of_date": "2026-04-15"},
    )
    assert baseline_response.status_code == 200
    baseline_cash_row = next(
        row
        for row in baseline_response.json()["accounts"]
        if row["account"]["account_id"] == "cash-usd-main"
    )
    baseline_cash_balance = baseline_cash_row["derived_cash_balance"]

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

    trade_date_response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"as_of_date": "2026-04-15"},
    )
    assert trade_date_response.status_code == 200
    trade_date_cash_row = next(
        row
        for row in trade_date_response.json()["accounts"]
        if row["account"]["account_id"] == "cash-usd-main"
    )
    assert trade_date_cash_row["derived_cash_balance"] == pytest.approx(baseline_cash_balance)
    assert trade_date_cash_row["pending_settlement"] == pytest.approx(-206.47)
    assert trade_date_cash_row["pending_settlement_base"] == pytest.approx(-206.47)

    trade_date_broker_row = next(
        row
        for row in trade_date_response.json()["accounts"]
        if row["account"]["account_id"] == "broker-us-core"
    )
    assert trade_date_broker_row["position_market_value"] >= 206.47

    post_buy_summary_response = client.get("/api/workspace/summary", params={"portfolio_id": "yungu"})
    assert post_buy_summary_response.status_code == 200
    assert post_buy_summary_response.json()["nav"] == pytest.approx(baseline_nav)

    settlement_date_response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"as_of_date": "2026-04-16"},
    )
    assert settlement_date_response.status_code == 200
    settlement_date_cash_row = next(
        row
        for row in settlement_date_response.json()["accounts"]
        if row["account"]["account_id"] == "cash-usd-main"
    )
    assert settlement_date_cash_row["derived_cash_balance"] == pytest.approx(baseline_cash_balance - 206.47)
    assert settlement_date_cash_row["pending_settlement"] == pytest.approx(0.0)


def test_holdings_workspace_includes_shared_price_sparklines(client):
    response = client.get("/api/workspace/holdings", params={"portfolio_id": "yungu"})
    assert response.status_code == 200
    holdings = response.json()
    assert "price_chart_range" not in holdings

    abbv_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "equity-us-abbv")
    assert "price_chart" not in abbv_row
    assert abbv_row["price_chart_1m"][0]["date"] == "2026-03-15"
    assert abbv_row["price_chart_6m"]
    assert abbv_row["price_chart_6m"][-1]["date"] == "2026-04-15"
    assert abbv_row["price_chart_6m"][-1]["value"] == pytest.approx(206.47)
    assert abbv_row["price_chart_1y"][-1]["value"] == pytest.approx(206.47)
    assert abbv_row["instrument_trend_as_of_date"] == "2026-04-15"
    assert abbv_row["instrument_trend_basis"] == "close"
    assert abbv_row["instrument_return_1w"] == pytest.approx(206.47 / 207.18 - 1)
    assert abbv_row["instrument_return_mtd"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_return_ytd"] == pytest.approx(0)
    assert abbv_row["instrument_return_1y"] is None
    assert abbv_row["instrument_current_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_max_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_holding_max_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_holding_start_date"] == "2026-02-10"
    assert abbv_row["instrument_volatility_1m"] is not None
    assert abbv_row["instrument_volatility_3m"] is not None
    assert abbv_row["instrument_volatility_6m"] is not None
    assert abbv_row["instrument_volatility_1y"] is not None

    assert abbv_row["price_chart_3m"][0]["date"] == "2026-02-10"
    assert abbv_row["instrument_return_mtd"] == pytest.approx(206.47 / 210.20 - 1)


def test_instrument_price_chart_endpoint_returns_filtered_shared_history(client):
    response = client.get(
        "/api/portfolios/yungu/instruments/equity-us-abbv/price-chart",
        params={"as_of_date": "2026-04-15", "range": "1m"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["portfolio_id"] == "yungu"
    assert payload["instrument_core"]["instrument_id"] == "equity-us-abbv"
    assert payload["range_key"] == "1m"
    assert payload["chart_basis"] == "close"
    assert [point["date"] for point in payload["points"]] == ["2026-03-15", "2026-04-08", "2026-04-15"]
    assert payload["summary"]["point_count"] == 3
    assert payload["summary"]["change_value"] == pytest.approx(-3.73)
    assert payload["summary"]["high"] == pytest.approx(210.20)
    assert payload["summary"]["low"] == pytest.approx(206.47)


def test_transaction_position_preview_returns_quantity_as_of_trade_moment(client):
    response = client.get(
        "/api/portfolios/yungu/transactions/position-preview",
        params={
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "as_of_date": "2026-04-15",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["portfolio_id"] == "yungu"
    assert payload["account_id"] == "broker-us-core"
    assert payload["instrument_id"] == "equity-us-abbv"
    assert payload["as_of_date"] == "2026-04-15"
    assert payload["quantity"] == pytest.approx(880.0)


def test_position_lots_support_historical_as_of_date(client):
    response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"instrument_id": "equity-us-abbv", "status": "open", "as_of_date": "2026-04-02"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["position_lot_count"] == 1
    position_lot = payload["position_lots"][0]
    assert position_lot["entry_quantity"] == pytest.approx(1000.0)
    assert position_lot["remaining_quantity"] == pytest.approx(1000.0)
    assert position_lot["current_market_value"] == pytest.approx(210200.0)
    assert position_lot["unrealized_pnl"] == pytest.approx(3712.0)


def test_position_transfer_uses_average_cost_bucket_for_moving_average_accounts(client):
    source_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Test MA Equity Source",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "moving_average",
            "allowed_instrument_types": ["equity"],
            "status": "active",
        },
    ).json()
    destination_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Test Equity Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-reserve",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "status": "active",
        },
    ).json()

    for trade_date, price, gross_amount in [("2026-04-01", 10.0, 1000.0), ("2026-04-02", 20.0, 2000.0)]:
        buy_response = client.post(
            "/api/portfolios/yungu/transactions",
            json={
                "transaction_type": "buy",
                "trade_date": trade_date,
                "account_id": source_account["account_id"],
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": 100.0,
                "price": price,
                "gross_amount": gross_amount,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
            },
        )
        assert buy_response.status_code == 200

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-10",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 150.0,
        },
    )
    assert transfer_response.status_code == 200
    transfer_batch = transfer_response.json()
    assert {txn["gross_amount"] for txn in transfer_batch["transactions"]} == {2250.0}

    destination_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": destination_account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert destination_lots_response.status_code == 200
    destination_lots = destination_lots_response.json()["position_lots"]
    assert len(destination_lots) == 1
    assert destination_lots[0]["entry_quantity"] == pytest.approx(150.0)
    assert destination_lots[0]["entry_cost_basis"] == pytest.approx(2250.0)

    source_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": source_account["account_id"], "instrument_id": "equity-us-abbv", "status": "open"},
    )
    assert source_lots_response.status_code == 200
    source_lots = source_lots_response.json()["position_lots"]
    assert len(source_lots) == 1
    assert source_lots[0]["entry_cost_basis"] == pytest.approx(3000.0)
    assert source_lots[0]["remaining_quantity"] == pytest.approx(50.0)
    assert source_lots[0]["remaining_cost_basis"] == pytest.approx(750.0)
    assert source_lots[0]["transferred_cost_basis"] == pytest.approx(2250.0)


def test_position_transfer_allows_zero_cost_basis_lots(client):
    source_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Zero Cost Source",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()
    destination_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Zero Cost Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    opening_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-20",
            "account_id": source_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 0.0,
            "currency": "USD",
        },
    )
    assert opening_response.status_code == 200

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-21",
            "transfer_object_type": "position",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 4.0,
        },
    )
    assert transfer_response.status_code == 200
    assert {transaction["gross_amount"] for transaction in transfer_response.json()["transactions"]} == {0.0}

    source_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": source_account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    destination_lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": destination_account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert source_lots_response.status_code == 200
    assert destination_lots_response.status_code == 200
    source_lots = source_lots_response.json()["position_lots"]
    destination_lots = destination_lots_response.json()["position_lots"]
    assert len(source_lots) == 1
    assert len(destination_lots) == 1
    assert source_lots[0]["remaining_quantity"] == pytest.approx(6.0)
    assert source_lots[0]["remaining_cost_basis"] == pytest.approx(0.0)
    assert source_lots[0]["transferred_cost_basis"] == pytest.approx(0.0)
    assert destination_lots[0]["entry_quantity"] == pytest.approx(4.0)
    assert destination_lots[0]["entry_cost_basis"] == pytest.approx(0.0)

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-22",
            "account_id": destination_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 2.0,
            "price": 100.0,
            "gross_amount": 200.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200


def test_copy_portfolio_remaps_counterparty_account_ids(client):
    copy_response = client.post("/api/portfolios/yungu/copy")
    assert copy_response.status_code == 200
    copied_portfolio_id = copy_response.json()["portfolio_id"]

    transactions_response = client.get(
        f"/api/portfolios/{copied_portfolio_id}/transactions",
        params={"transaction_type": "fx_conversion"},
    )
    assert transactions_response.status_code == 200
    copied_fx_transaction = transactions_response.json()["transactions"][0]
    assert copied_fx_transaction["account"]["account_id"].endswith(f"-{copied_portfolio_id}")
    assert copied_fx_transaction["counterparty_account_id"].endswith(f"-{copied_portfolio_id}")


def test_fx_conversion_target_account_filter_includes_dual_account_fact(client):
    transactions_response = client.get(
        "/api/portfolios/yungu/transactions",
        params={"account_id": "cash-cny-main", "transaction_type": "fx_conversion"},
    )
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()["transactions"]
    assert len(transactions) == 1
    assert transactions[0]["transaction_id"] == "txn-0018"


def test_rejects_backdated_sell_before_position_exists(client):
    copy_response = client.post("/api/portfolios/yungu/copy")
    assert copy_response.status_code == 200
    copied_portfolio_id = copy_response.json()["portfolio_id"]
    destination_account_response = client.post(
        f"/api/portfolios/{copied_portfolio_id}/accounts",
        json={
            "account_name": "Copy Equity Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-reserve-yungu-copy",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-01-15",
            "status": "active",
        },
    )
    assert destination_account_response.status_code == 200
    destination_account = destination_account_response.json()

    sell_response = client.post(
        f"/api/portfolios/{copied_portfolio_id}/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-01-20",
            "account_id": "broker-us-core-yungu-copy",
            "settlement_cash_account_id": "cash-usd-main-yungu-copy",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 200.0,
            "gross_amount": 2000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 400
    assert "as of trade_date" in sell_response.json()["detail"]

    transfer_response = client.post(
        f"/api/portfolios/{copied_portfolio_id}/transactions/internal-transfer",
        json={
            "trade_date": "2026-01-20",
            "from_account_id": "broker-us-core-yungu-copy",
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
        },
    )
    assert transfer_response.status_code == 400
    assert "as of trade_date" in transfer_response.json()["detail"]


def test_rejects_inconsistent_buy_sell_amount_contracts(client):
    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 422
    assert "gross_amount must equal quantity multiplied by price" in buy_response.text

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 206.47,
            "gross_amount": 999999.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 422
    assert "gross_amount must equal quantity multiplied by price" in sell_response.text


def test_accepts_display_rounded_price_when_gross_amount_is_authoritative(client):
    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "quantity": 700.0,
            "price": 11.5436,
            "gross_amount": 8080.50,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200
    payload = buy_response.json()
    assert payload["gross_amount"] == pytest.approx(8080.50)
    assert payload["price"] == pytest.approx(11.5436)


def test_normalizes_transaction_precision_conventions(client):
    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "quantity": 700.004,
            "price": 11.54364,
            "gross_amount": 8080.504,
            "fees": 0.004,
            "taxes": 0.004,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200
    payload = buy_response.json()
    assert payload["quantity"] == pytest.approx(700.00)
    assert payload["price"] == pytest.approx(11.5436)
    assert payload["gross_amount"] == pytest.approx(8080.50)
    assert payload["fees"] == pytest.approx(0.00)
    assert payload["taxes"] == pytest.approx(0.00)


def test_rejects_inconsistent_opening_balance_and_dividend_reinvestment_amount_contracts(client):
    opening_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Opening Balance Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    opening_balance_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": opening_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 999.0,
            "gross_amount": 1.0,
            "currency": "USD",
        },
    )
    assert opening_balance_response.status_code == 422
    assert "security opening balance" in opening_balance_response.text.lower()

    reinvestment_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 999.0,
            "gross_amount": 1.0,
            "currency": "USD",
        },
    )
    assert reinvestment_response.status_code == 422
    assert "dividend reinvestment" in reinvestment_response.text.lower()


def test_rejects_opening_balance_settlement_account_and_deposit_account_instrument(client):
    opening_balance_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": "broker-us-core",
            "gross_amount": 1000.0,
            "currency": "USD",
        },
    )
    assert opening_balance_response.status_code == 422
    assert "opening balance must not carry settlement_cash_account_id" in opening_balance_response.text.lower()

    deposit_account_security_opening_balance = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        },
    )
    assert deposit_account_security_opening_balance.status_code == 400
    assert "cash opening balance must not reference instrument" in deposit_account_security_opening_balance.json()["detail"].lower()


def test_rejects_deposit_account_fee_with_instrument_reference(client):
    fee_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 25.0,
            "currency": "USD",
        },
    )
    assert fee_response.status_code == 400
    assert "deposit-account fee and tax must not reference instrument" in fee_response.json()["detail"].lower()

    fee_with_settlement_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": "cash-usd-reserve",
            "gross_amount": 25.0,
            "currency": "USD",
        },
    )
    assert fee_with_settlement_response.status_code == 400
    assert (
        "deposit-account fee and tax must not carry settlement cash account"
        in fee_with_settlement_response.json()["detail"].lower()
    )


def test_rejects_irrelevant_cash_and_reinvestment_fields(client):
    deposit_with_quantity = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "quantity": 1.0,
            "currency": "USD",
        },
    )
    assert deposit_with_quantity.status_code == 422
    assert "cash-flow transactions must not carry quantity or price" in deposit_with_quantity.text.lower()

    deposit_with_settlement = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "settlement_cash_account_id": "cash-usd-reserve",
            "currency": "USD",
        },
    )
    assert deposit_with_settlement.status_code == 422
    assert "cash-flow transactions must not carry settlement_cash_account_id" in deposit_with_settlement.text.lower()

    interest_with_settlement = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "interest",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "settlement_cash_account_id": "cash-usd-reserve",
            "currency": "USD",
        },
    )
    assert interest_with_settlement.status_code == 422
    assert "interest must not carry settlement_cash_account_id" in interest_with_settlement.text.lower()

    drip_with_settlement = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "gross_amount": 100.0,
            "settlement_cash_account_id": "cash-usd-main",
            "currency": "USD",
        },
    )
    assert drip_with_settlement.status_code == 422
    assert "dividend reinvestment must not carry settlement_cash_account_id" in drip_with_settlement.text.lower()

    deposit_with_counterparty = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "counterparty_account_id": "cash-usd-reserve",
            "currency": "USD",
        },
    )
    assert deposit_with_counterparty.status_code == 422
    assert "counterparty_account_id is only allowed for fx_conversion" in deposit_with_counterparty.text.lower()

    dividend_with_quantity = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "gross_amount": 10.0,
            "currency": "USD",
        },
    )
    assert dividend_with_quantity.status_code == 422
    assert "dividend and coupon must not carry quantity or price" in dividend_with_quantity.text.lower()


def test_dividend_and_return_of_capital_keep_gross_income_and_separate_expense_allocation(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Income Attribution Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 15.0,
            "currency": "USD",
        },
    )
    assert dividend_response.status_code == 200

    roc_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-16",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 40.0,
            "fees": 0.0,
            "taxes": 5.0,
            "currency": "USD",
        },
    )
    assert roc_response.status_code == 200

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lot = lots_response.json()["position_lots"][0]
    assert lot["income_cash_amount"] == pytest.approx(100.0)
    assert lot["expense_cash_amount"] == pytest.approx(20.0)
    assert lot["return_of_capital_amount"] == pytest.approx(40.0)
    assert lot["remaining_cost_basis"] == pytest.approx(9960.0)


def test_flat_position_rejects_follow_on_sell_transfer_and_return_of_capital(client):
    source_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Flat Position Source",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()
    destination_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Flat Position Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-reserve",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 110.0,
            "gross_amount": 1100.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    oversell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 120.0,
            "gross_amount": 120.0,
            "currency": "USD",
        },
    )
    assert oversell_response.status_code == 400
    assert "exceeds account position as of trade_date" in oversell_response.json()["detail"]

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-12",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
        },
    )
    assert transfer_response.status_code == 400
    assert "exceeds source position as of trade_date" in transfer_response.json()["detail"]

    roc_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-12",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 10.0,
            "currency": "USD",
        },
    )
    assert roc_response.status_code == 400
    assert "exceeds account position cost basis as of trade_date" in roc_response.json()["detail"]


def test_rejects_instrument_income_and_expense_without_open_position(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Post-Close Income Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 110.0,
            "gross_amount": 1100.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 50.0,
            "currency": "USD",
        },
    )
    assert dividend_response.status_code == 400
    assert "requires account position as of entitlement_date" in dividend_response.json()["detail"]

    fee_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "fee",
            "trade_date": "2026-04-13",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 5.0,
            "currency": "USD",
        },
    )
    assert fee_response.status_code == 400
    assert "requires account position as of entitlement_date" in fee_response.json()["detail"]


def test_rejects_nested_fee_and_tax_fields_on_fee_tax_transactions(client):
    fee_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "fees": 1.0,
            "taxes": 2.0,
            "currency": "USD",
        },
    )
    assert fee_response.status_code == 422
    assert "must not carry nested fees or taxes" in fee_response.text.lower()

    tax_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "tax",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "fees": 1.0,
            "currency": "USD",
        },
    )
    assert tax_response.status_code == 422
    assert "must not carry nested fees or taxes" in tax_response.text.lower()


def test_rejects_dividend_reinvestment_without_existing_position(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Empty DRIP Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        },
    )
    assert response.status_code == 400
    assert "requires account position as of trade_date" in response.json()["detail"]


def test_rejects_entitlement_date_on_dividend_reinvestment(client):
    response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        },
    )
    assert response.status_code == 422
    assert "does not yet support entitlement_date" in response.text


def test_accepts_late_paid_dividend_when_entitlement_date_precedes_sale(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Late Income Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    for trade_date in ("2026-04-01", "2026-04-02"):
        buy_response = client.post(
            "/api/portfolios/yungu/transactions",
            json={
                "transaction_type": "buy",
                "trade_date": trade_date,
                "account_id": account["account_id"],
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": 50.0,
                "price": 100.0,
                "gross_amount": 5000.0,
                "currency": "USD",
            },
        )
        assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 50.0,
            "price": 110.0,
            "gross_amount": 5500.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 100.0,
            "currency": "USD",
        },
    )
    assert dividend_response.status_code == 200
    assert dividend_response.json()["entitlement_date"] == "2026-04-10"

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    closed_lot = next(lot for lot in lots if lot["status"] == "closed")
    open_lot = next(lot for lot in lots if lot["status"] == "open")
    assert closed_lot["income_cash_amount"] == pytest.approx(50.0)
    assert open_lot["income_cash_amount"] == pytest.approx(50.0)


def test_rejects_late_paid_dividend_reinvestment_and_preserves_workspace_reads(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Late DRIP Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-01",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 110.0,
            "gross_amount": 11000.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    drip_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        },
    )
    assert drip_response.status_code == 422

    ledger_response = client.get("/api/portfolios/yungu/ledger-postings")
    assert ledger_response.status_code == 200

    workspace_response = client.get("/api/portfolios/yungu/accounts/workspace")
    assert workspace_response.status_code == 200


def test_rejects_entitlement_date_on_return_of_capital(client):
    response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 10.0,
            "currency": "USD",
        },
    )
    assert response.status_code == 422
    assert "does not yet support entitlement_date" in response.text


def test_ledger_postings_sort_by_trade_time_within_same_day():
    instrument_ref = {
        "instrument_id": "equity-us-abbv",
        "instrument_name": "AbbVie Inc",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    transactions = [
        {
            "transaction_id": "fee-1",
            "portfolio_id": "p",
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "trade_at": "2026-04-15T01:00:00Z",
            "settlement_date": "2026-04-15",
            "account_id": "broker",
            "settlement_cash_account_id": "cash",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": instrument_ref,
            "gross_amount": 5.0,
            "currency": "USD",
            "created_at": "2026-04-15T01:00:00Z",
        },
        {
            "transaction_id": "buy-1",
            "portfolio_id": "p",
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "trade_at": "2026-04-15T07:00:00Z",
            "settlement_date": "2026-04-15",
            "account_id": "broker",
            "settlement_cash_account_id": "cash",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": instrument_ref,
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-04-15T07:00:00Z",
        },
        {
            "transaction_id": "sell-1",
            "portfolio_id": "p",
            "transaction_type": "sell",
            "trade_date": "2026-04-15",
            "trade_at": "2026-04-15T08:00:00Z",
            "settlement_date": "2026-04-15",
            "account_id": "broker",
            "settlement_cash_account_id": "cash",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": instrument_ref,
            "quantity": 10.0,
            "price": 105.0,
            "gross_amount": 1050.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-04-15T08:00:00Z",
        },
    ]

    postings = derive_ledger_postings(
        "p",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )

    transaction_order: list[str] = []
    for posting in postings:
        transaction_id = str(posting["transaction_id"])
        if transaction_id not in transaction_order:
            transaction_order.append(transaction_id)
    assert transaction_order == ["sell-1", "buy-1", "fee-1"]


def test_dividend_reinvestment_allocates_income_to_existing_position_lots(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "DRIP Attribution Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    drip_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        },
    )
    assert drip_response.status_code == 200

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    original_lot = next(lot for lot in lots if lot["opening_transaction_type"] == "buy")
    drip_lot = next(lot for lot in lots if lot["opening_transaction_type"] == "dividend_reinvestment")
    assert original_lot["income_cash_amount"] == pytest.approx(100.0)
    assert drip_lot["income_cash_amount"] == pytest.approx(0.0)
    assert drip_lot["entry_cost_basis"] == pytest.approx(100.0)


def test_transfer_derivation_rejects_missing_source_lots():
    accounts = [
        {
            "account_id": "src",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
        {
            "account_id": "dst",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    malformed_transactions = [
        {
            "transaction_id": "t1",
            "portfolio_id": "p",
            "transaction_type": "transfer_out",
            "trade_date": "2026-04-10",
            "trade_at": "2026-04-10T04:00:00Z",
            "settlement_date": "2026-04-10",
            "account_id": "src",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": {
                "instrument_id": "equity-us-abbv",
                "instrument_name": "AbbVie Inc",
                "instrument_type": "equity",
                "currency": "USD",
                "identifiers": [],
            },
            "quantity": 1.0,
            "gross_amount": 100.0,
            "currency": "USD",
            "transfer_object_type": "position",
            "transfer_group_id": "g1",
        },
        {
            "transaction_id": "t2",
            "portfolio_id": "p",
            "transaction_type": "transfer_in",
            "trade_date": "2026-04-10",
            "trade_at": "2026-04-10T04:00:00Z",
            "settlement_date": "2026-04-10",
            "account_id": "dst",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": {
                "instrument_id": "equity-us-abbv",
                "instrument_name": "AbbVie Inc",
                "instrument_type": "equity",
                "currency": "USD",
                "identifiers": [],
            },
            "quantity": 1.0,
            "gross_amount": 100.0,
            "currency": "USD",
            "transfer_object_type": "position",
            "transfer_group_id": "g1",
        },
    ]

    with pytest.raises(ValueError, match="source lots"):
        _build_position_state(malformed_transactions, account_cost_methods={"src": "fifo", "dst": "fifo"})

    with pytest.raises(ValueError, match="source lots"):
        derive_ledger_postings("p", malformed_transactions, account_cost_methods={"src": "fifo", "dst": "fifo"})

    with pytest.raises(ValueError, match="source position lots|linked source position lots"):
        build_position_lots("p", accounts, malformed_transactions)


def test_lot_kernels_reject_oversell_when_route_validation_is_bypassed():
    accounts = [
        {
            "account_id": "broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
        {
            "account_id": "cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
    ]
    instrument_ref = {
        "instrument_id": "equity-us-abbv",
        "instrument_name": "AbbVie Inc",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    malformed_transactions = [
        {
            "transaction_id": "buy-1",
            "portfolio_id": "p",
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "trade_at": "2026-04-10T04:00:00Z",
            "settlement_date": "2026-04-10",
            "account_id": "broker",
            "settlement_cash_account_id": "cash",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": instrument_ref,
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-04-10T04:00:00Z",
        },
        {
            "transaction_id": "sell-1",
            "portfolio_id": "p",
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "trade_at": "2026-04-11T04:00:00Z",
            "settlement_date": "2026-04-11",
            "account_id": "broker",
            "settlement_cash_account_id": "cash",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": instrument_ref,
            "quantity": 2.0,
            "price": 110.0,
            "gross_amount": 220.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-04-11T04:00:00Z",
        },
    ]

    with pytest.raises(ValueError, match="exceeds account position"):
        _build_position_state(malformed_transactions, account_cost_methods={"broker": "fifo"})

    with pytest.raises(ValueError, match="exceeds account position"):
        derive_ledger_postings(
            "p",
            malformed_transactions,
            account_cost_methods={"broker": "fifo"},
            account_currency_map={"broker": "USD", "cash": "USD"},
        )

    with pytest.raises(ValueError, match="exceeds account position"):
        build_position_lots("p", accounts, malformed_transactions)


def test_rejects_settlement_before_trade_date(client):
    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-01",
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
    assert buy_response.status_code == 422
    assert "settlement_date must not be earlier than trade_date" in buy_response.text

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-01",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": 100.0,
        },
    )
    assert transfer_response.status_code == 422
    assert "settlement_date must not be earlier than trade_date" in transfer_response.text


def test_rejects_transactions_outside_account_lifecycle(client):
    closed_account_response = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Closed Equity Sleeve",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-01-01",
            "closed_at": "2026-02-01",
            "status": "closed",
        },
    )
    assert closed_account_response.status_code == 200
    closed_account = closed_account_response.json()

    late_trade_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": closed_account["account_id"],
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
    assert late_trade_response.status_code == 400
    assert "is closed on 2026-04-15" in late_trade_response.json()["detail"]

    future_account_response = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Future HKD Cash",
            "account_type": "deposit_account",
            "currency": "HKD",
            "institution": "Test Bank",
            "opened_at": "2026-05-01",
            "status": "active",
        },
    )
    assert future_account_response.status_code == 200
    future_account = future_account_response.json()

    early_fx_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "fx_conversion",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "counterparty_account_id": future_account["account_id"],
            "gross_amount": 100.0,
            "counter_amount": 780.0,
            "fx_rate": 7.8,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert early_fx_response.status_code == 400
    assert "is not open on 2026-04-15" in early_fx_response.json()["detail"]


def test_defaults_trade_time_and_trade_at_when_not_provided(client):
    deposit_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert deposit_response.status_code == 200
    transaction = deposit_response.json()
    assert transaction["trade_time"] == "12:00"
    assert transaction["trade_timezone"] == "Asia/Shanghai"
    assert transaction["trade_time_is_estimated"] is True
    assert transaction["trade_at"] == "2026-04-15T04:00:00Z"


def test_transaction_list_sorts_same_day_by_trade_time_not_creation_order(client):
    later_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "trade_time": "15:00",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert later_response.status_code == 200

    earlier_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "trade_time": "09:00",
            "account_id": "cash-usd-main",
            "gross_amount": 500.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert earlier_response.status_code == 200

    transactions_response = client.get(
        "/api/portfolios/yungu/transactions",
        params={"account_id": "cash-usd-main", "transaction_type": "deposit"},
    )
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()["transactions"]
    assert transactions[0]["trade_time"] == "15:00"
    assert transactions[1]["trade_time"] == "09:00"


def test_rejects_same_day_sell_before_later_buy_by_trade_time(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Timed Equity Sleeve",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "trade_time": "15:00",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-15",
            "trade_time": "09:00",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 110.0,
            "gross_amount": 11000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 400
    assert "as of trade_date" in sell_response.json()["detail"]


def test_same_day_buy_then_sell_uses_trade_order_not_settlement_order(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Same Day Equity",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "trade_time": "09:30",
            "settlement_date": "2026-04-17",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-15",
            "trade_time": "15:00",
            "settlement_date": "2026-04-16",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 110.0,
            "gross_amount": 11000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    account_workspace_response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"account_id": account["account_id"]},
    )
    assert account_workspace_response.status_code == 200
    assert account_workspace_response.json()["positions"] == []

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    assert len(lots) == 1
    assert lots[0]["status"] == "closed"
    assert lots[0]["realized_cost_basis"] == pytest.approx(10000.0)
    assert lots[0]["realized_pnl"] == pytest.approx(1000.0)

    ledger_response = client.get(
        f"/api/portfolios/yungu/transactions/{sell_response.json()['transaction_id']}/ledger-postings"
    )
    assert ledger_response.status_code == 200
    sell_position_posting = next(
        posting
        for posting in ledger_response.json()["ledger_postings"]
        if posting["posting_role"] == "security_position"
    )
    assert sell_position_posting["cost_basis_delta"] == pytest.approx(-10000.0)


def test_position_lot_entry_price_excludes_capitalized_fees_and_taxes(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Entry Price Review Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-16",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "fees": 5.0,
            "taxes": 3.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv", "status": "open"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    assert len(lots) == 1
    lot = lots[0]
    assert lot["entry_gross_amount"] == pytest.approx(1000.0)
    assert lot["entry_fee_amount"] == pytest.approx(5.0)
    assert lot["entry_tax_amount"] == pytest.approx(3.0)
    assert lot["entry_cost_basis"] == pytest.approx(1008.0)
    assert lot["entry_price"] == pytest.approx(100.0)
    assert lot["entry_cost_per_unit"] == pytest.approx(100.8)


def test_moving_average_position_lots_match_account_cost_basis_method(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "MA Review Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "moving_average",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    for trade_date, price in [("2026-04-10", 100.0), ("2026-04-11", 120.0)]:
        buy_response = client.post(
            "/api/portfolios/yungu/transactions",
            json={
                "transaction_type": "buy",
                "trade_date": trade_date,
                "account_id": account["account_id"],
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": 100.0,
                "price": price,
                "gross_amount": price * 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
            },
        )
        assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 50.0,
            "price": 130.0,
            "gross_amount": 6500.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert sell_response.status_code == 200

    account_workspace_response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"account_id": account["account_id"]},
    )
    assert account_workspace_response.status_code == 200
    account_position = next(
        row
        for row in account_workspace_response.json()["positions"]
        if row["instrument_id"] == "equity-us-abbv"
    )
    assert account_position["cost_basis"] == pytest.approx(16500.0)

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={"account_id": account["account_id"], "instrument_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    assert len(lots) == 1
    assert lots[0]["entry_quantity"] == pytest.approx(200.0)
    assert lots[0]["remaining_quantity"] == pytest.approx(150.0)
    assert lots[0]["entry_cost_basis"] == pytest.approx(22000.0)
    assert sum(lot["remaining_cost_basis"] for lot in lots) == pytest.approx(16500.0)
    assert sum(lot["realized_cost_basis"] for lot in lots) == pytest.approx(5500.0)
    assert sum(lot["realized_pnl"] for lot in lots) == pytest.approx(1000.0)


def test_rejects_position_transfer_with_inconsistent_gross_amount(client):
    source_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Transfer Source",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()
    destination_account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Transfer Dest",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-reserve",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    transfer_response = client.post(
        "/api/portfolios/yungu/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-15",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 50.0,
            "gross_amount": 1.0,
        },
    )
    assert transfer_response.status_code == 400
    assert "must match source cost basis" in transfer_response.json()["detail"]


def test_rejects_return_of_capital_above_remaining_cost_basis(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "ROC Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    roc_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 1500.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert roc_response.status_code == 400
    assert "exceeds account position cost basis" in roc_response.json()["detail"]


def test_accounts_workspace_includes_selected_account_linked_transactions(client):
    response = client.get(
        "/api/portfolios/yungu/accounts/workspace",
        params={"account_id": "broker-us-core"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["selected_account_id"] == "broker-us-core"
    assert payload["linked_transactions_summary"]["total_transactions"] > 0
    assert payload["linked_transactions_summary"]["instrument_transactions"] > 0
    assert payload["ledger_postings"]
    assert payload["positions"]
    assert all(
        item["account"]["account_id"] == "broker-us-core" for item in payload["linked_transactions"]
    )

    selected_account_row = next(
        row for row in payload["accounts"] if row["account"]["account_id"] == "broker-us-core"
    )
    assert selected_account_row["linked_transaction_count"] > 0
    assert selected_account_row["linked_transaction_count"] <= payload["linked_transactions_summary"]["total_transactions"]


def test_transactions_workspace_returns_selected_fact_ledger_and_related_position_lots(client):
    response = client.get(
        "/api/portfolios/yungu/transactions/workspace",
        params={"transaction_id": "txn-0003"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["selected_transaction_id"] == "txn-0003"
    assert payload["selected_transaction"]["transaction_id"] == "txn-0003"
    assert payload["selected_transaction"]["account"]["account_id"] == "broker-us-core"
    assert payload["ledger_summary"]["posting_count"] > 0
    assert all(posting["transaction_id"] == "txn-0003" for posting in payload["ledger_postings"])
    assert payload["related_position_lot_summary"]["position_lot_count"] > 0
    assert any(lot["opened_by_transaction_id"] == "txn-0003" for lot in payload["related_position_lots"])


def test_security_trade_cash_posting_uses_settlement_effective_date(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Settlement Timing Review",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    buy_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "settlement_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200

    ledger_response = client.get(
        f"/api/portfolios/yungu/transactions/{buy_response.json()['transaction_id']}/ledger-postings"
    )
    assert ledger_response.status_code == 200
    postings = ledger_response.json()["ledger_postings"]

    position_posting = next(item for item in postings if item["posting_role"] == "security_position")
    cash_posting = next(item for item in postings if item["posting_role"] == "security_settlement_cash")

    assert position_posting["effective_date"] == "2026-04-10"
    assert cash_posting["effective_date"] == "2026-04-12"


def test_security_opening_balance_preserves_acquisition_date_in_position_lots(client):
    account = client.post(
        "/api/portfolios/yungu/accounts",
        json={
            "account_name": "Imported Lot Account",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    opening_balance_response = client.post(
        "/api/portfolios/yungu/transactions",
        json={
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-10",
            "settlement_date": "2026-04-10",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "acquisition_date": "2025-03-01",
        },
    )
    assert opening_balance_response.status_code == 200
    assert opening_balance_response.json()["acquisition_date"] == "2025-03-01"

    lots_response = client.get(
        "/api/portfolios/yungu/position-lots",
        params={
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "status": "open",
            "as_of_date": "2026-04-15",
        },
    )
    assert lots_response.status_code == 200
    payload = lots_response.json()
    assert payload["summary"]["position_lot_count"] == 1

    lot = payload["position_lots"][0]
    assert lot["opened_at"] == "2026-04-10"
    assert lot["acquisition_date"] == "2025-03-01"
    assert lot["holding_period_days"] == (date(2026, 4, 15) - date(2025, 3, 1)).days
