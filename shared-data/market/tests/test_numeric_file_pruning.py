"""File pruning must preserve the canonical query and source-clock contracts."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import json

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector
from studio_market.numeric.schema import batches

UTC = timezone.utc


def stamp(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path/'data'))
    result.create_schema_for_testing()
    yield result
    result.close()


def statement(symbol, day, value=1):
    return dict(symbol=symbol, statement_type='income', period_end=date(2026, 6, 30),
                fiscal_year=2026, fiscal_period='Q2', reported_currency='USD',
                value=value, observed_at=stamp(day), available_at=stamp(day))


def test_financial_pruning_retains_uncertain_scope_and_every_revision(store, monkeypatch):
    single = {'endpoint': 'income-statement', 'parameters': {'symbol': 'AAPL'}}
    for day in (1, 3):
        store.ingest('financial_statements', [[statement('AAPL', day, day)]], source='fmp', details=single)
    unrelated = store.ingest('financial_statements', [[statement('MSFT', 2)]], source='fmp',
                             details={'endpoint': 'income-statement', 'parameters': {'symbol': 'MSFT'}})
    uncertain = [
        ('imported_market_archive', single), ('another_provider', single),
        ('fmp', {'endpoint': 'income-statement-bulk', 'parameters': {'symbol': 'MSFT'}}),
        ('fmp', {'endpoint': 'future-endpoint', 'parameters': {'symbol': 'MSFT'}}),
        ('fmp', {}), ('fmp', {'endpoint': None}), ('fmp', {'endpoint': ['income-statement']}),
        *[('fmp', {'endpoint': 'income-statement', 'parameters': {'symbol': value}})
          for value in (None, '', [], 123, ' msft ', 'msft')],
    ]
    for index, (source, details) in enumerate(uncertain):
        row = statement('AAPL', 2, 10 + index)
        row['period_end'] = date(2026, 5, index + 1)
        store.ingest('financial_statements', [[row]], source=source, details=details)
    original_paths = store._paths
    unrestricted = original_paths('financial_statements')
    retained = original_paths('financial_statements', symbols=['AAPL'])
    assert len(retained) == len(unrestricted) - 1
    assert not any(unrelated['batch_id'] in path for path in retained)
    assert original_paths('financial_statements', symbols=None) == unrestricted
    # The reference reads all files but retains the same canonical row filters,
    # ranking, pagination and lineage. It is independent of metadata pruning.
    cases = [dict(symbols=['AAPL']), dict(symbols=['AAPL'], versions=True),
             dict(symbols=['AAPL'], as_of=stamp(2)),
             dict(symbols=['AAPL'], versions=True, as_of=stamp(2), limit=2, offset=1),
             dict(symbols=['AAPL'], start='2026-06-01', end='2026-06-30'),
             dict(symbols=['AAPL'], limit=1, offset=100), dict(symbols=[]),
             dict(symbols=['UNKNOWN']), dict(symbols=['MSFT'], batch_id=unrelated['batch_id'])]
    for options in cases:
        actual = store.query('financial_statements', **options)
        with monkeypatch.context() as context:
            context.setattr(store, '_paths', lambda *args, **kwargs: original_paths(*args))
            assert actual == store.query('financial_statements', **options)


def test_financial_collector_enforces_recorded_single_company_scope(store):
    class Client:
        def get_json(self, endpoint, params):
            payload = [dict(symbol=symbol, date='2026-06-30', fiscalYear=2026, period='Q2',
                            reportedCurrency='USD', revenue=100)
                       for symbol in ('AAPL', 'MSFT')]
            return SimpleNamespace(endpoint=endpoint, params=params, payload=payload,
                                   body=json.dumps(payload).encode(), received_at=stamp(3))
    Collector(store.settings, store=store, client=Client()).financials(date(2026, 9, 1), date(2026, 9, 3), ['AAPL'])
    with store.engine.connect() as conn:
        captures = conn.execute(select(batches).where(batches.c.dataset == 'financial_statements')).mappings().all()
    assert len(captures) == 6
    assert {row['details']['endpoint'] for row in captures} == {
        'income-statement', 'balance-sheet-statement', 'cash-flow-statement'}
    assert all(row['source'] == 'fmp' and row['details']['parameters']['symbol'] == 'AAPL' for row in captures)
    assert {row['symbol'] for row in store.query('financial_statements', versions=True)['rows']} == {'AAPL'}
    assert store.query('financial_statements', symbols=['MSFT'])['rows'] == []


def snapshot_row(dataset, symbol, day, value):
    if dataset == 'analyst_estimates':
        return dict(symbol=symbol, estimate_period='annual', target_period_end=date(2027, 12, 31),
                    eps_avg=value, observed_at=stamp(day), available_at=stamp(day))
    return dict(etf_symbol=symbol, holding_key='holding', holding_symbol='AAPL',
                snapshot_date=date(2026, 9, day), weight_percent=value,
                observed_at=stamp(day), available_at=stamp(day))


@pytest.mark.parametrize('dataset', ['analyst_estimates', 'etf_holdings'])
def test_snapshot_queries_keep_union_fields_from_older_and_other_scopes(store, dataset):
    store.ingest(dataset, [[dict(snapshot_row(dataset, 'AAA', 1, 10), legacy_field='retained')],
                           [dict(snapshot_row(dataset, 'BBB', 1, 20), another_field='other scope')]], source='fixture')
    store.ingest(dataset, [[snapshot_row(dataset, 'AAA', 2, 30)]], source='fixture')
    result = store.query(dataset, symbols=['AAA'])
    assert result['total'] == 1
    assert result['rows'][0]['legacy_field'] is None
    assert result['rows'][0]['another_field'] is None
    assert store.query(dataset, symbols=['AAA'], as_of=stamp(1))['rows'][0]['legacy_field'] == 'retained'
