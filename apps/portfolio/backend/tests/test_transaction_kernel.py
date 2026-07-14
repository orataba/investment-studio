from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from portfolio_ops_instrument_core import instrument_store as shared_store

from tests.store_fixture import TEST_PORTFOLIO_STORE

from portfolio_app.db.session import get_session_factory
from portfolio_app.api.routes import fx_rates as fx_rate_routes
from portfolio_app.services import portfolio_store


TEST_ACTOR = {
    "actor_type": "user",
    "actor_id": "pm:transaction-kernel-test",
    "display_name": "Transaction Kernel Test Manager",
    "actor_source": "client_asserted",
}
TRANSACTION_DECIMAL_FIELDS = {
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "quoted_fx_rate",
    "fees",
    "taxes",
}


def _transaction_request(values: dict[str, object]) -> dict[str, object]:
    """Build an explicit request using the revision-era actor/Decimal contract."""

    payload = deepcopy(values)
    payload.setdefault("actor", deepcopy(TEST_ACTOR))
    for field_name in TRANSACTION_DECIMAL_FIELDS:
        value = payload.get(field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            payload[field_name] = str(value)
    return payload


def _assert_ledger_replay_conflict(response, *, reason_code: str) -> None:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "transaction_command_validation_failed"
    assert detail["validation_code"] == "ledger_replay_failed"
    assert detail["reason_code"] == reason_code


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


def test_account_cost_method_can_change_after_decimal_transaction_history(client):
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
            json=_transaction_request({
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
            }),
        )
        assert transaction_response.status_code == 200

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


def test_transaction_fact_can_be_updated_and_deleted(client):
    created_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-16",
            "settlement_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "currency": "USD",
            "note": "Initial note",
        }),
    )
    assert created_response.status_code == 200
    transaction_id = created_response.json()["transaction_id"]

    missing_value_date_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-17",
            "account_id": "cash-usd-main",
            "gross_amount": 1250.0,
            "currency": "USD",
            "note": "Must not be persisted",
            **{
                "expected_revision_id": created_response.json()["revision_id"],
                "expected_revision_number": created_response.json()["revision_number"],
                "change_reason": "Attempt update without a value date",
            },
        }),
    )
    assert missing_value_date_response.status_code == 422
    assert "settlement_date" in missing_value_date_response.text
    unchanged_record = portfolio_store.get_transaction("portfolio-ops", transaction_id)
    assert unchanged_record is not None
    assert unchanged_record["settlement_date"] == "2026-04-16"
    assert unchanged_record["note"] == "Initial note"

    updated_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-17",
            "settlement_date": "2026-04-18",
            "account_id": "cash-usd-main",
            "gross_amount": 1250.0,
            "currency": "USD",
            "note": "Corrected note",
            "expected_revision_id": created_response.json()["revision_id"],
            "expected_revision_number": created_response.json()["revision_number"],
            "change_reason": "Correct the cash fact",
        }),
    )
    assert updated_response.status_code == 200
    updated_payload = updated_response.json()
    assert updated_payload["transaction_id"] == transaction_id
    assert updated_payload["trade_date"] == "2026-04-17"
    assert updated_payload["settlement_date"] == "2026-04-18"
    assert updated_payload["gross_amount"] == "1250"
    assert updated_payload["note"] == "Corrected note"

    deleted_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{transaction_id}",
        json=_transaction_request({
            "expected_revision_id": updated_payload["revision_id"],
            "expected_revision_number": updated_payload["revision_number"],
            "actor": TEST_ACTOR,
            "change_reason": "Delete the corrected cash fact",
        }),
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


@pytest.mark.parametrize("transaction_type", ["deposit", "withdrawal"])
@pytest.mark.parametrize("settlement_value", ["omitted", None, ""])
def test_external_cash_flow_requires_explicit_cash_value_date(
    client,
    transaction_type: str,
    settlement_value: object,
) -> None:
    request_payload: dict[str, object] = {
        "transaction_type": transaction_type,
        "trade_date": "2026-04-16",
        "account_id": "cash-usd-main",
        "gross_amount": 100.0,
        "currency": "USD",
    }
    if settlement_value != "omitted":
        request_payload["settlement_date"] = settlement_value

    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request(request_payload),
    )

    assert response.status_code == 422
    assert "settlement_date" in response.text


