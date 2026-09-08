from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance
from portfolio_app.services.asset_deliveries import expand_asset_deliveries, pending_asset_delivery_quantities
from portfolio_app.services.transaction_dates import transaction_affected_dates


STOCK = {"instrument_id": "fcn-hk-stock", "instrument_name": "HK delivery", "instrument_type": "equity", "currency": "HKD", "exchange_code": "XHKG"}
CONTRACT = {"derivative_contract_id": "fcn-review", "contract_name": "USD FCN", "account_id": "fcn", "currency": "USD", "contract_type": "fcn", "terms": {
    "notional": 500000, "issue_date": "2026-06-01", "maturity_date": "2026-06-03", "settlement_type": "physical",
    "underlyings": [{"instrument_id": STOCK["instrument_id"], "deliverable": True}],
}}
ACCOUNTS = [{"account_id": key, "account_name": key, "account_type": "deposit_account" if category == "cash" else "securities_account", "account_category": category, "currency": currency, "cost_basis_method": "fifo"}
            for key, category, currency in [("usd", "cash", "USD"), ("hkd", "cash", "HKD"), ("fcn", "fcn", "USD"), ("stock", "security", "HKD")]]
PORTFOLIO = {"portfolio_id": "fcn-review", "base_currency": "USD", "inception_date": "2026-06-01", "as_of_date": "2026-06-08", "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"}


def fact(sequence, kind, day, account, amount, **extra):
    return {"portfolio_id": "fcn-review", "transaction_id": f"fcn-{sequence}", "transaction_sequence": sequence,
            "transaction_type": kind, "trade_date": day, "trade_at": day + "T10:00:00Z", "settlement_date": day,
            "account_id": account, "currency": "USD", "gross_amount": amount, "quantity": None, "fees": 0, "taxes": 0, **extra}


def facts():
    reference = {"derivative_contract_id": CONTRACT["derivative_contract_id"], "derivative_contract": CONTRACT}
    return [
        fact(1, "opening_balance", "2026-06-01", "usd", 500000),
        fact(2, "opening_balance", "2026-06-01", "hkd", 10000, currency="HKD"),
        fact(3, "buy", "2026-06-01", "fcn", 500000, quantity=1, settlement_cash_account_id="usd", **reference),
        fact(4, "maturity_redemption", "2026-06-03", "fcn", 100, quantity=1,
             lifecycle_event_type="fcn_maturity", settlement_cash_account_id="usd", settlement_date="2026-06-05", **reference,
             asset_deliveries=[{"account_id": "stock", "instrument_id": STOCK["instrument_id"], "instrument_ref": STOCK,
                                "quantity": 4800, "fair_value": 2880000, "currency": "HKD", "fx_rate_to_contract": "0.128205128205128205",
                                "delivery_date": "2026-06-05", "settlement_cash_account_id": "hkd", "fees": 39, "taxes": 2880,
                                "fee_settlement_date": "2026-06-05", "quantity_fx_rate": "7.8"}],
             settlement_cashflows=[
                 {"kind": "coupon", "cash_account_id": "usd", "currency": "USD", "amount": "3333.33", "recognition_date": "2026-06-04", "settlement_date": "2026-06-08"},
                 {"kind": "fee", "cash_account_id": "hkd", "currency": "HKD", "amount": 78, "recognition_date": "2026-06-04", "settlement_date": "2026-06-05"},
             ]),
    ]


def lots(transactions, day):
    return ledger.build_position_lots("fcn-review", ACCOUNTS, transactions, corporate_actions=[], as_of_date=date.fromisoformat(day), resolve_pricing=False, pricing_map={})


def test_delivery_cost_cash_dates_and_closed_fcn_income_are_separate():
    transactions = facts()
    expanded = expand_asset_deliveries(transactions)
    assert expand_asset_deliveries(expanded) == expanded
    assert len({tx["transaction_id"] for tx in expanded}) == len(transactions)
    assert not any(tx["transaction_type"] == "fx_conversion" for tx in expanded)
    stock = next(lot for lot in lots(transactions, "2026-06-03") if lot["account_id"] == "stock")
    assert stock["remaining_quantity"] == 4800
    assert stock["remaining_cost_basis"] == 2882919
    assert stock["opened_by_transaction_id"] == "fcn-4"
    closed = next(lot for lot in lots(transactions, "2026-06-04") if lot["account_id"] == "fcn")
    assert closed["remaining_quantity"] == 0
    assert closed["realized_pnl"] == pytest.approx(2880000 / 7.8 + 100 - 500000)
    assert closed["income_cash_amount"] == 3333.33
    assert closed["expense_cash_amount"] == 0  # 78 HKD must never become 78 USD.
    postings = ledger.derive_ledger_postings("fcn-review", transactions, corporate_actions=[])
    effects = [(row["account_id"], row["effective_date"], row["cash_amount_delta"]) for row in postings if row["transaction_id"] == "fcn-4" and row["cash_amount_delta"] is not None]
    assert sorted(effects) == sorted([("usd", "2026-06-05", 100), ("hkd", "2026-06-05", -2919), ("usd", "2026-06-08", 3333.33), ("hkd", "2026-06-05", -78)])
    assert pending_asset_delivery_quantities(transactions, date(2026, 6, 4)) == {("stock", STOCK["instrument_id"]): 4800}
    assert pending_asset_delivery_quantities(transactions, date(2026, 6, 5)) == {}
    assert transaction_affected_dates(transactions[-1]) >= {date(2026, 6, day) for day in (3, 4, 5, 8)}


