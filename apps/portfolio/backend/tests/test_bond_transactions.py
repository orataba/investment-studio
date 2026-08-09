from __future__ import annotations

import pytest

from portfolio_app.api.routes import transactions as transaction_routes


BOND_ID = "bond-us-contract"


def _install_bond(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transaction_routes.get_registry_instrument
    bond = {
        "instrument_id": BOND_ID,
        "instrument_name": "Contract Bond",
        "instrument_type": "bond",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "internal",
                "identifier_value": BOND_ID,
                "is_primary": True,
            }
        ],
    }
    monkeypatch.setattr(
        transaction_routes,
        "get_registry_instrument",
        lambda instrument_id: dict(bond) if instrument_id == BOND_ID else original(instrument_id),
    )


def _create_bond_account(client, *, name: str) -> str:
    response = client.post(
        "/api/portfolios/portfolio-ops/accounts",
        json={
            "account_name": name,
            "account_type": "securities_account",
            "currency": "USD",
            "institution": "Contract Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "allowed_instrument_types": ["bond"],
            "opened_at": "2026-04-01",
            "status": "active",
        },
    )
    assert response.status_code == 200
    return str(response.json()["account_id"])


def _trade_payload(
    *,
    transaction_type: str,
    account_id: str,
    trade_date: str,
    quantity: float,
    price: float,
    gross_amount: float,
) -> dict[str, object]:
    return {
        "transaction_type": transaction_type,
        "trade_date": trade_date,
        "account_id": account_id,
        "settlement_cash_account_id": "cash-usd-main",
        "instrument_id": BOND_ID,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross_amount,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
    }


def test_bond_trade_amount_contract_and_lot_display_use_percent_of_par(
    client,
    monkeypatch,
):
    _install_bond(monkeypatch)
    account_id = _create_bond_account(client, name="Bond Scale Account")

    buy_payload = _trade_payload(
        transaction_type="buy",
        account_id=account_id,
        trade_date="2026-04-10",
        quantity=1000,
        price=98.5,
        gross_amount=985,
    )
    buy_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=buy_payload,
    )
    assert buy_response.status_code == 200

    updated_payload = {
        **buy_payload,
        "price": 99.0,
        "gross_amount": 990.0,
        "expected_row_version": buy_response.json()["row_version"],
    }
    update_response = client.put(
        f"/api/portfolios/portfolio-ops/transactions/{buy_response.json()['transaction_id']}",
        json=updated_payload,
    )
    assert update_response.status_code == 200

    rejected = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            **_trade_payload(
                transaction_type="buy",
                account_id=account_id,
                trade_date="2026-04-11",
                quantity=1000,
                price=98.5,
                gross_amount=98500,
            )
        },
    )
    assert rejected.status_code == 422
    assert "gross_amount must equal quantity multiplied by price" in rejected.text
    assert "price scale 0.01" in rejected.text

    sell_payload = _trade_payload(
        transaction_type="sell",
        account_id=account_id,
        trade_date="2026-04-12",
        quantity=200,
        price=99.0,
        gross_amount=198.0,
    )
    sell_payload["fees"] = 2.0
    sell_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=sell_payload,
    )
    assert sell_response.status_code == 200

    lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account_id, "position_reference_id": BOND_ID},
    )
    assert lots_response.status_code == 200
    lot = lots_response.json()["position_lots"][0]
    assert lot["entry_price"] == pytest.approx(99.0)
    assert lot["average_exit_price"] == pytest.approx(99.0)
    assert lot["entry_cost_per_unit"] == pytest.approx(0.99)
    assert lot["realized_gross_proceeds"] == pytest.approx(198.0)
    assert lot["realized_proceeds"] == pytest.approx(196.0)
    assert lot["realized_pnl"] == pytest.approx(-2.0)


def test_bond_opening_balance_uses_percent_of_par_amount_contract(client, monkeypatch):
    _install_bond(monkeypatch)
    account_id = _create_bond_account(client, name="Bond Opening Account")
    payload = _trade_payload(
        transaction_type="opening_balance",
        account_id=account_id,
        trade_date="2026-04-01",
        quantity=1000,
        price=98.5,
        gross_amount=985,
    )
    payload["settlement_cash_account_id"] = None

    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=payload,
    )

    assert response.status_code == 200
    lots_response = client.get(
        "/api/portfolios/portfolio-ops/position-lots",
        params={"account_id": account_id, "position_reference_id": BOND_ID},
    )
    assert lots_response.status_code == 200
    lot = lots_response.json()["position_lots"][0]
    assert lot["entry_gross_amount"] == pytest.approx(985.0)
    assert lot["entry_price"] == pytest.approx(98.5)