def test_deleting_transfer_leg_removes_entire_pair(client):
    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-16",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": 250.0,
            "note": "Sweep",
        }),
    )
    assert transfer_response.status_code == 200
    transfer_payload = transfer_response.json()
    delete_target = transfer_payload["transactions"][0]["transaction_id"]

    delete_record = transfer_payload["transactions"][0]
    deleted_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{delete_target}",
        json=_transaction_request({
            "expected_revision_id": delete_record["revision_id"],
            "expected_revision_number": delete_record["revision_number"],
            "actor": TEST_ACTOR,
            "change_reason": "Reverse the complete transfer pair",
        }),
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
                    "gross_amount": "100.00000000",
                    "counter_amount": None,
                    "quoted_fx_rate": None,
                    "consideration_basis": None,
                    "fees": "0.00000000",
                    "taxes": "0.00000000",
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
                    "fees": "0.00000000",
                    "taxes": "0.00000000",
                    "currency": "USD",
                },
            ],
            actor=TEST_ACTOR,
            change_reason="Verify transaction batch rollback",
        )

    after = list_transactions("portfolio-ops")
    assert {item["transaction_id"] for item in after} == before_ids
    assert all(item["note"] != "batch rollback sentinel" for item in after)


def test_batch_transaction_write_requires_settlement_date_per_record() -> None:
    from portfolio_app.services.portfolio_store import create_transactions, list_transactions

    before_ids = {item["transaction_id"] for item in list_transactions("portfolio-ops")}

    with pytest.raises(KeyError, match="settlement_date"):
        create_transactions(
            portfolio_id="portfolio-ops",
            records=[
                {
                    "transaction_type": "deposit",
                    "trade_date": date(2026, 4, 20),
                }
            ],
            actor=TEST_ACTOR,
            change_reason="Verify settlement date validation",
        )

    assert {item["transaction_id"] for item in list_transactions("portfolio-ops")} == before_ids


def test_rejects_cross_currency_security_facts(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 400
    assert "Securities account currency must match instrument currency" in buy_response.json()["detail"]

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-15",
            "from_account_id": "broker-us-core",
            "to_account_id": "broker-hk-core",
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 0.0,
        }),
    )
    assert transfer_response.status_code == 400
    assert "same currency as the instrument" in transfer_response.json()["detail"]


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
    assert [point["date"] for point in payload["points"]] == [
        "2026-03-15",
        "2026-04-02",
        "2026-04-08",
        "2026-04-15",
    ]
    assert payload["summary"]["point_count"] == 4
    assert payload["summary"]["change_value"] == pytest.approx(-3.73)
    assert payload["summary"]["high"] == pytest.approx(210.20)
    assert payload["summary"]["low"] == pytest.approx(206.47)


def test_position_transfer_declares_exact_moving_average_cost(client):
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
            "cost_basis_method": "moving_average",
            "allowed_instrument_types": ["equity"],
            "status": "active",
        },
    ).json()

    for trade_date, price, gross_amount in [("2026-04-01", 10.0, 1000.0), ("2026-04-02", 20.0, 2000.0)]:
        buy_response = client.post(
            "/api/portfolios/portfolio-ops/transactions",
            json=_transaction_request({
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
            }),
        )
        assert buy_response.status_code == 200

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-10",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 150.0,
            "gross_amount": 2250.0,
        }),
    )
    assert transfer_response.status_code == 200
    transfer_batch = transfer_response.json()
    assert {txn["gross_amount"] for txn in transfer_batch["transactions"]} == {
        "2250"
    }



