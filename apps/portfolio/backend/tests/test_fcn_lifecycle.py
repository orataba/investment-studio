from datetime import date

import pytest

from portfolio_app.services import fcn_lifecycle, ledger


CONTRACT = {"derivative_contract_id": "fcn-1", "contract_type": "fcn", "contract_name": "USD / HK shares", "currency": "USD", "terms": {"notional": 500000}}
HK = {"instrument_id": "hk-1", "instrument_name": "HK stock", "instrument_type": "equity", "currency": "HKD", "identifiers": []}
ACCOUNTS = [{"account_id": "a", "account_type": "securities_account", "cost_basis_method": "fifo", "currency": "USD"}]


def fact(sequence, kind, day, amount, quantity=None, **overrides):
    return {"transaction_id": f"txn-{sequence}", "transaction_sequence": sequence, "transaction_type": kind,
            "portfolio_id": "p", "trade_date": day, "settlement_date": day, "account_id": "a",
            "currency": "USD", "derivative_contract_id": "fcn-1", "derivative_contract": CONTRACT,
            "instrument_id": None, "gross_amount": amount, "quantity": quantity, "fees": 0, "taxes": 0,
            **overrides}


def leg(quantity, value, **overrides):
    return {"account_id": "a", "instrument_id": "hk-1", "instrument_ref": HK, "quantity": quantity,
            "fair_value": value, "currency": "HKD", "fx_rate_to_contract": 1 / 7.81, **overrides}


def stock_fact(sequence, kind, day, amount, quantity=None, **overrides):
    return fact(sequence, kind, day, amount, quantity, derivative_contract_id=None, derivative_contract=None,
                instrument_id="hk-1", instrument_ref=HK, currency="HKD", **overrides)


def calculate(monkeypatch, transactions, *, day="2026-04-03", method="fifo", prices=None, resolve_rate=None, reference="fcn-1", actions=None):
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *args, **kwargs: actions or [])
    accounts = [{**ACCOUNTS[0], "cost_basis_method": method}]
    lots = ledger.build_position_lots("p", accounts, transactions, as_of_date=date.fromisoformat(day),
                                      corporate_actions=actions or [], pricing_map=prices or {"hk-1": {"value": 700, "price_scale": 1}}, resolve_pricing=False)
    return fcn_lifecycle.build_fcn_lifecycles(
        portfolio_id="p", position_reference_id=reference, accounts=accounts, transactions=transactions,
        contracts=[CONTRACT], as_of_date=date.fromisoformat(day), position_lots=lots,
        resolve_rate=resolve_rate or (lambda source, target, on: 1 / 7.81),
    )["lifecycles"][0]


def test_usd_fcn_hkd_delivery_pending_and_charges_count_once(monkeypatch):
    transactions = [
        fact(1, "buy", "2026-01-02", 500000, 1),
        fact(2, "coupon", "2026-02-02", 10000 / 3, entitlement_date="2026-02-02"),
        fact(3, "coupon", "2026-03-02", 10000 / 3, entitlement_date="2026-03-02"),
        fact(4, "maturity_redemption", "2026-04-02", 175 / 7.81, 1,
             asset_deliveries=[leg(4881, 3416700, taxes=3416.7, settlement_cash_account_id="hkd-cash", delivery_date="2026-04-07")],
             settlement_cashflows=[
                 {"kind": "coupon", "cash_account_id": "usd-cash", "currency": "USD", "amount": 10000 / 3, "recognition_date": "2026-04-02"},
                 {"kind": "fee", "cash_account_id": "hkd-cash", "currency": "HKD", "amount": 78.1, "recognition_date": "2026-04-02"},
             ]),
    ]
    result = calculate(monkeypatch, transactions)
    assert result["contract_status"] == "closed"
    assert result["contract_disposal_pnl"] == pytest.approx(-62500)
    assert result["coupon_income"] == pytest.approx(10000)
    assert result["contract_charges"] == pytest.approx(10)
    assert result["stock_unrealized_pnl"] == pytest.approx(-3416.7 / 7.81)
    assert result["total_pnl"] == pytest.approx(-52510 - 3416.7 / 7.81)
    assert result["deliveries"][0]["status"] == "pending"
    assert result["stocks"][0]["remaining_quantity"] == 4881
    delivered = calculate(monkeypatch, transactions, day="2026-04-07", reference="hk-1")
    assert delivered["deliveries"][0]["status"] == "delivered"
    assert delivered["total_pnl"] == pytest.approx(result["total_pnl"])


@pytest.mark.parametrize("method,remaining,sold,dividend", [("fifo", 50, 50, 50), ("moving_average", 75, 25, 75)])
def test_different_price_mixed_holdings_partial_sale_and_dividend(monkeypatch, method, remaining, sold, dividend):
    transactions = [
        fact(1, "buy", "2026-01-02", 10000 / 7.81, 1),
        fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 7500)]),
        stock_fact(3, "buy", "2026-02-03", 10000, 100),
        stock_fact(4, "sell", "2026-03-02", 4500, 50),
        stock_fact(5, "dividend", "2026-03-05", 150, entitlement_date="2026-03-05"),
    ]
    result = calculate(monkeypatch, transactions, method=method, prices={"hk-1": {"value": 90, "price_scale": 1}})
    assert result["stocks"][0]["remaining_quantity"] == pytest.approx(remaining)
    assert result["stocks"][0]["realized_quantity"] == pytest.approx(sold)
    assert result["stock_realized_pnl"] == pytest.approx(sold * 15 / 7.81)
    assert result["stock_unrealized_pnl"] == pytest.approx(remaining * 15 / 7.81)
    assert result["stock_income"] == pytest.approx(dividend / 7.81)
    assert result["total_pnl"] == pytest.approx((-2500 + 1500 + dividend) / 7.81)


