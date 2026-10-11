"""Imported writer lots must release the original FIFO premium and reconcile NAV."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import performance
from portfolio_app.services.ledger import build_position_lots
from portfolio_app.services.option_obligations import derive_option_obligation_events, open_option_obligations
from portfolio_app.services.portfolio_store import list_accounts
from tests.test_transaction_repair_scenarios import account, funded, history, option, post, PORTFOLIO


@pytest.mark.parametrize("reverse_import", [False, True])
def test_original_written_acquisition_order_controls_partial_close_and_nav(client, monkeypatch, reverse_import):
    cash = funded(client)
    writer = account(client, "option", cash)
    entries = [("2025-12-20", 100), ("2025-12-10", 300)]
    if reverse_import:
        entries.reverse()
    for index, (acquired, basis) in enumerate(entries):
        post(client, transaction_type="option_opening_balance", trade_date="2026-01-02",
             acquisition_date=acquired, account_id=writer, quantity=1, gross_amount=basis,
             derivative_contract_id="scenario-option", **({"derivative_contract": option()} if index == 0 else {}))
    closed = post(client, transaction_type="option_buy_to_close", trade_date="2026-01-05",
                  account_id=writer, settlement_cash_account_id=cash, quantity=1, price=0.5,
                  gross_amount=50, derivative_contract_id="scenario-option")
    facts = history(cash, writer)
    end = date(2026, 1, 5)
    events = derive_option_obligation_events(facts, as_of_date=end)
    assert events == derive_option_obligation_events(list(reversed(facts)), as_of_date=end)
    realized = [event for event in events if event["transaction_id"] == closed["transaction_id"]]
    assert sum(event["released_premium_basis"] for event in realized) == 300
    assert sum(event["realized_pnl_delta"] for event in realized) == 250
    remaining = open_option_obligations(facts, as_of_date=end)
    assert [(row["opened_at"], row["premium_basis_remaining"]) for row in remaining] == [("2025-12-20", 100)]

    accounts = [row for row in list_accounts(PORTFOLIO) if row["account_id"] in {cash, writer}]
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD"], "rates": []})
    snapshots = performance.build_daily_portfolio_snapshots(
        {"portfolio_id": PORTFOLIO, "base_currency": "USD", "inception_date": "2026-01-02",
         "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"},
        accounts, facts, start_date=date(2026, 1, 2), end_date=end,
    )
    assert snapshots[0]["ending_nav"] == 9600
    assert snapshots[-1]["ending_nav"] == 9850  # Cash 9,950 less remaining liability 100, not 300.

    # Long options and written obligations share the same acquisition ordering.
    long_facts = deepcopy(facts)
    for row in long_facts:
        if row["transaction_type"] == "option_opening_balance":
            row["transaction_type"] = "opening_balance"
        elif row["transaction_type"] == "option_buy_to_close":
            row["transaction_type"] = "sell"
    lots = build_position_lots(PORTFOLIO, accounts, long_facts, as_of_date=end,
                               resolve_pricing=False, pricing_map={}, corporate_actions=[])
    assert sum(row["remaining_cost_basis"] for row in lots) == 100


def test_same_acquisition_day_keeps_original_opening_fact_order(client):
    cash = funded(client)
    writer = account(client, "option", cash)
    for index, basis in enumerate((100, 300)):
        post(client, transaction_type="option_opening_balance", trade_date="2026-01-02",
             acquisition_date="2025-12-10", account_id=writer, quantity=1, gross_amount=basis,
             derivative_contract_id="scenario-option", **({"derivative_contract": option()} if index == 0 else {}))
    closed = post(client, transaction_type="option_buy_to_close", trade_date="2026-01-05",
                  account_id=writer, settlement_cash_account_id=cash, quantity=1, price=0.5, gross_amount=50,
                  derivative_contract_id="scenario-option")
    facts = history(cash, writer)
    events = derive_option_obligation_events(list(reversed(facts)))
    assert sum(event["released_premium_basis"] for event in events
               if event["transaction_id"] == closed["transaction_id"]) == 100
