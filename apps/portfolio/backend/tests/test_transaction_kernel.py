from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from tests.store_fixture import TEST_PORTFOLIO_STORE

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.api.contracts import InstrumentOption, LedgerPostingRecord, PositionLotRecord
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshot_worker, daily_snapshots, portfolio_store
from portfolio_app.services.ledger import _build_position_state, build_position_lots, derive_ledger_postings


def _share_split_event(
    *,
    effective_date: str = "2026-07-10",
    record_date: str = "2026-07-09",
    new_units: str = "2",
    old_units: str = "1",
    quantity_rounding: str = "exact",
    status: str = "confirmed",
) -> dict[str, object]:
    return {
        "corporate_action_event_id": f"ca-equity-us-abbv-share-split-{effective_date}",
        "instrument_id": "equity-us-abbv",
        "action_type": "share_split",
        "announcement_date": "2026-07-06",
        "record_date": record_date,
        "effective_date": effective_date,
        "payable_date": effective_date,
        "new_units": new_units,
        "old_units": old_units,
        "quantity_rounding": quantity_rounding,
        "quantity_precision": 0,
        "cost_basis_treatment": "carry",
        "source": "issuer_announcement",
        "external_event_id": None,
        "status": status,
        "provenance": {},
        "created_at": "2026-07-06T00:00:00Z",
        "updated_at": "2026-07-06T00:00:00Z",
    }


def _split_test_transaction(
    transaction_id: str,
    transaction_type: str,
    trade_date: str,
    quantity: float,
    gross_amount: float,
    *,
    transaction_sequence: int,
    trade_time: str = "10:00",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "transaction_sequence": transaction_sequence,
        "portfolio_id": "p",
        "transaction_type": transaction_type,
        "trade_date": trade_date,
        "trade_at": f"{trade_date}T{trade_time}:00Z",
        "created_at": f"{trade_date}T{trade_time}:00Z",
        "settlement_date": trade_date,
        "account_id": "acct",
        "settlement_cash_account_id": "cash",
        "instrument_id": "equity-us-abbv",
        "instrument_ref": {
            "instrument_id": "equity-us-abbv",
            "instrument_name": "Split Security",
            "instrument_type": "equity",
            "currency": "USD",
            "identifiers": [],
        },
        "quantity": quantity,
        "gross_amount": gross_amount,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
    }


def _split_test_accounts() -> list[dict[str, object]]:
    return [
        {
            "account_id": "acct",
            "account_type": "securities_account",
            "cost_basis_method": "fifo",
        }
    ]


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
    response = client.get("/api/portfolios/portfolio-ops/instruments")
    assert response.status_code == 200

    payload = response.json()
    assert payload["portfolio_id"] == "portfolio-ops"
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
    assert abbv["latest_market_data"][0]["price_unit"] == "per_unit"
    assert abbv["latest_market_data"][0]["price_scale"] == "1"


def test_instrument_option_rejects_missing_or_noncanonical_price_contract() -> None:
    payload = {
        "instrument_core": {
            "instrument_id": "bond-test",
            "instrument_name": "Bond Test",
            "instrument_type": "bond",
            "currency": "USD",
            "identifiers": [],
        },
        "coverage_state": "complete",
        "quote_selection_policy": {
            "trading": ["clean_price", "dirty_price"],
            "valuation": ["dirty_price", "clean_price"],
            "total_return": ["dirty_price", "clean_price"],
            "chart": ["dirty_price", "clean_price"],
            "reference": ["clean_price", "dirty_price"],
        },
        "latest_market_data": [
            {
                "metric_family": "price",
                "quote_basis": "dirty_price",
                "as_of_date": "2026-07-15",
                "value": "98.5",
                "currency": "USD",
                "price_unit": "percent_of_par",
                "price_scale": "0.01",
                "status": "complete",
            }
        ],
    }

    assert str(InstrumentOption.model_validate(payload).latest_market_data[0].price_scale) == "0.01"
    missing = deepcopy(payload)
    missing["latest_market_data"][0].pop("price_scale")
    with pytest.raises(ValueError, match="price_scale"):
        InstrumentOption.model_validate(missing)

    wrong = deepcopy(payload)
    wrong["latest_market_data"][0]["price_unit"] = "per_unit"
    wrong["latest_market_data"][0]["price_scale"] = "1"
    with pytest.raises(ValueError, match="canonical instrument identity"):
        InstrumentOption.model_validate(wrong)

    for required_field in ("latest_market_data", "quote_selection_policy"):
        incomplete = deepcopy(payload)
        incomplete.pop(required_field)
        with pytest.raises(ValueError, match=required_field):
            InstrumentOption.model_validate(incomplete)


def test_transaction_write_enqueues_materialized_daily_snapshot_recalculation(client):
    baseline_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert baseline_response.status_code == 200

    created_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "stale"
        assert state.dirty_from == date(2026, 4, 16)

    assert daily_snapshot_worker.run_daily_snapshot_recalculation_worker_once()

    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.dirty_from is None
        assert state.refreshed_to == date(2026, 4, 16)
        latest_snapshot = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert latest_snapshot is not None
        assert latest_snapshot.as_of_date == date(2026, 4, 16)


