"""FCN settlement facts through the authenticated API and file contract."""
from copy import deepcopy
from datetime import date

import pytest

from tests.test_transaction_repair_scenarios import BASE, PORTFOLIO, account, funded, post, history
from portfolio_app.services.ledger import build_position_lots, derive_ledger_postings
from portfolio_app.services.portfolio_store import list_accounts, list_transactions
from portfolio_app.services.transaction_csv import parse_transaction_csv, render_transaction_csv
from portfolio_app.services.transaction_xlsx import render_transaction_xlsx, transaction_xlsx_to_csv


@pytest.fixture(autouse=True)
def fcn_test_member(client):
    # Only the identity lookup is replaced; the real portfolio ACL still runs.
    from portfolio_app.api.authorization import authenticated_principal
    from portfolio_app.db.models import PortfolioAccessStateModel, PortfolioMembershipModel
    from portfolio_app.db.session import get_session_factory
    from studio_identity import Principal
    principal = Principal(user_id="fcn-test-member", display_name="FCN test member", team_id="fcn-test-team")
    with get_session_factory()() as session:
        session.merge(PortfolioAccessStateModel(portfolio_id=PORTFOLIO, team_id=principal.team_id))
        session.merge(PortfolioMembershipModel(portfolio_id=PORTFOLIO, user_id=principal.user_id,
            display_name=principal.display_name, role="manager", granted_by=principal.user_id, granted_at="2026-01-01T00:00:00Z"))
        session.commit()
    client.app.dependency_overrides[authenticated_principal] = lambda: principal
    yield
    client.app.dependency_overrides.pop(authenticated_principal, None)


def settlement_case(client):
    usd = funded(client, 500000)
    hkd = account(client, "cash", currency="HKD")
    post(client, transaction_type="opening_balance", trade_date="2026-01-02", account_id=hkd, gross_amount=10000, currency="HKD")
    holder = account(client, "fcn", usd)
    stock = account(client, "security", hkd, currency="HKD")
    contract = {"derivative_contract_id": "usd-hkd-fcn", "contract_name": "USD FCN with HK delivery", "contract_type": "fcn", "terms": {
        "notional": 500000, "annual_coupon_rate_pct": 8, "issue_date": "2026-01-02", "maturity_date": "2026-04-02",
        "settlement_type": "physical", "issuer": "Fixture issuer", "counterparty": "Fixture broker",
        "underlyings": [{"instrument_id": "fund-hk-2800", "deliverable": True, "initial_reference_price": 1000, "strike_level_pct": 80}]}}
    post(client, transaction_type="buy", trade_date="2026-01-02", account_id=holder, derivative_contract_id="usd-hkd-fcn",
        derivative_contract=contract, quantity=1, price=500000, gross_amount=500000, settlement_cash_account_id=usd)
    for day in ("2026-02-02", "2026-03-02"):
        post(client, transaction_type="coupon", trade_date=day, account_id=holder, derivative_contract_id="usd-hkd-fcn",
            gross_amount="3333.33", settlement_cash_account_id=usd)
    payload = {"currency": "USD", "transaction_type": "maturity_redemption", "lifecycle_event_type": "fcn_knock_in",
        "trade_date": "2026-04-02", "settlement_date": "2026-04-07", "account_id": holder, "derivative_contract_id": "usd-hkd-fcn",
        "quantity": 1, "gross_amount": "22.41", "settlement_cash_account_id": usd, "note": "Fixture final delivery confirmation; prices and FX are illustrative",
        "asset_deliveries": [{"account_id": stock, "instrument_id": "fund-hk-2800", "quantity": 4881, "fair_value": 3416700,
            "currency": "HKD", "fx_rate_to_contract": "0.128040973111395647", "quantity_fx_rate": "7.81", "fractional_quantity": ".25",
            "fractional_reference_price": 700, "delivery_date": "2026-04-07", "settlement_cash_account_id": hkd,
            "taxes": 3905, "fee_settlement_date": "2026-04-03"}],
        "settlement_cashflows": [{"kind": "coupon", "cash_account_id": usd, "currency": "USD", "amount": "3333.34",
            "recognition_date": "2026-04-02", "settlement_date": "2026-04-07"}]}
    return usd, hkd, holder, stock, payload


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_cross_currency_settlement_persists_cash_and_share_cost_without_buying_twice(client, file_format):
    usd, hkd, holder, stock, payload = settlement_case(client)
    record = post(client, **payload)
    assert record["settlement_cashflows"][0]["amount"] == "3333.34"
    facts = history(usd, hkd, holder, stock)
    postings = derive_ledger_postings(PORTFOLIO, facts, account_currency_map={usd: "USD", hkd: "HKD"}, as_of_date=date(2026, 4, 7))
    balances = {cash_id: sum(float(row.get("cash_amount_delta") or 0) for row in postings if row["account_id"] == cash_id) for cash_id in (usd, hkd)}
    assert balances == pytest.approx({usd: 10022.41, hkd: 6095})
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    received = next(lot for lot in lots if lot["account_id"] == stock)
    assert (received["remaining_quantity"], received["remaining_cost_basis"]) == (4881, 3420605)
    assert sum(lot["remaining_quantity"] for lot in lots if lot["account_id"] == holder) == 0
    assert record["transaction_id"] in {row["transaction_id"] for row in list_transactions(PORTFOLIO, account_id=hkd)}
    content = render_transaction_csv(facts) if file_format == "csv" else transaction_xlsx_to_csv(render_transaction_xlsx(facts))
    _, parsed = parse_transaction_csv(content, default_source_system="fcn-cashflow-roundtrip")
    assert not [row.errors for row in parsed if row.errors]
    close = next(row.transaction for row in parsed if row.transaction and row.transaction.settlement_cashflows)
    assert close.asset_deliveries[0].delivery_date == date(2026, 4, 7)
    assert close.asset_deliveries[0].taxes == 3905
    assert str(close.settlement_cashflows[0].amount) == "3333.34"


