from __future__ import annotations

import pytest

from portfolio_app.services.option_obligations import open_option_obligations
from portfolio_app.services.portfolio_store import (
    list_option_delivery_links,
    list_transactions,
)


def _create_option_account(client, account_name: str) -> str:
    response = client.post(
        "/api/portfolios/portfolio-ops/accounts",
        json={
            "account_name": account_name,
            "account_category": "option",
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "opened_at": "2026-01-01",
            "status": "active",
        },
    )
    assert response.status_code == 200, response.json()
    return str(response.json()["account_id"])


def _open_option(
    client,
    *,
    account_id: str,
    contract_id: str,
    transaction_type: str,
    underlying_id: str = "equity-us-abbv",
    option_type: str = "call",
    expiry_date: str = "2026-12-18",
    quantity: int = 1,
) -> dict[str, object]:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": transaction_type,
            "trade_date": "2026-05-01",
            "settlement_date": "2026-05-01",
            "account_id": account_id,
            "settlement_cash_account_id": "cash-usd-main",
            "derivative_contract_id": contract_id,
            "derivative_contract": {
                "derivative_contract_id": contract_id,
                "contract_name": contract_id,
                "contract_type": "option",
                "terms": {
                    "underlying_instrument_id": underlying_id,
                    "option_type": option_type,
                    "expiry_date": expiry_date,
                    "strike": 200,
                    "contract_multiplier": 100,
                },
            },
            "quantity": quantity,
            "price": 5,
            "gross_amount": 500 * quantity,
            "currency": "USD",
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _physical_outcome_payload(
    contract_id: str,
    *,
    side: str,
    stock_account_id: str = "broker-us-core",
) -> dict[str, object]:
    return {
        "derivative_contract_id": contract_id,
        "side": side,
        "outcome": "physical",
        "quantity": 1,
        "event_date": "2026-06-01",
        "settlement_date": "2026-06-03",
        "stock_account_id": stock_account_id,
        "settlement_cash_account_id": "cash-usd-main",
        "fees": 2,
        "taxes": 1,
    }


def _buy_underlying_stock(client) -> None:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-04-01",
            "settlement_date": "2026-04-03",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100,
            "price": 100,
            "gross_amount": 10_000,
            "currency": "USD",
        },
    )
    assert response.status_code == 200, response.json()


def test_long_call_exercise_creates_linked_stock_delivery_atomically(client) -> None:
    option_account_id = _create_option_account(client, "Long Option Account")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-long-physical",
        transaction_type="buy",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload("option-long-physical", side="long"),
        headers={"Idempotency-Key": "long-call-physical-1"},
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert [item["transaction_type"] for item in body["transactions"]] == [
        "maturity_redemption",
        "buy",
    ]
    option_fact, stock_fact = body["transactions"]
    assert option_fact["lifecycle_event_type"] == "option_long_exercise"
    assert option_fact["gross_amount"] == 0
    assert option_fact["net_cash_effect"] == 0
    assert stock_fact["instrument_id"] == "equity-us-abbv"
    assert stock_fact["quantity"] == 100
    assert stock_fact["price"] == 200
    assert stock_fact["gross_amount"] == 20_000
    assert stock_fact["net_cash_effect"] == -20_003
    assert body["option_delivery_link"] == {
        "portfolio_id": "portfolio-ops",
        "option_transaction_id": option_fact["transaction_id"],
        "stock_transaction_id": stock_fact["transaction_id"],
        "underlying_instrument_id": "equity-us-abbv",
        "created_at": body["option_delivery_link"]["created_at"],
    }
    delivery_links = client.get(
        "/api/portfolios/portfolio-ops/options/delivery-links",
        params={"underlying_instrument_id": "equity-us-abbv"},
    )
    assert delivery_links.status_code == 200, delivery_links.json()
    assert delivery_links.json()["links"] == [body["option_delivery_link"]]

    replay = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload("option-long-physical", side="long"),
        headers={"Idempotency-Key": "long-call-physical-1"},
    )
    assert replay.status_code == 200, replay.json()
    assert [item["transaction_id"] for item in replay.json()["transactions"]] == [
        option_fact["transaction_id"],
        stock_fact["transaction_id"],
    ]

    workspace = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": option_fact["transaction_id"]},
    )
    assert workspace.status_code == 200, workspace.json()
    delete_scope = workspace.json()["delete_scope_row_versions"]
    assert set(delete_scope) == {
        option_fact["transaction_id"],
        stock_fact["transaction_id"],
    }
    assert {
        posting["transaction_id"]
        for posting in workspace.json()["ledger_postings"]
    } == {
        option_fact["transaction_id"],
        stock_fact["transaction_id"],
    }

    stock_filtered_workspace = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={
            "account_id": "broker-us-core",
            "transaction_id": stock_fact["transaction_id"],
        },
    )
    assert stock_filtered_workspace.status_code == 200, stock_filtered_workspace.json()
    filtered_body = stock_filtered_workspace.json()
    assert filtered_body["selected_transaction_id"] == option_fact["transaction_id"]
    assert {
        transaction["transaction_id"]
        for transaction in filtered_body["transactions"]
    }.issuperset(
        {
            option_fact["transaction_id"],
            stock_fact["transaction_id"],
        }
    )

    update = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{stock_fact['transaction_id']}",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-06-01",
            "settlement_date": "2026-06-03",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "quantity": 100,
            "price": 200,
            "gross_amount": 20_000,
            "fees": 2,
            "taxes": 1,
            "currency": "USD",
            "expected_row_version": stock_fact["row_version"],
        },
    )
    assert update.status_code == 400
    assert "deleted and recreated together" in update.json()["detail"]

    deleted = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{option_fact['transaction_id']}",
        json={"expected_row_versions": delete_scope},
    )
    assert deleted.status_code == 200, deleted.json()
    assert deleted.json()["deleted_count"] == 2
    assert list_option_delivery_links("portfolio-ops") == []