def test_transaction_update_marks_daily_snapshots_dirty_from_old_trade_date():
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )
    existing = portfolio_store.get_transaction("portfolio-ops", "txn-0002")
    assert existing is not None

    updated = portfolio_store.update_transaction(
        "portfolio-ops",
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
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
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
                portfolio_id="portfolio-ops",
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

    result = daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(
        "portfolio-ops"
    )

    assert build_calls["count"] == 2
    assert result is not None
    assert result["refreshed_to"] == date(2026, 4, 18)
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "current"
        assert state.dirty_from is None
        assert state.refreshed_to == date(2026, 4, 18)
        latest_snapshot = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert latest_snapshot is not None
        assert latest_snapshot.as_of_date == date(2026, 4, 18)


def test_securities_account_defaults_to_fifo_when_cost_basis_omitted(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        f"/api/portfolios/portfolio-ops/accounts/{account['account_id']}",
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
        "/api/portfolios/portfolio-ops/accounts",
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
            "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert fifo_lots_response.status_code == 200
    fifo_lots = fifo_lots_response.json()["position_lots"]
    assert sum(lot["remaining_cost_basis"] for lot in fifo_lots) == pytest.approx(400000.0)
    assert sum(lot["realized_cost_basis"] for lot in fifo_lots) == pytest.approx(350000.0)
    assert sum(lot["realized_pnl"] for lot in fifo_lots) == pytest.approx(100000.0)

    updated_response = client.patch(
        f"/api/portfolios/portfolio-ops/accounts/{account['account_id']}",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert restated_lots_response.status_code == 200
    restated_lots = restated_lots_response.json()["position_lots"]
    assert len(restated_lots) == 1
    assert sum(lot["remaining_cost_basis"] for lot in restated_lots) == pytest.approx(300000.0)
    assert sum(lot["realized_cost_basis"] for lot in restated_lots) == pytest.approx(450000.0)
    assert sum(lot["realized_pnl"] for lot in restated_lots) == pytest.approx(0.0)


def test_transaction_fact_can_be_updated_and_deleted(client):
    created_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-17",
            "account_id": "cash-usd-main",
            "gross_amount": 1250.0,
            "currency": "USD",
            "note": "Corrected note",
            "expected_row_version": created_response.json()["row_version"],
        },
    )
    assert updated_response.status_code == 200
    updated_payload = updated_response.json()
    assert updated_payload["transaction_id"] == transaction_id
    assert updated_payload["trade_date"] == "2026-04-17"
    assert updated_payload["gross_amount"] == pytest.approx(1250.0)
    assert updated_payload["note"] == "Corrected note"

    deleted_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json={
            "expected_row_versions": {
                transaction_id: updated_payload["row_version"]
            }
        },
    )
    assert deleted_response.status_code == 200
    deleted_payload = deleted_response.json()
    assert deleted_payload["deleted_count"] == 1
    assert deleted_payload["deleted_transaction_ids"] == [transaction_id]

    listing_response = client.get("/api/portfolios/portfolio-ops/transactions")
    assert listing_response.status_code == 200
    assert transaction_id not in {
        item["transaction_id"] for item in listing_response.json()["transactions"]
    }


def test_deleting_transfer_leg_removes_entire_pair(client):
    caller_named_group = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-16",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": 250.0,
            "transfer_group_id": "caller-controlled-group",
        },
    )
    assert caller_named_group.status_code == 422

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
    expected_row_versions = {
        transaction["transaction_id"]: transaction["row_version"]
        for transaction in transfer_payload["transactions"]
    }
    workspace_response = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": delete_target},
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["delete_scope_row_versions"] == expected_row_versions

    incomplete_delete = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{delete_target}",
        json={
            "expected_row_versions": {
                delete_target: transfer_payload["transactions"][0]["row_version"]
            }
        },
    )
    assert incomplete_delete.status_code == 409
    assert "delete scope changed" in incomplete_delete.json()["detail"].lower()

    deleted_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{delete_target}",
        json={"expected_row_versions": expected_row_versions},
    )
    assert deleted_response.status_code == 200
    deleted_payload = deleted_response.json()
    assert deleted_payload["deleted_count"] == 2
    assert deleted_payload["transfer_group_id"] == transfer_payload["transfer_group_id"]