@pytest.mark.parametrize("kind", ["sell", "short_sell", "transfer_out"])
def test_pending_delivery_cannot_be_disposed_before_broker_delivery(kind):
    transactions = facts()
    disposal = fact(5, kind, "2026-06-04", "stock", 2882919 if kind == "transfer_out" else 2880000,
                    quantity=4800, currency="HKD", instrument_id=STOCK["instrument_id"], instrument_ref=STOCK,
                    transfer_object_type="position" if kind == "transfer_out" else None)
    with pytest.raises(ValueError, match="awaiting delivery"):
        ledger.validate_transaction_position_history("fcn-review", transactions + [disposal], corporate_actions=[])
    disposal.update(trade_date="2026-06-05", trade_at="2026-06-05T10:00:00Z", settlement_date="2026-06-05")
    ledger.validate_transaction_position_history("fcn-review", transactions + [disposal], corporate_actions=[])


def mixed_delivery_facts():
    ordinary_buy = fact(5, "buy", "2026-06-04", "stock", 7000, quantity=10, currency="HKD",
                        instrument_id=STOCK["instrument_id"], instrument_ref=STOCK)
    sale = fact(6, "sell", "2026-06-04", "stock", 3500, quantity=5, currency="HKD",
                instrument_id=STOCK["instrument_id"], instrument_ref=STOCK)
    return facts() + [ordinary_buy, sale]


def test_fifo_disposal_must_select_delivered_lot_when_older_fcn_is_pending():
    transactions = mixed_delivery_facts()
    with pytest.raises(ValueError, match="Select an already delivered opening lot"):
        ledger.validate_transaction_position_history("fcn-review", transactions, corporate_actions=[])
    transactions[-1]["lot_selections"] = [{"opening_transaction_id": "fcn-5", "quantity": 5}]
    ledger.validate_transaction_position_history("fcn-review", transactions, corporate_actions=[])
    stock_lots = [lot for lot in lots(transactions, "2026-06-04") if lot["account_id"] == "stock"]
    fcn_lot = next(lot for lot in stock_lots if lot["opened_by_transaction_id"] == "fcn-4")
    ordinary_lot = next(lot for lot in stock_lots if lot["opened_by_transaction_id"] == "fcn-5")
    assert (fcn_lot["remaining_quantity"], fcn_lot["realized_quantity"]) == (4800, 0)
    assert (ordinary_lot["remaining_quantity"], ordinary_lot["realized_quantity"]) == (5, 5)


def test_selecting_pending_fcn_lot_is_rejected_despite_enough_other_delivered_shares():
    transactions = mixed_delivery_facts()
    transactions[-1]["lot_selections"] = [{"opening_transaction_id": "fcn-4", "quantity": 5}]
    with pytest.raises(ValueError, match="selects FCN shares awaiting delivery"):
        ledger.validate_transaction_position_history("fcn-review", transactions, corporate_actions=[])


def test_moving_average_disposal_waits_until_pending_origin_is_delivered():
    transactions = mixed_delivery_facts()
    with pytest.raises(ValueError, match="Moving-average disposals must wait"):
        ledger.validate_transaction_position_history("fcn-review", transactions, corporate_actions=[], account_cost_methods={"stock": "moving_average"})
    transactions[-1].update(trade_date="2026-06-05", trade_at="2026-06-05T10:00:00Z", settlement_date="2026-06-05")
    ledger.validate_transaction_position_history("fcn-review", transactions, corporate_actions=[], account_cost_methods={"stock": "moving_average"})


@pytest.mark.parametrize("recognition_date", ["2026-06-02", "2026-06-03", "2026-06-04"])
def test_cash_redemption_final_coupon_is_recognized_once_on_its_own_date(recognition_date):
    transactions = facts()
    redemption = transactions[-1]
    redemption.update(asset_deliveries=[], gross_amount=500000, settlement_cashflows=[
        {"kind": "coupon", "amount": 5000, "currency": "USD", "cash_account_id": "usd", "recognition_date": recognition_date, "settlement_date": "2026-06-08"},
        {"kind": "fee", "amount": 10, "currency": "USD", "cash_account_id": "usd", "recognition_date": "2026-06-04", "settlement_date": "2026-06-08"},
    ])
    for day in ("2026-06-02", "2026-06-03", "2026-06-04"):
        closed_or_open = next(lot for lot in lots(transactions, day) if lot["account_id"] == "fcn")
        assert closed_or_open["income_cash_amount"] == (5000 if day >= recognition_date else 0)
        assert closed_or_open["expense_cash_amount"] == (10 if day >= "2026-06-04" else 0)
        assert closed_or_open["realized_pnl"] == 0