def test_writer_call_assignment_releases_obligation_and_sells_stock(client) -> None:
    option_account_id = _create_option_account(client, "Writer Option Account")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-writer-assignment",
        transaction_type="option_write",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload(
            "option-writer-assignment",
            side="written",
        ),
    )

    assert response.status_code == 200, response.json()
    option_fact, stock_fact = response.json()["transactions"]
    assert option_fact["lifecycle_event_type"] == "option_writer_assignment"
    assert stock_fact["transaction_type"] == "sell"
    assert stock_fact["quantity"] == 100
    assert open_option_obligations(list_transactions("portfolio-ops")) == []
    obligations = client.get(
        "/api/portfolios/portfolio-ops/options/obligations",
        params={
            "derivative_contract_id": "option-writer-assignment",
            "as_of_date": "2026-06-03",
        },
    )
    assert obligations.status_code == 200, obligations.json()
    obligation = obligations.json()["obligations"][0]
    assert obligation["status"] == "assigned"
    assert obligation["remaining_quantity"] == 0
    assert obligation["realized_pnl"] == 500


@pytest.mark.parametrize(
    ("side", "opening_type", "expected_stock_type"),
    [
        ("long", "buy", "sell"),
        ("written", "option_write", "buy"),
    ],
)
def test_put_physical_outcome_uses_the_contractual_stock_direction(
    client,
    side: str,
    opening_type: str,
    expected_stock_type: str,
) -> None:
    if side == "long":
        _buy_underlying_stock(client)
    option_account_id = _create_option_account(client, f"{side.title()} Put Account")
    contract_id = f"option-{side}-put-physical"
    _open_option(
        client,
        account_id=option_account_id,
        contract_id=contract_id,
        transaction_type=opening_type,
        option_type="put",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload(contract_id, side=side),
    )

    assert response.status_code == 200, response.json()
    option_fact, stock_fact = response.json()["transactions"]
    assert option_fact["lifecycle_event_type"] == (
        "option_long_exercise" if side == "long" else "option_writer_assignment"
    )
    assert stock_fact["transaction_type"] == expected_stock_type
    assert stock_fact["quantity"] == 100
    assert stock_fact["price"] == 200


def test_uncovered_writer_assignment_rolls_back_both_delivery_facts(client) -> None:
    option_account_id = _create_option_account(client, "Uncovered Writer Account")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-uncovered-assignment",
        transaction_type="option_write",
        underlying_id="fund-us-watch",
    )
    before_ids = {
        str(item["transaction_id"])
        for item in list_transactions("portfolio-ops")
    }

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload(
            "option-uncovered-assignment",
            side="written",
        ),
    )

    assert response.status_code == 409
    assert "exceeds account position" in response.json()["detail"]
    assert {
        str(item["transaction_id"])
        for item in list_transactions("portfolio-ops")
    } == before_ids
    assert list_option_delivery_links("portfolio-ops") == []


def test_physical_outcome_rejects_cross_currency_underlying(client) -> None:
    option_account_id = _create_option_account(client, "Quanto Option Account")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-cross-currency",
        transaction_type="buy",
        underlying_id="fund-hk-2800",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json=_physical_outcome_payload("option-cross-currency", side="long"),
    )

    assert response.status_code == 400
    assert "differs from the underlying quote currency" in response.json()["detail"]


