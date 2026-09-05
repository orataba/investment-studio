"""End-to-end transaction scenarios with cash/position oracles calculated by hand.

Only isolated fixture accounts are mutated; no user database or external broker is used.
"""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import performance
from portfolio_app.services.ledger import build_position_lots, derive_ledger_postings
from portfolio_app.services.option_obligations import open_option_obligations
from portfolio_app.services.portfolio_store import list_accounts, list_transactions
from portfolio_app.services.transaction_csv import parse_transaction_csv, render_transaction_csv
from portfolio_app.services.transaction_xlsx import XLSX_MEDIA_TYPE, render_transaction_xlsx, transaction_xlsx_to_csv


PORTFOLIO = "investment-studio"
BASE = f"/api/portfolios/{PORTFOLIO}"


def test_api_rejects_missing_or_blank_mutation_identity(client):
    from fastapi.testclient import TestClient
    with TestClient(client.app) as raw:
        for path in ("/transactions", "/options/outcomes", "/transactions/internal-transfer"):
            response = raw.post(BASE + path, json={})
            assert response.status_code == 422
            assert any("Idempotency-Key" in str(error["loc"]) for error in response.json()["detail"])


def account(client, category, cash_id=None, currency="USD"):
    response = client.post(f"{BASE}/accounts", json={
        "account_name": f"Scenario {category}", "account_category": category,
        "currency": currency, "institution": "Scenario Broker", "status": "active",
        "opened_at": "2026-01-02", "cost_basis_method": None if category == "cash" else "fifo",
        "default_settlement_cash_account_id": cash_id,
    })
    assert response.status_code == 200, response.text
    return response.json()["account_id"]


def post(client, **values):
    response = client.post(f"{BASE}/transactions", json={"currency": "USD", **values})
    assert response.status_code == 200, response.text
    return response.json()


def funded(client, amount=10000):
    cash = account(client, "cash")
    post(client, transaction_type="opening_balance", trade_date="2026-01-02",
         account_id=cash, gross_amount=amount)
    return cash


def test_selected_lot_sale_and_file_rebinding(client):
    cash = funded(client)
    stock = account(client, "security", cash)
    entries = [post(client, transaction_type="buy", trade_date=day, account_id=stock,
                    instrument_id="equity-us-abbv", quantity=10, price=price,
                    gross_amount=10*price, settlement_cash_account_id=cash)
               for day, price in [("2026-01-05", 100), ("2026-01-06", 120)]]
    disposed = post(client, transaction_type="sell", trade_date="2026-01-07", account_id=stock,
                    instrument_id="equity-us-abbv", quantity=5, price=130, gross_amount=650,
                    settlement_cash_account_id=cash, fees=2,
                    lot_selections=[{"opening_transaction_id": entries[1]["transaction_id"], "quantity": 5}])
    facts = history(cash, stock)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_cost_basis"] for lot in lots) == 1600
    assert sum(lot["realized_pnl"] for lot in lots) == 48
    assert next(lot for lot in lots if lot["opened_by_transaction_id"] == entries[0]["transaction_id"])["remaining_quantity"] == 10
    assert sum(row.get("cost_basis_delta") or 0 for row in derive_ledger_postings(PORTFOLIO, facts) if row.get("instrument_id") == "equity-us-abbv") == 1600
    clone_cash, clone_stock = account(client, "cash"), None
    clone_stock = account(client, "security", clone_cash)
    copied = deepcopy(facts)
    for record in copied:
        record["account_id"] = clone_cash if record["account_id"] == cash else clone_stock
        if record.get("settlement_cash_account_id"):
            record["settlement_cash_account_id"] = clone_cash
        record["source_system"] = "lot-scenario"
        record["external_reference"] = record["transaction_id"]
    csv = render_transaction_csv(copied)
    files = {"file": ("lot-roundtrip.csv", csv.encode(), "text/csv")}
    preview = client.post(f"{BASE}/transactions/files/preview", files=files)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["error_count"] == 0, body
    imported = client.post(f"{BASE}/transactions/files/import", files=files,
        data={"preview_digest": body["preview_digest"]}, headers={"Idempotency-Key": "selected-lot-file"})
    assert imported.status_code == 200, imported.text
    clone_lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), history(clone_cash, clone_stock), resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_cost_basis"] for lot in clone_lots) == 1600
    assert sum(lot["realized_pnl"] for lot in clone_lots) == 48
    invalid = client.post(f"{BASE}/transactions", json={"currency": "USD", "transaction_type": "sell", "trade_date": "2026-01-08", "account_id": stock,
        "instrument_id": "equity-us-abbv", "quantity": 6, "price": 130, "gross_amount": 780,
        "settlement_cash_account_id": cash, "lot_selections": [{"opening_transaction_id": entries[1]["transaction_id"], "quantity": 6}]})
    assert invalid.status_code >= 400
    assert len(history(cash, stock)) == len(facts)


def test_financing_collateral_interest_and_actual_liquidation(client):
    cash = funded(client)
    stock = account(client, "security", cash)
    debt, collateral = account(client, "cash"), account(client, "cash")
    assert client.patch(f"{BASE}/accounts/{debt}", json={"cash_purpose": "financing"}).status_code == 200
    assert client.patch(f"{BASE}/accounts/{collateral}", json={"cash_purpose": "collateral", "collateral_reference": "Broker collateral confirmation QA-01"}).status_code == 200

    def transfer(source, target, amount, day):
        response = client.post(f"{BASE}/transactions/internal-transfer", json={"trade_date": day, "from_account_id": source, "to_account_id": target,
            "transfer_object_type": "cash", "gross_amount": amount, "note": "Actual broker financing/collateral movement"})
        assert response.status_code == 200, response.text
    transfer(debt, cash, 12000, "2026-01-05")
    transfer(cash, collateral, 4000, "2026-01-05")
    initial = history(cash, debt, collateral)
    assert cash_balance(initial, debt) == -12000
    assert cash_balance(initial, collateral) == 4000
    assert sum(cash_balance(initial, item) for item in (cash, debt, collateral)) == 10000
    from portfolio_app.services.holdings_market_profile import build_cash_holding_rows
    rows = build_cash_holding_rows(cash_balances=[{"account_id": collateral, "currency": "USD", "amount": 4000, "amount_base": 4000}, {"account_id": debt, "currency": "USD", "amount": -12000, "amount_base": -12000}],
        account_lookup={item["account_id"]: item for item in list_accounts(PORTFOLIO)}, as_of_date=date(2026,1,5), previous_as_of_date=date(2026,1,2), base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={}, cash_day_change=lambda **kwargs: {})
    assert not rows[0]["available_for_trading"]
    assert next(row for row in rows if row["account_id"] == debt)["financing_liability"] == 12000
    post(client, transaction_type="buy", trade_date="2026-01-06", account_id=stock, instrument_id="equity-us-abbv", quantity=100, price=160, gross_amount=16000, fees=10, settlement_cash_account_id=cash)
    post(client, transaction_type="fee", trade_date="2026-01-07", account_id=cash, gross_amount=30, fee_category="financing_interest", note="Actual financing interest debit")
    post(client, transaction_type="sell", trade_date="2026-01-08", account_id=stock, instrument_id="equity-us-abbv", quantity=100, price=130, gross_amount=13000, fees=10, settlement_cash_account_id=cash, note="Broker-confirmed forced liquidation, actual execution")
    transfer(collateral, cash, 4000, "2026-01-09")
    transfer(cash, debt, 12000, "2026-01-09")
    final = history(cash, stock, debt, collateral)
    assert [cash_balance(final, item) for item in (cash, debt, collateral)] == [6950, 0, 0]