def test_create_transactions_rolls_back_whole_batch_on_later_failure(client):
    from portfolio_app.services.portfolio_store import create_transactions, list_transactions

    before_ids = {item["transaction_id"] for item in list_transactions("portfolio-ops")}

    with pytest.raises(KeyError, match="gross_amount"):
        create_transactions(
            portfolio_id="portfolio-ops",
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

    after = list_transactions("portfolio-ops")
    assert {item["transaction_id"] for item in after} == before_ids
    assert all(item["note"] != "batch rollback sentinel" for item in after)


def test_rejects_cross_currency_security_facts(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "Securities account currency must match asset currency" in buy_response.json()["detail"]

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
    holdings_response = client.get("/api/workspace/holdings", params={"portfolio_id": "portfolio-ops"})
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

    cash_rows = [row for row in holdings["rows"] if row["instrument_core"]["instrument_type"] == "cash"]
    assert cash_rows
    expected_cash_balance = sum(
        (row["market_value"] or 0.0) * fx_to_usd[row["instrument_core"]["currency"]]
        for row in cash_rows
        if row["market_value"] is not None
    )
    assert holdings["totals"]["cash_balance"] == pytest.approx(expected_cash_balance)
    usd_cash_row = next(row for row in cash_rows if row["instrument_core"]["instrument_id"] == "cash:USD")
    assert usd_cash_row["instrument_core"]["instrument_name"] == "Cash (USD)"
    assert usd_cash_row["coverage_status"] == "cash"
    assert usd_cash_row["cost_basis"] is None
    assert usd_cash_row["cost_basis_base"] is None
    assert usd_cash_row["market_value_base"] == pytest.approx(usd_cash_row["market_value"])
    assert usd_cash_row["day_change_pct"] == pytest.approx(0.0)
    assert usd_cash_row["day_change_value"] == pytest.approx(0.0)
    assert usd_cash_row["day_change_value_base"] == pytest.approx(0.0)

    hkd_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "fund-hk-2800")
    expected_total_nav = holdings["totals"]["nav"]
    expected_hkd_allocation = (hkd_row["market_value"] * fx_to_usd["HKD"]) / expected_total_nav
    assert hkd_row["allocation"] == pytest.approx(expected_hkd_allocation)
    assert holdings["totals"]["allocation"] == pytest.approx(expected_total_market_value / expected_total_nav)

    accounts_response = client.get("/api/portfolios/portfolio-ops/accounts/workspace")
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
        params={"portfolio_id": "portfolio-ops", "as_of_date": "2026-04-02"},
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
    baseline_summary_response = client.get("/api/workspace/summary", params={"portfolio_id": "portfolio-ops"})
    assert baseline_summary_response.status_code == 200
    baseline_nav = baseline_summary_response.json()["nav"]

    baseline_response = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts/workspace",
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

    post_buy_summary_response = client.get("/api/workspace/summary", params={"portfolio_id": "portfolio-ops"})
    assert post_buy_summary_response.status_code == 200
    assert post_buy_summary_response.json()["nav"] == pytest.approx(baseline_nav)

    settlement_date_response = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
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
    response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "portfolio-ops", "include_details": True},
    )
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
    assert abbv_row["instrument_trend_basis"] == "adjusted_close"
    assert abbv_row["day_change_pct"] == pytest.approx(206.47 / 207.18 - 1)
    assert abbv_row["day_change_value"] == pytest.approx(abbv_row["quantity"] * (206.47 - 207.18))
    assert abbv_row["instrument_return_1w"] == pytest.approx(206.47 / 207.18 - 1)
    assert abbv_row["instrument_return_mtd"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_return_ytd"] is None
    assert abbv_row["instrument_return_1y"] is None
    assert abbv_row["instrument_current_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_max_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_holding_max_drawdown"] == pytest.approx(206.47 / 210.20 - 1)
    assert abbv_row["instrument_holding_start_date"] == "2026-02-10"
    assert abbv_row["instrument_volatility_1m"] is None
    assert abbv_row["instrument_volatility_3m"] is None
    assert abbv_row["instrument_volatility_6m"] is None
    assert abbv_row["instrument_volatility_1y"] is None

    assert abbv_row["price_chart_3m"][0]["date"] == "2026-02-10"
    assert abbv_row["instrument_return_mtd"] == pytest.approx(206.47 / 210.20 - 1)


def test_live_holdings_workspace_propagates_position_day_change(client, monkeypatch):
    from portfolio_app.api.routes import workspace as workspace_routes

    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: None,
    )

    response = client.get("/api/workspace/holdings", params={"portfolio_id": "portfolio-ops"})
    assert response.status_code == 200
    holdings = response.json()

    abbv_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "equity-us-abbv")
    assert abbv_row["day_change_pct"] == pytest.approx(206.47 / 207.18 - 1)
    assert abbv_row["day_change_value"] == pytest.approx(abbv_row["quantity"] * (206.47 - 207.18))
    usd_cash_row = next(row for row in holdings["rows"] if row["instrument_core"]["instrument_id"] == "cash:USD")
    assert usd_cash_row["instrument_core"]["instrument_type"] == "cash"
    assert usd_cash_row["coverage_status"] == "cash"
    assert usd_cash_row["day_change_pct"] == pytest.approx(0.0)
    assert usd_cash_row["day_change_value"] == pytest.approx(0.0)
    expected_day_change_base = sum(row["day_change_value_base"] for row in holdings["rows"])
    expected_prior_market_value = holdings["totals"]["market_value"] - expected_day_change_base
    assert holdings["totals"]["day_change_value"] == pytest.approx(expected_day_change_base)
    assert holdings["totals"]["day_change_pct"] == pytest.approx(
        expected_day_change_base / expected_prior_market_value
    )