def test_position_transfer_accepts_zero_declared_cost_and_subsequent_sale(client):
    portfolio_response = client.post(
        "/api/portfolios",
        json={
            "name": "Zero Cost Transfer Book",
            "base_currency": "USD",
            "operating_profile": "standard_taxonomy",
        },
    )
    assert portfolio_response.status_code == 200
    portfolio_id = portfolio_response.json()["portfolio_id"]
    cash_account = client.post(
        f"/api/portfolios/{portfolio_id}/accounts",
        json={
            "account_name": "Zero Cost Cash",
            "account_type": "deposit_account",
            "currency": "USD",
            "institution": "Test Bank",
            "opened_at": "2026-04-20",
            "status": "active",
        },
    ).json()
    source_account = client.post(
        f"/api/portfolios/{portfolio_id}/accounts",
        json={
            "account_name": "Zero Cost Source",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": cash_account["account_id"],
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()
    destination_account = client.post(
        f"/api/portfolios/{portfolio_id}/accounts",
        json={
            "account_name": "Zero Cost Destination",
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": cash_account["account_id"],
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["equity"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    ).json()

    opening_response = client.post(
        f"/api/portfolios/{portfolio_id}/transactions",
        json=_transaction_request({
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-20",
            "account_id": source_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 0.0,
            "currency": "USD",
        }),
    )
    assert opening_response.status_code == 200

    transfer_response = client.post(
        f"/api/portfolios/{portfolio_id}/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-21",
            "transfer_object_type": "position",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 4.0,
            "gross_amount": 0.0,
        }),
    )
    assert transfer_response.status_code == 200
    assert {
        transaction["gross_amount"]
        for transaction in transfer_response.json()["transactions"]
    } == {"0"}

    sell_response = client.post(
        f"/api/portfolios/{portfolio_id}/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-22",
            "account_id": destination_account["account_id"],
            "settlement_cash_account_id": cash_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 2.0,
            "price": 100.0,
            "gross_amount": 200.0,
            "currency": "USD",
        }),
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
    source_fx_transaction = portfolio_store.get_transaction("portfolio-ops", "txn-0018")
    assert source_fx_transaction is not None
    for field_name in (
        "numeric_scale_state",
        "gross_amount_input_scale",
        "counter_amount_input_scale",
        "quoted_fx_rate_input_scale",
        "fees_input_scale",
        "taxes_input_scale",
    ):
        assert copied_fx_transaction[field_name] == source_fx_transaction[field_name]


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
        json=_transaction_request({
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
        }),
    )
    _assert_ledger_replay_conflict(sell_response, reason_code="position_not_found")

    transfer_response = client.post(
        f"/api/portfolios/{copied_portfolio_id}/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-01-20",
            "from_account_id": "broker-us-core-portfolio-ops-copy",
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 0.0,
        }),
    )
    _assert_ledger_replay_conflict(transfer_response, reason_code="position_not_found")


def test_rejects_inconsistent_buy_sell_amount_contracts(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1.0,
            "consideration_basis": "exact_quantity_price",
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 422
    assert "gross_amount must exactly equal the amount implied" in buy_response.text

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 206.47,
            "gross_amount": 999999.0,
            "consideration_basis": "exact_quantity_price",
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert sell_response.status_code == 422
    assert "gross_amount must exactly equal the amount implied" in sell_response.text


def test_rejects_display_rounded_price_when_exact_amount_contract_does_not_close(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "quantity": 700.0,
            "price": 11.5436,
            "gross_amount": 8080.50,
            "consideration_basis": "exact_quantity_price",
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 422
    assert "gross_amount must exactly equal the amount implied" in buy_response.text


def test_preserves_transaction_precision_conventions(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "fund-us-agg",
            "quantity": 700.004,
            "price": 11.54364,
            "gross_amount": 8080.59417456,
            "fees": 0.004,
            "taxes": 0.004,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 200
    payload = buy_response.json()
    assert payload["quantity"] == "700.004"
    assert payload["price"] == "11.54364"
    assert payload["gross_amount"] == "8080.59417456"
    assert payload["fees"] == "0.004"
    assert payload["taxes"] == "0.004"


def test_exact_quantity_price_gross_bridge_fails_closed_at_api_boundary(client):
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
        json=_transaction_request({
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": opening_account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 999.0,
            "gross_amount": 1.0,
            "consideration_basis": "exact_quantity_price",
            "currency": "USD",
        }),
    )
    assert opening_balance_response.status_code == 422

    reinvestment_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 999.0,
            "gross_amount": 1.0,
            "consideration_basis": "exact_quantity_price",
            "currency": "USD",
        }),
    )
    assert reinvestment_response.status_code == 422
    for response in (opening_balance_response, reinvestment_response):
        detail = response.json()["detail"]
        assert detail["code"] == "transaction_revision_invalid"
        assert (
            "gross_amount must exactly equal the amount implied by "
            "quantity, price"
        ) in detail["message"]


def test_rejects_opening_balance_settlement_account_and_deposit_account_instrument(client):
    opening_balance_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": "broker-us-core",
            "gross_amount": 1000.0,
            "currency": "USD",
        }),
    )
    assert opening_balance_response.status_code == 422
    assert "opening balance must not carry settlement_cash_account_id" in opening_balance_response.text.lower()

    deposit_account_security_opening_balance = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "opening_balance",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        }),
    )
    assert deposit_account_security_opening_balance.status_code == 400
    assert "cash opening balance must not reference instrument" in deposit_account_security_opening_balance.json()["detail"].lower()


