from decimal import Decimal

import pytest
from pydantic import ValidationError

from portfolio_app.api.contracts import TransactionImportCommand, implied_conversion_rate


@pytest.mark.parametrize('source,target,currency,source_account,target_account', [
    ('780', '100', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('100', '780', 'USD', 'cash-usd-main', 'cash-hkd-main'),
    ('0.07', '0.01', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('780000000', '100000000', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('0.01', '0.07812346', 'USD', 'cash-usd-main', 'cash-hkd-main'),
    ('0.07812346', '0.01', 'HKD', 'cash-hkd-main', 'cash-usd-main'),
    ('0.010000000', '0.078123460', 'USD', 'cash-usd-main', 'cash-hkd-main'),
    ('1000e-10', '0.00000078', 'USD', 'cash-usd-main', 'cash-hkd-main'),
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
    payload['note'] = 'Corrected note only'
    payload['expected_row_version'] = record['row_version']
    edited = client.put(f"/api/portfolios/investment-studio/transactions/{record['transaction_id']}", json=payload)
    assert edited.status_code == 200, edited.text
    assert Decimal(edited.json()['source_counter_amount']) == Decimal(target)
    assert Decimal(edited.json()['source_gross_amount']) == Decimal(source)
    assert edited.json()['source_fx_rate'] == record['source_fx_rate']
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
    record = response.json()
    update = client.put(f"/api/portfolios/investment-studio/transactions/{record['transaction_id']}", json={
        'transaction_type': 'fx_conversion', 'trade_date': '2026-04-15',
        'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
        'gross_amount': record['source_gross_amount'], 'counter_amount': record['source_counter_amount'],
        'fx_rate': record['source_fx_rate'], 'currency': 'USD',
        'expected_row_version': record['row_version'], 'note': 'Only note changed',
    })
    assert update.status_code == 200, update.text
    assert update.json()['source_fx_rate'] == record['source_fx_rate']
    assert update.json()['source_counter_amount'] == record['source_counter_amount']


@pytest.mark.parametrize('field', ['gross_amount', 'counter_amount'])
@pytest.mark.parametrize('value', ['0.078123461', '0.000000001', '0', '-1'])
def test_conversion_rejects_unrepresentable_or_nonpositive_amount_without_writing(client, field, value):
    path = '/api/portfolios/investment-studio/transactions'
    before = client.get(path).json()
    response = client.post(path, json={
        'transaction_type': 'fx_conversion', 'trade_date': '2026-04-15',
        'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
        'gross_amount': '1', 'counter_amount': '7.8', 'currency': 'USD', field: value,
    })
    assert response.status_code == 422, response.text
    if Decimal(value) > 0:
        assert 'at most 8 decimal places' in response.text
    assert client.get(path).json() == before


@pytest.mark.parametrize('field', ['gross_amount', 'counter_amount'])
def test_import_command_does_not_round_an_fx_cash_fact_before_preview(field):
    with pytest.raises(ValidationError, match='at most 8 decimal places'):
        TransactionImportCommand.model_validate({
            'external_reference': 'confirmed-fx', 'asset_type': 'cash',
            'transaction_action': 'fx_conversion', 'trade_date': '2026-04-15',
            'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
            'gross_amount': '1', 'counter_amount': '7.8', 'currency': 'USD', field: '.078123461',
        })


@pytest.mark.parametrize('field', ['gross_amount', 'counter_amount'])
def test_fx_correction_rejects_rounding_without_replacing_the_confirmed_fact(client, field):
    path = '/api/portfolios/investment-studio/transactions'
    payload = {
        'transaction_type': 'fx_conversion', 'trade_date': '2026-04-15',
        'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
        'gross_amount': '.01', 'counter_amount': '.07812346', 'currency': 'USD',
    }
    original = client.post(path, json=payload)
    assert original.status_code == 200, original.text
    record = original.json()
    failed = client.put(path + '/' + record['transaction_id'], json={
        **payload, field: '.078123461', 'expected_row_version': record['row_version'],
    })
    assert failed.status_code == 422, failed.text
    selected = client.get(path + '/workspace', params={'transaction_id': record['transaction_id']}).json()['selected_transaction']
    for key in ('row_version', 'source_gross_amount', 'source_counter_amount', 'source_fx_rate'):
        assert selected[key] == record[key]


@pytest.mark.parametrize('fee_currency,fee_account', [('USD', 'cash-usd-main'), ('HKD', 'cash-hkd-main')])
def test_fx_fee_posts_only_to_the_actual_charged_currency(client, fee_currency, fee_account):
    path = '/api/portfolios/investment-studio/transactions'
    conversion = client.post(path, json={
        'transaction_type': 'fx_conversion', 'trade_date': '2026-04-15',
        'account_id': 'cash-usd-main', 'counterparty_account_id': 'cash-hkd-main',
        'gross_amount': '10', 'counter_amount': '78', 'currency': 'USD',
    })
    assert conversion.status_code == 200, conversion.text
    fee = client.post(path, json={
        'transaction_type': 'fee', 'trade_date': '2026-04-15',
        'account_id': fee_account, 'gross_amount': '2', 'currency': fee_currency,
        'fee_category': 'transaction_cost',
    })
    assert fee.status_code == 200, fee.text
    workspace = client.get(path + '/workspace', params={'transaction_id': fee.json()['transaction_id']}).json()
    cash = [row for row in workspace['ledger_postings'] if row['cash_amount_delta'] is not None]
    assert [(row['account_id'], row['currency'], row['cash_amount_delta']) for row in cash] == [(fee_account, fee_currency, -2)]
    conversion_workspace = client.get(path + '/workspace', params={'transaction_id': conversion.json()['transaction_id']}).json()
    assert sorted(row['cash_amount_delta'] for row in conversion_workspace['ledger_postings']) == [-10, 78]


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