def test_instrument_price_chart_endpoint_returns_filtered_shared_history(client):
    response = client.get(
        "/api/portfolios/portfolio-ops/instruments/equity-us-abbv/price-chart",
        params={"as_of_date": "2026-04-15", "range": "1m"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["portfolio_id"] == "portfolio-ops"
    assert payload["instrument_core"]["instrument_id"] == "equity-us-abbv"
    assert payload["range_key"] == "1m"
    assert payload["chart_basis"] == "adjusted_close"
    assert [point["date"] for point in payload["points"]] == ["2026-03-15", "2026-04-08", "2026-04-15"]
    assert payload["summary"]["point_count"] == 3
    assert payload["summary"]["change_value"] == pytest.approx(-3.73)
    assert payload["summary"]["high"] == pytest.approx(210.20)
    assert payload["summary"]["low"] == pytest.approx(206.47)


def test_transaction_position_preview_returns_quantity_as_of_trade_moment(client):
    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/position-preview",
        params={
            "account_id": "broker-us-core",
            "position_kind": "instrument",
            "position_reference_id": "equity-us-abbv",
            "as_of_date": "2026-04-15",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["portfolio_id"] == "portfolio-ops"
    assert payload["account_id"] == "broker-us-core"
    assert payload["position_kind"] == "instrument"
    assert payload["position_reference_id"] == "equity-us-abbv"
    assert payload["as_of_date"] == "2026-04-15"
    assert payload["quantity"] == pytest.approx(880.0)


def test_position_lots_support_historical_as_of_date(client):
    response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"position_reference_id": "equity-us-abbv", "status": "open", "as_of_date": "2026-04-02"},
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/accounts",
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
            "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": destination_account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert destination_lots_response.status_code == 200
    destination_lots = destination_lots_response.json()["position_lots"]
    assert len(destination_lots) == 1
    assert destination_lots[0]["entry_quantity"] == pytest.approx(150.0)
    assert destination_lots[0]["entry_cost_basis"] == pytest.approx(2250.0)

    source_lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": source_account["account_id"], "position_reference_id": "equity-us-abbv", "status": "open"},
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": source_account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    destination_lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": destination_account["account_id"], "position_reference_id": "equity-us-abbv"},
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
        "/api/portfolios/portfolio-ops/transactions",
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
    copy_response = client.post("/api/portfolios/portfolio-ops/copy")
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
        "/api/portfolios/portfolio-ops/transactions",
        params={"account_id": "cash-cny-main", "transaction_type": "fx_conversion"},
    )
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()["transactions"]
    assert len(transactions) == 1
    assert transactions[0]["transaction_id"] == "txn-0018"


def test_rejects_backdated_sell_before_position_exists(client):
    copy_response = client.post("/api/portfolios/portfolio-ops/copy")
    assert copy_response.status_code == 200
    copied_portfolio_id = copy_response.json()["portfolio_id"]
    destination_account_response = client.post(
        f"/api/portfolios/{copied_portfolio_id}/accounts",
        json={
            "account_name": "Copy Equity Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-reserve-portfolio-ops-copy",
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
            "account_id": "broker-us-core-portfolio-ops-copy",
            "settlement_cash_account_id": "cash-usd-main-portfolio-ops-copy",
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
            "from_account_id": "broker-us-core-portfolio-ops-copy",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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


def test_preserves_transaction_source_precision_and_keeps_float_calculation_projection(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "quantity": "700.004",
            "price": "11.54364",
            "gross_amount": "8080.59417456",
            "fees": "0.004",
            "taxes": "0.004",
            "currency": "USD",
        },
    )
    assert buy_response.status_code == 200
    payload = buy_response.json()
    assert payload["quantity"] == pytest.approx(700.004)
    assert payload["price"] == pytest.approx(11.54364)
    assert payload["gross_amount"] == pytest.approx(8080.59417456)
    assert payload["fees"] == pytest.approx(0.004)
    assert payload["taxes"] == pytest.approx(0.004)
    assert payload["source_quantity"] == "700.004000000000"
    assert payload["source_price"] == "11.543640000000"
    assert payload["source_gross_amount"] == "8080.59417456"
    assert payload["source_fees"] == "0.00400000"
    assert payload["source_taxes"] == "0.00400000"


def test_rejects_inconsistent_opening_balance_and_dividend_reinvestment_amount_contracts(client):
    opening_account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "cash opening balance must not reference an asset" in deposit_account_security_opening_balance.json()["detail"].lower()


def test_rejects_deposit_account_fee_with_instrument_reference(client):
    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "deposit-account fee and tax must not reference an asset" in fee_response.json()["detail"].lower()

    fee_with_settlement_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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

    lots_after_dividend_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={
            "account_id": account["account_id"],
            "position_reference_id": "equity-us-abbv",
        },
    )
    assert lots_after_dividend_response.status_code == 200
    lot_after_dividend = lots_after_dividend_response.json()["position_lots"][0]
    assert lot_after_dividend["income_cash_amount"] == pytest.approx(100.0)
    assert lot_after_dividend["expense_cash_amount"] == pytest.approx(15.0)
    assert lot_after_dividend["remaining_cost_basis"] == pytest.approx(10000.0)
    assert lot_after_dividend["realized_pnl"] == pytest.approx(0.0)

    roc_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lot = lots_response.json()["position_lots"][0]
    assert lot["income_cash_amount"] == pytest.approx(100.0)
    assert lot["expense_cash_amount"] == pytest.approx(20.0)
    assert lot["return_of_capital_amount"] == pytest.approx(40.0)
    assert lot["remaining_cost_basis"] == pytest.approx(9960.0)


def test_flat_position_rejects_follow_on_sell_transfer_and_return_of_capital(client):
    source_account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "requires an account position as of entitlement_date" in dividend_response.json()["detail"]

    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "requires an account position as of entitlement_date" in fee_response.json()["detail"]


def test_rejects_nested_fee_and_tax_fields_on_fee_tax_transactions(client):
    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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