def test_open_stock_short_borrow_fee_is_an_expense_not_income(client):
    cash = funded(client)
    stock = account(client, "security", cash)
    post(client, transaction_type="short_sell", trade_date="2026-01-05", account_id=stock, instrument_id="equity-us-abbv", quantity=100, price=100, gross_amount=10000, fees=2, settlement_cash_account_id=cash)
    post(client, transaction_type="fee", trade_date="2026-01-06", account_id=stock, instrument_id="equity-us-abbv", gross_amount=15, fee_category="borrow_fee", settlement_cash_account_id=cash)
    facts = history(cash, stock)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert cash_balance(facts, cash) == 19983
    assert lots[0]["expense_cash_amount"] == 15
    assert lots[0]["opening_transaction_type"] == "short_sell"


@pytest.mark.parametrize("entry", ["outcome", "import"])
def test_same_day_afternoon_cover_purchase_precedes_actual_assignment(client, entry):
    cash = funded(client, 20000)
    writer = account(client, "option", cash)
    stock = account(client, "security", cash)
    post(client, transaction_type="option_write", trade_date="2026-01-05", account_id=writer,
         settlement_cash_account_id=cash, derivative_contract_id="scenario-option", derivative_contract=option(),
         quantity=1, price=5, gross_amount=500)
    post(client, transaction_type="buy", trade_date="2026-01-09", trade_time="15:00", settlement_date="2026-01-12",
         account_id=stock, settlement_cash_account_id=cash, instrument_id="equity-us-abbv",
         quantity=100, price=110, gross_amount=11000, fees=2)
    if entry == "outcome":
        response = client.post(BASE + "/options/outcomes", json={
            "derivative_contract_id": "scenario-option", "side": "written", "outcome": "physical",
            "quantity": 1, "event_date": "2026-01-09", "trade_time": "16:00", "settlement_date": "2026-01-12",
            "stock_account_id": stock, "settlement_cash_account_id": cash, "fees": 3,
            "note": "Broker confirmation: stock acquired at 15:00, assignment at 16:00",
        })
        assert response.status_code == 200, response.text
    else:
        response = client.post(BASE + "/transaction-imports/preview", json={"source_system": "audit", "records": [{
            "external_reference": "assignment-16h",
            "asset_type": "option", "transaction_action": "physical_written", "trade_date": "2026-01-09",
            "trade_time": "16:00", "settlement_date": "2026-01-12", "account_id": writer,
            "derivative_contract_id": "scenario-option", "quantity": 1, "gross_amount": 0, "currency": "USD",
            "option_delivery": {"stock_account_id": stock, "settlement_cash_account_id": cash, "fees": 3},
            "note": "Broker actual assignment 16:00",
        }]})
        assert response.status_code == 200, response.text
        assert response.json()["error_count"] == 0, response.text
        command = response.json()["rows"][0]["command"]
        response = client.post(BASE + "/transaction-imports/commit", headers={"Idempotency-Key": "afternoon-assignment"}, json={
            "source_system": "audit", "records": [command], "preview_digest": response.json()["preview_digest"],
        })
        assert response.status_code == 200, response.text
    facts = history(cash, writer, stock)
    delivered = next(row for row in facts if row["account_id"] == stock and row["transaction_type"] == "sell")
    assert delivered["trade_time"] == "16:00"
    assert delivered["trade_time_is_estimated"] is False
    assert cash_balance(facts, cash) == 19495
    assert open_option_obligations(facts) == []
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_quantity"] for lot in lots) == 0
    assert sum(lot["realized_pnl"] for lot in lots) == -1005

def test_same_key_protects_cash_but_a_new_key_records_a_new_fact(client):
    cash = account(client, "cash")
    payload = {"transaction_type": "deposit", "account_id": cash, "trade_date": "2026-01-05",
               "gross_amount": 1000, "currency": "USD"}
    first = client.post(BASE + "/transactions", json=payload, headers={"Idempotency-Key": "first-submit"})
    replay = client.post(BASE + "/transactions", json=payload, headers={"Idempotency-Key": "first-submit"})
    retry_new_key = client.post(BASE + "/transactions", json=payload, headers={"Idempotency-Key": "new-ui-retry"})
    assert first.status_code == replay.status_code == retry_new_key.status_code == 200
    assert first.json()["transaction_id"] == replay.json()["transaction_id"]
    assert first.json()["transaction_id"] != retry_new_key.json()["transaction_id"]
    assert cash_balance(history(cash), cash) == 2000

