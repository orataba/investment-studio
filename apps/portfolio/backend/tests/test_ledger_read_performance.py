from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.api.routes import accounts as accounts_route
from portfolio_app.services import ledger


def _detail(
    instrument_id: str,
    price: float,
) -> dict[str, object]:
    quote_basis = "close"
    point = {
        "metric_family": "price",
        "quote_basis": quote_basis,
        "as_of_date": "2026-04-15",
        "value": str(price),
        "currency": "USD",
        "status": "complete",
    }
    point["price_unit"] = "per_unit"
    point["price_scale"] = 1.0
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
        "quote_selection_policy": {"valuation": [quote_basis]},
        "market_data": [point],
        "latest_market_data": [point],
    }


def _account(account_id: str = "broker") -> dict[str, object]:
    return {
        "account_id": account_id,
        "account_name": account_id,
        "account_type": "securities_account",
        "currency": "USD",
        "cost_basis_method": "fifo",
    }


def _buy(
    transaction_id: str,
    instrument_id: str,
    *,
    transaction_sequence: int,
    account_id: str = "broker",
    quantity: float = 10.0,
    gross_amount: float = 1000.0,
    instrument_type: str = "equity",
) -> dict[str, object]:
    is_derivative = instrument_type in {"fcn", "option"}
    derivative_contract = (
        {
            "derivative_contract_id": instrument_id,
            "portfolio_id": "portfolio",
            "account_id": account_id,
            "contract_name": instrument_id,
            "contract_type": instrument_type,
            "currency": "USD",
            "external_reference": f"TEST-{instrument_id}",
            "terms": {
                "notional": "1000",
                "issue_date": "2026-01-01",
                "maturity_date": "2026-12-31",
                "issuer": "Test Issuer",
                "counterparty": "Test Broker",
                "underlying_instrument_ids": ["equity-a"],
                "deliverable_instrument_ids": ["equity-a"],
                "barrier_type": "none",
                "barrier_level": None,
            },
            "created_at": "2026-04-01T10:00:00Z",
        }
        if is_derivative
        else None
    )
    return {
        "transaction_id": transaction_id,
        "transaction_sequence": transaction_sequence,
        "portfolio_id": "portfolio",
        "transaction_type": "buy",
        "trade_date": "2026-04-01",
        "trade_at": "2026-04-01T10:00:00Z",
        "settlement_date": "2026-04-01",
        "account_id": account_id,
        "settlement_cash_account_id": None,
        "instrument_id": None if is_derivative else instrument_id,
        "instrument_ref": (
            None
            if is_derivative
            else {
                "instrument_id": instrument_id,
                "instrument_name": instrument_id,
                "instrument_type": instrument_type,
                "currency": "USD",
                "identifiers": [],
            }
        ),
        "derivative_contract_id": instrument_id if is_derivative else None,
        "derivative_contract": derivative_contract,
        "quantity": quantity,
        "gross_amount": gross_amount,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
        "created_at": "2026-04-01T10:00:00Z",
    }


def test_historical_pricing_uses_one_bulk_registry_load(monkeypatch) -> None:
    calls: list[set[str]] = []

    def load_details(instrument_ids):
        calls.append(set(instrument_ids))
        return {
            "equity-a": _detail("equity-a", 11.0),
            "equity-b": _detail("equity-b", 22.0),
        }

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(
        ledger,
        "list_registry_instruments",
        lambda: pytest.fail("specific-id pricing must not scan the registry"),
    )

    pricing_quotes = ledger._resolve_pricing_quote_map(
        {"equity-a", "equity-b"},
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{"equity-a", "equity-b"}]
    assert {
        instrument_id: ledger._pricing_value(quote)
        for instrument_id, quote in pricing_quotes.items()
    } == {"equity-a": 11.0, "equity-b": 22.0}