def test_rejects_deposit_account_fee_with_instrument_reference(client):
    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 25.0,
            "currency": "USD",
        }),
    )
    assert fee_response.status_code == 400
    assert "deposit-account fee and tax must not reference instrument" in fee_response.json()["detail"].lower()

    fee_with_settlement_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": "cash-usd-reserve",
            "gross_amount": 25.0,
            "currency": "USD",
        }),
    )
    assert fee_with_settlement_response.status_code == 400
    assert (
        "deposit-account fee and tax must not carry settlement cash account"
        in fee_with_settlement_response.json()["detail"].lower()
    )


def test_rejects_irrelevant_cash_and_reinvestment_fields(client):
    deposit_with_quantity = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "quantity": 1.0,
            "currency": "USD",
        }),
    )
    assert deposit_with_quantity.status_code == 422
    assert "cash-flow transactions must not carry quantity or price" in deposit_with_quantity.text.lower()

    deposit_with_settlement = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "settlement_cash_account_id": "cash-usd-reserve",
            "currency": "USD",
        }),
    )
    assert deposit_with_settlement.status_code == 422
    assert "cash-flow transactions must not carry settlement_cash_account_id" in deposit_with_settlement.text.lower()

    interest_with_settlement = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "interest",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "settlement_cash_account_id": "cash-usd-reserve",
            "currency": "USD",
        }),
    )
    assert interest_with_settlement.status_code == 422
    assert "interest must not carry settlement_cash_account_id" in interest_with_settlement.text.lower()

    drip_with_settlement = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "gross_amount": 100.0,
            "settlement_cash_account_id": "cash-usd-main",
            "currency": "USD",
        }),
    )
    assert drip_with_settlement.status_code == 422
    assert "dividend reinvestment must not carry settlement_cash_account_id" in drip_with_settlement.text.lower()

    deposit_with_counterparty = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 100.0,
            "counterparty_account_id": "cash-usd-reserve",
            "currency": "USD",
        }),
    )
    assert deposit_with_counterparty.status_code == 422
    assert (
        "counterparty_account_id is only allowed for fx conversion and internal transfer"
        in deposit_with_counterparty.text.lower()
    )

    dividend_with_quantity = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "gross_amount": 10.0,
            "currency": "USD",
        }),
    )
    assert dividend_with_quantity.status_code == 422
    assert "dividend and coupon must not carry quantity or price" in dividend_with_quantity.text.lower()


def test_dividend_and_return_of_capital_preserve_exact_gross_and_tax_facts(client):
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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 15.0,
            "currency": "USD",
        }),
    )
    assert dividend_response.status_code == 200

    roc_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-16",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 40.0,
            "fees": 0.0,
            "taxes": 5.0,
            "currency": "USD",
        }),
    )
    assert roc_response.status_code == 200
    assert dividend_response.json()["gross_amount"] == "100"
    assert dividend_response.json()["taxes"] == "15"
    assert roc_response.json()["gross_amount"] == "40"
    assert roc_response.json()["taxes"] == "5"


def test_flat_position_rejects_follow_on_sell_and_transfer(client):
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
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 110.0,
            "gross_amount": 1100.0,
            "currency": "USD",
        }),
    )
    assert sell_response.status_code == 200

    oversell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": source_account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 120.0,
            "gross_amount": 120.0,
            "currency": "USD",
        }),
    )
    _assert_ledger_replay_conflict(oversell_response, reason_code="position_not_found")

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-12",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "gross_amount": 0.0,
        }),
    )
    _assert_ledger_replay_conflict(transfer_response, reason_code="position_not_found")


def test_accepts_instrument_income_and_expense_after_position_is_closed(client):
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
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 100.0,
            "gross_amount": 1000.0,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-11",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 10.0,
            "price": 110.0,
            "gross_amount": 1100.0,
            "currency": "USD",
        }),
    )
    assert sell_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 50.0,
            "currency": "USD",
        }),
    )
    assert dividend_response.status_code == 200
    assert dividend_response.json()["gross_amount"] == "50"

    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "fee",
            "trade_date": "2026-04-13",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 5.0,
            "currency": "USD",
        }),
    )
    assert fee_response.status_code == 200
    assert fee_response.json()["gross_amount"] == "5"


def test_rejects_nested_fee_and_tax_fields_on_fee_tax_transactions(client):
    fee_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "fee",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "fees": 1.0,
            "taxes": 2.0,
            "currency": "USD",
        }),
    )
    assert fee_response.status_code == 422
    assert "must not carry nested fees or taxes" in fee_response.text.lower()

    tax_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "tax",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 10.0,
            "fees": 1.0,
            "currency": "USD",
        }),
    )
    assert tax_response.status_code == 422
    assert "must not carry nested fees or taxes" in tax_response.text.lower()