def test_fcn_receipt_covers_an_existing_stock_short_without_fake_cash(client):
    cash = funded(client, 200000)
    holder = account(client, "fcn", cash)
    stock = account(client, "security", cash)
    post(client, transaction_type="short_sell", trade_date="2026-01-05", account_id=stock,
         instrument_id="equity-us-abbv", quantity=100, price=100, gross_amount=10000, settlement_cash_account_id=cash)
    contract = {"derivative_contract_id": "cover-fcn", "contract_name": "Delivery FCN", "contract_type": "fcn",
                "terms": {"notional": 100000, "issue_date": "2026-01-02", "maturity_date": "2026-01-30",
                          "settlement_type": "physical", "issuer": "QA", "counterparty": "QA",
                          "underlyings": [{"instrument_id": "equity-us-abbv", "deliverable": True}]}}
    post(client, transaction_type="buy", trade_date="2026-01-06", account_id=holder, derivative_contract_id="cover-fcn",
         derivative_contract=contract, quantity=1, price=100000, gross_amount=100000, settlement_cash_account_id=cash)
    post(client, transaction_type="maturity_redemption", lifecycle_event_type="fcn_knock_in", trade_date="2026-01-30",
         account_id=holder, derivative_contract_id="cover-fcn", quantity=1, gross_amount=0,
         asset_deliveries=[{"account_id": stock, "instrument_id": "equity-us-abbv", "quantity": 100,
                            "fair_value": 7500, "currency": "USD", "fx_rate_to_contract": 1}],
         note="Actual stock delivery closes the existing short")
    facts = history(cash, holder, stock)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_quantity"] for lot in lots if lot["account_id"] == stock) == 0
    assert sum(lot["realized_pnl"] for lot in lots) == -90000
    assert cash_balance(facts, cash) == 110000


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_physical_option_selected_lot_fee_and_time_survive_file_round_trip(client, file_format):
    cash = funded(client, 30000)
    holder = account(client, "option", cash)
    stock = account(client, "security", cash)
    entries = [post(client, transaction_type="buy", trade_date=day, account_id=holder,
        settlement_cash_account_id=cash, derivative_contract_id="scenario-option", derivative_contract=option(),
        quantity=1, price=price, gross_amount=100 * price)
        for day, price in [("2026-01-05", 3), ("2026-01-06", 7)]]
    payload = {"derivative_contract_id": "scenario-option", "side": "long", "outcome": "physical",
        "quantity": 1, "event_date": "2026-01-08", "trade_time": "16:00", "settlement_date": "2026-01-12",
        "stock_account_id": stock, "settlement_cash_account_id": cash, "fees": 3, "fee_category": "transaction_cost",
        "lot_selections": [{"opening_transaction_id": entries[1]["transaction_id"], "quantity": 1}]}
    for invalid_lots in [
        [{"opening_transaction_id": entries[1]["transaction_id"], "quantity": 2}],
        [{"opening_transaction_id": entries[1]["transaction_id"], "quantity": "0.5"}] * 2,
    ]:
        rejected = client.post(BASE + "/options/outcomes", json={**payload, "lot_selections": invalid_lots})
        assert rejected.status_code == 422, rejected.text
        assert len(history(cash, holder, stock)) == 3
    response = client.post(BASE + "/options/outcomes", json=payload)
    assert response.status_code == 200, response.text
    facts = history(cash, holder, stock)
    delivered = next(row for row in facts if row["account_id"] == stock)
    assert (delivered["fee_category"], delivered["trade_time"]) == ("transaction_cost", "16:00")
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert next(lot for lot in lots if lot["opened_by_transaction_id"] == entries[0]["transaction_id"])["remaining_cost_basis"] == 300
    assert next(lot for lot in lots if lot["opened_by_transaction_id"] == entries[1]["transaction_id"])["realized_pnl"] == -700
    assert sum(lot["remaining_cost_basis"] for lot in lots if lot["account_id"] == stock) == 10003
    assert cash_balance(facts, cash) == 18997

    from portfolio_app.api.routes.transactions import _export_transaction_records
    records = [deepcopy(row) for row in _export_transaction_records(PORTFOLIO) if row["account_id"] in {cash, holder, stock}]
    new_cash = account(client, "cash")
    new_holder = account(client, "option", new_cash)
    new_stock = account(client, "security", new_cash)
    mapping = {cash: new_cash, holder: new_holder, stock: new_stock}
    for row in records:
        row["account_id"] = mapping[row["account_id"]]
        row["settlement_cash_account_id"] = mapping.get(row.get("settlement_cash_account_id"))
        row["source_system"] = "selected-option-roundtrip"
        row["external_reference"] = row["transaction_id"]
        if row.get("derivative_contract"):
            row["derivative_contract_id"] = "copied-option"
            row["derivative_contract"]["derivative_contract_id"] = "copied-option"
        if row.get("option_delivery"):
            row["option_delivery"]["stock_account_id"] = new_stock
            row["option_delivery"]["settlement_cash_account_id"] = new_cash
    content = render_transaction_csv(records).encode() if file_format == "csv" else render_transaction_xlsx(records)
    file = (f"selected-option.{file_format}", content, "text/csv" if file_format == "csv" else XLSX_MEDIA_TYPE)
    preview = client.post(BASE + "/transactions/files/preview", files={"file": file})
    assert preview.status_code == 200, preview.text
    assert preview.json()["error_count"] == 0, preview.text
    imported = client.post(BASE + "/transactions/files/import", files={"file": file},
        headers={"Idempotency-Key": "selected-option-roundtrip"}, data={"preview_digest": preview.json()["preview_digest"]})
    assert imported.status_code == 200, imported.text
    copied = history(new_cash, new_holder, new_stock)
    assert cash_balance(copied, new_cash) == 18997
    copied_stock = next(row for row in copied if row["account_id"] == new_stock)
    assert (copied_stock["fee_category"], copied_stock["trade_time"]) == ("transaction_cost", "16:00")
    copied_lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), copied, resolve_pricing=False, pricing_map={})
    assert sum(lot["realized_pnl"] for lot in copied_lots) == -700
    assert sum(lot["remaining_cost_basis"] for lot in copied_lots) == 10303
    post(client, transaction_type="sell", trade_date="2026-01-09", trade_time="15:00", settlement_date="2026-01-12",
        account_id=new_stock, instrument_id="equity-us-abbv", quantity=100, price=120, gross_amount=12000,
        fees=2, settlement_cash_account_id=new_cash)
    expired = client.post(BASE + "/options/outcomes", json={"derivative_contract_id": "copied-option", "side": "long",
        "outcome": "expired", "quantity": 1, "event_date": "2026-01-09", "trade_time": "16:00", "settlement_date": "2026-01-09"})
    assert expired.status_code == 200, expired.text
    final = history(new_cash, new_holder, new_stock)
    final_lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), final, resolve_pricing=False, pricing_map={})
    assert cash_balance(final, new_cash) == 30995
    assert sum(lot["remaining_quantity"] for lot in final_lots) == 0
    assert sum(lot["realized_pnl"] for lot in final_lots) == 995