def test_multiple_stocks_from_same_parent_keep_their_source_unit_costs(monkeypatch):
    second = {**HK, "instrument_id": "hk-2", "instrument_name": "Other HK stock"}
    transactions = [
        fact(1, "buy", "2026-01-02", 12000 / 7.81, 1),
        fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 7500), leg(10, 1000, instrument_id="hk-2", instrument_ref=second)]),
        stock_fact(3, "buy", "2026-02-03", 10000, 100),
        stock_fact(4, "sell", "2026-03-02", 4500, 50),
    ]
    result = calculate(monkeypatch, transactions, method="moving_average", prices={"hk-1": {"value": 90, "price_scale": 1}, "hk-2": {"value": 120, "price_scale": 1}})
    rows = {row["instrument_id"]: row for row in result["stocks"]}
    assert rows["hk-1"]["remaining_quantity"] == pytest.approx(75)
    assert rows["hk-2"]["remaining_quantity"] == pytest.approx(10)
    assert rows["hk-1"]["realized_pnl"] == pytest.approx(375 / 7.81)
    assert rows["hk-2"]["unrealized_pnl"] == pytest.approx(200 / 7.81)


def test_missing_current_fx_does_not_publish_partial_whole_investment_result(monkeypatch):
    transactions = [fact(1, "buy", "2026-01-02", 10000, 1), fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 7500)])]
    result = calculate(monkeypatch, transactions, resolve_rate=lambda *args: None)
    assert result["contract_pnl"] is not None
    assert result["stock_unrealized_pnl"] is None
    assert result["total_pnl"] is None
    assert result["warnings"]


def test_reinvested_stock_dividend_is_marked_incomplete_instead_of_omitted(monkeypatch):
    transactions = [
        fact(1, "buy", "2026-01-02", 10000, 1),
        fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 7500)]),
        stock_fact(3, "dividend_reinvestment", "2026-03-05", 150, 2, entitlement_date="2026-03-04"),
    ]
    result = calculate(monkeypatch, transactions)
    assert result["stock_income"] is None
    assert result["total_pnl"] is None
    assert "Stock reinvestment or capital repayment attribution requires review" in result["warnings"]


@pytest.mark.parametrize("redeemed_quantity", [0.5, 1])
def test_fcn_acquisition_and_disposal_charges_are_counted_once(monkeypatch, redeemed_quantity):
    transactions = [
        fact(1, "buy", "2026-01-02", 10000, 1, fees=100, taxes=20),
        fact(2, "coupon", "2026-02-02", 500, entitlement_date="2026-02-02", fees=5, taxes=10),
        fact(3, "maturity_redemption", "2026-03-02", 10000 * redeemed_quantity,
             redeemed_quantity, fees=30),
    ]
    result = calculate(monkeypatch, transactions)
    assert result["contract_disposal_pnl"] == -30
    assert result["contract_charges"] == 135
    assert result["contract_pnl"] == 500 - 135 - 30
    assert result["total_pnl"] == result["contract_pnl"]


def test_pooled_source_quantities_follow_split_before_later_acquisition(monkeypatch):
    transactions = [
        fact(1, "buy", "2026-01-02", 10000 / 7.81, 1),
        fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 7500)]),
        stock_fact(3, "buy", "2026-02-04", 5000, 100),
        stock_fact(4, "sell", "2026-03-02", 3000, 60),
        stock_fact(5, "dividend", "2026-03-05", 240, entitlement_date="2026-03-05"),
    ]
    split = {"corporate_action_event_id": "split-1", "action_type": "share_split", "status": "confirmed",
             "instrument_id": "hk-1", "effective_date": "2026-02-03", "new_units": 2, "old_units": 1,
             "quantity_rounding": "exact"}
    result = calculate(monkeypatch, transactions, method="moving_average", actions=[split],
                       prices={"hk-1": {"value": 50, "price_scale": 1}})
    assert result["stocks"][0]["remaining_quantity"] == pytest.approx(160)
    assert result["stocks"][0]["realized_quantity"] == pytest.approx(40)
    assert result["stock_realized_pnl"] == pytest.approx(500 / 7.81)
    assert result["stock_unrealized_pnl"] == pytest.approx(2000 / 7.81)
    assert result["stock_income"] == pytest.approx(160 / 7.81)


def test_zero_value_delivery_does_not_silently_omit_untracked_stock_profit(monkeypatch):
    transactions = [fact(1, "buy", "2026-01-02", 10000, 1),
                    fact(2, "maturity_redemption", "2026-02-02", 0, 1, asset_deliveries=[leg(100, 0)])]
    result = calculate(monkeypatch, transactions)
    assert result["contract_pnl"] == -10000
    assert result["stock_pnl"] is None
    assert result["total_pnl"] is None
    assert result["warnings"]
