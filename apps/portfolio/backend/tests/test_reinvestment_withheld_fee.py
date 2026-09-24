from datetime import date

import pytest
from pydantic import ValidationError

from portfolio_app.api.contracts import TransactionCreateRequest
from portfolio_app.services import performance
from tests.test_instrument_event_tasks import _insert_distribution_revision, _reconcile_tasks


def _command():
    return {
        "external_reference": "test-confirmed-reinvestment",
        "asset_type": "security", "transaction_action": "dividend_reinvestment",
        "trade_date": "2026-04-15", "settlement_date": "2026-04-15",
        "position_effective_date": "2026-04-15", "entitlement_date": "2026-04-10",
        "account_id": "broker-us-core", "instrument_id": "fund-us-agg",
        "quantity": "0.3", "price": "100", "gross_amount": "30",
        "fees": "0.42", "fee_category": "performance_fee", "currency": "USD",
    }


def test_withheld_reinvestment_preview_commit_replay_edit_and_event_match(client):
    _insert_distribution_revision(event_id="fund-nav-event-fee", revision_number=1,
        revision_kind="original", supersedes_event_id=None, cash_per_unit="0.1")
    task = _reconcile_tasks(client).json()["tasks"][0]
    lot_query = {"account_id": "broker-us-core", "position_reference_id": "fund-us-agg", "as_of_date": "2026-04-15"}
    prior_lots = client.get("/api/portfolios/investment-studio/position-lots", params=lot_query).json()["position_lots"]
    prior_income = sum(lot["income_cash_amount"] for lot in prior_lots)
    prior_expense = sum(lot["expense_cash_amount"] for lot in prior_lots)
    batch = {"source_system": "test_confirmation", "records": [_command()]}
    preview = client.post("/api/portfolios/investment-studio/transaction-imports/preview", json=batch)
    assert preview.status_code == 200, preview.text
    assert preview.json()["error_count"] == 0, preview.text
    body = {**batch, "preview_digest": preview.json()["preview_digest"]}
    headers = {"Idempotency-Key": "test-withheld-reinvestment"}
    created = client.post("/api/portfolios/investment-studio/transaction-imports/commit", json=body, headers=headers)
    assert created.status_code == 200, created.text
    tx = created.json()["transactions"][0]
    replayed = client.post("/api/portfolios/investment-studio/transaction-imports/commit", json=body, headers=headers)
    assert replayed.status_code == 200
    assert replayed.json()["transactions"][0]["transaction_id"] == tx["transaction_id"]
    assert tx["net_cash_effect"] == 0
    assert tx["gross_amount"] == 30
    assert tx["fees"] == 0.42
    assert tx["fee_category"] == "performance_fee"
    # A normal editor round trip retains the attached fee and net investment.
    edit = {k: v for k, v in _command().items() if k not in {"asset_type", "transaction_action"}}
    edit.update(transaction_type="dividend_reinvestment", expected_row_version=tx["row_version"], source_system="test_confirmation")
    updated = client.put(f"/api/portfolios/investment-studio/transactions/{tx['transaction_id']}", json=edit)
    assert updated.status_code == 200, updated.text
    assert updated.json()["fees"] == 0.42
    postings = client.get(f"/api/portfolios/investment-studio/transactions/{tx['transaction_id']}/ledger-postings").json()["ledger_postings"]
    assert all((p.get("cash_amount_delta") or 0) == 0 for p in postings)
    bridge = next(p for p in postings if p["posting_role"] == "position_recognition_bridge")
    assert bridge["pending_amount_delta"] == 30
    assert bridge["recognition_start_date"] == "2026-04-10"
    assert bridge["effective_date"] == "2026-04-15"
    position = next(p for p in postings if p["posting_role"] == "security_reinvestment_position")
    assert position["quantity_delta"] == 0.3
    assert position["cost_basis_delta"] == 30
    lots = client.get("/api/portfolios/investment-studio/position-lots", params={"account_id": "broker-us-core", "position_reference_id": "fund-us-agg", "as_of_date": "2026-04-15"}).json()["position_lots"]
    original = [lot for lot in lots if lot["opening_transaction_type"] != "dividend_reinvestment"]
    assert sum(lot["income_cash_amount"] for lot in lots) - prior_income == pytest.approx(30.42)
    assert sum(lot["expense_cash_amount"] for lot in lots) - prior_expense == pytest.approx(0.42)
    drip = next(lot for lot in lots if lot["opening_transaction_type"] == "dividend_reinvestment")
    assert drip["entry_cost_basis"] == 30
    response = client.post(f"/api/portfolios/investment-studio/instrument-event-tasks/{task['instrument_event_task_id']}/reviews", json={"decision": "processed", "transaction_ids": [tx["transaction_id"]], "note": "Confirmed net reinvestment and withheld fee.", "reviewed_by": "test", "expected_row_version": task["row_version"]})
    assert response.status_code == 200, response.text


def test_reinvestment_withheld_fee_is_gross_income_and_expense_without_external_flow():
    tx = {**_command(), "transaction_type": "dividend_reinvestment", "transaction_id": "test-reinvestment", "transaction_sequence": 1, "instrument_ref": {"instrument_id": "fund-us-agg", "instrument_name": "Test fund", "instrument_type": "fund", "currency": "USD"}}
    buckets = performance._sum_period_transaction_buckets([tx], base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={})
    assert buckets["earnings"] == pytest.approx(30.42)
    assert buckets["fees"] == pytest.approx(0.42)
    assert buckets["net_external_inflow"] == 0
    events = performance._build_contribution_daily_events(axis="instrument", as_of_date=date(2026, 4, 10), position_lots=[], transactions_on_date=[tx], base_currency="USD", account_name_map={}, account_currency_map={}, direct_fx_instruments={}, instrument_detail_cache={})
    event = next(iter(events.values()))
    assert event["income_cash_amount"] == pytest.approx(30.42)
    assert event["expense_cash_amount"] == pytest.approx(0.42)
    assert event["fee_amount"] == pytest.approx(0.42)


@pytest.mark.parametrize("overrides", [{"fees": 1, "fee_category": "unknown"}, {"fees": 1, "fee_category": "transaction_cost"}, {"taxes": 1}])
def test_reinvestment_only_accepts_explicit_withheld_performance_fee(overrides):
    payload = {k: v for k, v in _command().items() if k not in {"asset_type", "transaction_action"}}
    with pytest.raises(ValidationError):
        TransactionCreateRequest.model_validate({**payload, "transaction_type": "dividend_reinvestment", **overrides})