def test_documented_fcn_csv_import_records_non_cash_delivery(client):
    import csv
    from io import StringIO
    from pathlib import Path
    from portfolio_app.services.transaction_csv import IMPORT_COLUMNS

    cash = funded(client, 200000)
    holder = account(client, "fcn", cash)
    stock = account(client, "security", cash)
    sample = Path(__file__).resolve().parents[4] / "docs/examples/transaction_import_stock_fund_option_fcn.csv"
    reader = csv.DictReader(StringIO(sample.read_text()))
    assert reader.fieldnames == list(IMPORT_COLUMNS)
    rows = list(reader)
    assert not any(row["external_reference"] == "FCN-STOCK-001" for row in rows)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=IMPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(row for row in rows if row["asset_type"] == "fcn")
    content = output.getvalue().replace("cash-usd-main", cash).replace("broker-us-fcn", holder).replace("broker-us-core", stock).replace("equity-demo-001", "equity-us-abbv")
    file = ("documented-fcn.csv", content.encode(), "text/csv")
    preview = client.post(BASE + "/transactions/files/preview", files={"file": file})
    assert preview.status_code == 200, preview.text
    assert preview.json()["error_count"] == 0, preview.text
    committed = client.post(BASE + "/transactions/files/import", files={"file": file},
        headers={"Idempotency-Key": "documented-fcn"}, data={"preview_digest": preview.json()["preview_digest"]})
    assert committed.status_code == 200, committed.text
    facts = history(cash, holder, stock)
    assert len(facts) == 4  # Initial cash, actual FCN entry, coupon and non-cash redemption.
    assert cash_balance(facts, cash) == 102000
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_cost_basis"] for lot in lots) == 75000
    assert sum(lot["realized_pnl"] for lot in lots) == -25000


def option(contract_id="scenario-option", **terms):
    return {
        "derivative_contract_id": contract_id, "contract_name": contract_id,
        "contract_type": "option", "terms": {
            "underlying_instrument_id": "equity-us-abbv", "option_type": "call",
            "expiry_date": "2026-01-09", "strike": 100, "contract_multiplier": 100,
            "settlement_type": "physical", "exercise_style": "american", "strike_currency": "USD",
            "terms_reference": "Scenario broker confirmation", **terms,
        },
    }


def history(*accounts):
    return [row for row in list_transactions(PORTFOLIO) if row["account_id"] in accounts]


def cash_balance(transactions, cash_id, as_of=None):
    postings = derive_ledger_postings(
        PORTFOLIO, transactions, account_currency_map={cash_id: "USD"}, as_of_date=as_of,
    )
    return sum(row.get("cash_amount_delta") or 0 for row in postings if row["account_id"] == cash_id)


def test_fcn_physical_settlement_cash_fraction_and_later_stock_sale(client):
    cash = funded(client, 200000)
    holder = account(client, "fcn", cash)
    stock = account(client, "security", cash)
    contract = {"derivative_contract_id": "physical-fcn", "contract_name": "Physical FCN",
                "contract_type": "fcn", "terms": {"notional": 100000, "annual_coupon_rate_pct": 12,
                "issue_date": "2026-01-02", "maturity_date": "2026-01-30", "settlement_type": "physical",
                "issuer": "Scenario issuer", "counterparty": "Scenario broker",
                "underlyings": [{"instrument_id": "equity-us-abbv", "deliverable": True}]}}
    post(client, transaction_type="buy", trade_date="2026-01-05", account_id=holder,
         derivative_contract_id="physical-fcn", derivative_contract=contract,
         quantity=1, price=100000, gross_amount=100000, settlement_cash_account_id=cash)
    deliveries = [{"account_id": stock, "instrument_id": "equity-us-abbv", "quantity": 999,
                   "fair_value": 74925, "currency": "USD", "fx_rate_to_contract": 1}]
    redemption = post(client, transaction_type="maturity_redemption", lifecycle_event_type="fcn_knock_in",
         trade_date="2026-01-30", settlement_date="2026-02-02", account_id=holder,
         derivative_contract_id="physical-fcn", quantity=1, gross_amount=75,
         settlement_cash_account_id=cash, asset_deliveries=deliveries, note="Broker final settlement: 999 shares at fair value 75, cash in lieu 75")
    assert redemption["asset_deliveries"][0]["fair_value"] == "74925"
    transactions = history(cash, holder, stock)
    assert cash_balance(transactions, cash) == 100075
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(lot["realized_pnl"] for lot in lots if lot["account_id"] == holder) == -25000
    shares = [lot for lot in lots if lot["account_id"] == stock][0]
    assert (shares["remaining_quantity"], shares["remaining_cost_basis"]) == (999, 74925)
    assert shares["opened_by_transaction_id"] == redemption["transaction_id"]
    assert redemption["transaction_id"] in {row["transaction_id"] for row in list_transactions(PORTFOLIO, account_id=stock, asset_domain="security", position_reference_id="equity-us-abbv")}
    csv_text = render_transaction_csv(transactions)
    _, rows = parse_transaction_csv(csv_text, default_source_system="physical-roundtrip")
    assert all(not row.errors for row in rows), [row.errors for row in rows]
    assert any(row.transaction and row.transaction.asset_deliveries for row in rows)
    post(client, transaction_type="sell", trade_date="2026-02-03", account_id=stock,
         instrument_id="equity-us-abbv", quantity=999, price=80, gross_amount=79920,
         settlement_cash_account_id=cash, fees=20)
    transactions = history(cash, holder, stock)
    assert cash_balance(transactions, cash) == 179975
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(lot["realized_pnl"] for lot in lots) == -20025


def test_option_premium_currency_is_separate_from_stock_delivery_currency(client):
    usd = funded(client)
    hkd = account(client, "cash", currency="HKD")
    post(client, transaction_type="opening_balance", trade_date="2026-01-02", account_id=hkd, gross_amount=5000, currency="HKD")
    holder = account(client, "option", usd)
    stock = account(client, "security", hkd, currency="HKD")
    contract = option("cross-premium", underlying_instrument_id="fund-hk-2800", strike=20, strike_currency="HKD")
    post(client, transaction_type="buy", trade_date="2026-01-05", account_id=holder, derivative_contract_id="cross-premium", derivative_contract=contract, quantity=1, price=2, gross_amount=200, settlement_cash_account_id=usd)
    response = client.post(f"{BASE}/options/outcomes", json={"derivative_contract_id": "cross-premium", "side": "long", "outcome": "physical", "quantity": 1,
        "event_date": "2026-01-09", "settlement_date": "2026-01-12", "stock_account_id": stock, "settlement_cash_account_id": hkd, "fees": 3})
    assert response.status_code == 200, response.text
    facts = history(usd, hkd, holder, stock)
    assert cash_balance(facts, usd) == 9800
    assert cash_balance(facts, hkd) == 2997
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    received = next(lot for lot in lots if lot["account_id"] == stock)
    assert (received["currency"], received["remaining_quantity"], received["remaining_cost_basis"]) == ("HKD", 100, 2003)
    terms = {**contract["terms"], "strike_currency": "USD"}
    changed = client.patch(f"{BASE}/derivative-contracts/cross-premium", json={"expected_row_version": 1, "terms": terms, "reason": "Conflicting strike currency", "reviewed_by": "QA"})
    assert changed.status_code == 400