def test_settlement_dates_and_cash_currency_are_validated_before_write(client):
    usd, hkd, holder, stock, payload = settlement_case(client)
    invalid = deepcopy(payload)
    invalid["asset_deliveries"][0]["settlement_cash_account_id"] = usd
    assert client.post(BASE + "/transactions", json=invalid).status_code == 400
    invalid = deepcopy(payload)
    invalid["asset_deliveries"][0]["delivery_date"] = "2026-04-01"
    assert client.post(BASE + "/transactions", json=invalid).status_code == 400
    invalid = deepcopy(payload)
    invalid["settlement_cashflows"][0]["settlement_date"] = "2026-04-01"
    assert client.post(BASE + "/transactions", json=invalid).status_code == 400
    assert not any(row.get("asset_deliveries") for row in history(usd, hkd, holder, stock))


def test_pending_delivery_cannot_be_sold_and_date_edit_cannot_break_a_later_sale(client):
    usd, hkd, holder, stock, payload = settlement_case(client)
    record = post(client, **payload)
    sale = {"currency": "HKD", "transaction_type": "sell", "trade_date": "2026-04-06", "account_id": stock,
        "instrument_id": "fund-hk-2800", "quantity": 4881, "price": 750, "gross_amount": 3660750, "settlement_cash_account_id": hkd}
    rejected = client.post(BASE + "/transactions", json=sale)
    assert rejected.status_code == 409, rejected.text
    assert "delivered" in rejected.text
    sale["trade_date"] = "2026-04-08"
    sold = post(client, **sale)
    invalid = deepcopy(payload)
    invalid["asset_deliveries"][0]["delivery_date"] = "2026-04-09"
    invalid["expected_row_version"] = record["row_version"]
    changed = client.put(BASE + "/transactions/" + record["transaction_id"], json=invalid)
    assert changed.status_code == 409, changed.text
    assert "delivered" in changed.text
    deleted = client.request("DELETE", BASE + "/transactions/" + record["transaction_id"], json={"expected_row_versions": {record["transaction_id"]: record["row_version"]}})
    assert deleted.status_code == 409, deleted.text
    facts = history(usd, hkd, holder, stock)
    assert {record["transaction_id"], sold["transaction_id"]}.issubset({row["transaction_id"] for row in facts})