@pytest.fixture
def confirmed_market(monkeypatch):
    days = [f"2026-06-{day:02d}" for day in range(1, 9)]
    stock = {**STOCK, "quote_selection_policy": {role: ["close"] for role in ("valuation", "trading", "reference", "total_return")},
             "market_data": [{"metric_family": "price", "quote_basis": "close", "as_of_date": day, "value": 600,
                              "currency": "HKD", "price_unit": "per_unit", "price_scale": 1, "status": "complete"} for day in days]}
    fx = {"instrument_id": "fx-usd-hkd", "instrument_name": "USD/HKD", "instrument_type": "fx", "currency": "HKD",
          "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
          "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
          "market_data": [{"metric_family": "fx", "quote_basis": "spot", "as_of_date": day, "value": 7.8,
                           "currency": "HKD", "price_unit": "rate", "price_scale": 1, "status": "complete"} for day in days]}
    details = {STOCK["instrument_id"]: stock, "fx-usd-hkd": fx}
    for module in (ledger, performance):
        monkeypatch.setattr(module, "get_registry_instrument_detail", lambda key: deepcopy(details.get(key)))
        monkeypatch.setattr(module, "get_registry_instrument_details", lambda keys: {key: deepcopy(details[key]) for key in keys if key in details})
        monkeypatch.setattr(module, "list_registry_corporate_actions", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD", "HKD"], "maintained_pairs": ["USD/HKD"], "rates": [
            {"base_currency": "USD", "quote_currency": "HKD", "source_kind": "direct", "instrument_id": "fx-usd-hkd"},
        ]})
    monkeypatch.setattr(performance, "list_option_delivery_links", lambda *args: [])


def test_nav_contribution_and_materialized_cash_components_follow_recognition(confirmed_market):
    transactions = facts()
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, transactions, end_date=date(2026, 6, 8), include_materialized_rows=True)
    rows = {row["as_of_date"].isoformat(): row for row in snapshots}
    before_coupon = 2880000 / 7.8 + (10000 - 2919) / 7.8 + 100
    ending_nav = before_coupon + 3333.33 - 78 / 7.8
    assert rows["2026-06-03"]["nav"] == pytest.approx(before_coupon)
    for day in (4, 5, 8):
        assert rows[f"2026-06-{day:02d}"]["nav"] == pytest.approx(ending_nav)
    assert rows["2026-06-03"]["income_cash_amount"] == 0
    assert rows["2026-06-04"]["income_cash_amount"] == 3333.33
    components = rows["2026-06-04"]["_calculation_state"]["cash_components"]
    assert sum(row["value"] for row in components if row["field"] == "earnings") == 3333.33
    assert sum(row["value"] for row in components if row["field"] == "fees") == pytest.approx(10)
    report = performance.build_contribution_report(PORTFOLIO, ACCOUNTS, transactions, start_date=date(2026, 6, 2), end_date=date(2026, 6, 8), axis="instrument")
    assert report["summary"]["contribution_residual"] == pytest.approx(0, abs=1e-12)
    line = next(row for row in report["lines"] if row["group_key"] == "fcn-review")
    assert line["income_cash_amount"] == 3333.33
    assert line["expense_cash_amount"] == pytest.approx(10)


def test_delivery_expense_consumes_existing_hkd_historical_basis():
    transactions = facts()
    postings = ledger.derive_ledger_postings("fcn-review", transactions, corporate_actions=[])
    state, impacts = ledger._replay_settled_monetary_postings(
        postings=postings, as_of_date=date(2026, 6, 5), base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={},
        resolve_fx_rate_on=lambda **kwargs: {"rate": 1 if kwargs["base_currency"] == "USD" else 0.125 if kwargs["as_of_date"] == date(2026, 6, 1) else 1 / 7.8, "stale": False},
        impact_transaction_ids={"fcn-4"},
    )
    assert state[("hkd", "HKD")]["amount"] == 7003
    assert state[("hkd", "HKD")]["historical_cost_basis_base"] == pytest.approx(7003 * 0.125)
    paid_hkd = [row for row in impacts if row["currency"] == "HKD"]
    assert sum(row["realized_cash_fx_pnl_base"] for row in paid_hkd) == pytest.approx(2997 * (1 / 7.8 - 0.125))
