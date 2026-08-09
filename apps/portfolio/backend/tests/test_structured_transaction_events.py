from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_app.api.assemblers import serialize_transactions
from portfolio_app.api.contracts import TransactionCreateRequest
from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.services import ledger, performance
from portfolio_app.services.ledger import (
    build_position_lots,
    derive_ledger_postings,
    validate_transaction_position_history,
)
from portfolio_app.services.option_obligations import (
    build_option_obligations,
    derive_option_obligation_events,
    open_option_obligations,
)
from portfolio_app.services.transaction_csv import (
    parse_transaction_csv,
    render_transaction_csv,
)


def _instrument_ref(
    instrument_id: str,
    instrument_type: str,
    *,
    currency: str = "USD",
    option_underlying_id: str | None = None,
    option_strike: str = "110",
    option_type: str = "call",
    option_expiry_date: str = "2026-12-18",
    option_contract_multiplier: str = "100",
    option_settlement_type: str = "physical",
) -> dict[str, object]:
    if instrument_type in {"fcn", "option"}:
        raise ValueError("Derivative test fixtures must use a Portfolio contract.")
    ref: dict[str, object] = {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": instrument_type,
        "currency": currency,
        "identifiers": [],
        "broker_identifiers": [],
    }
    return ref


def _derivative_contract(
    derivative_contract_id: str,
    contract_type: str,
    *,
    account_id: str = "broker",
    currency: str = "USD",
    option_underlying_id: str | None = None,
    option_strike: str = "110",
    option_type: str = "call",
    option_expiry_date: str = "2026-12-18",
    option_contract_multiplier: str = "100",
    option_settlement_type: str = "physical",
    persisted: bool = True,
) -> dict[str, object]:
    if contract_type == "option":
        if option_underlying_id is None:
            raise ValueError("Option test fixtures require an underlying instrument id.")
        terms: dict[str, object] = {
            "underlying_instrument_id": option_underlying_id,
            "option_type": option_type,
            "expiry_date": option_expiry_date,
            "strike": option_strike,
            "contract_multiplier": option_contract_multiplier,
            "settlement_type": option_settlement_type,
        }
    elif contract_type == "fcn":
        terms = {
            "notional": "100000",
            "issue_date": "2026-01-01",
            "maturity_date": "2026-12-31",
            "issuer": "Test Issuer",
            "counterparty": "Test Broker",
            "underlying_instrument_ids": ["equity-1"],
            "deliverable_instrument_ids": ["equity-1"],
            "barrier_type": "none",
            "barrier_level": None,
        }
    else:
        raise ValueError("Derivative test fixtures support only FCN and option contracts.")
    contract: dict[str, object] = {
        "derivative_contract_id": derivative_contract_id,
        "contract_name": derivative_contract_id,
        "contract_type": contract_type,
        "external_reference": f"TEST-{derivative_contract_id}",
        "terms": terms,
    }
    if persisted:
        contract.update(
            {
                "portfolio_id": "portfolio",
                "account_id": account_id,
                "currency": currency,
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
    return contract


def _transaction(
    transaction_id: str,
    transaction_type: str,
    trade_date: str,
    *,
    account_id: str = "broker",
    settlement_cash_account_id: str | None = "cash",
    instrument_id: str | None = None,
    instrument_type: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    gross_amount: float = 0.0,
    lifecycle_event_type: str | None = None,
    fees: float = 0.0,
    taxes: float = 0.0,
    currency: str = "USD",
    option_underlying_id: str | None = None,
    option_strike: str = "110",
    option_type: str = "call",
    option_expiry_date: str = "2026-12-18",
    option_contract_multiplier: str = "100",
    option_settlement_type: str = "physical",
) -> dict[str, object]:
    is_derivative = instrument_type in {"fcn", "option"}
    derivative_contract = (
        _derivative_contract(
            instrument_id,
            instrument_type,
            account_id=account_id,
            currency=currency,
            option_underlying_id=option_underlying_id,
            option_strike=option_strike,
            option_type=option_type,
            option_expiry_date=option_expiry_date,
            option_contract_multiplier=option_contract_multiplier,
            option_settlement_type=option_settlement_type,
        )
        if instrument_id and is_derivative and instrument_type
        else None
    )
    return {
        "transaction_id": transaction_id,
        "transaction_sequence": int(transaction_id.removeprefix("txn-")) + 1,
        "portfolio_id": "portfolio",
        "transaction_type": transaction_type,
        "lifecycle_event_type": lifecycle_event_type,
        "trade_date": trade_date,
        "trade_time": "10:00",
        "trade_at": f"{trade_date}T10:00:00+08:00",
        "created_at": f"{trade_date}T02:00:00Z",
        "settlement_date": trade_date,
        "position_effective_date": (
            trade_date
            if transaction_type
            in {"buy", "sell", "maturity_redemption", "dividend_reinvestment"}
            else None
        ),
        "account_id": account_id,
        "settlement_cash_account_id": settlement_cash_account_id,
        "instrument_id": None if is_derivative else instrument_id,
        "instrument_ref": (
            _instrument_ref(
                instrument_id,
                instrument_type,
                currency=currency,
                option_underlying_id=(
                    option_underlying_id
                    if instrument_type == "option"
                    else None
                ),
                option_strike=option_strike,
                option_type=option_type,
                option_expiry_date=option_expiry_date,
                option_contract_multiplier=option_contract_multiplier,
                option_settlement_type=option_settlement_type,
            )
            if instrument_id and instrument_type and not is_derivative
            else None
        ),
        "derivative_contract_id": instrument_id if is_derivative else None,
        "derivative_contract": derivative_contract,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross_amount,
        "fees": fees,
        "fee_category": "unknown",
        "taxes": taxes,
        "currency": currency,
    }


def _equity_detail(
    instrument_id: str,
    history: list[tuple[str, str]],
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
        "quote_selection_policy": {
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": ["close"],
            "chart": ["close"],
            "reference": ["close"],
        },
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for as_of_date, value in history
        ],
    }


def test_simulated_stock_fund_option_and_fcn_chain_uses_independent_facts() -> None:
    accounts = [
        {
            "account_id": "broker",
            "account_type": "securities_account",
            "cost_basis_method": "fifo",
        }
    ]
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-05",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100,
            price=50,
            gross_amount=5_000,
        ),
        _transaction(
            "txn-2",
            "buy",
            "2026-01-06",
            instrument_id="fund-1",
            instrument_type="fund",
            quantity=200,
            price=10,
            gross_amount=2_000,
        ),
        _transaction(
            "txn-3",
            "buy",
            "2026-01-10",
            instrument_id="call-1",
            instrument_type="option",
            option_underlying_id="equity-1",
            quantity=2,
            price=3,
            gross_amount=600,
        ),
        _transaction(
            "txn-4",
            "sell",
            "2026-02-10",
            instrument_id="call-1",
            instrument_type="option",
            option_underlying_id="equity-1",
            quantity=1,
            price=5,
            gross_amount=500,
        ),
        _transaction(
            "txn-5",
            "option_write",
            "2026-02-15",
            instrument_id="put-1",
            instrument_type="option",
            option_underlying_id="equity-1",
            option_type="put",
            quantity=3,
            price=2,
            gross_amount=600,
        ),
        _transaction(
            "txn-6",
            "option_buy_to_close",
            "2026-03-15",
            instrument_id="put-1",
            instrument_type="option",
            option_underlying_id="equity-1",
            option_type="put",
            quantity=1,
            price=1,
            gross_amount=100,
        ),
        _transaction(
            "txn-7",
            "lifecycle_event",
            "2026-04-17",
            settlement_cash_account_id=None,
            instrument_id="put-1",
            instrument_type="option",
            option_underlying_id="equity-1",
            option_type="put",
            quantity=2,
            gross_amount=0,
            lifecycle_event_type="option_assignment",
        ),
        _transaction(
            "txn-8",
            "buy",
            "2026-04-17",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=200,
            price=45,
            gross_amount=9_000,
        ),
        _transaction(
            "txn-9",
            "buy",
            "2026-05-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1,
            price=100_000,
            gross_amount=100_000,
        ),
        _transaction(
            "txn-10",
            "coupon",
            "2026-06-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            gross_amount=2_000,
        ),
        _transaction(
            "txn-11",
            "maturity_redemption",
            "2026-09-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1,
            gross_amount=100_000,
            lifecycle_event_type="fcn_knock_in",
        ),
        _transaction(
            "txn-12",
            "buy",
            "2026-09-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=1_000,
            price=100,
            gross_amount=100_000,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )

    lots = build_position_lots(
        "portfolio",
        accounts,
        transactions,
        as_of_date=date(2026, 9, 1),
        pricing_map={},
        resolve_pricing=False,
    )
    open_quantities: dict[str, float] = {}
    for lot in lots:
        if lot["status"] != "open":
            continue
        position_reference_id = str(lot["position_reference_id"])
        open_quantities[position_reference_id] = open_quantities.get(
            position_reference_id, 0.0
        ) + float(lot["remaining_quantity"])
    assert open_quantities["equity-1"] == pytest.approx(1_300)
    assert open_quantities["fund-1"] == pytest.approx(200)
    assert open_quantities["call-1"] == pytest.approx(1)
    assert "fcn-1" not in open_quantities

    obligations = build_option_obligations(transactions)
    assert obligations[0]["status"] == "assigned"
    assert obligations[0]["realized_pnl"] == pytest.approx(500)
    assert open_option_obligations(transactions) == []

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    fcn_income = [
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-10"
    ]
    assert sum(float(row.get("cash_amount_delta") or 0) for row in fcn_income) == pytest.approx(2_000)


