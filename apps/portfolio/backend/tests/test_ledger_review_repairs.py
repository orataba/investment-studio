from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance
from portfolio_app.services.transaction_dates import transaction_performance_effective_date

REF = {
    'instrument_id': 'audit-fund', 'instrument_name': 'Audit fund',
    'instrument_type': 'public_fund', 'currency': 'USD', 'identifiers': [],
}
ACCOUNTS = [
    {
        'portfolio_id': 'audit', 'account_id': key, 'account_name': key,
        'account_type': 'deposit_account' if key == 'cash' else 'securities_account',
        'account_category': 'cash' if key == 'cash' else 'security',
        'currency': 'USD', 'cost_basis_method': None if key == 'cash' else 'fifo',
        'default_settlement_cash_account_id': None if key == 'cash' else 'cash',
        'status': 'active',
    }
    for key in ('cash', 'a', 'b')
]


def fact(sequence, kind, day, amount, quantity=None, account='a', **overrides):
    return {
        'transaction_id': f'txn-{sequence:04d}', 'transaction_sequence': sequence,
        'portfolio_id': 'audit', 'transaction_type': kind, 'trade_date': day,
        'trade_at': day + 'T10:00:00Z', 'created_at': '2026-09-06T00:00:00Z',
        'settlement_date': day, 'account_id': account,
        'instrument_id': REF['instrument_id'] if account != 'cash' else None,
        'instrument_ref': REF if account != 'cash' else None,
        'gross_amount': amount, 'quantity': quantity, 'fees': 0, 'taxes': 0,
        'currency': 'USD', **overrides,
    }


def position_lots(facts, day='2026-06-05', price=10):
    return ledger.build_position_lots(
        'audit', ACCOUNTS, facts, as_of_date=date.fromisoformat(day),
        corporate_actions=[], pricing_map={REF['instrument_id']: {'value': price, 'price_scale': 1}},
        resolve_pricing=False,
    )


@pytest.mark.parametrize('mode', ['transfer', 'historical_opening', 'intraday_transfer'])
def test_fifo_retains_original_acquisition_order_in_lots_and_postings(mode):
    if mode == 'historical_opening':
        facts = [
            fact(1, 'opening_balance', '2026-03-01', 2000, 100, acquisition_date='2026-02-01'),
            fact(2, 'opening_balance', '2026-03-01', 1000, 100, acquisition_date='2026-01-01'),
        ]
        account = 'a'
    else:
        second_date = '2026-01-01' if mode == 'intraday_transfer' else '2026-02-01'
        facts = [
            fact(1, 'buy', '2026-01-01', 1000, 100),
            fact(2, 'buy', second_date, 2000, 100, account='b', trade_at=second_date+'T11:00:00Z'),
            fact(3, 'transfer_out', '2026-03-01', 1000, 100, transfer_object_type='position', transfer_group_id='g', counterparty_account_id='b'),
            fact(4, 'transfer_in', '2026-03-01', 1000, 100, account='b', transfer_object_type='position', transfer_group_id='g', counterparty_account_id='a'),
        ]
        account = 'b'
    sale = fact(5, 'sell', '2026-04-01', 3000, 100, account=account)
    facts.append(sale)
    lots = [item for item in position_lots(facts, price=30) if item['account_id'] == account]
    assert sum(item['realized_pnl'] for item in lots) == 2000
    assert sum(item['remaining_cost_basis'] for item in lots) == 2000
    assert sum(item['unrealized_pnl'] for item in lots) == 1000
    state = ledger._build_position_state(facts, corporate_actions=[])
    assert state[(account, REF['instrument_id'])]['cost_basis'] == 2000
    postings = ledger.derive_ledger_postings('audit', facts, corporate_actions=[])
    assert next(item for item in postings if item['transaction_id'] == sale['transaction_id'])['cost_basis_delta'] == -1000