def test_dividend_reinvestment_can_establish_a_position(client):
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
        json=_transaction_request({
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        }),
    )
    assert response.status_code == 200
    assert response.json()["quantity"] == "1"
    assert response.json()["gross_amount"] == "100"


def test_rejects_entitlement_date_on_dividend_reinvestment(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        }),
    )
    assert response.status_code == 422
    assert "does not yet support entitlement_date" in response.text


def test_accepts_late_paid_dividend_fact_when_entitlement_precedes_sale(client):
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
            json=_transaction_request({
                "transaction_type": "buy",
                "trade_date": trade_date,
                "account_id": account["account_id"],
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": 50.0,
                "price": 100.0,
                "gross_amount": 5000.0,
                "currency": "USD",
            }),
        )
        assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "sell",
            "trade_date": "2026-04-12",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 50.0,
            "price": 110.0,
            "gross_amount": 5500.0,
            "currency": "USD",
        }),
    )
    assert sell_response.status_code == 200

    dividend_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 100.0,
            "currency": "USD",
        }),
    )
    assert dividend_response.status_code == 200
    assert dividend_response.json()["entitlement_date"] == "2026-04-10"


def test_rejects_entitlement_date_on_return_of_capital(client):
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-15",
            "entitlement_date": "2026-04-10",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 10.0,
            "currency": "USD",
        }),
    )
    assert response.status_code == 422
    assert "does not yet support entitlement_date" in response.text


def test_dividend_reinvestment_persists_exact_authoritative_fact(client):
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
        json=_transaction_request({
            "transaction_type": "buy",
            "trade_date": "2026-04-10",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "price": 100.0,
            "gross_amount": 10000.0,
            "currency": "USD",
        }),
    )
    assert buy_response.status_code == 200

    drip_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "dividend_reinvestment",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "currency": "USD",
        }),
    )
    assert drip_response.status_code == 200
    assert drip_response.json()["quantity"] == "1"
    assert drip_response.json()["price"] == "100"
    assert drip_response.json()["gross_amount"] == "100"


def test_rejects_settlement_before_trade_date(client):
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 422
    assert "settlement_date must not be earlier than trade_date" in buy_response.text

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-01",
            "from_account_id": "cash-usd-main",
            "to_account_id": "cash-usd-reserve",
            "transfer_object_type": "cash",
            "gross_amount": 100.0,
        }),
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
        json=_transaction_request({
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
        }),
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
        json=_transaction_request({
            "transaction_type": "fx_conversion",
            "trade_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "counterparty_account_id": future_account["account_id"],
            "gross_amount": 100.0,
            "counter_amount": 780.0,
            "quoted_fx_rate": 7.8,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert early_fx_response.status_code == 400
    assert "is not open on 2026-04-15" in early_fx_response.json()["detail"]


def test_defaults_trade_time_and_trade_at_when_not_provided(client):
    deposit_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
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
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "trade_time": "15:00",
            "account_id": "cash-usd-main",
            "gross_amount": 1000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert later_response.status_code == 200

    earlier_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-04-15",
            "trade_time": "09:00",
            "account_id": "cash-usd-main",
            "gross_amount": 500.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
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


def test_transaction_execution_quote_uses_raw_valuation_basis_not_adjusted_chart(
    client,
) -> None:
    policy = {
        "trading": ["close", "last"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    }
    assert shared_store.upsert_quote_selection_policy(
        get_session_factory(),
        instrument_id="equity-us-abbv",
        quote_selection_policy=policy,
    ) is not None
    changed_count = shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id="equity-us-abbv",
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": date(2026, 3, 27),
                "value": "206.47",
                "currency": "USD",
                "source_ref": "test:raw-close",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": date(2026, 3, 27),
                "value": "204.25",
                "currency": "USD",
                "source_ref": "test:adjusted-close",
                "status": "complete",
            },
        ],
    )
    assert changed_count == 2

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "equity-us-abbv", "as_of_date": "2026-03-27"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["quote_basis"] == "close"
    assert payload["value"] == "206.47"
    assert payload["suggested_transaction_price"] == "206.47"
    assert payload["quote_date"] == "2026-03-27"
    assert payload["source_ref"] == "test:raw-close"
    assert payload["resolution_status"] == "resolved"
    assert payload["stale"] is False


