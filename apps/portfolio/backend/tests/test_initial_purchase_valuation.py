"""Hand-calculated initial purchase valuations; no synthetic shared quotes."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance
from tests.test_strict_market_valuation import ACCOUNTS, PORTFOLIO, REF, source, transaction


def setup_source(monkeypatch, prices):
    stock = source(monkeypatch, prices)
    for module in (ledger, performance):
        monkeypatch.setattr(module, 'get_registry_instrument_details',
                            lambda ids: {key: deepcopy(stock) for key in ids if key == REF['instrument_id']})
    return stock


def facts():
    return [transaction(1, 'deposit', '2026-08-03', 1000),
            {**transaction(2, 'buy', '2026-08-03', 990, 10, 99), 'fees': 10}]


def test_initial_purchase_values_assets_at_gross_and_keeps_fees_in_loss(monkeypatch):
    stock = setup_source(monkeypatch, {'2026-08-04': 110})
    original = deepcopy(stock)
    portfolio = {**PORTFOLIO, 'as_of_date': '2026-08-04'}
    snapshots = performance.build_daily_portfolio_snapshots(portfolio, ACCOUNTS, facts(), include_materialized_rows=True)
    first, second = snapshots
    # 1000 cash - 990 purchase - 10 fee + 990 security = NAV 990.
    assert first['nav'] == 990
    assert first['total_pnl'] == -10
    assert first['daily_twr'] == pytest.approx(-.01)
    assert first['market_observation_count'] == 0
    assert not first['market_risk_return_observation_eligible']
    assert first['market_risk_return_observation_exclusion_reason'] == 'transaction_price_valuation'
    assert first['transaction_price_valuations'][0]['transaction_ids'] == ['strict-2']
    holding = next(row for row in first['_holding_rows'] if row.get('instrument_id') == REF['instrument_id'])
    assert holding['market_value'] == 990
    assert holding['cost_basis'] == 1000
    assert holding['valuation_basis'] == 'transaction_price'
    assert holding['quote_status'] == 'transaction-price'
    assert holding['quote_basis'] is None
    assert holding['day_change_value'] is None
    instrument_slice = next(row for row in first['_contribution_slices'] if row['axis'] == 'instrument' and row['group_key'] == REF['instrument_id'])
    assert instrument_slice['total_pnl'] == -10
    assert instrument_slice['daily_contribution'] == pytest.approx(-.01)
    assert not instrument_slice['market_risk_return_observation_eligible']
    # The next real close is 110: NAV 1100, daily +110/990 and total +10%.
    assert second['nav'] == 1100
    assert second['daily_twr'] == pytest.approx(1100 / 990 - 1)
    assert second['cumulative_twr'] == pytest.approx(.1)
    assert second['market_risk_return_observation_eligible']
    direct = performance.build_holdings_report(portfolio, ACCOUNTS, facts(), as_of_date=date(2026, 8, 3))
    assert next(row for row in direct['positions'] if row.get('instrument_id') == REF['instrument_id'])['market_value'] == 990
    lots = ledger.build_position_lots(PORTFOLIO['portfolio_id'], ACCOUNTS, facts(), as_of_date=date(2026, 8, 3))
    assert sum(lot['current_market_value'] for lot in lots) == 990
    assert sum(lot['unrealized_pnl'] for lot in lots) == -10
    accounts_view = ledger.build_account_workspace(PORTFOLIO['portfolio_id'], ACCOUNTS, facts(), as_of_date=date(2026, 8, 3), selected_account_id='broker')
    assert accounts_view['positions'][0]['market_value'] == 990
    assert accounts_view['positions'][0]['valuation_basis'] == 'transaction_price'
    assert accounts_view['positions'][0]['fair_value'] is None
    calculation = performance.build_period_calculation_report(portfolio, ACCOUNTS, facts())
    assert calculation['summary']['final_value'] == 1100
    assert calculation['summary']['delta'] == 100
    # With capital already present before the purchase day, that day's
    # transaction valuation is a genuine close-to-close opening boundary.
    pre_funded = [{**facts()[0], 'trade_date': '2026-08-02', 'settlement_date': '2026-08-02'}, facts()[1]]
    boundary = performance.build_period_calculation_report({**portfolio, 'inception_date': '2026-08-02'}, ACCOUNTS, pre_funded, start_date=date(2026, 8, 3), end_date=date(2026, 8, 4))
    assert boundary['summary']['initial_value'] == 990
    assert boundary['summary']['delta'] == 110
    assert boundary['summary']['fees'] == 0
    assert stock == original


def test_next_session_missing_still_stops_even_if_later_price_exists(monkeypatch):
    setup_source(monkeypatch, {'2026-08-05': 120})
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, facts())
    assert [row['as_of_date'] for row in snapshots] == [date(2026, 8, 3), date(2026, 8, 4)]
    assert snapshots[0]['nav'] == 990
    assert snapshots[-1]['nav'] is None


def test_formal_price_overrides_transaction_price(monkeypatch):
    setup_source(monkeypatch, {'2026-08-03': 100})
    snapshots = performance.build_daily_portfolio_snapshots({**PORTFOLIO, 'as_of_date': '2026-08-03'}, ACCOUNTS, facts())
    assert snapshots[0]['nav'] == 1000
    assert snapshots[0]['daily_twr'] == 0
    assert snapshots[0]['transaction_price_valuations'] == []
    assert snapshots[0]['market_observation_count'] == 1


def test_multiple_initial_buys_share_quantity_weighted_price_across_accounts(monkeypatch):
    setup_source(monkeypatch, {})
    accounts = ACCOUNTS + [{**ACCOUNTS[1], 'account_id': 'broker-2'}]
    transactions = [transaction(1, 'deposit', '2026-08-03', 2100),
                    {**transaction(2, 'buy', '2026-08-03', 900, 10, 90), 'fees': 10},
                    {**transaction(3, 'buy', '2026-08-03', 1100, 10, 110), 'fees': 10, 'account_id': 'broker-2'}]
    snapshots = performance.build_daily_portfolio_snapshots({**PORTFOLIO, 'as_of_date': '2026-08-03'}, accounts, transactions, include_materialized_rows=True)
    assert snapshots[0]['nav'] == 2080
    assert snapshots[0]['total_pnl'] == -20
    rows = [row for row in snapshots[0]['_holding_rows'] if row.get('instrument_id') == REF['instrument_id']]
    assert [row['last_price'] for row in rows] == [100, 100]
    assert sum(row['market_value'] for row in rows) == 2000


def test_additional_buy_cannot_price_preexisting_holding(monkeypatch):
    setup_source(monkeypatch, {'2026-08-03': 100})
    transactions = facts() + [transaction(3, 'deposit', '2026-08-04', 1000), transaction(4, 'buy', '2026-08-04', 1000, 10, 100)]
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, transactions)
    assert snapshots[-1]['as_of_date'] == date(2026, 8, 4)
    assert snapshots[-1]['nav'] is None


def test_fund_confirmation_date_controls_transaction_valuation(monkeypatch):
    setup_source(monkeypatch, {})
    transactions = facts()
    transactions[1]['position_effective_date'] = '2026-08-05'
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, transactions)
    assert snapshots[0]['total_position_count'] == 0
    assert snapshots[0]['transaction_price_valuations'] == []
    assert snapshots[1]['transaction_price_valuations'] == []
    assert snapshots[2]['nav'] == 990
    assert snapshots[2]['transaction_price_valuations'][0]['valuation_date'] == date(2026, 8, 5)


def test_purchase_basis_carries_only_over_confirmed_non_sessions(monkeypatch):
    setup_source(monkeypatch, {'2026-09-08': 110})
    portfolio = {**PORTFOLIO, 'inception_date': '2026-09-04', 'as_of_date': '2026-09-08'}
    transactions = [transaction(1, 'deposit', '2026-09-04', 1000),
                    {**transaction(2, 'buy', '2026-09-04', 990, 10, 99), 'fees': 10}]
    snapshots = performance.build_daily_portfolio_snapshots(portfolio, ACCOUNTS, transactions)
    assert [row['nav'] for row in snapshots] == [990, 990, 990, 990, 1100]
    assert all(not row['market_risk_return_observation_eligible'] for row in snapshots[:4])
    assert snapshots[-1]['transaction_price_valuations'] == []


def test_transaction_basis_does_not_hide_ambiguous_market_data(monkeypatch):
    stock = setup_source(monkeypatch, {'2026-08-03': 100})
    stock['market_data'].append(deepcopy(stock['market_data'][0]))
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, facts())
    assert snapshots[0]['nav'] is None


def test_linked_physical_option_purchase_cannot_use_strike_as_market_price(monkeypatch):
    from portfolio_app.services import portfolio_store
    from tests.test_derivative_performance_review import (
        ACCOUNTS as option_accounts, PORTFOLIO as option_portfolio, market, physical_facts,
    )

    stock = market(monkeypatch, [('2026-05-29', 120)])
    transactions = physical_facts('long', 'call', 120)
    links = lambda portfolio_id: [{'option_transaction_id': 'review-4', 'stock_transaction_id': 'review-5'}]
    monkeypatch.setattr(performance, 'list_option_delivery_links', links)
    monkeypatch.setattr(portfolio_store, 'list_option_delivery_links', links)
    snapshots = performance.build_daily_portfolio_snapshots(
        option_portfolio, option_accounts, transactions,
        start_date=date(2026, 5, 29), end_date=date(2026, 6, 1),
    )
    # The 100 x strike 100 leg is contractual settlement, not evidence of spot.
    assert snapshots[-1]['nav'] is None
    assert not snapshots[-1].get('transaction_price_valuations')
    accounts_view = ledger.build_account_workspace(
        option_portfolio['portfolio_id'], option_accounts, transactions,
        as_of_date=date(2026, 6, 1), selected_account_id='stock',
    )
    assert accounts_view['positions'][0]['valuation_basis'] != 'transaction_price'
    # With a real 120 close: 20,000 - 500 premium - 10,000 strike + 12,000 stock.
    stock['market_data'].append({**stock['market_data'][0], 'as_of_date': '2026-06-01'})
    priced = performance.build_daily_portfolio_snapshots(
        option_portfolio, option_accounts, transactions,
        start_date=date(2026, 5, 29), end_date=date(2026, 6, 1),
    )
    assert priced[-1]['nav'] == 21500
    assert priced[-1]['transaction_price_valuations'] == []


def test_noncash_fcn_delivery_does_not_supply_an_initial_market_price(monkeypatch):
    from tests.test_derivative_performance_review import (
        ACCOUNTS as original_accounts, PORTFOLIO as portfolio, STOCK, fact, market,
    )

    stock = market(monkeypatch, [])
    accounts = [{**account, 'account_category': 'fcn'} if account['account_id'] == 'option' else account
                for account in original_accounts]
    contract = {'derivative_contract_id': 'review-fcn', 'contract_name': 'Review FCN', 'contract_type': 'fcn',
                'currency': 'USD', 'terms': {'notional': 5000, 'issue_date': '2026-01-02',
                'underlyings': [{'instrument_id': STOCK['instrument_id'], 'deliverable': True}]}}
    transactions = [
        fact(1, 'opening_balance', '2026-01-02', 'cash', 20000),
        fact(2, 'buy', '2026-01-02', 'option', 5000, quantity=1, contract=contract, settlement_cash_account_id='cash'),
        fact(3, 'maturity_redemption', '2026-06-01', 'option', 0, quantity=1, contract=contract,
             lifecycle_event_type='fcn_knock_in', asset_deliveries=[{
                 'account_id': 'stock', 'instrument_id': STOCK['instrument_id'], 'instrument_ref': STOCK,
                 'quantity': 100, 'fair_value': 4000, 'currency': 'USD', 'fx_rate_to_contract': 1,
             }]),
    ]
    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio, accounts, transactions, start_date=date(2026, 5, 29), end_date=date(2026, 6, 1),
    )
    assert snapshots[-1]['nav'] is None
    assert not snapshots[-1].get('transaction_price_valuations')
    # The declared delivery value establishes cost, but a market quote is still
    # required: 15,000 cash + 100 shares at the independent 40 close = 19,000.
    market(monkeypatch, [('2026-06-01', 40)])
    priced = performance.build_daily_portfolio_snapshots(
        portfolio, accounts, transactions, start_date=date(2026, 5, 29), end_date=date(2026, 6, 1),
    )
    assert stock['market_data'] == []
    assert priced[-1]['nav'] == 19000
    assert priced[-1]['transaction_price_valuations'] == []
    # A later ordinary buy is an addition to the delivered shares, not a new
    # initial position; its 40 execution cannot repair the missing June 2 close.
    transactions.append(fact(4, 'buy', '2026-06-02', 'stock', 40, quantity=1, stock=True, settlement_cash_account_id='cash'))
    after_addition = performance.build_daily_portfolio_snapshots(
        {**portfolio, 'as_of_date': '2026-06-02'}, accounts, transactions,
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 2),
    )
    assert after_addition[-1]['as_of_date'] == date(2026, 6, 2)
    assert after_addition[-1]['nav'] is None