def test_same_batch_cash_replay_uses_transaction_sequence():
    facts = [
        fact(1, 'opening_balance', '2026-01-01', 100, account='cash'),
        fact(2, 'withdrawal', '2026-01-02', 100, account='cash'),
        fact(3, 'deposit', '2026-01-02', 100, account='cash'),
    ]
    postings = ledger.derive_ledger_postings('audit', facts, corporate_actions=[])
    states, impacts = ledger._replay_settled_monetary_postings(
        postings=postings, as_of_date=date(2026, 1, 2), base_currency='CNY',
        direct_fx_instruments={}, instrument_detail_cache={},
        resolve_fx_rate_on=lambda **kwargs: {'rate': 7 if kwargs['as_of_date'].day == 1 else 8, 'stale': False},
        impact_transaction_ids={'txn-0002'},
    )
    assert states[('cash', 'USD')]['amount'] == 100
    assert states[('cash', 'USD')]['historical_cost_basis_base'] == 800
    assert impacts[0]['realized_cash_fx_pnl_base'] == 100


def reinvestment_facts():
    return [
        fact(1, 'opening_balance', '2026-06-01', 1000, 100, acquisition_date='2026-05-01'),
        fact(2, 'dividend_reinvestment', '2026-06-05', 100, 10, entitlement_date='2026-06-02'),
    ]


@pytest.mark.parametrize('day,price,expected_quantity,expected_pending,expected_nav', [
    ('2026-06-01', 10, 100, 0, 1000),
    ('2026-06-02', 9, 100, 100, 1000),
    ('2026-06-04', 9, 100, 100, 1000),
    ('2026-06-05', 10, 110, 0, 1100),
])
def test_reinvestment_receivable_converts_to_units_without_new_cash(day, price, expected_quantity, expected_pending, expected_nav):
    facts = reinvestment_facts()
    as_of = date.fromisoformat(day)
    lots = position_lots(facts, day, price)
    postings = ledger.derive_ledger_postings('audit', facts, as_of_date=as_of, corporate_actions=[])
    pending = ledger.pending_monetary_balances_from_postings(postings=postings, as_of_date=as_of, base_currency='USD', direct_fx_instruments={}, instrument_detail_cache={})
    pending_amount = sum(item['amount'] for item in pending)
    assert sum(item['remaining_quantity'] for item in lots) == expected_quantity
    assert pending_amount == expected_pending
    assert sum(item['current_market_value'] for item in lots) + pending_amount == expected_nav
    assert not any(item.get('cash_amount_delta') for item in postings)
    assert sum(item['income_cash_amount'] for item in lots) == (0 if day == '2026-06-01' else 100)
    assert ledger._build_position_state(facts, as_of_date=as_of, corporate_actions=[])[('a', REF['instrument_id'])]['quantity'] == expected_quantity
    assert transaction_performance_effective_date(facts[1]) == date(2026, 6, 2)


def test_reinvestment_income_and_daily_nav_use_entitlement_boundary(monkeypatch):
    details = {
        **REF, 'quote_selection_policy': {role: ['official_nav'] for role in ('valuation', 'trading', 'total_return', 'chart', 'reference')},
        'market_data': [
            {'metric_family': 'nav', 'quote_basis': 'official_nav', 'as_of_date': f'2026-06-0{day}',
             'value': value, 'currency': 'USD', 'price_unit': 'per_unit', 'price_scale': 1, 'status': 'complete'}
            for day, value in [(1, 10), (2, 9), (3, 9), (4, 9), (5, 10)]
        ],
    }
    monkeypatch.setattr(performance, 'get_shared_fx_rates', lambda: {'rates': [], 'maintained_pairs': [], 'supported_currencies': ['USD']})
    monkeypatch.setattr(performance, 'get_registry_instrument_detail', lambda key: deepcopy(details))
    monkeypatch.setattr(performance, 'list_registry_corporate_actions', lambda *args, **kwargs: [])
    monkeypatch.setattr(ledger, 'list_registry_corporate_actions', lambda *args, **kwargs: [])
    snapshots = performance.build_daily_portfolio_snapshots(
        {'portfolio_id': 'audit', 'base_currency': 'USD', 'inception_date': '2026-06-01',
         'valuation_timezone': 'Asia/Shanghai', 'valuation_cutoff_policy': 'latest_complete_eod'},
        ACCOUNTS, reinvestment_facts(), start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
    )
    rows = {str(item['as_of_date']): item for item in snapshots}
    assert rows['2026-06-02']['nav'] == 1000
    assert rows['2026-06-02']['daily_twr'] == pytest.approx(0)
    assert rows['2026-06-02']['income_cash_amount'] == 100
    assert rows['2026-06-05']['nav'] == 1100
    assert rows['2026-06-05']['income_cash_amount'] == rows['2026-06-04']['income_cash_amount'] == 100


