from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import event

from portfolio_app.api.contracts import TransactionImportPreviewRequest
from portfolio_app.api.routes import transactions as routes
from portfolio_app.db.session import get_engine
from portfolio_app.services import daily_snapshots, ledger


def _fact(sequence, kind, day, *, account="a", quantity=None, **fields):
    return {
        "transaction_id": f"txn-{sequence}", "transaction_sequence": sequence,
        "transaction_type": kind, "trade_date": day,
        "trade_at": f"{day}T10:00:00Z", "created_at": "2026-09-01T00:00:00Z",
        "settlement_date": day, "account_id": account,
        "instrument_id": "equity-us-abbv", "quantity": quantity,
        "gross_amount": 100, "fees": 0, "taxes": 0, "currency": "USD",
        **fields,
    }


def test_trade_command_and_preview_do_not_read_derived_valuation(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("Transaction commands must not load a valuation summary")

    monkeypatch.setattr(daily_snapshots, "_state_requires_refresh", unavailable)
    preview = client.get(
        "/api/portfolios/investment-studio/transactions/position-preview",
        params={"account_id": "broker-us-core", "position_kind": "instrument",
                "position_reference_id": "equity-us-abbv", "as_of_date": "2026-05-01"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["quantity"] > 0
    result = client.post("/api/portfolios/investment-studio/transactions", json={
        "transaction_type": "buy", "trade_date": "2026-05-01",
        "account_id": "broker-us-core", "settlement_cash_account_id": "cash-usd-main",
        "instrument_id": "equity-us-abbv", "quantity": 1, "price": 100,
        "gross_amount": 100, "currency": "USD",
    })
    assert result.status_code == 200, result.text
    assert result.json()["quantity"] == 1


def test_import_batch_metadata_reads_do_not_grow_per_row():
    statements = []

    def count(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        counts = []
        for row_count in (1, 20):
            statements.clear()
            response = routes.preview_transaction_import(
                "investment-studio", TransactionImportPreviewRequest.model_validate({
                    "source_system": "read-regression", "records": [
                        {"external_reference": f"deposit-{index}", "asset_type": "cash",
                         "transaction_action": "deposit", "trade_date": "2026-05-01",
                         "account_id": "cash-usd-main", "gross_amount": "100", "currency": "USD"}
                        for index in range(row_count)
                    ],
                }),
            )
            assert response.valid_count == row_count
            counts.append(len(statements))
        assert counts[1] == counts[0]
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)


@pytest.mark.parametrize("other_account,other_asset", [("b", "equity-us-abbv"), ("a", "fund-us-agg")])
def test_entitlement_replay_is_shared_but_never_across_positions_or_calls(monkeypatch, other_account, other_asset):
    facts = [
        _fact(1, "buy", "2026-01-02", quantity=10),
        _fact(2, "buy", "2026-01-02", quantity=20, account=other_account, instrument_id=other_asset),
        _fact(3, "dividend", "2026-01-03"),
        _fact(4, "dividend", "2026-01-03", account=other_account, instrument_id=other_asset),
    ]
    original = deepcopy(facts)
    original_replay = ledger._build_position_state
    replays = []
    source_reads = []

    def replay(*args, **kwargs):
        replays.append(kwargs.get("as_of_date"))
        return original_replay(*args, **kwargs)

    def actions(*args, **kwargs):
        source_reads.append(kwargs)
        return []

    monkeypatch.setattr(ledger, "_build_position_state", replay)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", actions)
    ledger.validate_transaction_position_history("audit", facts)
    assert replays == [None, date(2026, 1, 3)]
    assert len(source_reads) == 1
    assert facts == original
    # A later request has different facts and must rebuild the entitlement;
    # another account's previously cached positive position cannot authorize it.
    with pytest.raises(ValueError, match="requires an account position"):
        ledger.validate_transaction_position_history("audit", [facts[0], *facts[2:]])
    assert len(source_reads) == 2


def test_expenses_keep_execution_time_when_income_has_same_day_entitlement():
    opening = _fact(1, "buy", "2026-01-02", quantity=10)
    income = _fact(2, "dividend", "2026-01-03")
    disposal = _fact(3, "sell", "2026-01-03", quantity=10, trade_at="2026-01-03T11:00:00Z")
    expense = _fact(4, "fee", "2026-01-03", trade_at="2026-01-03T12:00:00Z")
    with pytest.raises(ValueError, match="requires an account position"):
        ledger.validate_transaction_position_history(
            "audit", [opening, income, disposal, expense], corporate_actions=[],
        )
    ledger.validate_transaction_position_history(
        "audit", [opening, income, disposal, {**expense, "entitlement_date": "2026-01-03"}],
        corporate_actions=[],
    )


def test_future_entitlement_excludes_own_reinvestment():
    # The income's own acquired units precede its explicit future entitlement;
    # that fact must be removed before checking whether it was ever entitled.
    opening = _fact(1, "buy", "2026-01-02", quantity=10)
    disposal = _fact(2, "sell", "2026-01-03", quantity=10)
    reinvestment = _fact(4, "dividend_reinvestment", "2026-01-04", quantity=1,
                         entitlement_date="2026-01-05")
    with pytest.raises(ValueError, match="requires an account position"):
        ledger.validate_transaction_position_history(
            "audit", [opening, disposal, reinvestment], corporate_actions=[],
        )


def test_corporate_action_input_includes_deliveries_and_future_boundaries(monkeypatch):
    from tests.test_fcn_settlement_accounting import facts, STOCK

    transactions = facts()
    reads = []

    def actions(instrument_ids, *, effective_on_or_before):
        reads.append((instrument_ids, effective_on_or_before))
        return []

    monkeypatch.setattr(ledger, "list_registry_corporate_actions", actions)
    # The only instrument reference in these facts is inside physical delivery.
    ledger.validate_transaction_position_history("fcn-review", transactions)
    assert len(reads) == 1
    assert STOCK["instrument_id"] in reads[0][0]
    reads.clear()
    transactions.append({
        **_fact(5, "dividend", "2099-01-01", account="stock"),
        "instrument_id": STOCK["instrument_id"], "currency": "HKD",
    })
    ledger.validate_transaction_position_history("fcn-review", transactions)
    assert reads == [({STOCK["instrument_id"]}, date(2099, 1, 1))]


def test_prefetched_future_action_is_not_applied_to_an_earlier_replay(monkeypatch):
    action = {
        "instrument_id": "equity-us-abbv", "action_type": "share_split",
        "status": "confirmed", "record_date": "2099-01-01", "effective_date": "2099-01-03",
        "new_units": "2", "old_units": "1", "quantity_rounding": "exact",
    }
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *args, **kwargs: [action])
    facts = [
        _fact(1, "buy", "2026-01-02", quantity=10),
        _fact(2, "buy", "2099-01-02", quantity=10),
        _fact(3, "deposit", "2099-01-05", account="cash", instrument_id=None),
    ]
    # Loading a wider source window must not validate/apply that future split
    # before a replay actually reaches its effective date.
    ledger.validate_transaction_position_history("audit", facts)
    facts.append(_fact(4, "dividend", "2099-01-06"))
    with pytest.raises(ValueError, match="due-bill"):
        ledger.validate_transaction_position_history("audit", facts)