def test_event_valued_fcn_is_carried_at_remaining_cost_without_quote() -> None:
    accounts = [
        {
            "account_id": "broker",
            "account_type": "securities_account",
            "cost_basis_method": "fifo",
        }
    ]
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            price=100_000.0,
            gross_amount=100_000.0,
        )
    ]

    lots = build_position_lots(
        "portfolio",
        accounts,
        transactions,
        status="open",
        as_of_date=date(2026, 1, 2),
        pricing_map={},
        resolve_pricing=False,
    )

    assert len(lots) == 1
    assert lots[0]["remaining_cost_basis"] == pytest.approx(100_000.0)
    assert lots[0]["current_market_value"] == pytest.approx(100_000.0)
    assert lots[0]["unrealized_pnl"] == pytest.approx(0.0)


def test_event_valued_fcn_daily_holding_uses_cost_without_market_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_market_lookup(_instrument_id: str) -> dict[str, object]:
        pytest.fail("Event-valued holdings must not request a Registry market quote.")

    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        unexpected_market_lookup,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )
    portfolio = {
        "portfolio_id": "portfolio",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-01-02",
    }
    accounts = [
        {
            "account_id": "cash",
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
        {
            "account_id": "broker",
            "account_name": "Broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    transactions = [
        _transaction(
            "txn-0",
            "opening_balance",
            "2026-01-01",
            account_id="cash",
            settlement_cash_account_id=None,
            gross_amount=100_000.0,
        ),
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            price=100_000.0,
            gross_amount=100_000.0,
        ),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        include_materialized_rows=True,
    )

    final_snapshot = snapshots[-1]
    position = next(
        row
        for row in final_snapshot["_holding_rows"]
        if row["derivative_contract_id"] == "fcn-1"
    )
    assert final_snapshot["ending_nav"] == pytest.approx(100_000.0)
    assert final_snapshot["valuation_coverage_state"] == "complete"
    assert position["market_value"] == pytest.approx(100_000.0)
    assert position["quote_basis"] == "carried_cost"
    assert position["coverage_status"] == "event-cost"
    assert position["day_change_pct"] is None
    assert position["day_change_value"] is None
    assert position["fair_value"] is None
    assert position["fair_value_coverage_status"] == "unavailable"
    assert position["risk_eligible"] is False
    assert final_snapshot["total_position_count"] == 1
    assert final_snapshot["priced_position_count"] == 0


def test_fcn_knock_in_close_and_stock_buy_are_independent_facts() -> None:
    accounts = [
        {
            "account_id": "broker",
            "account_type": "securities_account",
            "cost_basis_method": "fifo",
        }
    ]
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            price=100_000.0,
            gross_amount=100_000.0,
            fees=10.0,
            taxes=5.0,
        ),
        _transaction(
            "txn-2",
            "maturity_redemption",
            "2026-02-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            gross_amount=110_000.0,
            lifecycle_event_type="fcn_knock_in",
            fees=2.0,
            taxes=3.0,
        ),
        _transaction(
            "txn-3",
            "buy",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=1_000.0,
            price=110.0,
            gross_amount=110_000.0,
            fees=4.0,
            taxes=1.0,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )

    lots = build_position_lots(
        "portfolio",
        accounts,
        transactions,
        as_of_date=date(2026, 2, 1),
        pricing_map={},
        resolve_pricing=False,
    )

    fcn_lot = next(
        lot for lot in lots if lot["derivative_contract_id"] == "fcn-1"
    )
    stock_lot = next(lot for lot in lots if lot["instrument_id"] == "equity-1")
    assert fcn_lot["status"] == "closed"
    assert stock_lot["status"] == "open"
    assert stock_lot["remaining_quantity"] == pytest.approx(1_000.0)
    assert stock_lot["remaining_cost_basis"] == pytest.approx(110_005.0)
    assert fcn_lot["realized_pnl"] == pytest.approx(9_995.0)

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    delivered_position = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "security_position"
    )
    delivery_cash = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "security_settlement_cash"
    )
    fcn_close = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-2"
        and posting["posting_role"] == "security_position"
    )
    assert delivered_position["cost_basis_delta"] == pytest.approx(110_005.0)
    assert delivery_cash["cash_amount_delta"] == pytest.approx(-110_005.0)
    assert fcn_close["cost_basis_delta"] == pytest.approx(-100_000.0)

    serialized = serialize_transactions(
        "portfolio",
        transactions,
        {
            "broker": {
                "account_id": "broker",
                "portfolio_id": "portfolio",
                "account_name": "Broker",
                "account_type": "securities_account",
                "currency": "USD",
                "cost_basis_method": "fifo",
            },
            "cash": {
                "account_id": "cash",
                "portfolio_id": "portfolio",
                "account_name": "Cash",
                "account_type": "deposit_account",
                "currency": "USD",
            },
        },
    )
    serialized_by_id = {item.transaction_id: item for item in serialized}
    assert serialized_by_id["txn-2"].net_cash_effect == pytest.approx(109_995.0)
    assert serialized_by_id["txn-3"].net_cash_effect == pytest.approx(-110_005.0)