@pytest.mark.parametrize('fee_type', ['fee', 'tax'])
@pytest.mark.parametrize('asset_kind', ['security', 'option_long', 'option_written'])
def test_first_day_asset_charge_uses_event_time_not_dividend_bod(fee_type, asset_kind):
    opening = fact(1, 'buy', '2026-01-02', 500, 1, settlement_cash_account_id='cash')
    expense = fact(2, fee_type, '2026-01-02', 5, trade_at='2026-01-02T16:00:00Z', settlement_cash_account_id='cash')
    if asset_kind != 'security':
        contract = {'derivative_contract_id': 'call', 'contract_type': 'option', 'currency': 'USD', 'terms': {
            'underlying_instrument_id': REF['instrument_id'], 'option_type': 'call', 'strike': 100,
            'expiry_date': '2026-06-30', 'contract_multiplier': 100,
        }}
        opening.update(instrument_id=None, instrument_ref=None, derivative_contract_id='call', derivative_contract=contract)
        expense.update(instrument_id=None, instrument_ref=None, derivative_contract_id='call', derivative_contract=contract)
        if asset_kind == 'option_written':
            opening['transaction_type'] = 'option_write'
    ledger.validate_transaction_position_history('audit', [opening, expense], corporate_actions=[])
    if asset_kind == 'security':
        assert position_lots([opening, expense])[0]['expense_cash_amount'] == 5
    earlier_expense = {**expense, 'trade_at': '2026-01-02T09:00:00Z'}
    with pytest.raises(ValueError, match='requires an account position'):
        ledger.validate_transaction_position_history('audit', [opening, earlier_expense], corporate_actions=[])
    historical_expense = {**expense, 'entitlement_date': '2026-01-02'}
    with pytest.raises(ValueError, match='requires an account position'):
        ledger.validate_transaction_position_history('audit', [opening, historical_expense], corporate_actions=[])


def test_accounts_workspace_loads_reinvestment_before_its_recorded_trade_date(client, monkeypatch):
    from portfolio_app.api.routes import accounts as routes
    facts = reinvestment_facts()
    monkeypatch.setattr(routes, 'get_portfolio', lambda pid: {'base_currency': 'USD', 'as_of_date': '2026-06-05'})
    monkeypatch.setattr(routes, 'list_accounts', lambda pid: ACCOUNTS)
    monkeypatch.setattr(routes, 'list_transactions', lambda pid, **kwargs: [item for item in facts if not kwargs.get('end_date') or item['trade_date'] <= str(kwargs['end_date'])])
    monkeypatch.setattr(ledger, 'list_registry_corporate_actions', lambda *args, **kwargs: [])
    monkeypatch.setattr(ledger, '_resolve_pricing_quote_map', lambda *args, **kwargs: {REF['instrument_id']: {'value': 9, 'price_scale': 1}})
    response = client.get('/api/portfolios/investment-studio/accounts/workspace?as_of_date=2026-06-02')
    assert response.status_code == 200, response.text
    asset_account = next(item for item in response.json()['accounts'] if item['account']['account_id'] == 'a')
    assert asset_account['pending_settlement'] == 100
    assert asset_account['account_value_base'] == 1000