def test_expired_open_long_and_written_options_are_returned_as_actions(client) -> None:
    option_account_id = _create_option_account(client, "Expired Option Account")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-expired-long",
        transaction_type="buy",
        expiry_date="2026-08-31",
    )
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-expired-written",
        transaction_type="option_write",
        expiry_date="2026-08-31",
        quantity=2,
    )

    response = client.get(
        "/api/portfolios/portfolio-ops/options/unresolved-actions"
    )

    assert response.status_code == 200, response.json()
    actions = {
        (item["derivative_contract_id"], item["side"]): item
        for item in response.json()["actions"]
    }
    assert actions[("option-expired-long", "long")]["open_contract_quantity"] == 1
    assert actions[("option-expired-written", "written")]["open_contract_quantity"] == 2
    assert all(item["days_past_expiry"] >= 1 for item in actions.values())


def test_generic_transaction_endpoint_rejects_unpaired_physical_outcome(client) -> None:
    option_account_id = _create_option_account(client, "Generic Outcome Guard")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-generic-guard",
        transaction_type="buy",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "maturity_redemption",
            "lifecycle_event_type": "option_long_exercise",
            "trade_date": "2026-06-01",
            "settlement_date": "2026-06-01",
            "account_id": option_account_id,
            "derivative_contract_id": "option-generic-guard",
            "quantity": 1,
            "gross_amount": 0,
            "currency": "USD",
        },
    )

    assert response.status_code == 400
    assert "option outcome command" in response.json()["detail"]


@pytest.mark.parametrize(
    ("side", "opening_type", "expected_lifecycle", "expected_net_cash"),
    [
        ("long", "buy", "option_long_cash_settlement", 997),
        ("written", "option_write", "option_writer_cash_settlement", -1003),
    ],
)
def test_cash_outcome_closes_the_selected_side_without_a_delivery_link(
    client,
    side: str,
    opening_type: str,
    expected_lifecycle: str,
    expected_net_cash: int,
) -> None:
    option_account_id = _create_option_account(client, f"{side.title()} Cash Outcome")
    contract_id = f"option-{side}-cash-outcome"
    _open_option(
        client,
        account_id=option_account_id,
        contract_id=contract_id,
        transaction_type=opening_type,
        expiry_date="2026-06-02",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json={
            "derivative_contract_id": contract_id,
            "side": side,
            "outcome": "cash_settled",
            "quantity": 1,
            "event_date": "2026-06-01",
            "settlement_date": "2026-06-03",
            "settlement_cash_account_id": "cash-usd-main",
            "cash_settlement_amount": 1000,
            "fees": 2,
            "taxes": 1,
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert len(body["transactions"]) == 1
    transaction = body["transactions"][0]
    assert transaction["lifecycle_event_type"] == expected_lifecycle
    assert transaction["gross_amount"] == 1000
    assert transaction["net_cash_effect"] == expected_net_cash
    assert body["option_delivery_link"] is None
    assert list_option_delivery_links("portfolio-ops") == []


@pytest.mark.parametrize(
    ("side", "opening_type", "expected_lifecycle"),
    [
        ("long", "buy", "option_long_expiry"),
        ("written", "option_write", "option_writer_expiry"),
    ],
)
def test_expiry_outcome_closes_the_selected_side_at_zero_cash(
    client,
    side: str,
    opening_type: str,
    expected_lifecycle: str,
) -> None:
    option_account_id = _create_option_account(client, f"{side.title()} Expiry Outcome")
    contract_id = f"option-{side}-expiry-outcome"
    _open_option(
        client,
        account_id=option_account_id,
        contract_id=contract_id,
        transaction_type=opening_type,
        expiry_date="2026-06-02",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json={
            "derivative_contract_id": contract_id,
            "side": side,
            "outcome": "expired",
            "quantity": 1,
            "event_date": "2026-06-02",
            "settlement_date": "2026-06-02",
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert len(body["transactions"]) == 1
    transaction = body["transactions"][0]
    assert transaction["lifecycle_event_type"] == expected_lifecycle
    assert transaction["gross_amount"] == 0
    assert transaction["net_cash_effect"] == 0
    assert body["option_delivery_link"] is None
    assert list_option_delivery_links("portfolio-ops") == []


def test_expiry_outcome_uses_the_contract_expiry_date(client) -> None:
    option_account_id = _create_option_account(client, "Expiry Date Guard")
    _open_option(
        client,
        account_id=option_account_id,
        contract_id="option-expiry-date-guard",
        transaction_type="buy",
        expiry_date="2026-06-02",
    )

    response = client.post(
        "/api/portfolios/portfolio-ops/options/outcomes",
        json={
            "derivative_contract_id": "option-expiry-date-guard",
            "side": "long",
            "outcome": "expired",
            "quantity": 1,
            "event_date": "2026-06-03",
            "settlement_date": "2026-06-03",
        },
    )

    assert response.status_code == 400
    assert "must use the contract expiry date" in response.json()["detail"]