def test_shared_fx_reference_preserves_more_than_six_decimal_places(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fx_rate_routes,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "CNY"],
            "maintained_pairs": ["USD/CNY"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "CNY",
                    "rate": Decimal("7.123456789012345678"),
                    "as_of_date": date(2026, 7, 14),
                    "source_kind": "direct",
                    "instrument_id": "fx-usd-cny",
                    "source_instrument_ids": ["fx-usd-cny"],
                    "source_ref": "test:high-precision-fx",
                    "status": "complete",
                }
            ],
        },
    )

    response = client.get("/api/portfolios/portfolio-ops/fx-rates")

    assert response.status_code == 200, response.text
    assert response.json()["rates"][0]["rate"] == "7.123456789012345678"


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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
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
        }),
    )
    _assert_ledger_replay_conflict(sell_response, reason_code="position_not_found")


def test_same_day_buy_then_sell_facts_preserve_trade_order(client):
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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
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
        }),
    )
    assert sell_response.status_code == 200
    assert buy_response.json()["trade_at"] < sell_response.json()["trade_at"]
    assert buy_response.json()["settlement_date"] == "2026-04-17"
    assert sell_response.json()["settlement_date"] == "2026-04-16"


def test_buy_fact_keeps_price_separate_from_capitalized_fees_and_taxes(client):
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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200
    fact = buy_response.json()
    assert fact["price"] == "100"
    assert fact["gross_amount"] == "1000"
    assert fact["fees"] == "5"
    assert fact["taxes"] == "3"


def test_moving_average_account_accepts_exact_multi_trade_history(client):
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
            json=_transaction_request({
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
            }),
        )
        assert buy_response.status_code == 200

    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
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
        }),
    )
    assert sell_response.status_code == 200
    assert sell_response.json()["quantity"] == "50"
    assert sell_response.json()["gross_amount"] == "6500"


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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200

    transfer_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=_transaction_request({
            "trade_date": "2026-04-15",
            "from_account_id": source_account["account_id"],
            "to_account_id": destination_account["account_id"],
            "transfer_object_type": "position",
            "instrument_id": "equity-us-abbv",
            "quantity": 50.0,
            "gross_amount": 1.0,
        }),
    )
    _assert_ledger_replay_conflict(
        transfer_response,
        reason_code="gross_amount_mismatch",
    )


def test_accepts_return_of_capital_above_remaining_cost_basis(client):
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
        json=_transaction_request({
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
        }),
    )
    assert buy_response.status_code == 200

    roc_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "return_of_capital",
            "trade_date": "2026-04-15",
            "account_id": account["account_id"],
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "gross_amount": 1500.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }),
    )
    assert roc_response.status_code == 200
    assert roc_response.json()["gross_amount"] == "1500"


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
    assert payload["positions"]
    assert "ledger_postings" not in payload
    assert all(
        item["account"]["account_id"] == "broker-us-core"
        or item["counterparty_account_id"] == "broker-us-core"
        for item in payload["linked_transactions"]
    )

    selected_account_row = next(
        row for row in payload["accounts"] if row["account"]["account_id"] == "broker-us-core"
    )
    assert selected_account_row["linked_transaction_count"] > 0
    assert selected_account_row["linked_transaction_count"] <= payload["linked_transactions_summary"]["total_transactions"]


def test_transactions_workspace_returns_selected_authoritative_fact(client):
    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": "txn-0003"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["selected_transaction_id"] == "txn-0003"
    assert payload["selected_transaction"]["transaction_id"] == "txn-0003"
    assert payload["selected_transaction"]["account"]["account_id"] == "broker-us-core"
    assert payload["summary"]["total_transactions"] > 0
    assert "ledger_summary" not in payload
    assert "ledger_postings" not in payload
    assert "related_position_lot_summary" not in payload
    assert "related_position_lots" not in payload


def test_security_opening_balance_persists_exact_acquisition_date_fact(client):
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
            "opened_at": "2026-01-01",
            "status": "active",
        },
    ).json()

    opening_balance_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=_transaction_request({
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-02",
            "settlement_date": "2026-01-02",
            "account_id": account["account_id"],
            "instrument_id": "equity-us-abbv",
            "quantity": 100.0,
            "gross_amount": 10000.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "acquisition_date": "2025-03-01",
        }),
    )
    assert opening_balance_response.status_code == 200
    assert opening_balance_response.json()["acquisition_date"] == "2025-03-01"
    assert opening_balance_response.json()["trade_date"] == "2026-01-02"
    assert opening_balance_response.json()["gross_amount"] == "10000"