def test_independent_stock_buy_after_fcn_knock_in_requires_stock_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    equity_detail = _equity_detail("equity-1", [])
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: deepcopy(equity_detail)
            for instrument_id in instrument_ids
            if instrument_id == "equity-1"
        },
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: (
            deepcopy(equity_detail) if instrument_id == "equity-1" else None
        ),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )
    portfolio = {
        "portfolio_id": "portfolio",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-02-01",
    }
    accounts = [
        {
            "account_id": "cash",
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
        {
            "account_id": "broker",
            "account_name": "Broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    transactions = [
        _transaction(
            "txn-0",
            "opening_balance",
            "2026-01-01",
            account_id="cash",
            settlement_cash_account_id=None,
            gross_amount=100_000.0,
        ),
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            price=100_000.0,
            gross_amount=100_000.0,
        ),
        _transaction(
            "txn-2",
            "maturity_redemption",
            "2026-02-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            gross_amount=110_000.0,
            lifecycle_event_type="fcn_knock_in",
        ),
        _transaction(
            "txn-3",
            "buy",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=1_000.0,
            price=110.0,
            gross_amount=110_000.0,
        ),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 31),
        end_date=date(2026, 2, 1),
        include_materialized_rows=True,
    )

    before_delivery, delivery_day = snapshots
    delivered_stock = next(
        row
        for row in delivery_day["_holding_rows"]
        if row["holding_kind"] == "position"
        and row["instrument_id"] == "equity-1"
    )
    assert before_delivery["ending_nav"] == pytest.approx(100_000.0)
    assert delivery_day["valuation_coverage_state"] == "partial"
    assert delivery_day["return_coverage_state"] == "partial"
    assert delivery_day["position_market_value"] is None
    assert delivery_day["ending_nav"] is None
    assert delivery_day["daily_twr"] is None
    assert delivery_day["total_position_count"] == 1
    assert delivery_day["priced_position_count"] == 0
    assert delivered_stock["cost_basis"] == pytest.approx(110_000.0)
    assert delivered_stock["market_value"] is None
    assert delivered_stock["market_value_base"] is None
    assert delivered_stock["carrying_value"] is None
    assert delivered_stock["fair_value"] is None
    assert delivered_stock["coverage_status"] == "unpriced"


def test_independent_fcn_close_and_stock_buy_reconcile_to_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    equity_detail = _equity_detail(
        "equity-1",
        [("2026-02-01", "105")],
    )
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: deepcopy(equity_detail)
            for instrument_id in instrument_ids
            if instrument_id == "equity-1"
        },
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: (
            deepcopy(equity_detail) if instrument_id == "equity-1" else None
        ),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )
    portfolio = {
        "portfolio_id": "portfolio",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-02-01",
    }
    accounts = [
        {
            "account_id": "cash",
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
        {
            "account_id": "broker",
            "account_name": "Broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    transactions = [
        _transaction(
            "txn-0",
            "deposit",
            "2026-01-01",
            account_id="cash",
            settlement_cash_account_id=None,
            gross_amount=100_025.0,
        ),
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            price=100_000.0,
            gross_amount=100_000.0,
            fees=10.0,
            taxes=5.0,
        ),
        _transaction(
            "txn-2",
            "maturity_redemption",
            "2026-02-01",
            instrument_id="fcn-1",
            instrument_type="fcn",
            quantity=1.0,
            gross_amount=110_000.0,
            lifecycle_event_type="fcn_knock_in",
            fees=2.0,
            taxes=3.0,
        ),
        _transaction(
            "txn-3",
            "buy",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=1_000.0,
            price=110.0,
            gross_amount=110_000.0,
            fees=4.0,
            taxes=1.0,
        ),
    ]

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    cash_by_transaction = {
        transaction_id: sum(
            float(posting["cash_amount_delta"])
            for posting in postings
            if posting["transaction_id"] == transaction_id
            and posting.get("cash_amount_delta") is not None
        )
        for transaction_id in {"txn-0", "txn-1", "txn-2", "txn-3"}
    }
    assert cash_by_transaction == pytest.approx(
        {
            "txn-0": 100_025.0,
            "txn-1": -100_015.0,
            "txn-2": 109_995.0,
            "txn-3": -110_005.0,
        }
    )
    assert sum(cash_by_transaction.values()) == pytest.approx(0.0)

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 1),
        include_materialized_rows=True,
    )
    before_delivery, delivery_day = snapshots[-2:]
    delivered_stock = next(
        row
        for row in delivery_day["_holding_rows"]
        if row["holding_kind"] == "position"
        and row["instrument_id"] == "equity-1"
    )

    assert before_delivery["ending_nav"] == pytest.approx(100_010.0)
    assert delivery_day["valuation_coverage_state"] == "complete"
    assert delivery_day["cash_balance"] == pytest.approx(0.0)
    assert delivery_day["open_cost_basis"] == pytest.approx(110_005.0)
    assert delivery_day["position_market_value"] == pytest.approx(105_000.0)
    assert delivery_day["realized_pnl"] == pytest.approx(9_995.0)
    assert delivery_day["derivative_lifecycle_realized_pnl"] == pytest.approx(
        9_995.0
    )
    assert delivery_day["unrealized_pnl"] == pytest.approx(-5_005.0)
    assert delivery_day["expense_cash_amount"] == pytest.approx(25.0)
    assert delivery_day["ending_nav"] == pytest.approx(105_000.0)
    assert delivery_day["absolute_change"] == pytest.approx(4_990.0)
    assert delivery_day["total_pnl"] == pytest.approx(4_975.0)
    assert delivery_day["daily_twr"] == pytest.approx(105_000.0 / 100_010.0 - 1.0)
    assert delivered_stock["cost_basis"] == pytest.approx(110_005.0)
    assert delivered_stock["market_value"] == pytest.approx(105_000.0)
    assert delivered_stock["fair_value"] == pytest.approx(105_000.0)
    assert delivered_stock["carrying_value"] is None