def test_position_lot_filters_run_before_pricing(monkeypatch) -> None:
    calls: list[set[str]] = []

    def load_details(instrument_ids):
        calls.append(set(instrument_ids))
        return {instrument_id: _detail(instrument_id, 25.0) for instrument_id in instrument_ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    lots = ledger.build_position_lots(
        "portfolio",
        [_account("broker-a"), _account("broker-b")],
        [
            _buy("txn-a", "equity-a", transaction_sequence=1, account_id="broker-a"),
            _buy("txn-b", "equity-b", transaction_sequence=2, account_id="broker-b"),
        ],
        account_id="broker-a",
        position_reference_id="equity-a",
        status="open",
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{"equity-a"}]
    assert len(lots) == 1
    assert lots[0]["current_market_value"] == pytest.approx(250.0)


def test_portfolio_positions_reuse_lot_pricing(monkeypatch) -> None:
    calls: list[set[str]] = []

    def load_details(instrument_ids):
        calls.append(set(instrument_ids))
        details = {
            "equity-a": _detail("equity-a", 25.0),
            "equity-b": _detail("equity-b", 98.5),
        }
        return {instrument_id: details.get(instrument_id) for instrument_id in instrument_ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    positions = ledger.build_portfolio_positions(
        "portfolio",
        [_account()],
        [
            _buy("txn-equity", "equity-a", transaction_sequence=1, quantity=10.0),
            _buy(
                "txn-equity-b",
                "equity-b",
                transaction_sequence=2,
                quantity=10.0,
                gross_amount=985.0,
            ),
        ],
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{"equity-a", "equity-b"}]
    by_instrument = {position["instrument_id"]: position for position in positions}
    assert by_instrument["equity-a"]["last_price"] == pytest.approx(25.0)
    assert by_instrument["equity-a"]["market_value"] == pytest.approx(250.0)
    assert by_instrument["equity-b"]["last_price"] == pytest.approx(98.5)
    assert by_instrument["equity-b"]["market_value"] == pytest.approx(985.0)


def test_account_workspace_reuses_pricing_and_corporate_actions(monkeypatch) -> None:
    pricing_calls: list[set[str]] = []
    corporate_action_calls: list[set[str]] = []

    def load_details(instrument_ids):
        pricing_calls.append(set(instrument_ids))
        return {instrument_id: _detail(instrument_id, 25.0) for instrument_id in instrument_ids}

    def load_corporate_actions(instrument_ids, **_kwargs):
        corporate_action_calls.append(set(instrument_ids))
        return []

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", load_corporate_actions)

    workspace = ledger.build_account_workspace(
        "portfolio",
        [_account()],
        [_buy("txn-a", "equity-a", transaction_sequence=1)],
        as_of_date=date(2026, 4, 15),
    )

    assert pricing_calls == [{"equity-a"}]
    assert corporate_action_calls == [{"equity-a"}]
    position = workspace["positions"][0]
    assert position["last_price"] == pytest.approx(25.0)
    assert position["market_value"] == pytest.approx(250.0)
    assert position["carrying_value"] is None
    assert position["fair_value"] == pytest.approx(250.0)
    assert position["fair_value_coverage_status"] == "complete"
    assert position["valuation_basis"] == "market_quote"
    assert position["coverage_status"] == "price-nav-fx"
    assert workspace["accounts"][0]["position_market_value"] == pytest.approx(250.0)


def test_account_workspace_labels_event_position_as_carried_not_market_priced(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: _detail(instrument_id, 25.0)
            for instrument_id in instrument_ids
        },
    )
    monkeypatch.setattr(
        ledger,
        "list_registry_corporate_actions",
        lambda *_args, **_kwargs: [],
    )

    workspace = ledger.build_account_workspace(
        "portfolio",
        [_account()],
        [_buy("txn-fcn", "fcn-a", transaction_sequence=1, instrument_type="fcn")],
        as_of_date=date(2026, 4, 15),
    )

    position = workspace["positions"][0]
    assert position["last_price"] is None
    assert position["market_value"] == pytest.approx(1000.0)
    assert position["carrying_value"] == pytest.approx(1000.0)
    assert position["fair_value"] is None
    assert position["fair_value_coverage_status"] == "unavailable"
    assert position["valuation_basis"] == "carried_cost"
    assert position["coverage_status"] == "event-cost"


def test_account_workspace_reuses_supplied_instrument_detail_cache(monkeypatch) -> None:
    pricing_calls: list[set[str]] = []

    def load_details(instrument_ids):
        pricing_calls.append(set(instrument_ids))
        return {instrument_id: _detail(instrument_id, 25.0) for instrument_id in instrument_ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])
    instrument_detail_cache: dict[str, dict[str, object] | None] = {}

    for _ in range(2):
        workspace = ledger.build_account_workspace(
            "portfolio",
            [_account()],
            [_buy("txn-a", "equity-a", transaction_sequence=1)],
            as_of_date=date(2026, 4, 15),
            instrument_detail_cache=instrument_detail_cache,
        )
        assert workspace["positions"][0]["last_price"] == pytest.approx(25.0)

    assert pricing_calls == [{"equity-a"}]
    assert instrument_detail_cache["equity-a"] is not None


def test_position_lot_identity_scan_can_skip_pricing_resolution(monkeypatch) -> None:
    pricing_calls: list[set[str]] = []

    def load_details(instrument_ids):
        pricing_calls.append(set(instrument_ids))
        return {instrument_id: _detail(instrument_id, 25.0) for instrument_id in instrument_ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    position_lots = ledger.build_position_lots(
        "portfolio",
        [_account()],
        [_buy("txn-a", "equity-a", transaction_sequence=1)],
        as_of_date=date(2026, 4, 15),
        resolve_pricing=False,
    )

    assert len(position_lots) == 1
    assert position_lots[0]["instrument_id"] == "equity-a"
    assert pricing_calls == []


def test_account_workspace_preserves_missing_price_propagation(monkeypatch) -> None:
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {instrument_id: None for instrument_id in instrument_ids},
    )
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    workspace = ledger.build_account_workspace(
        "portfolio",
        [_account()],
        [_buy("txn-a", "equity-a", transaction_sequence=1)],
        as_of_date=date(2026, 4, 15),
    )

    assert workspace["positions"][0]["last_price"] is None
    assert workspace["positions"][0]["market_value"] is None
    assert workspace["accounts"][0]["position_market_value"] is None
    assert workspace["accounts"][0]["account_value_base"] is None
    assert workspace["accounts"][0]["valuation_missing_components"] == ["position_price"]