def test_fcn_multiple_non_cash_deliveries_keep_native_cost_and_explicit_fx(client):
    usd = funded(client, 200000)
    hkd = account(client, "cash", currency="HKD")
    holder = account(client, "fcn", usd)
    us_stock = account(client, "security", usd)
    hk_stock = account(client, "security", hkd, currency="HKD")
    contract = {"derivative_contract_id": "multi-fcn", "contract_name": "Multi delivery", "contract_type": "fcn", "terms": {
        "notional": 100000, "annual_coupon_rate_pct": 10, "issue_date": "2026-01-02", "maturity_date": "2026-02-20", "settlement_type": "physical", "issuer": "QA", "counterparty": "QA",
        "underlyings": [{"instrument_id": "equity-us-abbv", "deliverable": True}, {"instrument_id": "fund-hk-2800", "deliverable": True}]}}
    post(client, transaction_type="buy", trade_date="2026-01-05", account_id=holder, derivative_contract_id="multi-fcn", derivative_contract=contract, quantity=1, price=100000, gross_amount=100000, settlement_cash_account_id=usd)
    post(client, transaction_type="maturity_redemption", lifecycle_event_type="fcn_knock_in", trade_date="2026-02-20", account_id=holder, derivative_contract_id="multi-fcn", quantity=1, gross_amount=0, note="Confirmed issuer multi-asset delivery; HKD translated at 7.8 per USD", asset_deliveries=[
        {"account_id": us_stock, "instrument_id": "equity-us-abbv", "quantity": 500, "fair_value": 62500, "currency": "USD", "fx_rate_to_contract": 1},
        {"account_id": hk_stock, "instrument_id": "fund-hk-2800", "quantity": 5000, "fair_value": 97500, "currency": "HKD", "fx_rate_to_contract": "0.128205128205128205"},
    ])
    facts = history(usd, hkd, holder, us_stock, hk_stock)
    assert cash_balance(facts, usd) == 100000
    assert cash_balance(facts, hkd) == 0
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert sum(lot["realized_pnl"] for lot in lots if lot["account_id"] == holder) == pytest.approx(-25000)
    assert next(lot for lot in lots if lot["account_id"] == hk_stock)["remaining_cost_basis"] == 97500


def test_existing_written_position_partial_close_then_expiry_has_no_duplicate_premium(client, monkeypatch):
    cash = funded(client)
    writer = account(client, "option", cash)
    opened = post(client, transaction_type="option_opening_balance", trade_date="2026-01-02",
                  acquisition_date="2025-12-15", account_id=writer, quantity=2, gross_amount=1000,
                  derivative_contract_id="scenario-option", derivative_contract=option())
    assert opened["flow_scope"] == "bootstrap"
    assert opened["net_cash_effect"] == 0
    transactions = history(cash, writer)
    assert cash_balance(transactions, cash) == 10000
    initial = open_option_obligations(transactions)[0]
    assert initial["carrying_liability"] == 1000
    assert initial["opened_at"] == "2025-12-15"
    initial_accounts = [row for row in list_accounts(PORTFOLIO) if row["account_id"] in {cash, writer}]
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD"], "rates": []})
    snapshots = performance.build_daily_portfolio_snapshots(
        {"portfolio_id": PORTFOLIO, "base_currency": "USD", "inception_date": "2026-01-02",
         "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"},
        initial_accounts, transactions, start_date=date(2026, 1, 2), end_date=date(2026, 1, 2),
    )
    assert snapshots[0]["ending_nav"] == 9000  # 10,000 cash minus 1,000 opening liability.

    post(client, transaction_type="option_buy_to_close", trade_date="2026-01-05",
         account_id=writer, settlement_cash_account_id=cash, quantity=1, price=2,
         gross_amount=200, fees=2, derivative_contract_id="scenario-option")
    transactions = history(cash, writer)
    remaining = open_option_obligations(transactions)[0]
    assert remaining["remaining_quantity"] == 1
    assert remaining["premium_basis_remaining"] == 500
    assert remaining["realized_pnl"] == 298
    assert cash_balance(transactions, cash) == 9798
    response = client.post(f"{BASE}/options/outcomes", json={
        "derivative_contract_id": "scenario-option", "side": "written", "outcome": "expired",
        "quantity": 1, "event_date": "2026-01-09", "settlement_date": "2026-01-09",
    })
    assert response.status_code == 200, response.text
    transactions = history(cash, writer)
    assert open_option_obligations(transactions) == []
    assert cash_balance(transactions, cash) == 9798  # Total wealth gain: 798, not 1,798.
    snapshots = performance.build_daily_portfolio_snapshots(
        {"portfolio_id": PORTFOLIO, "base_currency": "USD", "inception_date": "2026-01-02",
         "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"},
        initial_accounts, transactions, start_date=date(2026, 1, 2), end_date=date(2026, 1, 9),
    )
    assert snapshots[-1]["ending_nav"] == 9798