def test_long_option_exercise_close_and_stock_buy_are_independent_facts() -> None:
    accounts = [
        {
            "account_id": "broker",
            "account_type": "securities_account",
            "cost_basis_method": "fifo",
        }
    ]
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="option-long-1",
            instrument_type="option",
            quantity=1.0,
            price=500.0,
            gross_amount=500.0,
            fees=10.0,
            taxes=5.0,
            option_underlying_id="equity-1",
            option_strike="100",
        ),
        _transaction(
            "txn-2",
            "maturity_redemption",
            "2026-02-01",
            instrument_id="option-long-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=0.0,
            lifecycle_event_type="option_long_exercise",
            fees=2.0,
            taxes=1.0,
            option_underlying_id="equity-1",
            option_strike="100",
        ),
        _transaction(
            "txn-3",
            "buy",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=100.0,
            gross_amount=10_000.0,
            fees=4.0,
            taxes=1.0,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )
    lots = build_position_lots(
        "portfolio",
        accounts,
        transactions,
        as_of_date=date(2026, 2, 1),
        pricing_map={},
        resolve_pricing=False,
    )

    option_lot = next(
        lot
        for lot in lots
        if lot["derivative_contract_id"] == "option-long-1"
    )
    stock_lot = next(lot for lot in lots if lot["instrument_id"] == "equity-1")
    assert option_lot["status"] == "closed"
    assert option_lot["realized_pnl"] == pytest.approx(-500.0)
    assert stock_lot["remaining_quantity"] == pytest.approx(100.0)
    assert stock_lot["remaining_cost_basis"] == pytest.approx(10_005.0)

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    option_release = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-2"
        and posting["posting_role"] == "security_position"
    )
    stock_open = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "security_position"
    )
    assert option_release["cost_basis_delta"] == pytest.approx(-500.0)
    assert stock_open["cost_basis_delta"] == pytest.approx(10_005.0)


def test_written_option_partial_close_and_expiry_release_premium_basis() -> None:
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=200.0,
            price=100.0,
            gross_amount=20_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=2.0,
            gross_amount=600.0,
            option_underlying_id="equity-1",
            fees=10.0,
            taxes=5.0,
        ),
        _transaction(
            "txn-3",
            "option_buy_to_close",
            "2026-06-01",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=100.0,
            option_underlying_id="equity-1",
            fees=2.0,
            taxes=1.0,
        ),
        _transaction(
            "txn-4",
            "lifecycle_event",
            "2026-12-18",
            settlement_cash_account_id=None,
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=0.0,
            lifecycle_event_type="option_writer_expiry",
            option_underlying_id="equity-1",
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )
    events = derive_option_obligation_events(transactions)
    close_event = next(event for event in events if event["transaction_id"] == "txn-3")
    expiry_event = next(event for event in events if event["transaction_id"] == "txn-4")
    assert close_event["released_premium_basis"] == pytest.approx(300.0)
    assert close_event["realized_pnl_delta"] == pytest.approx(197.0)
    assert expiry_event["released_premium_basis"] == pytest.approx(300.0)
    assert expiry_event["realized_pnl_delta"] == pytest.approx(300.0)

    obligation = build_option_obligations(transactions)[0]
    assert obligation["status"] == "expired"
    assert obligation["remaining_quantity"] == pytest.approx(0.0)
    assert obligation["premium_basis_remaining"] == pytest.approx(0.0)
    assert obligation["carrying_liability"] == pytest.approx(0.0)
    assert obligation["realized_pnl"] == pytest.approx(497.0)
    assert obligation["opening_fee_expense"] == pytest.approx(15.0)

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    write_cash = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-2"
        and posting["posting_role"] == "security_settlement_cash"
    )
    close_cash = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "security_settlement_cash"
    )
    assert write_cash["cash_amount_delta"] == pytest.approx(585.0)
    assert close_cash["cash_amount_delta"] == pytest.approx(-103.0)


def test_written_option_full_buy_to_close_clears_obligation() -> None:
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=100.0,
            gross_amount=10_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=300.0,
            option_underlying_id="equity-1",
            fees=10.0,
            taxes=5.0,
        ),
        _transaction(
            "txn-3",
            "option_buy_to_close",
            "2026-06-01",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=100.0,
            option_underlying_id="equity-1",
            fees=2.0,
            taxes=1.0,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )
    events = derive_option_obligation_events(transactions)
    close_event = next(event for event in events if event["transaction_id"] == "txn-3")
    obligation = build_option_obligations(transactions)[0]
    assert close_event["released_quantity"] == pytest.approx(1.0)
    assert close_event["released_premium_basis"] == pytest.approx(300.0)
    assert close_event["liability_delta"] == pytest.approx(-300.0)
    assert close_event["realized_pnl_delta"] == pytest.approx(197.0)
    assert obligation["status"] == "closed"
    assert obligation["remaining_quantity"] == pytest.approx(0.0)
    assert obligation["open_contract_quantity"] == pytest.approx(0.0)
    assert obligation["premium_basis_remaining"] == pytest.approx(0.0)
    assert obligation["carrying_liability"] == pytest.approx(0.0)
    assert obligation["realized_pnl"] == pytest.approx(197.0)
    assert obligation["opening_fee_expense"] == pytest.approx(15.0)

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    close_cash = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "security_settlement_cash"
    )
    liability_release = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "option_liability_release"
    )
    assert close_cash["cash_amount_delta"] == pytest.approx(-103.0)
    assert liability_release["liability_amount_delta"] == pytest.approx(-300.0)
    assert liability_release["realized_pnl_delta"] == pytest.approx(197.0)


