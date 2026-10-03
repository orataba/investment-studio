#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
"${PYTHON_BIN:-$ROOT/.venv/bin/python}" - <<'PY'
"""Evaluate the production audit SQL on PostgreSQL session-local fixtures only."""
import ast
import getpass
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Json

source = ast.parse(Path('infra/scripts/audit_live_data.py').read_text())
queries = []
for node in ast.walk(source):
    if isinstance(node, ast.Call):
        keywords = {item.arg: item.value for item in node.keywords}
        name = keywords.get('name')
        if isinstance(name, ast.Constant) and name.value == 'portfolio_derivative_contract_integrity':
            queries.append(keywords['query'].value)
assert len(queries) == 1
query = queries[0]
for production, fixture in {
    'portfolio.derivative_contract_record': 'pg_temp.audit_contract',
    'instrument_data.instrument': 'pg_temp.audit_instrument',
    'portfolio.transaction_record': 'pg_temp.audit_transaction',
    'portfolio.portfolio_daily_holding_snapshot': 'pg_temp.audit_holding',
}.items():
    assert production in query
    query = query.replace(production, fixture)
assert 'portfolio.' not in query and 'instrument_data.' not in query

settings = {
    'host': os.environ.get('INVESTMENT_STUDIO_TEST_DB_HOST', '127.0.0.1'),
    'port': os.environ.get('INVESTMENT_STUDIO_TEST_DB_PORT', '5432'),
    'user': os.environ.get('INVESTMENT_STUDIO_TEST_DB_USER', getpass.getuser()),
    'dbname': 'postgres',
    'connect_timeout': 10,
}
if 'INVESTMENT_STUDIO_TEST_DB_PASSWORD' in os.environ:
    settings['password'] = os.environ['INVESTMENT_STUDIO_TEST_DB_PASSWORD']

with psycopg.connect(**settings) as connection:
    connection.execute("SET LOCAL statement_timeout = '10s'")
    connection.execute('''CREATE TEMP TABLE audit_contract (
        portfolio_id text, derivative_contract_id text, contract_type text,
        account_id text, currency text, terms_json json)''')
    connection.execute('CREATE TEMP TABLE audit_instrument (instrument_id text)')
    connection.execute('''CREATE TEMP TABLE audit_transaction (
        portfolio_id text, derivative_contract_id text, account_id text, currency text,
        transaction_type text, lifecycle_event_type text, quantity numeric,
        gross_amount numeric, fees numeric, taxes numeric,
        settlement_cash_account_id text, position_effective_date date, trade_date date)''')
    connection.execute('''CREATE TEMP TABLE audit_holding (
        portfolio_id text, derivative_contract_id text, position_reference_id text,
        account_id text, currency text)''')
    connection.execute("INSERT INTO audit_instrument VALUES ('underlying')")
    base_terms = {'underlying_instrument_id': 'underlying', 'option_type': 'call',
                  'expiry_date': '2026-10-02', 'strike': 100, 'contract_multiplier': 100}
    connection.execute("INSERT INTO audit_contract VALUES ('portfolio', 'option', 'option', 'broker', 'USD', %s)",
                       (Json(base_terms),))
    checked = 0

    def expect(label, issues):
        global checked
        actual = connection.execute(query).fetchone()[0]
        assert actual == issues, (label, actual, issues)
        checked += 1

    # Missing and JSON null both represent unconfirmed terms. Presence alone
    # must not reject a valid contract, while non-enum JSON values remain invalid.
    expect('missing settlement type', 0)
    for value in [None, 'physical', 'cash', '', 'Cash', 'cash ', 'net', False, 0, [], {}]:
        connection.execute('UPDATE audit_contract SET terms_json=%s',
                           (Json({**base_terms, 'settlement_type': value}),))
        expect(f'settlement type {value!r}', 0 if value is None or value in ('physical', 'cash') else 1)

    for terms, label in [({**base_terms, 'strike': 0}, 'zero strike'),
                         ({**base_terms, 'underlying_instrument_id': 'missing'}, 'missing Registry underlying')]:
        connection.execute('UPDATE audit_contract SET terms_json=%s', (Json(terms),))
        expect(label, 1)

    connection.execute('UPDATE audit_contract SET terms_json=%s',
                       (Json({**base_terms, 'settlement_type': 'cash'}),))
    connection.execute('''INSERT INTO audit_transaction VALUES (
        'portfolio', 'option', 'broker', 'USD', 'maturity_redemption',
        'option_long_cash_settlement', 1, 100, 0, 0, 'cash', NULL, '2026-10-02')''')
    expect('explicit cash result', 0)
    for field, value, original, label in [
        ('gross_amount', 0, 100, 'cash result needs positive proceeds'),
        ('settlement_cash_account_id', None, 'cash', 'cash result needs cash account'),
        ('quantity', 0, 1, 'cash result needs positive quantity'),
        ('transaction_type', 'buy', 'maturity_redemption', 'cash result needs matching action'),
        ('account_id', 'another', 'broker', 'transaction account matches contract'),
        ('currency', 'EUR', 'USD', 'transaction currency matches contract'),
    ]:
        connection.execute(f'UPDATE audit_transaction SET {field}=%s', (value,))
        expect(label, 1)
        connection.execute(f'UPDATE audit_transaction SET {field}=%s', (original,))
    connection.execute("UPDATE audit_transaction SET lifecycle_event_type='option_long_expiry', gross_amount=0, settlement_cash_account_id=NULL")
    expect('explicit worthless expiry', 0)
    connection.execute("UPDATE audit_transaction SET trade_date='2026-10-01'")
    expect('expiry cannot precede contract date', 1)
    connection.execute("UPDATE audit_transaction SET trade_date='2026-10-02', gross_amount=1")
    expect('expiry cannot carry proceeds', 1)
    connection.execute('UPDATE audit_transaction SET gross_amount=0, fees=1')
    expect('expiry cannot carry fees', 1)
    connection.execute('UPDATE audit_transaction SET fees=0')
    connection.execute("INSERT INTO audit_holding VALUES ('portfolio', 'option', 'option', 'broker', 'EUR')")
    expect('holding currency matches contract', 1)

print(f'Real PostgreSQL derivative audit passed {checked} scenarios using temporary tables only.')
PY