def test_import_then_edit_and_delete_keeps_the_entire_settlement_together(client):
    usd, hkd, holder, stock, payload = settlement_case(client)
    command = {key: value for key, value in payload.items() if key not in {"transaction_type", "lifecycle_event_type"}}
    command.update(asset_type="fcn", transaction_action="knock_in_close", external_reference="fcn-settlement-import-1")
    batch = {"source_system": "fcn-settlement-test", "records": [command]}
    preview = client.post(BASE + "/transaction-imports/preview", json=batch)
    assert preview.status_code == 200, preview.text
    assert preview.json()["error_count"] == 0, preview.text
    committed = client.post(BASE + "/transaction-imports/commit", headers={"Idempotency-Key": "fcn-settlement-import-1"}, json={**batch, "preview_digest": preview.json()["preview_digest"]})
    assert committed.status_code == 200, committed.text
    record = committed.json()["transactions"][0]
    assert record["asset_deliveries"][0]["delivery_date"] == "2026-04-07"
    assert record["settlement_cashflows"][0]["amount"] == "3333.34"

    correction = deepcopy(payload)
    correction["asset_deliveries"][0]["taxes"] = 4000
    correction["settlement_cashflows"][0]["amount"] = "3334"
    changed = client.put(BASE + "/transactions/" + record["transaction_id"], json={**correction, "expected_row_version": record["row_version"]})
    assert changed.status_code == 200, changed.text
    postings = derive_ledger_postings(PORTFOLIO, history(usd, hkd, holder, stock), account_currency_map={usd: "USD", hkd: "HKD"}, as_of_date=date(2026, 4, 7))
    assert sum(float(row.get("cash_amount_delta") or 0) for row in postings if row["account_id"] == hkd) == 6000
    assert sum(float(row.get("cash_amount_delta") or 0) for row in postings if row["account_id"] == usd) == pytest.approx(10023.07)
    removed = client.request("DELETE", BASE + "/transactions/" + record["transaction_id"], json={"expected_row_versions": {record["transaction_id"]: changed.json()["row_version"]}})
    assert removed.status_code == 200, removed.text
    facts = history(usd, hkd, holder, stock)
    assert record["transaction_id"] not in {row["transaction_id"] for row in facts}
    lots = build_position_lots(PORTFOLIO, list_accounts(PORTFOLIO), facts, resolve_pricing=False, pricing_map={})
    assert not any(lot["remaining_quantity"] for lot in lots if lot["account_id"] == stock)
    assert sum(lot["remaining_quantity"] for lot in lots if lot["account_id"] == holder) == 1
    postings = derive_ledger_postings(PORTFOLIO, facts, account_currency_map={usd: "USD", hkd: "HKD"}, as_of_date=date(2026, 4, 7))
    assert sum(float(row.get("cash_amount_delta") or 0) for row in postings if row["account_id"] == hkd) == 10000
    assert sum(float(row.get("cash_amount_delta") or 0) for row in postings if row["account_id"] == usd) == pytest.approx(6666.66)


def test_account_dates_cannot_exclude_bound_stock_or_fee_settlements(client):
    usd, hkd, holder, stock, payload = settlement_case(client)
    post(client, **payload)
    for account_id, close_date in ((hkd, "2026-04-02"), (stock, "2026-04-06")):
        changed = client.patch(BASE + "/accounts/" + account_id, json={"closed_at": close_date})
        assert changed.status_code == 400, changed.text
        assert "FCN settlement history" in changed.text
