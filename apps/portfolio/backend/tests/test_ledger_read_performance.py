from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services import ledger


def _detail(
    instrument_id: str,
    price: float,
    *,
    instrument_type: str = "equity",
) -> dict[str, object]:
    quote_basis = "dirty_price" if instrument_type == "bond" else "close"
    point = {
        "metric_family": "price",
        "quote_basis": quote_basis,
        "as_of_date": "2026-04-15",
        "value": str(price),
        "currency": "USD",
        "status": "complete",
    }
    if instrument_type == "bond":
        point["price_unit"] = "percent_of_par"
        point["price_scale"] = 0.01
    else:
        point["price_unit"] = "per_unit"
        point["price_scale"] = 1.0
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": instrument_type,
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
    account_id: str = "broker",
    quantity: float = 10.0,
    gross_amount: float = 1000.0,
    instrument_type: str = "equity",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "portfolio_id": "portfolio",
        "transaction_type": "buy",
        "trade_date": "2026-04-01",
        "trade_at": "2026-04-01T10:00:00Z",
        "settlement_date": "2026-04-01",
        "account_id": account_id,
        "settlement_cash_account_id": None,
        "instrument_id": instrument_id,
        "instrument_ref": {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": instrument_type,
            "currency": "USD",
            "identifiers": [],
        },
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
            _buy("txn-a", "equity-a", account_id="broker-a"),
            _buy("txn-b", "equity-b", account_id="broker-b"),
        ],
        account_id="broker-a",
        instrument_id="equity-a",
        status="open",
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{"equity-a"}]
    assert len(lots) == 1
    assert lots[0]["current_market_value"] == pytest.approx(250.0)


def test_portfolio_positions_reuse_lot_pricing_and_preserve_bond_scaling(monkeypatch) -> None:
    calls: list[set[str]] = []

    def load_details(instrument_ids):
        calls.append(set(instrument_ids))
        details = {
            "equity-a": _detail("equity-a", 25.0),
            "bond-a": _detail("bond-a", 98.5, instrument_type="bond"),
        }
        return {instrument_id: details.get(instrument_id) for instrument_id in instrument_ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load_details)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    positions = ledger.build_portfolio_positions(
        "portfolio",
        [_account()],
        [
            _buy("txn-equity", "equity-a", quantity=10.0),
            _buy(
                "txn-bond",
                "bond-a",
                quantity=1000.0,
                gross_amount=985.0,
                instrument_type="bond",
            ),
        ],
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{"equity-a", "bond-a"}]
    by_instrument = {position["instrument_id"]: position for position in positions}
    assert by_instrument["equity-a"]["last_price"] == pytest.approx(25.0)
    assert by_instrument["equity-a"]["market_value"] == pytest.approx(250.0)
    assert by_instrument["bond-a"]["last_price"] == pytest.approx(98.5)
    assert by_instrument["bond-a"]["market_value"] == pytest.approx(985.0)


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
        [_buy("txn-a", "equity-a")],
        as_of_date=date(2026, 4, 15),
    )

    assert pricing_calls == [{"equity-a"}]
    assert corporate_action_calls == [{"equity-a"}]
    assert workspace["positions"][0]["last_price"] == pytest.approx(25.0)
    assert workspace["positions"][0]["market_value"] == pytest.approx(250.0)
    assert workspace["accounts"][0]["position_market_value"] == pytest.approx(250.0)


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
        [_buy("txn-a", "equity-a")],
        as_of_date=date(2026, 4, 15),
    )

    assert workspace["positions"][0]["last_price"] is None
    assert workspace["positions"][0]["market_value"] is None
    assert workspace["accounts"][0]["position_market_value"] is None
    assert workspace["accounts"][0]["account_value_base"] is None
    assert workspace["accounts"][0]["valuation_missing_components"] == ["position_price"]