def test_rejects_dividend_reinvestment_without_entitled_position(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
    assert "requires an account position as of entitlement_date" in response.json()["detail"]


def test_accepts_entitlement_date_on_dividend_reinvestment(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
    assert response.status_code == 200
    assert response.json()["entitlement_date"] == "2026-04-10"


def test_accepts_late_paid_dividend_when_entitlement_date_precedes_sale(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
            "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    closed_lot = next(lot for lot in lots if lot["status"] == "closed")
    open_lot = next(lot for lot in lots if lot["status"] == "open")
    assert closed_lot["income_cash_amount"] == pytest.approx(50.0)
    assert open_lot["income_cash_amount"] == pytest.approx(50.0)


def test_entitlement_bod_excludes_same_day_buy_from_income_allocation():
    instrument_ref = {
        "instrument_id": "equity-entitlement-test",
        "instrument_name": "Entitlement Test Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    account = {
        "account_id": "broker-entitlement-test",
        "account_type": "securities_account",
        "cost_basis_method": "fifo",
    }
    common = {
        "portfolio_id": "portfolio-entitlement-test",
        "account_id": account["account_id"],
        "instrument_id": instrument_ref["instrument_id"],
        "instrument_ref": instrument_ref,
        "currency": "USD",
        "fees": 0.0,
        "taxes": 0.0,
    }
    lots = build_position_lots(
        "portfolio-entitlement-test",
        [account],
        [
            {
                **common,
                "transaction_id": "txn-prior-buy",
                "transaction_sequence": 1,
                "transaction_type": "buy",
                "trade_date": "2026-04-09",
                "settlement_date": "2026-04-09",
                "quantity": 10.0,
                "gross_amount": 1000.0,
                "created_at": "2026-04-09T09:00:00Z",
            },
            {
                **common,
                "transaction_id": "txn-ex-date-buy",
                "transaction_sequence": 2,
                "transaction_type": "buy",
                "trade_date": "2026-04-10",
                "settlement_date": "2026-04-10",
                "quantity": 10.0,
                "gross_amount": 1000.0,
                "created_at": "2026-04-10T09:00:00Z",
            },
            {
                **common,
                "transaction_id": "txn-dividend",
                "transaction_sequence": 3,
                "transaction_type": "dividend",
                "trade_date": "2026-04-15",
                "entitlement_date": "2026-04-10",
                "settlement_date": "2026-04-15",
                "quantity": None,
                "gross_amount": 100.0,
                "created_at": "2026-04-15T09:00:00Z",
            },
        ],
    )

    by_opening_transaction = {lot["opened_by_transaction_id"]: lot for lot in lots}
    assert by_opening_transaction["txn-prior-buy"]["income_cash_amount"] == pytest.approx(100.0)
    assert by_opening_transaction["txn-ex-date-buy"]["income_cash_amount"] == pytest.approx(0.0)


def test_entitlement_bod_keeps_same_day_sale_in_income_allocation():
    instrument_ref = {
        "instrument_id": "equity-entitlement-test",
        "instrument_name": "Entitlement Test Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    account = {
        "account_id": "broker-entitlement-test",
        "account_type": "securities_account",
        "cost_basis_method": "fifo",
    }
    common = {
        "portfolio_id": "portfolio-entitlement-test",
        "account_id": account["account_id"],
        "instrument_id": instrument_ref["instrument_id"],
        "instrument_ref": instrument_ref,
        "currency": "USD",
        "fees": 0.0,
        "taxes": 0.0,
    }
    lots = build_position_lots(
        "portfolio-entitlement-test",
        [account],
        [
            {
                **common,
                "transaction_id": "txn-prior-buy",
                "transaction_sequence": 1,
                "transaction_type": "buy",
                "trade_date": "2026-04-09",
                "settlement_date": "2026-04-09",
                "quantity": 10.0,
                "gross_amount": 1000.0,
                "created_at": "2026-04-09T09:00:00Z",
            },
            {
                **common,
                "transaction_id": "txn-ex-date-sell",
                "transaction_sequence": 2,
                "transaction_type": "sell",
                "trade_date": "2026-04-10",
                "settlement_date": "2026-04-10",
                "quantity": 10.0,
                "gross_amount": 1100.0,
                "created_at": "2026-04-10T09:00:00Z",
            },
            {
                **common,
                "transaction_id": "txn-dividend",
                "transaction_sequence": 3,
                "transaction_type": "dividend",
                "trade_date": "2026-04-15",
                "entitlement_date": "2026-04-10",
                "settlement_date": "2026-04-15",
                "quantity": None,
                "gross_amount": 100.0,
                "created_at": "2026-04-15T09:00:00Z",
            },
        ],
    )

    assert len(lots) == 1
    assert lots[0]["status"] == "closed"
    assert lots[0]["income_cash_amount"] == pytest.approx(100.0)


def test_entitlement_bod_accepts_same_day_opening_balance_with_prior_acquisition():
    instrument_ref = {
        "instrument_id": "equity-entitlement-test",
        "instrument_name": "Entitlement Test Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    account = {
        "account_id": "broker-entitlement-test",
        "account_type": "securities_account",
        "cost_basis_method": "fifo",
    }
    common = {
        "portfolio_id": "portfolio-entitlement-test",
        "account_id": account["account_id"],
        "instrument_id": instrument_ref["instrument_id"],
        "instrument_ref": instrument_ref,
        "currency": "USD",
        "fees": 0.0,
        "taxes": 0.0,
    }
    lots = build_position_lots(
        "portfolio-entitlement-test",
        [account],
        [
            {
                **common,
                "transaction_id": "txn-opening",
                "transaction_sequence": 1,
                "transaction_type": "opening_balance",
                "trade_date": "2026-04-10",
                "acquisition_date": "2026-04-01",
                "settlement_date": "2026-04-10",
                "quantity": 10.0,
                "gross_amount": 1000.0,
                "created_at": "2026-04-10T08:00:00Z",
            },
            {
                **common,
                "transaction_id": "txn-dividend",
                "transaction_sequence": 2,
                "transaction_type": "dividend",
                "trade_date": "2026-04-15",
                "entitlement_date": "2026-04-10",
                "settlement_date": "2026-04-15",
                "quantity": None,
                "gross_amount": 100.0,
                "created_at": "2026-04-15T09:00:00Z",
            },
        ],
    )

    assert len(lots) == 1
    assert lots[0]["income_cash_amount"] == pytest.approx(100.0)


def test_accepts_late_paid_dividend_reinvestment_after_entitled_position_was_sold(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
    assert drip_response.status_code == 200
    assert drip_response.json()["entitlement_date"] == "2026-04-10"

    ledger_response = client.get("/api/portfolios/portfolio-ops/ledger-postings")
    assert ledger_response.status_code == 200

    workspace_response = client.get("/api/portfolios/portfolio-ops/accounts/workspace")
    assert workspace_response.status_code == 200


def test_rejects_entitlement_date_on_return_of_capital(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
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
            "transaction_sequence": 1,
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
            "transaction_sequence": 2,
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
            "transaction_sequence": 3,
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
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
            "transaction_sequence": 1,
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
            "transaction_sequence": 2,
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
            "transaction_sequence": 1,
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
            "transaction_sequence": 2,
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
        params={"account_id": "cash-usd-main", "transaction_type": "deposit"},
    )
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()["transactions"]
    assert transactions[0]["trade_time"] == "15:00"
    assert transactions[1]["trade_time"] == "09:00"


def test_confirmed_share_split_applies_at_effective_bod_before_same_day_buy() -> None:
    transactions = [
        _split_test_transaction(
            "buy-before", "buy", "2026-07-08", 100.0, 1000.0, transaction_sequence=1
        ),
        _split_test_transaction(
            "sell-record", "sell", "2026-07-09", 20.0, 240.0, transaction_sequence=2
        ),
        _split_test_transaction(
            "buy-effective", "buy", "2026-07-10", 10.0, 100.0, transaction_sequence=3
        ),
    ]
    event = _share_split_event()

    state = _build_position_state(
        transactions,
        account_cost_methods={"acct": "fifo"},
        corporate_actions=[event],
        as_of_date=date(2026, 7, 10),
    )
    bucket = state[("acct", "equity-us-abbv")]
    assert bucket["quantity"] == pytest.approx(170.0)
    assert bucket["cost_basis"] == pytest.approx(900.0)

    postings = derive_ledger_postings(
        "p",
        transactions,
        account_cost_methods={"acct": "fifo"},
        corporate_actions=[event],
        as_of_date=date(2026, 7, 10),
    )
    split_posting = next(
        posting
        for posting in postings
        if posting["posting_role"] == "corporate_action_position_adjustment"
    )
    assert split_posting["quantity_delta"] == pytest.approx(80.0)
    assert split_posting["cost_basis_delta"] == 0.0
    assert split_posting["effective_date"] == "2026-07-10"
    LedgerPostingRecord.model_validate(split_posting)


def test_share_split_record_date_buys_and_sells_define_entitled_eod_quantity() -> None:
    transactions = [
        _split_test_transaction(
            "buy-before", "buy", "2026-07-08", 100.0, 1000.0, transaction_sequence=1
        ),
        _split_test_transaction(
            "buy-record",
            "buy",
            "2026-07-09",
            20.0,
            220.0,
            transaction_sequence=2,
            trade_time="09:30",
        ),
        _split_test_transaction(
            "sell-record",
            "sell",
            "2026-07-09",
            10.0,
            120.0,
            transaction_sequence=3,
            trade_time="14:30",
        ),
        _split_test_transaction(
            "buy-effective", "buy", "2026-07-10", 5.0, 50.0, transaction_sequence=4
        ),
    ]
    state = _build_position_state(
        transactions,
        account_cost_methods={"acct": "fifo"},
        corporate_actions=[_share_split_event()],
        as_of_date=date(2026, 7, 10),
    )
    # Record-date EOD owns 110 units -> 220 at effective BOD; the effective-day
    # buy is already expressed in post-split units and must not be multiplied.
    assert state[("acct", "equity-us-abbv")]["quantity"] == pytest.approx(225.0)


def test_share_split_truncates_once_at_account_total_then_allocates_lots() -> None:
    transactions = [
        _split_test_transaction(
            "lot-a",
            "buy",
            "2026-07-08",
            10.2,
            102.0,
            transaction_sequence=1,
            trade_time="09:30",
        ),
        _split_test_transaction(
            "lot-b",
            "buy",
            "2026-07-08",
            5.3,
            53.0,
            transaction_sequence=2,
            trade_time="14:30",
        ),
    ]
    event = _share_split_event(
        new_units="3",
        old_units="2",
        quantity_rounding="truncate",
    )
    lots = build_position_lots(
        "p",
        _split_test_accounts(),
        transactions,
        as_of_date=date(2026, 7, 10),
        corporate_actions=[event],
    )
    open_lots = [lot for lot in lots if lot["status"] == "open"]
    assert sum(lot["remaining_quantity"] for lot in open_lots) == pytest.approx(23.0)
    assert sum(lot["remaining_cost_basis"] for lot in open_lots) == pytest.approx(155.0)
    assert len(open_lots) == 2
    assert all(lot["opening_transaction_type"] == "corporate_action" for lot in open_lots)
    assert all(lot["unit_cost_basis_after"] < lot["unit_cost_basis_before"] for lot in open_lots)
    for lot in lots:
        PositionLotRecord.model_validate(lot)


def test_share_split_rejects_unmodeled_due_bill_interval_trades() -> None:
    transactions = [
        _split_test_transaction(
            "buy-record", "buy", "2026-07-09", 100.0, 1000.0, transaction_sequence=1
        ),
        _split_test_transaction(
            "sell-between", "sell", "2026-07-10", 10.0, 110.0, transaction_sequence=2
        ),
    ]
    event = _share_split_event(effective_date="2026-07-11", record_date="2026-07-09")
    with pytest.raises(ValueError, match="due-bill processing"):
        _build_position_state(
            transactions,
            account_cost_methods={"acct": "fifo"},
            corporate_actions=[event],
            as_of_date=date(2026, 7, 11),
        )


def test_provider_detected_split_never_changes_portfolio_quantity() -> None:
    transactions = [
        _split_test_transaction(
            "buy-before", "buy", "2026-07-08", 100.0, 1000.0, transaction_sequence=1
        ),
    ]
    state = _build_position_state(
        transactions,
        account_cost_methods={"acct": "fifo"},
        corporate_actions=[_share_split_event(status="detected")],
        as_of_date=date(2026, 7, 10),
    )
    assert state[("acct", "equity-us-abbv")]["quantity"] == pytest.approx(100.0)


def test_confirmed_cash_in_lieu_split_fails_closed_without_cash_fact() -> None:
    transactions = [
        _split_test_transaction(
            "buy-before", "buy", "2026-07-08", 101.0, 1010.0, transaction_sequence=1
        ),
    ]
    event = _share_split_event(new_units="1", old_units="2")
    event["quantity_rounding"] = "cash_in_lieu"
    with pytest.raises(ValueError, match="cash-in-lieu valuation and receivable"):
        _build_position_state(
            transactions,
            account_cost_methods={"acct": "fifo"},
            corporate_actions=[event],
            as_of_date=date(2026, 7, 10),
        )


def test_transaction_execution_quote_uses_raw_valuation_basis_not_adjusted_chart(
    client,
    monkeypatch,
) -> None:
    from portfolio_app.services import execution_quotes

    monkeypatch.setattr(
        execution_quotes,
        "get_registry_instrument_detail",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "instrument_name": "Split ETF",
            "instrument_type": "etf",
            "currency": "CNY",
            "identifiers": [],
            "quote_selection_policy": {
                "valuation": ["close", "last"],
                "chart": ["adjusted_close", "close"],
            },
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-03-27",
                    "value": "1.66",
                    "currency": "CNY",
                    "price_unit": "per_unit",
                    "price_scale": 1.0,
                    "provider": "tushare:fund_daily",
                    "status": "complete",
                },
                {
                    "metric_family": "price",
                    "quote_basis": "adjusted_close",
                    "as_of_date": "2026-03-27",
                    "value": "0.4152179894",
                    "currency": "CNY",
                    "price_unit": "per_unit",
                    "price_scale": 1.0,
                    "provider": "tushare:fund_adj",
                    "status": "complete",
                },
            ],
        },
    )

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "159516-sz", "as_of_date": "2026-03-27"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["quote_basis"] == "close"
    assert payload["value"] == pytest.approx(1.66)
    assert payload["quote_date"] == "2026-03-27"
    assert payload["stale"] is False


def test_rejects_same_day_sell_before_later_buy_by_trade_time(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts/workspace",
        params={"account_id": account["account_id"]},
    )
    assert account_workspace_response.status_code == 200
    assert account_workspace_response.json()["positions"] == []

    lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
    )
    assert lots_response.status_code == 200
    lots = lots_response.json()["position_lots"]
    assert len(lots) == 1
    assert lots[0]["status"] == "closed"
    assert lots[0]["realized_cost_basis"] == pytest.approx(10000.0)
    assert lots[0]["realized_pnl"] == pytest.approx(1000.0)

    ledger_response = client.get(
        f"/api/portfolios/portfolio-ops/transactions/{sell_response.json()['transaction_id']}/ledger-postings"
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv", "status": "open"},
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
        "/api/portfolios/portfolio-ops/accounts",
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
            "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts/workspace",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account["account_id"], "position_reference_id": "equity-us-abbv"},
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
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
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/accounts/workspace",
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
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": "txn-0003"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["selected_transaction_id"] == "txn-0003"
    assert payload["selected_transaction"]["transaction_id"] == "txn-0003"
    assert payload["delete_scope_row_versions"] == {
        "txn-0003": payload["selected_transaction"]["row_version"]
    }
    assert payload["selected_transaction"]["account"]["account_id"] == "broker-us-core"
    assert payload["ledger_summary"]["posting_count"] > 0
    assert all(posting["transaction_id"] == "txn-0003" for posting in payload["ledger_postings"])
    assert payload["related_position_lot_summary"]["position_lot_count"] > 0
    assert any(lot["opened_by_transaction_id"] == "txn-0003" for lot in payload["related_position_lots"])