def test_written_option_write_date_nav_changes_only_by_charges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    equity_detail = _equity_detail(
        "equity-1",
        [("2026-01-01", "100"), ("2026-01-02", "100")],
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: deepcopy(equity_detail)
        if instrument_id == "equity-1"
        else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )
    portfolio = {
        "portfolio_id": "portfolio",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-01-02",
    }
    accounts = [
        {
            "account_id": "cash",
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
        {
            "account_id": "broker",
            "account_name": "Broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    transactions = [
        _transaction(
            "txn-0",
            "opening_balance",
            "2026-01-01",
            account_id="cash",
            settlement_cash_account_id=None,
            gross_amount=20_015.0,
        ),
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=200.0,
            price=100.0,
            gross_amount=20_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=2.0,
            gross_amount=600.0,
            option_underlying_id="equity-1",
            fees=10.0,
            taxes=5.0,
        ),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        include_materialized_rows=True,
    )
    before_write, write_day = snapshots
    obligation_row = next(
        row
        for row in write_day["_holding_rows"]
        if row["holding_kind"] == "option_obligation"
    )

    assert before_write["ending_nav"] == pytest.approx(20_015.0)
    assert write_day["cash_balance"] == pytest.approx(600.0)
    assert write_day["position_market_value"] == pytest.approx(20_000.0)
    assert write_day["derivative_liability_base"] == pytest.approx(600.0)
    assert write_day["ending_nav"] == pytest.approx(20_000.0)
    assert write_day["absolute_change"] == pytest.approx(-15.0)
    assert write_day["daily_twr"] == pytest.approx(20_000 / 20_015 - 1.0)
    assert write_day["return_observation_eligible"] is False
    assert write_day["return_observation_exclusion_reason"] == (
        "event_valued_or_derivative_liability"
    )
    assert write_day["total_position_count"] == 1
    assert write_day["priced_position_count"] == 1
    assert obligation_row["market_value_base"] == pytest.approx(-600.0)
    assert obligation_row["day_change_pct"] is None
    assert obligation_row["risk_eligible"] is False


def test_written_option_lifecycle_reconciles_portfolio_calculation_and_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    equity_detail = _equity_detail(
        "equity-1",
        [
            ("2026-01-01", "100"),
            ("2026-01-02", "100"),
            ("2026-01-03", "100"),
        ],
    )
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: deepcopy(equity_detail)
            for instrument_id in instrument_ids
            if instrument_id == "equity-1"
        },
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: (
            deepcopy(equity_detail) if instrument_id == "equity-1" else None
        ),
    )
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            instrument_id: deepcopy(equity_detail)
            for instrument_id in instrument_ids
            if instrument_id == "equity-1"
        },
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "rates": []},
    )
    portfolio = {
        "portfolio_id": "portfolio",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-01-03",
    }
    accounts = [
        {
            "account_id": "cash",
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
        },
        {
            "account_id": "broker",
            "account_name": "Broker",
            "account_type": "securities_account",
            "currency": "USD",
            "cost_basis_method": "fifo",
        },
    ]
    transactions = [
        _transaction(
            "txn-0",
            "deposit",
            "2026-01-01",
            account_id="cash",
            settlement_cash_account_id=None,
            gross_amount=10_015.0,
        ),
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=100.0,
            gross_amount=10_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=300.0,
            option_underlying_id="equity-1",
            fees=10.0,
            taxes=5.0,
        ),
        _transaction(
            "txn-3",
            "option_buy_to_close",
            "2026-01-03",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=100.0,
            option_underlying_id="equity-1",
            fees=2.0,
            taxes=1.0,
        ),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        include_materialized_rows=True,
    )
    write_day = snapshots[1]
    close_day = snapshots[2]
    assert write_day["delta"] == pytest.approx(-15.0)
    assert close_day["delta"] == pytest.approx(197.0)
    assert close_day["total_pnl"] == pytest.approx(182.0)
    assert close_day["realized_pnl"] == pytest.approx(197.0)
    assert close_day["expense_cash_amount"] == pytest.approx(18.0)
    assert close_day["ending_nav"] == pytest.approx(10_197.0)

    for snapshot in (write_day, close_day):
        for axis in ("instrument", "account", "instrument_type", "currency"):
            axis_slices = [
                row
                for row in snapshot["_contribution_slices"]
                if row["axis"] == axis
            ]
            assert axis_slices
            assert all(row["total_pnl"] is not None for row in axis_slices)
            assert sum(float(row["total_pnl"]) for row in axis_slices) == pytest.approx(
                snapshot["delta"]
            )

    write_option_slice = next(
        row
        for row in write_day["_contribution_slices"]
        if row["axis"] == "instrument" and row["group_key"] == "option-1"
    )
    close_option_slice = next(
        row
        for row in close_day["_contribution_slices"]
        if row["axis"] == "instrument" and row["group_key"] == "option-1"
    )
    assert write_option_slice["total_pnl"] == pytest.approx(-15.0)
    assert write_option_slice["realized_pnl"] == pytest.approx(0.0)
    assert write_option_slice["expense_cash_amount"] == pytest.approx(15.0)
    assert close_option_slice["total_pnl"] == pytest.approx(197.0)
    assert close_option_slice["realized_pnl"] == pytest.approx(197.0)
    assert close_option_slice["expense_cash_amount"] == pytest.approx(0.0)

    calculation = performance.build_period_calculation_report(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )
    calculation_summary = calculation["summary"]
    assert calculation_summary["delta"] == pytest.approx(182.0)
    assert calculation_summary["capital_gains"] == pytest.approx(200.0)
    assert calculation_summary["realized_capital_gains"] == pytest.approx(200.0)
    assert calculation_summary["unrealized_capital_gains"] == pytest.approx(0.0)
    assert calculation_summary["fees"] == pytest.approx(12.0)
    assert calculation_summary["taxes"] == pytest.approx(6.0)

    performance_report = performance.build_portfolio_performance_report(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )
    performance_summary = performance_report["summary"]
    assert performance_summary["performance_basis"] == "operational_carrying_basis"
    assert performance_summary["performance_label"] == (
        "Total Portfolio Operational Return"
    )
    assert performance_summary["ordinary_sleeve_twr_status"] == "unavailable"
    assert performance_summary["ordinary_sleeve_twr_reason"] == (
        "Sleeve boundary cash flows are not maintained as a cash subledger; "
        "ordinary sleeve TWR is not derived by filtering total portfolio TWR."
    )
    assert performance_summary["derivative_lifecycle_realized_pnl"] == pytest.approx(
        197.0
    )
    assert performance_summary["annualization_eligible"] is False
    assert performance_summary["annualization_years"] is None
    assert performance_summary["annualization_unavailable_reason"] == (
        "operational_carrying_basis_not_annualized"
    )
    assert performance_summary["annualized_twr"] is None
    assert performance_summary["irr"] is None
    assert performance_summary["mean_daily_return"] is None
    assert performance_summary["annualized_volatility"] is None
    assert performance_summary["sharpe_ratio"] is None
    assert performance_summary["current_drawdown"] is None
    assert performance_summary["max_drawdown"] is None

    for axis in ("instrument", "account", "instrument_type", "currency"):
        report = performance.build_contribution_report(
            portfolio,
            accounts,
            transactions,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 3),
            axis=axis,
        )
        assert sum(float(line["total_pnl"]) for line in report["lines"]) == pytest.approx(
            calculation_summary["delta"]
        )
    instrument_report = performance.build_contribution_report(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        axis="instrument",
    )
    option_line = next(
        line for line in instrument_report["lines"] if line["group_key"] == "option-1"
    )
    assert option_line["total_pnl"] == pytest.approx(182.0)
    assert option_line["realized_pnl"] == pytest.approx(197.0)
    assert option_line["expense_cash_amount"] == pytest.approx(15.0)