def test_account_workspace_exposes_and_filters_written_option_obligations(monkeypatch) -> None:
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: _detail(instrument_id, 25.0)
            for instrument_id in instrument_ids
        },
    )
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])
    accounts = [_account("broker-a"), _account("broker-b")]
    transactions = [
        _buy(
            "txn-underlying",
            "equity-a",
            transaction_sequence=1,
            account_id="broker-a",
            quantity=100.0,
            gross_amount=2_500.0,
        ),
        {
            "transaction_id": "txn-write",
            "transaction_sequence": 2,
            "portfolio_id": "portfolio",
            "transaction_type": "option_write",
            "trade_date": "2026-04-02",
            "trade_at": "2026-04-02T10:00:00Z",
            "settlement_date": "2026-04-02",
            "account_id": "broker-a",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "derivative_contract_id": "option-a",
            "derivative_contract": {
                "derivative_contract_id": "option-a",
                "portfolio_id": "portfolio",
                "account_id": "broker-a",
                "contract_name": "option-a",
                "contract_type": "option",
                "currency": "USD",
                "external_reference": "TEST-option-a",
                "terms": {
                    "underlying_instrument_id": "equity-a",
                    "option_type": "call",
                    "expiry_date": "2026-12-18",
                    "strike": "30",
                    "contract_multiplier": "100",
                },
                "created_at": "2026-04-02T10:00:00Z",
            },
            "quantity": 1.0,
            "gross_amount": 150.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-04-02T10:00:00Z",
        },
    ]

    all_accounts = ledger.build_account_workspace(
        "portfolio",
        accounts,
        transactions,
        selected_account_id=None,
        as_of_date=date(2026, 4, 15),
    )
    assert len(all_accounts["option_obligations"]) == 1
    assert all_accounts["option_obligations"][0]["account_id"] == "broker-a"
    assert all_accounts["summary"]["open_option_obligation_count"] == 1
    assert all_accounts["summary"]["derivative_liability_base"] == pytest.approx(150.0)

    selected = ledger.build_account_workspace(
        "portfolio",
        accounts,
        transactions,
        selected_account_id="broker-b",
        as_of_date=date(2026, 4, 15),
    )
    assert selected["option_obligations"] == []
    assert selected["summary"]["open_option_obligation_count"] == 1


def test_accounts_workspace_http_contract_preserves_option_obligations(
    client,
    monkeypatch,
) -> None:
    portfolio_id = "portfolio-ops"
    account = next(
        item
        for item in accounts_route.list_accounts(portfolio_id)
        if item["account_id"] == "broker-us-core"
    )
    obligation = {
        "obligation_id": "obligation-http-contract",
        "account_id": account["account_id"],
        "instrument_id": "option-short-call",
        "related_underlying_id": "equity-us-abbv",
        "open_contract_quantity": 1.0,
        "required_underlying_quantity": 100.0,
        "remaining_quantity": 1.0,
        "premium_received_gross": 150.0,
        "premium_basis_remaining": 150.0,
        "carrying_liability": 150.0,
        "status": "open",
    }
    fake_workspace = {
        "base_currency": "USD",
        "summary": {
            "account_count": 1,
            "deposit_account_count": 0,
            "securities_account_count": 1,
            "ledger_posting_count": 0,
            "position_line_count": 0,
            "open_option_obligation_count": 1,
            "derivative_liability_base": 150.0,
        },
        "derivation_boundary": {
            "ledger_postings": "next_layer",
            "positions": "not_started",
            "position_lots": "not_started",
            "holdings": "not_started",
            "snapshot": "not_started",
        },
        "selected_account_id": account["account_id"],
        "accounts": [
            {
                "account": account,
                "linked_transaction_count": 0,
                "linked_posting_count": 0,
                "derived_cash_balance": 0.0,
                "position_line_count": 0,
                "open_option_obligation_count": 1,
                "derivative_liability": 150.0,
            }
        ],
        "ledger_postings": [],
        "positions": [],
        "option_obligations": [obligation],
    }
    monkeypatch.setattr(
        accounts_route,
        "build_account_workspace",
        lambda *_args, **_kwargs: fake_workspace,
    )
    monkeypatch.setattr(
        accounts_route,
        "_transactions_booked_to_account",
        lambda *_args, **_kwargs: [],
    )

    response = client.get(
        f"/api/portfolios/{portfolio_id}/accounts/workspace",
        params={"account_id": account["account_id"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selected_account_id"] == account["account_id"]
    assert payload["option_obligations"] == [obligation]