def test_security_trade_cash_posting_uses_settlement_effective_date(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        f"/api/portfolios/portfolio-ops/transactions/{buy_response.json()['transaction_id']}/ledger-postings"
    )
    assert ledger_response.status_code == 200
    postings = ledger_response.json()["ledger_postings"]

    position_posting = next(item for item in postings if item["posting_role"] == "security_position")
    cash_posting = next(item for item in postings if item["posting_role"] == "security_settlement_cash")

    assert position_posting["effective_date"] == "2026-04-10"
    assert cash_posting["effective_date"] == "2026-04-12"


def test_confirmed_later_trade_enters_holdings_on_position_effective_date(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
        json={
            "account_name": "T Plus One Fund Account",
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
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "trade_time": "15:00",
            "position_effective_date": "2026-04-12",
            "settlement_date": "2026-04-10",
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
    transaction = buy_response.json()
    assert transaction["trade_date"] == "2026-04-10"
    assert transaction["position_effective_date"] == "2026-04-12"
    assert transaction["economic_date"] == "2026-04-12"

    trade_day_positions = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
        params={
            "account_id": account["account_id"],
            "as_of_date": "2026-04-10",
        },
    )
    assert trade_day_positions.status_code == 200
    assert not any(
        row["instrument_id"] == "equity-us-abbv"
        for row in trade_day_positions.json()["positions"]
    )
    trade_day_account = next(
        row
        for row in trade_day_positions.json()["accounts"]
        if row["account"]["account_id"] == account["account_id"]
    )
    trade_day_cash_account = next(
        row
        for row in trade_day_positions.json()["accounts"]
        if row["account"]["account_id"] == "cash-usd-main"
    )
    assert trade_day_account["pending_settlement"] == pytest.approx(0.0)
    assert trade_day_account["account_value_base"] == pytest.approx(0.0)
    assert trade_day_cash_account["pending_settlement"] == pytest.approx(
        1000.0
    )

    effective_day_positions = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
        params={
            "account_id": account["account_id"],
            "as_of_date": "2026-04-12",
        },
    )
    assert effective_day_positions.status_code == 200
    effective_position = next(
        row
        for row in effective_day_positions.json()["positions"]
        if row["instrument_id"] == "equity-us-abbv"
    )
    assert effective_position["quantity"] == pytest.approx(10.0)
    effective_day_account = next(
        row
        for row in effective_day_positions.json()["accounts"]
        if row["account"]["account_id"] == account["account_id"]
    )
    assert effective_day_account["pending_settlement"] == pytest.approx(0.0)
    effective_day_cash_account = next(
        row
        for row in effective_day_positions.json()["accounts"]
        if row["account"]["account_id"] == "cash-usd-main"
    )
    assert effective_day_cash_account["pending_settlement"] == pytest.approx(
        0.0
    )

    ledger_response = client.get(
        f"/api/portfolios/portfolio-ops/transactions/{transaction['transaction_id']}/ledger-postings"
    )
    assert ledger_response.status_code == 200
    postings = ledger_response.json()["ledger_postings"]
    position_posting = next(
        item for item in postings if item["posting_role"] == "security_position"
    )
    cash_posting = next(
        item for item in postings if item["posting_role"] == "security_settlement_cash"
    )
    bridge_posting = next(
        item
        for item in postings
        if item["posting_role"] == "position_recognition_bridge"
    )
    assert position_posting["effective_date"] == "2026-04-12"
    assert cash_posting["effective_date"] == "2026-04-10"
    assert bridge_posting["recognition_start_date"] == "2026-04-10"
    assert bridge_posting["effective_date"] == "2026-04-12"
    assert bridge_posting["pending_amount_delta"] == pytest.approx(1000.0)
    assert bridge_posting["account_id"] == "cash-usd-main"
    assert bridge_posting["attribution_account_id"] == account["account_id"]

    lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={
            "account_id": account["account_id"],
            "position_reference_id": "equity-us-abbv",
            "as_of_date": "2026-04-12",
        },
    )
    assert lots_response.status_code == 200
    assert lots_response.json()["position_lots"][0]["opened_at"] == "2026-04-12"