def test_short_call_assignment_and_stock_sale_are_independent_facts() -> None:
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=100.0,
            gross_amount=10_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=300.0,
            option_underlying_id="equity-1",
        ),
        _transaction(
            "txn-3",
            "lifecycle_event",
            "2026-02-01",
            settlement_cash_account_id=None,
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=0.0,
            lifecycle_event_type="option_assignment",
            option_underlying_id="equity-1",
        ),
        _transaction(
            "txn-4",
            "sell",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=110.0,
            gross_amount=11_000.0,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )

    postings = derive_ledger_postings(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
        account_currency_map={"broker": "USD", "cash": "USD"},
    )
    writer_cash_posting = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-2"
        and posting["posting_role"] == "security_settlement_cash"
    )
    writer_liability_posting = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-2"
        and posting["posting_role"] == "option_premium_liability"
    )
    assignment_release_posting = next(
        posting
        for posting in postings
        if posting["transaction_id"] == "txn-3"
        and posting["posting_role"] == "option_liability_release"
    )
    assert writer_cash_posting["cash_amount_delta"] == pytest.approx(300.0)
    assert writer_liability_posting["liability_amount_delta"] == pytest.approx(300.0)
    assert writer_liability_posting["realized_pnl_delta"] == pytest.approx(0.0)
    assert assignment_release_posting["liability_amount_delta"] == pytest.approx(-300.0)
    assert assignment_release_posting["realized_pnl_delta"] == pytest.approx(300.0)

    independently_entered_stock_sale = [dict(item) for item in transactions]
    independently_entered_stock_sale[-1]["quantity"] = 99.0
    validate_transaction_position_history(
        "portfolio",
        independently_entered_stock_sale,
        account_cost_methods={"broker": "fifo"},
    )

    over_written = [dict(item) for item in transactions[:2]]
    over_written[-1]["quantity"] = 2.0
    validate_transaction_position_history(
        "portfolio",
        over_written,
        account_cost_methods={"broker": "fifo"},
    )

    identity_less = [dict(item) for item in transactions[:2]]
    identity_less[-1]["derivative_contract"] = {
        key: value
        for key, value in dict(identity_less[-1]["derivative_contract"]).items()
        if key != "terms"
    }
    with pytest.raises(
        ValueError,
        match="Option contract identity is required for option transactions",
    ):
        validate_transaction_position_history(
            "portfolio",
            identity_less,
            account_cost_methods={"broker": "fifo"},
        )


def test_partial_assignment_preserves_remaining_short_option_position() -> None:
    transactions = [
        _transaction(
            "txn-1",
            "buy",
            "2026-01-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=200.0,
            price=100.0,
            gross_amount=20_000.0,
        ),
        _transaction(
            "txn-2",
            "option_write",
            "2026-01-02",
            instrument_id="option-1",
            instrument_type="option",
            quantity=2.0,
            gross_amount=600.0,
            option_underlying_id="equity-1",
        ),
        _transaction(
            "txn-3",
            "lifecycle_event",
            "2026-02-01",
            settlement_cash_account_id=None,
            instrument_id="option-1",
            instrument_type="option",
            quantity=1.0,
            gross_amount=0.0,
            lifecycle_event_type="option_assignment",
            option_underlying_id="equity-1",
        ),
        _transaction(
            "txn-4",
            "sell",
            "2026-02-01",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=100.0,
            price=110.0,
            gross_amount=11_000.0,
        ),
    ]

    validate_transaction_position_history(
        "portfolio",
        transactions,
        account_cost_methods={"broker": "fifo"},
    )
    assignment_event = next(
        event
        for event in derive_option_obligation_events(transactions)
        if event["transaction_id"] == "txn-3"
    )
    obligation = build_option_obligations(transactions)[0]
    assert assignment_event["released_quantity"] == pytest.approx(1.0)
    assert assignment_event["released_premium_basis"] == pytest.approx(300.0)
    assert assignment_event["realized_pnl_delta"] == pytest.approx(300.0)
    assert obligation["status"] == "open"
    assert obligation["remaining_quantity"] == pytest.approx(1.0)
    assert obligation["open_contract_quantity"] == pytest.approx(1.0)
    assert obligation["required_underlying_quantity"] == pytest.approx(100.0)
    assert obligation["premium_basis_remaining"] == pytest.approx(300.0)
    assert obligation["carrying_liability"] == pytest.approx(300.0)
    assert obligation["realized_pnl"] == pytest.approx(300.0)

    independent_sale = [
        *transactions,
        _transaction(
            "txn-5",
            "sell",
            "2026-02-02",
            instrument_id="equity-1",
            instrument_type="equity",
            quantity=1.0,
            price=100.0,
            gross_amount=100.0,
        ),
    ]
    validate_transaction_position_history(
        "portfolio",
        independent_sale,
        account_cost_methods={"broker": "fifo"},
    )


def test_transaction_csv_preserves_source_precision_and_formula_safety() -> None:
    rendered = render_transaction_csv(
        [
            {
                "transaction_id": "txn-1",
                "row_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "account_id": "cash",
                "gross_amount": 1.2,
                "source_gross_amount": "1.20000001",
                "fees": 0.0,
                "source_fees": "0.00000000",
                "fee_category": "unknown",
                "taxes": 0.0,
                "source_taxes": "0.00000000",
                "currency": "USD",
                "source_system": "test",
                "external_reference": "deposit-1",
                "note": "=unsafe formula",
            }
        ]
    )
    headers, rows = parse_transaction_csv(rendered)

    assert "transaction_id" in headers
    assert rows[0].errors == ()
    assert rows[0].transaction is not None
    assert rows[0].transaction.gross_amount == Decimal("1.20000001")
    assert rows[0].transaction.note == "=unsafe formula"


