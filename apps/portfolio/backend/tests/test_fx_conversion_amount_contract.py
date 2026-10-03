from decimal import Decimal

import pytest

from portfolio_app.api.contracts import implied_conversion_rate


@pytest.mark.parametrize('source,target,currency,source_account,target_account', [
    ('780', '100', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('100', '780', 'USD', 'cash-usd-main', 'cash-hkd-main'),
    ('0.07', '0.01', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('780000000', '100000000', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
])
def test_conversion_preserves_both_cash_facts_through_create_reload_and_edit(client, source, target, currency, source_account, target_account):
    payload = dict(transaction_type='fx_conversion', trade_date='2026-04-15',
        account_id=source_account, counterparty_account_id=target_account,
        gross_amount=source, counter_amount=target, currency=currency, fees=0, taxes=0)
    response = client.post('/api/portfolios/investment-studio/transactions', json=payload)
    assert response.status_code == 200, response.text
    record = response.json()
    assert Decimal(record['source_gross_amount']) == Decimal(source)
    assert Decimal(record['source_counter_amount']) == Decimal(target)
    assert Decimal(record['source_fx_rate']) == implied_conversion_rate(Decimal(source), Decimal(target))
    assert record['fees'] == record['taxes'] == 0
    payload['fx_rate'] = record['source_fx_rate']
    payload['expected_row_version'] = record['row_version']
    edited = client.put(f"/api/portfolios/investment-studio/transactions/{record['transaction_id']}", json=payload)
    assert edited.status_code == 200, edited.text
    assert Decimal(edited.json()['source_counter_amount']) == Decimal(target)
    workspace = client.get('/api/portfolios/investment-studio/transactions/workspace',
        params={'transaction_id': record['transaction_id']}).json()
    assert workspace['selected_transaction']['counter_amount'] == float(target)
    cash_postings = workspace['ledger_postings']
    assert sorted(float(row['cash_amount_delta']) for row in cash_postings) == sorted([-float(source), float(target)])


def test_conversion_fee_is_separate_and_inconsistent_supplied_rate_is_rejected(client):
    payload = dict(transaction_type='fx_conversion', trade_date='2026-04-15',
        account_id='cash-hkd-main', counterparty_account_id='cash-usd-main',
        gross_amount='780', counter_amount='100', currency='HKD')
    fee = client.post('/api/portfolios/investment-studio/transactions', json={**payload, 'fees': 1})
    assert fee.status_code == 422
    assert 'must not carry fees' in fee.text
    mismatch = client.post('/api/portfolios/investment-studio/transactions', json={**payload, 'fx_rate': '.128205'})
    assert mismatch.status_code == 400
    assert 'rounded to 12 decimals' in mismatch.text


def test_conversion_accepts_confirmed_quote_whose_product_rounds_to_amount_precision(client):
    response = client.post('/api/portfolios/investment-studio/transactions', json={
        'transaction_type': 'fx_conversion', 'trade_date': '2026-04-15',
        'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
        'gross_amount': '.01', 'counter_amount': '.07812346', 'fx_rate': '7.8123456789', 'currency': 'USD',
    })
    assert response.status_code == 200, response.text
    assert Decimal(response.json()['source_counter_amount']) == Decimal('.07812346')
    assert Decimal(response.json()['source_fx_rate']) == Decimal('7.8123456789')


def test_export_reference_warns_about_existing_fact_without_claiming_identity_match(client):
    record = client.post('/api/portfolios/investment-studio/transactions', json={
        'transaction_type': 'deposit', 'trade_date': '2026-04-15', 'account_id': 'cash-usd-main',
        'gross_amount': 100, 'currency': 'USD',
    }).json()
    csv_text = ('record_reference,asset_type,transaction_action,trade_date,account_id,gross_amount,currency\n'
        f"{record['transaction_id']},cash,deposit,2026-04-15,cash-usd-main,100,USD\n")
    preview = client.post('/api/portfolios/investment-studio/transactions/files/preview',
        files={'file': ('transactions.csv', csv_text.encode(), 'text/csv')})
    assert preview.status_code == 200, preview.text
    warnings = ' '.join(preview.json()['warnings'])
    assert 'add these 1 transactions again' in warnings
    assert f"Row 2: file reference {record['transaction_id']} already exists" in warnings