def test_confirmed_later_position_cannot_be_sold_before_it_is_effective(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
        json={
            "account_name": "Confirmed Later Disposal Review",
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
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "position_effective_date": "2026-04-12",
            "settlement_date": "2026-04-12",
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

    premature_sale = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "position_effective_date": "2026-04-12",
            "settlement_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 110.0,
            "gross_amount": 110.0,
            "currency": "USD",
        },
    )
    assert premature_sale.status_code == 400
    assert "as of trade_date" in premature_sale.json()["detail"]

    delayed_sale = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "position_effective_date": "2026-04-13",
            "settlement_date": "2026-04-13",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 5.0,
            "price": 120.0,
            "gross_amount": 600.0,
            "currency": "USD",
        },
    )
    assert delayed_sale.status_code == 200

    trade_day = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
        params={
            "account_id": account["account_id"],
            "as_of_date": "2026-04-12",
        },
    )
    effective_day = client.get(
        "/api/portfolios/portfolio-ops/accounts/workspace",
        params={
            "account_id": account["account_id"],
            "as_of_date": "2026-04-13",
        },
    )
    assert trade_day.status_code == 200
    assert effective_day.status_code == 200
    trade_day_position = next(
        row
        for row in trade_day.json()["positions"]
        if row["instrument_id"] == "equity-us-abbv"
    )
    effective_day_position = next(
        row
        for row in effective_day.json()["positions"]
        if row["instrument_id"] == "equity-us-abbv"
    )
    assert trade_day_position["quantity"] == pytest.approx(10.0)
    assert effective_day_position["quantity"] == pytest.approx(5.0)


def test_security_opening_balance_preserves_acquisition_date_in_position_lots(client):
    account = client.post(
        "/api/portfolios/portfolio-ops/accounts",
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
        "/api/portfolios/portfolio-ops/transactions",
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
        "/api/portfolios/portfolio-ops/position-lots",
        params={
            "account_id": account["account_id"],
            "position_reference_id": "equity-us-abbv",
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