def test_csv_api_imports_short_option_and_independent_assignment_facts(client) -> None:
    account_response = client.patch(
        "/api/portfolios/portfolio-ops/accounts/broker-us-core",
        json={"allowed_instrument_types": ["equity", "fund", "etf", "option"]},
    )
    assert account_response.status_code == 200

    csv_text = "\n".join(
        [
            (
                "transaction_type,trade_date,settlement_date,account_id,"
                "settlement_cash_account_id,derivative_contract_id,"
                "derivative_contract_name,derivative_contract_type,"
                "option_underlying_instrument_id,option_type,option_expiry_date,"
                "option_strike,option_contract_multiplier,option_settlement_type,"
                "quantity,price,"
                "gross_amount,fees,taxes,currency,source_system,"
                "external_reference"
            ),
            (
                "option_write,2026-05-01,2026-05-01,broker-us-core,"
                "cash-usd-main,option-short-call-1,ABBV Dec 220 Call,option,"
                "equity-us-abbv,call,2026-12-18,220,100,physical,1,5,"
                "500,0,0,USD,colleague_project,CALL-001-WRITE"
            ),
        ]
    )

    preview_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/preview",
        json={"csv_text": csv_text},
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["valid_count"] == 1, preview["rows"]
    assert preview["error_count"] == 0

    import_payload = {
        "csv_text": csv_text,
        "preview_digest": preview["preview_digest"],
    }
    first_import = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/import",
        headers={"Idempotency-Key": "csv-option-write-1"},
        json=import_payload,
    )
    assert first_import.status_code == 200
    created = first_import.json()["transactions"][0]
    assert created["transaction_type"] == "option_write"
    assert created["source_system"] == "colleague_project"
    assert created["net_cash_effect"] == pytest.approx(500.0)

    replay = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/import",
        headers={"Idempotency-Key": "csv-option-write-1"},
        json=import_payload,
    )
    assert replay.status_code == 200
    assert replay.json()["transactions"][0]["transaction_id"] == created["transaction_id"]

    duplicate_preview = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/preview",
        json={"csv_text": csv_text},
    ).json()
    assert duplicate_preview["error_count"] == 1
    assert "already exist" in duplicate_preview["batch_errors"][0]

    download = client.get("/api/portfolios/portfolio-ops/transactions.csv")
    assert download.status_code == 200
    assert "CALL-001-WRITE" in download.text

    assignment_csv = "\n".join(
        [
            (
                "transaction_type,lifecycle_event_type,trade_date,settlement_date,"
                "account_id,settlement_cash_account_id,instrument_id,"
                "derivative_contract_id,quantity,price,gross_amount,fees,"
                "taxes,currency,source_system,external_reference"
            ),
            (
                "lifecycle_event,option_assignment,2026-05-10,2026-05-10,"
                "broker-us-core,,,option-short-call-1,1,,0,0,0,USD,colleague_project,"
                "CALL-001-ASSIGNMENT"
            ),
            (
                "sell,,2026-05-10,2026-05-10,broker-us-core,cash-usd-main,"
                "equity-us-abbv,,100,220,22000,0,0,USD,"
                "colleague_project,CALL-001-DELIVERY"
            ),
        ]
    )
    assignment_preview_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/preview",
        json={"csv_text": assignment_csv},
    )
    assert assignment_preview_response.status_code == 200
    assignment_preview = assignment_preview_response.json()
    assert assignment_preview["error_count"] == 0, (
        assignment_preview["rows"],
        assignment_preview["batch_errors"],
    )
    assignment_import = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/import",
        headers={"Idempotency-Key": "csv-option-assignment-1"},
        json={
            "csv_text": assignment_csv,
            "preview_digest": assignment_preview["preview_digest"],
        },
    )
    assert assignment_import.status_code == 200
    assignment_transactions = assignment_import.json()["transactions"]
    assert assignment_transactions[1]["position_effective_date"] == "2026-05-10"
    assignment_id = assignment_transactions[0]["transaction_id"]
    expected_row_versions = {
        assignment_transactions[0]["transaction_id"]: assignment_transactions[0]["row_version"]
    }

    workspace_response = client.get(
        "/api/portfolios/portfolio-ops/transactions/workspace",
        params={"transaction_id": assignment_id},
    )
    assert workspace_response.status_code == 200
    assert workspace_response.json()["delete_scope_row_versions"] == expected_row_versions

    delete_response = client.request(
        "DELETE",
        f"/api/portfolios/portfolio-ops/transactions/{assignment_id}",
        json={"expected_row_versions": expected_row_versions},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_count"] == 1


def test_direct_transaction_source_identity_conflict_returns_409(client) -> None:
    payload = {
        "transaction_type": "deposit",
        "trade_date": "2026-05-01",
        "account_id": "cash-usd-main",
        "gross_amount": 100,
        "currency": "USD",
        "source_system": "colleague_project",
        "external_reference": "CASH-001",
    }
    first_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        headers={"Idempotency-Key": "direct-source-identity-1"},
        json=payload,
    )
    assert first_response.status_code == 200

    conflict_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        headers={"Idempotency-Key": "direct-source-identity-2"},
        json=payload,
    )
    assert conflict_response.status_code == 409
    assert "same portfolio/source_system/external_reference" in conflict_response.json()[
        "detail"
    ]


def test_inline_derivative_contract_rejects_unknown_registry_underlying(client) -> None:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-05-01",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "derivative_contract_id": "option-missing-underlying",
            "derivative_contract": {
                "derivative_contract_id": "option-missing-underlying",
                "contract_name": "Missing underlying option",
                "contract_type": "option",
                "external_reference": "MISSING-UNDERLYING-1",
                "terms": {
                    "underlying_instrument_id": "not-in-registry",
                    "option_type": "call",
                    "expiry_date": "2026-12-18",
                    "strike": 100,
                    "contract_multiplier": 100,
                    "settlement_type": "physical",
                },
            },
            "quantity": 1,
            "price": 1,
            "gross_amount": 100,
            "currency": "USD",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Instrument not found in shared registry."


