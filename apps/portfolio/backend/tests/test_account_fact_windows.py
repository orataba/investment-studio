"""Account edits and ledger writes must preserve each role's recorded date bounds."""
from datetime import date

import pytest

from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.services import portfolio_store
from tests.test_transaction_repair_scenarios import account, post, BASE, PORTFOLIO


@pytest.mark.parametrize("change", [
    {"closed_at": "2026-02-01", "status": "closed"},
    {"closed_at": None, "status": "closed"},
    {"opened_at": "2026-02-11"},
])
def test_account_cannot_exclude_existing_trade_and_note_remains_editable(client, change):
    before = portfolio_store.get_transaction(PORTFOLIO, "txn-0003")
    account_before = portfolio_store.get_account(PORTFOLIO, "broker-us-core")
    response = client.patch(BASE + "/accounts/broker-us-core", json=change)
    assert response.status_code == 400
    assert "recorded history" in response.text
    assert portfolio_store.get_account(PORTFOLIO, "broker-us-core") == account_before
    values = {key: before[key] for key in (
        "transaction_type", "trade_date", "settlement_date", "account_id", "settlement_cash_account_id",
        "instrument_id", "quantity", "price", "gross_amount", "fees", "taxes", "currency",
    )}
    response = client.put(BASE + "/transactions/txn-0003", json={
        **values, "expected_row_version": before["row_version"], "note": "Reviewed original confirmation",
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("role", ["security_cash", "fx_target", "transfer_target", "delayed_position"])
def test_account_bounds_include_settlement_counterparty_and_position_confirmation(client, role):
    cash = account(client, "cash", currency="HKD" if role == "fx_target" else "USD")
    if role in {"security_cash", "delayed_position"}:
        holder = account(client, "security", cash)
        post(client, transaction_type="buy", trade_date="2026-02-10", settlement_date="2026-02-12",
             position_effective_date="2026-02-16" if role == "delayed_position" else None,
             account_id=holder, settlement_cash_account_id=cash, instrument_id="equity-us-abbv",
             quantity=1, price=100, gross_amount=100)
        target = holder if role == "delayed_position" else cash
        close = "2026-02-15" if role == "delayed_position" else "2026-02-11"
    elif role == "fx_target":
        post(client, transaction_type="fx_conversion", trade_date="2026-02-10", settlement_date="2026-02-12",
             account_id="cash-usd-main", counterparty_account_id=cash, gross_amount=100, counter_amount=780)
        target, close = cash, "2026-02-11"
    else:
        response = client.post(BASE + "/transactions/internal-transfer", json={
            "trade_date": "2026-02-10", "settlement_date": "2026-02-12",
            "from_account_id": "cash-usd-main", "to_account_id": cash,
            "transfer_object_type": "cash", "gross_amount": 100,
        })
        assert response.status_code == 200, response.text
        target, close = cash, "2026-02-11"
    response = client.patch(BASE + "/accounts/" + target, json={"closed_at": close})
    assert response.status_code == 400, response.text
    assert "recorded history" in response.text
    if role in {"security_cash", "fx_target"}:
        # These roles are required on settlement, not on the source order date.
        response = client.patch(BASE + "/accounts/" + target, json={"opened_at": "2026-02-11"})
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("closed_at", [date(2026, 2, 1), None])
def test_account_date_edit_between_api_validation_and_write_rejects_whole_transaction(client, monkeypatch, closed_at):
    cash = account(client, "cash")
    holder = account(client, "security", cash)
    before_ids = {row["transaction_id"] for row in portfolio_store.list_transactions(PORTFOLIO)}
    original_create = transaction_routes.create_transaction

    def close_account_then_create(*args, **kwargs):
        current = portfolio_store.get_account(PORTFOLIO, holder)
        fields = {key: current[key] for key in (
            "account_name", "account_category", "institution", "default_settlement_cash_account_id", "cost_basis_method",
        )}
        portfolio_store.update_account(PORTFOLIO, holder, **fields, opened_at=date(2026, 1, 2),
                                       closed_at=closed_at, status="closed")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(transaction_routes, "create_transaction", close_account_then_create)
    response = client.post(BASE + "/transactions", json={
        "transaction_type": "buy", "trade_date": "2026-02-10", "account_id": holder,
        "settlement_cash_account_id": cash, "instrument_id": "equity-us-abbv",
        "quantity": 1, "price": 100, "gross_amount": 100, "currency": "USD",
    })
    assert response.status_code == 409, response.text
    assert "recorded history" in response.text
    assert {row["transaction_id"] for row in portfolio_store.list_transactions(PORTFOLIO)} == before_ids


def test_account_partial_date_edit_cannot_reverse_existing_boundaries(client):
    cash = account(client, "cash")
    response = client.patch(BASE + "/accounts/" + cash, json={"closed_at": "2026-01-01"})
    assert response.status_code == 400
    assert "closed_at must not be earlier than opened_at" in response.text