def test_contract_amendment_is_audited_and_cannot_invalidate_existing_outcome(client):
    cash = funded(client)
    holder = account(client, "option", cash)
    contract = option(settlement_type=None, exercise_style=None)
    post(client, transaction_type="option_write", trade_date="2026-01-05", account_id=holder,
         quantity=1, price=5, gross_amount=500, derivative_contract_id="scenario-option",
         derivative_contract=contract, settlement_cash_account_id=cash)
    response = client.post(f"{BASE}/options/outcomes", json={
        "derivative_contract_id": "scenario-option", "side": "written", "outcome": "cash_settled",
        "quantity": 1, "event_date": "2026-01-09", "settlement_date": "2026-01-12",
        "cash_settlement_amount": 600, "settlement_cash_account_id": cash,
    })
    assert response.status_code == 200, response.text
    payload = {"expected_row_version": 1, "terms": {**contract["terms"], "settlement_type": "cash"},
               "reason": "Issuer term sheet confirms cash settlement", "reviewed_by": "Scenario reviewer"}
    response = client.patch(f"{BASE}/derivative-contracts/scenario-option", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["row_version"] == 2
    assert response.json()["amendments"][0]["before"]["settlement_type"] is None
    assert client.patch(f"{BASE}/derivative-contracts/scenario-option", json=payload).status_code == 409
    response = client.patch(f"{BASE}/derivative-contracts/scenario-option", json={
        **payload, "expected_row_version": 2, "terms": {**payload["terms"], "settlement_type": "physical"},
    })
    assert response.status_code == 409
    assert "cash settlement" in response.text
    assert cash_balance(history(cash, holder), cash) == 9900


@pytest.mark.parametrize("cost_method", ["fifo", "moving_average"])
def test_stock_short_partial_cover_fifo_and_average(client, monkeypatch, cost_method):
    cash = funded(client, 20000)
    stock = account(client, "security", cash)
    if cost_method == "moving_average":
        current = next(row for row in list_accounts(PORTFOLIO) if row["account_id"] == stock)
        response = client.patch(f"{BASE}/accounts/{stock}", json={key: value for key, value in {**current, "cost_basis_method": cost_method}.items() if key in {"account_name", "account_category", "institution", "default_settlement_cash_account_id", "cost_basis_method", "opened_at", "closed_at", "status"}})
        assert response.status_code == 200, response.text
    post(client, transaction_type="short_sell", trade_date="2026-01-05", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=100, price=100, gross_amount=10000, fees=20)
    post(client, transaction_type="short_sell", trade_date="2026-01-06", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=100, price=120, gross_amount=12000, fees=20)
    post(client, transaction_type="buy_to_cover", trade_date="2026-01-07", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=150, price=90, gross_amount=13500, fees=30)
    transactions = history(cash, stock)
    assert cash_balance(transactions, cash) == 28430
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    # FIFO: first 100 at 99.8 and next 50 at 119.8, cover all 150 at 90.2.
    # Average: 150 at 109.8 against 90.2. Remaining 50 are a negative liability basis.
    expected = (2440, -5990) if cost_method == "fifo" else (2940, -5490)
    assert sum(lot["remaining_quantity"] for lot in lots) == -50
    assert sum(lot["remaining_cost_basis"] for lot in lots) == expected[1]
    assert sum(lot["realized_pnl"] for lot in lots) == expected[0]
    assert all(lot["position_side"] == "short" for lot in lots)
    from portfolio_app.services.ledger import build_portfolio_positions
    from portfolio_app.services import ledger
    from tests.conftest import _get_registry_instrument_detail, _market_point
    def scenario_detail(instrument_id):
        detail = _get_registry_instrument_detail(instrument_id)
        if detail and instrument_id == "equity-us-abbv":
            detail["market_data"] = [_market_point("price", "close", f"2026-01-{day:02d}", "90", "USD") for day in (2, 5, 6, 7, 8)]
        return detail
    monkeypatch.setattr(ledger, "get_registry_instrument_details", lambda ids: {key: scenario_detail(key) for key in ids})
    monkeypatch.setattr(performance, "get_registry_instrument_detail", scenario_detail)
    monkeypatch.setattr(performance, "get_registry_instrument_details", lambda ids: {key: scenario_detail(key) for key in ids})
    positions = build_portfolio_positions(PORTFOLIO, list_accounts(PORTFOLIO), transactions, as_of_date=date(2026, 1, 7))
    assert positions[0]["quantity"] == -50
    assert positions[0]["market_value"] == -4500
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD"], "rates": []})
    snapshot_accounts = [row for row in list_accounts(PORTFOLIO) if row["account_id"] in {cash, stock}]
    snapshots = performance.build_daily_portfolio_snapshots(
        {"portfolio_id": PORTFOLIO, "base_currency": "USD", "inception_date": "2026-01-02",
         "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"},
        snapshot_accounts, transactions, start_date=date(2026, 1, 2), end_date=date(2026, 1, 7),
    )
    assert snapshots[-1]["ending_nav"] == pytest.approx(28430 + positions[0]["market_value"])
    post(client, transaction_type="buy_to_cover", trade_date="2026-01-08", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=50, price=130, gross_amount=6500, fees=10)
    transactions = history(cash, stock)
    assert cash_balance(transactions, cash) == 21920
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(lot["realized_pnl"] for lot in lots) == pytest.approx(1920)


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_naked_call_assignment_creates_short_shares_then_actual_cover(client, file_format):
    cash = funded(client, 20000)
    writer = account(client, "option", cash)
    stock = account(client, "security", cash)
    post(client, transaction_type="option_write", trade_date="2026-01-05", account_id=writer,
         settlement_cash_account_id=cash, quantity=1, price=5, gross_amount=500,
         derivative_contract_id="scenario-option", derivative_contract=option())
    response = client.post(f"{BASE}/options/outcomes", headers={"Idempotency-Key": "assigned-then-covered"}, json={
        "derivative_contract_id": "scenario-option", "side": "written", "outcome": "physical",
        "quantity": 1, "event_date": "2026-01-09", "settlement_date": "2026-01-12",
        "stock_account_id": stock, "settlement_cash_account_id": cash, "allow_stock_short": True,
        "fees": 3, "note": "Broker confirmed assignment and borrowed stock delivery",
    })
    assert response.status_code == 200, response.text
    transactions = history(cash, stock, writer)
    assert cash_balance(transactions, cash) == 30497
    assert open_option_obligations(transactions) == []
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_quantity"] for lot in lots) == -100
    post(client, transaction_type="buy_to_cover", trade_date="2026-01-12", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=100, price=110,
         gross_amount=11000, fees=2)
    transactions = history(cash, stock, writer)
    assert cash_balance(transactions, cash) == 19495
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(lot["remaining_quantity"] for lot in lots) == 0
    assert sum(lot["realized_pnl"] for lot in lots) == -1005

    from portfolio_app.api.routes.transactions import _export_transaction_records
    exported = client.get(f"{BASE}/transactions.{file_format}")
    assert exported.status_code == 200, exported.text
    records = [deepcopy(row) for row in _export_transaction_records(PORTFOLIO) if row["account_id"] in {cash, writer, stock}]
    assert len(records) == 4  # Cash opening, write, atomic assignment, actual cover.
    assert sum(bool(row.get("option_delivery")) for row in records) == 1
    new_cash = account(client, "cash")
    new_writer = account(client, "option", new_cash)
    new_stock = account(client, "security", new_cash)
    mapping = {cash: new_cash, writer: new_writer, stock: new_stock}
    for row in records:
        row["account_id"] = mapping[row["account_id"]]
        row["settlement_cash_account_id"] = mapping.get(row.get("settlement_cash_account_id"))
        row["source_system"] = "scenario-delivery-roundtrip"
        row["external_reference"] = f"{file_format}-{row['transaction_id']}"
        if row.get("derivative_contract"):
            row["derivative_contract_id"] = "roundtrip-option"
            row["derivative_contract"]["derivative_contract_id"] = "roundtrip-option"
        if row.get("option_delivery"):
            row["option_delivery"]["stock_account_id"] = new_stock
            row["option_delivery"]["settlement_cash_account_id"] = new_cash
    content = render_transaction_csv(records).encode() if file_format == "csv" else render_transaction_xlsx(records)
    file = (f"settlements.{file_format}", content, "text/csv" if file_format == "csv" else XLSX_MEDIA_TYPE)
    preview = client.post(f"{BASE}/transactions/files/preview", files={"file": file})
    assert preview.status_code == 200, preview.text
    assert preview.json()["error_count"] == 0, preview.json()
    imported = client.post(f"{BASE}/transactions/files/import", files={"file": file},
        headers={"Idempotency-Key": "physical-roundtrip"}, data={"preview_digest": preview.json()["preview_digest"]})
    assert imported.status_code == 200, imported.text
    assert imported.json()["created_count"] == 5
    assert cash_balance(history(new_cash, new_writer, new_stock), new_cash) == 19495
    from portfolio_app.services.portfolio_store import list_option_delivery_links
    assert len(list_option_delivery_links(PORTFOLIO)) == 2


@pytest.mark.parametrize("opening_type,side,ending_cash", [("buy", "long", 10196), ("option_write", "written", 9796)])
def test_cash_settled_european_option_rejects_wrong_delivery_then_settles(client, opening_type, side, ending_cash):
    cash = funded(client)
    holder = account(client, "option", cash)
    contract = option(settlement_type="cash", exercise_style="european", settlement_formula="Official final fixing × multiplier × intrinsic")
    post(client, transaction_type=opening_type, trade_date="2026-01-05", account_id=holder,
         settlement_cash_account_id=cash, quantity=2, price=5, gross_amount=1000, fees=2,
         derivative_contract_id="scenario-option", derivative_contract=contract)
    before = deepcopy(history(cash, holder))
    outcome = {"derivative_contract_id": "scenario-option", "side": side, "outcome": "cash_settled",
               "quantity": 2, "event_date": "2026-01-08", "settlement_date": "2026-01-12",
               "cash_settlement_amount": 1200, "settlement_cash_account_id": cash, "fees": 2}
    response = client.post(f"{BASE}/options/outcomes", json=outcome)
    assert response.status_code == 400
    assert "European" in response.json()["detail"]
    physical = {**outcome, "outcome": "physical", "event_date": "2026-01-09", "stock_account_id": "broker-us-core"}
    physical.pop("cash_settlement_amount")
    response = client.post(f"{BASE}/options/outcomes", json=physical)
    assert response.status_code == 400
    assert "Cash-settled" in response.json()["detail"]
    assert history(cash, holder) == before
    response = client.post(f"{BASE}/options/outcomes", json={**outcome, "event_date": "2026-01-09"})
    assert response.status_code == 200, response.text
    transactions = history(cash, holder)
    assert cash_balance(transactions, cash) == ending_cash
    assert open_option_obligations(transactions) == []
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(row["remaining_quantity"] for row in lots) == 0


def test_fcn_discount_entry_knock_in_observation_coupon_and_final_cash_redemption(client):
    cash = funded(client, 200000)
    holder = account(client, "fcn", cash)
    contract = {"derivative_contract_id": "scenario-fcn", "contract_name": "Discounted FCN",
                "contract_type": "fcn", "terms": {
                    "notional": 100000, "annual_coupon_rate_pct": 12, "issue_date": "2026-01-02",
                    "final_observation_date": "2026-01-29", "maturity_date": "2026-01-30",
                    "issuer": "Scenario Issuer", "counterparty": "Scenario Broker",
                    "knock_in_observation": "daily_close", "settlement_type": "cash",
                    "underlyings": [{"instrument_id": "equity-us-abbv", "deliverable": False}],
                }}
    post(client, transaction_type="buy", trade_date="2026-01-05", account_id=holder,
         settlement_cash_account_id=cash, derivative_contract_id="scenario-fcn", derivative_contract=contract,
         quantity=1, price=98000, gross_amount=98000, fees=10)
    observation = dict(transaction_type="lifecycle_event", lifecycle_event_type="fcn_knock_in",
                       trade_date="2026-01-08", account_id=holder, derivative_contract_id="scenario-fcn",
                       gross_amount=0, note="Issuer notice: daily-close knock-in confirmed; note remains outstanding.")
    observed = post(client, **observation)
    assert observed["net_cash_effect"] == 0
    transactions = history(cash, holder)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(row["remaining_quantity"] for row in lots) == 1
    assert sum(row["remaining_cost_basis"] for row in lots) == 98000
    assert cash_balance(transactions, cash) == 101990
    close = dict(transaction_type="maturity_redemption", lifecycle_event_type="fcn_knock_in",
                 trade_date="2026-01-08", account_id=holder, derivative_contract_id="scenario-fcn",
                 settlement_cash_account_id=cash, quantity=1, gross_amount=85000, currency="USD")
    rejected = client.post(f"{BASE}/transactions", json=close)
    assert rejected.status_code == 400
    assert "does not redeem" in rejected.json()["detail"]
    post(client, transaction_type="coupon", trade_date="2026-01-20", entitlement_date="2026-01-20",
         account_id=holder, settlement_cash_account_id=cash, derivative_contract_id="scenario-fcn", gross_amount=2500)
    post(client, **{**close, "trade_date": "2026-01-30"})
    assert cash_balance(history(cash, holder), cash) == 189490
    rejected = client.post(f"{BASE}/transactions", json={**observation, "currency": "USD", "trade_date": "2026-01-30"})
    assert rejected.status_code == 409  # Closed contract cannot acquire another knock-in observation.


def test_naked_call_buy_stock_then_assignment_records_actual_trades_and_cost(client):
    cash = funded(client, 20000)
    writer = account(client, "option", cash)
    stock = account(client, "security", cash)
    post(client, transaction_type="option_write", trade_date="2026-01-05", account_id=writer,
         settlement_cash_account_id=cash, derivative_contract_id="scenario-option", derivative_contract=option(),
         quantity=1, price=5, gross_amount=500)
    payload = {"derivative_contract_id": "scenario-option", "side": "written", "outcome": "physical",
               "quantity": 1, "event_date": "2026-01-09", "settlement_date": "2026-01-12",
               "stock_account_id": stock, "settlement_cash_account_id": cash, "fees": 3}
    rejected = client.post(f"{BASE}/options/outcomes", json=payload)
    assert rejected.status_code == 409  # No fictional cash-settlement fallback and no incomplete pair.
    assert len(history(writer, stock)) == 1
    post(client, transaction_type="buy", trade_date="2026-01-09", settlement_date="2026-01-12",
         account_id=stock, settlement_cash_account_id=cash, instrument_id="equity-us-abbv",
         quantity=100, price=110, gross_amount=11000, fees=2)
    response = client.post(f"{BASE}/options/outcomes", json=payload, headers={"Idempotency-Key": "scenario-assignment"})
    assert response.status_code == 200, response.text
    replay = client.post(f"{BASE}/options/outcomes", json=payload, headers={"Idempotency-Key": "scenario-assignment"})
    assert replay.status_code == 200
    assert replay.json() == response.json()
    transactions = history(cash, writer, stock)
    assert open_option_obligations(transactions) == []
    assert cash_balance(transactions, cash) == 19495  # 20,000 + 500 - 11,002 + 9,997.
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    assert sum(row["remaining_quantity"] for row in lots) == 0
    assert sum(row["realized_pnl"] for row in lots) == -1005  # 9,997 proceeds minus 11,002 stock cost.


def test_contract_does_not_allow_comparing_different_strike_and_quote_currencies(client):
    cash = funded(client)
    holder = account(client, "option", cash)
    response = client.post(f"{BASE}/transactions", json={
        "transaction_type": "buy", "trade_date": "2026-01-05", "account_id": holder,
        "settlement_cash_account_id": cash, "derivative_contract_id": "scenario-option",
        "derivative_contract": option(strike_currency="HKD"),
        "quantity": 1, "price": 5, "gross_amount": 500, "currency": "USD",
    })
    assert response.status_code == 400
    assert "contractual FX conversion model" in response.json()["detail"]
    assert history(holder) == []


def test_return_of_capital_is_per_share_and_excess_is_not_lost(client):
    cash = funded(client)
    stock = account(client, "security", cash)
    for day, price in [("2026-01-05", 10), ("2026-01-06", 30)]:
        post(client, transaction_type="buy", trade_date=day, account_id=stock,
             settlement_cash_account_id=cash, instrument_id="equity-us-abbv",
             quantity=10, price=price, gross_amount=10 * price)
    post(client, transaction_type="return_of_capital", trade_date="2026-01-07", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", gross_amount=300)
    transactions = history(cash, stock)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), transactions, resolve_pricing=False, pricing_map={})
    lots.sort(key=lambda row: row["opened_at"])
    # Each of 20 shares receives 15: first lot releases 100 basis + 50 gain;
    # second releases 150 basis. Cost-weighted allocation would be wrong.
    assert [row["remaining_cost_basis"] for row in lots] == [0, 150]
    assert sum(row["realized_pnl"] for row in lots) == 50
    summary = performance._sum_period_realized_capital_gains(
        lots, transactions=transactions, start_date=date(2026, 1, 2), end_date=date(2026, 1, 7),
        base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={},
    )
    assert summary["realized_capital_gains"] == 50
    postings = derive_ledger_postings(PORTFOLIO, transactions)
    assert sum(row.get("cost_basis_delta") or 0 for row in postings if row["account_id"] == stock) == 150
    assert sum(row.get("realized_pnl_delta") or 0 for row in postings if row["account_id"] == stock) == 50
    assert cash_balance(transactions, cash) == 9900
    post(client, transaction_type="sell", trade_date="2026-01-08", account_id=stock,
         settlement_cash_account_id=cash, instrument_id="equity-us-abbv", quantity=10, price=10, gross_amount=100)
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), history(cash, stock), resolve_pricing=False, pricing_map={})
    assert sum(row["remaining_cost_basis"] for row in lots) == 150
    assert sum(row["realized_pnl"] for row in lots) == 150


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_written_opening_additional_terms_round_trip_through_file_preview_and_commit(client, format):
    cash = funded(client)
    holder = account(client, "option", cash)
    record = {"transaction_type": "option_opening_balance", "transaction_sequence": 9001,
              "trade_date": "2026-01-02", "acquisition_date": "2025-12-15", "account_id": holder,
              "derivative_contract_id": "scenario-option", "derivative_contract": option(), "quantity": 2,
              "gross_amount": 1000, "currency": "USD", "source_system": "scenario-file", "external_reference": format}
    csv_text = render_transaction_csv([record]) if format == "csv" else transaction_xlsx_to_csv(render_transaction_xlsx([record]))
    _, rows = parse_transaction_csv(csv_text)
    assert not rows[0].errors, rows[0].errors
    payload = rows[0].transaction.model_dump(mode="json")
    assert payload["derivative_contract"]["terms"]["settlement_type"] == "physical"
    assert payload["derivative_contract"]["terms"]["exercise_style"] == "american"
    assert payload["derivative_contract"]["terms"]["terms_reference"] == "Scenario broker confirmation"
    content = csv_text.encode("utf-8") if format == "csv" else render_transaction_xlsx([record])
    file = (f"transactions.{format}", content, "text/csv" if format == "csv" else XLSX_MEDIA_TYPE)
    preview = client.post(f"{BASE}/transactions/files/preview", files={"file": file})
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid_count"] == 1, preview.json()
    response = client.post(f"{BASE}/transactions/files/import", files={"file": file},
                           headers={"Idempotency-Key": f"opening-file-{format}"},
                           data={"preview_digest": preview.json()["preview_digest"]})
    assert response.status_code == 200, response.text
    assert cash_balance(history(cash, holder), cash) == 10000
    assert open_option_obligations(history(cash, holder))[0]["carrying_liability"] == 1000