def test_derivative_contract_external_reference_is_unique_within_portfolio(
    client,
) -> None:
    account_response = client.patch(
        "/api/portfolios/portfolio-ops/accounts/broker-us-core",
        json={"allowed_instrument_types": ["equity", "etf", "fund", "option"]},
    )
    assert account_response.status_code == 200, account_response.json()

    def payload(contract_id: str) -> dict[str, object]:
        return {
            "transaction_type": "buy",
            "trade_date": "2026-05-01",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "derivative_contract_id": contract_id,
            "derivative_contract": {
                "derivative_contract_id": contract_id,
                "contract_name": contract_id,
                "contract_type": "option",
                "external_reference": "BROKER-OPTION-001",
                "terms": {
                    "underlying_instrument_id": "equity-us-abbv",
                    "option_type": "call",
                    "expiry_date": "2026-12-18",
                    "strike": 200,
                    "contract_multiplier": 100,
                    "settlement_type": "physical",
                },
            },
            "quantity": 1,
            "price": 1,
            "gross_amount": 100,
            "currency": "USD",
        }

    first_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=payload("option-reference-a"),
    )
    assert first_response.status_code == 200, first_response.json()

    duplicate_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json=payload("option-reference-b"),
    )
    assert duplicate_response.status_code == 409
    assert "external_reference already belongs" in duplicate_response.json()["detail"]


def test_derivative_contract_identity_is_scoped_to_its_portfolio(client) -> None:
    contract_id = "option-local-identity"
    account_response = client.patch(
        "/api/portfolios/portfolio-ops/accounts/broker-us-core",
        json={"allowed_instrument_types": ["equity", "etf", "fund", "option"]},
    )
    assert account_response.status_code == 200, account_response.json()
    create_response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "buy",
            "trade_date": "2026-05-01",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "derivative_contract_id": contract_id,
            "derivative_contract": {
                "derivative_contract_id": contract_id,
                "contract_name": "Portfolio-local call",
                "contract_type": "option",
                "external_reference": "LOCAL-CALL-1",
                "terms": {
                    "underlying_instrument_id": "equity-us-abbv",
                    "option_type": "call",
                    "expiry_date": "2026-12-18",
                    "strike": 200,
                    "contract_multiplier": 100,
                    "settlement_type": "physical",
                },
            },
            "quantity": 1,
            "price": 1,
            "gross_amount": 100,
            "currency": "USD",
        },
    )
    assert create_response.status_code == 200, create_response.json()

    copy_response = client.post("/api/portfolios/portfolio-ops/copy")
    assert copy_response.status_code == 200, copy_response.json()
    copied_portfolio_id = copy_response.json()["portfolio_id"]

    original_contracts = client.get(
        "/api/portfolios/portfolio-ops/derivative-contracts"
    ).json()["derivative_contracts"]
    copied_contracts = client.get(
        f"/api/portfolios/{copied_portfolio_id}/derivative-contracts"
    ).json()["derivative_contracts"]
    original = next(
        item for item in original_contracts if item["derivative_contract_id"] == contract_id
    )
    copied = next(
        item for item in copied_contracts if item["derivative_contract_id"] == contract_id
    )
    assert copied["external_reference"] == original["external_reference"]
    assert copied["portfolio_id"] == copied_portfolio_id


def test_documented_multi_asset_independent_transactions_csv_imports_cleanly(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_response = client.patch(
        "/api/portfolios/portfolio-ops/accounts/broker-us-core",
        json={
            "allowed_instrument_types": [
                "equity",
                "fund",
                "etf",
                "fcn",
                "option",
            ]
        },
    )
    assert account_response.status_code == 200

    instrument_refs = {
        "equity-demo-001": _instrument_ref(
            "equity-demo-001",
            "equity",
        ),
        "fund-demo-001": _instrument_ref("fund-demo-001", "fund"),
    }
    original_loader = transaction_routes._load_instrument_ref

    def load_instrument(instrument_id: str) -> dict[str, object]:
        if instrument_id in instrument_refs:
            return instrument_refs[instrument_id]
        return original_loader(instrument_id)

    monkeypatch.setattr(transaction_routes, "_load_instrument_ref", load_instrument)
    workspace_root = Path(__file__).resolve().parents[4]
    csv_text = (
        workspace_root
        / "docs"
        / "examples"
        / "transaction_import_stock_fund_option_fcn.csv"
    ).read_text(encoding="utf-8")

    preview_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/preview",
        json={"csv_text": csv_text},
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["row_count"] == 12
    assert preview["valid_count"] == 12, (
        preview["rows"],
        preview["batch_errors"],
    )
    assert preview["error_count"] == 0, (
        preview["rows"],
        preview["batch_errors"],
    )

    import_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/csv/import",
        headers={"Idempotency-Key": "csv-mixed-independent-facts-1"},
        json={
            "csv_text": csv_text,
            "preview_digest": preview["preview_digest"],
        },
    )
    assert import_response.status_code == 200
    created = import_response.json()["transactions"]
    assert len(created) == 12

    download_response = client.get(
        "/api/portfolios/portfolio-ops/transactions.csv"
    )
    assert download_response.status_code == 200
    exported_csv = download_response.text
    assert "FCN-STOCK-001" in exported_csv


def test_transaction_contract_distinguishes_long_and_writer_option_events() -> None:
    long_expiry = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "maturity_redemption",
            "lifecycle_event_type": "option_long_expiry",
            "trade_date": "2026-06-01",
            "account_id": "broker",
            "derivative_contract_id": "option-long-1",
            "quantity": 1,
            "gross_amount": 0,
            "currency": "USD",
        }
    )
    writer_assignment = TransactionCreateRequest.model_validate(
        {
            "transaction_type": "lifecycle_event",
            "lifecycle_event_type": "option_assignment",
            "trade_date": "2026-06-01",
            "account_id": "broker",
            "derivative_contract_id": "option-short-1",
            "quantity": 1,
            "gross_amount": 0,
            "currency": "USD",
        }
    )

    assert long_expiry.gross_amount == 0
    assert writer_assignment.quantity == 1


def test_transaction_contract_rejects_removed_derivative_grouping_fields() -> None:
    payload = {
        "transaction_type": "maturity_redemption",
        "lifecycle_event_type": "fcn_knock_in",
        "trade_date": "2026-09-01",
        "account_id": "broker",
        "derivative_contract_id": "fcn-1",
        "quantity": 1,
        "gross_amount": 100_000,
        "currency": "USD",
    }

    for field_name in ("event_group_id", "related_instrument_id"):
        with pytest.raises(ValueError, match=field_name):
            TransactionCreateRequest.model_validate(
                {**payload, field_name: "removed-derivative-relation"}
            )
    with pytest.raises(ValueError, match="fcn_physical_settlement"):
        TransactionCreateRequest.model_validate(
            {**payload, "lifecycle_event_type": "fcn_physical_settlement"}
        )
