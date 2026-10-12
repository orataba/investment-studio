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
                line_item='revenue', value=value, observed_at=stamp(day), available_at=stamp(day))


@pytest.mark.parametrize('dataset', ['financial_statements', 'financial_facts'])
def test_financial_pruning_retains_uncertain_scope_and_every_revision(store, monkeypatch, dataset):
    single = {'endpoint': 'income-statement', 'parameters': {'symbol': 'AAPL'}}
    for day in (1, 3):
        store.ingest(dataset, [[statement('AAPL', day, day)]], source='fmp', details=single)
    unrelated = store.ingest(dataset, [[statement('MSFT', 2)]], source='fmp',
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
        store.ingest(dataset, [[row]], source=source, details=details)
    original_paths = store._paths
    unrestricted = original_paths(dataset)
    retained = original_paths(dataset, symbols=['AAPL'])
    assert len(retained) == len(unrestricted) - 1
    assert not any(unrelated['batch_id'] in path for path in retained)
    assert original_paths(dataset, symbols=None) == unrestricted
    # The reference reads all files but retains the same canonical row filters,
    # ranking, pagination and lineage. It is independent of metadata pruning.
    cases = [dict(symbols=['AAPL']), dict(symbols=['AAPL'], versions=True),
             dict(symbols=['AAPL'], as_of=stamp(2)),
             dict(symbols=['AAPL'], versions=True, as_of=stamp(2), limit=2, offset=1),
             dict(symbols=['AAPL'], start='2026-06-01', end='2026-06-30'),
             dict(symbols=['AAPL'], limit=1, offset=100), dict(symbols=[]),
             dict(symbols=['UNKNOWN']), dict(symbols=['MSFT'], batch_id=unrelated['batch_id'])]
    for options in cases:
        actual = store.query(dataset, **options)
        with monkeypatch.context() as context:
            context.setattr(store, '_paths', lambda *args, **kwargs: original_paths(*args))
            assert actual == store.query(dataset, **options)


@pytest.mark.parametrize('dataset', ['financial_statements', 'financial_facts'])
def test_financial_collector_enforces_recorded_single_company_scope(store, dataset):
    class Client:
        def get_json(self, endpoint, params):
            payload = [dict(symbol=symbol, date='2026-06-30', fiscalYear=2026, period='Q2',
                            reportedCurrency='USD', revenue=100)
                       for symbol in ('AAPL', 'MSFT')]
            return SimpleNamespace(endpoint=endpoint, params=params, payload=payload,
                                   body=json.dumps(payload).encode(), received_at=stamp(3))
    Collector(store.settings, store=store, client=Client()).financials(date(2026, 9, 1), date(2026, 9, 3), ['AAPL'])
    with store.engine.connect() as conn:
        captures = conn.execute(select(batches).where(batches.c.dataset == dataset)).mappings().all()
    assert len(captures) == 6
    assert {row['details']['endpoint'] for row in captures} == {
        'income-statement', 'balance-sheet-statement', 'cash-flow-statement'}
    assert all(row['source'] == 'fmp' and row['details']['parameters']['symbol'] == 'AAPL' for row in captures)
    assert {row['symbol'] for row in store.query(dataset, versions=True)['rows']} == {'AAPL'}
    assert store.query(dataset, symbols=['MSFT'])['rows'] == []


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


@pytest.mark.parametrize('dataset', ['analyst_estimates', 'etf_holdings'])
def test_selected_snapshot_files_match_full_scan_for_pit_pages_and_union_types(store, monkeypatch, dataset):
    old = store.ingest(dataset, [[dict(snapshot_row(dataset, 'AAA', 1, 10), extra='old')],
                                 [dict(snapshot_row(dataset, 'BBB', 1, 20), other_field='other')]], source='fixture')
    current = store.ingest(dataset, [[dict(snapshot_row(dataset, 'AAA', 2, 30), extra=7)]], source='fixture')
    cases = [dict(symbols=['AAA']), dict(symbols=['AAA'], as_of=stamp(1)),
             dict(symbols=['BBB']), dict(symbols=['AAA', 'BBB'], limit=1, offset=1),
             dict(symbols=['AAA'], versions=True), dict(symbols=['AAA'], batch_id=old['batch_id']),
             dict(symbols=['AAA'], batch_ids=[current['batch_id']]), dict(symbols=['UNKNOWN'])]
    query_facts = store._query_facts
    expected = []
    with monkeypatch.context() as patch:
        patch.setattr(store, '_query_facts', lambda name, paths, selected=None: query_facts(name, paths))
        expected = [store.query(dataset, **case) for case in cases]
    with store.query_session():
        assert [store.query(dataset, **case) for case in cases] == expected
    assert expected[0]['rows'][0]['extra'] == '7'  # preserve the full union's type
    assert store._history_session is None


def test_batch_reader_binds_global_snapshot_schema_once_and_invalidates_on_new_file(store, monkeypatch):
    from studio_market.numeric import store as module
    for symbol in ('AAA', 'BBB'):
        store.ingest('etf_holdings', [[snapshot_row('etf_holdings', symbol, 1, 10)]], source='fixture')
    connect = module.duckdb.connect
    file_reads = []
    class Connection:
        def __init__(self, *args, **kwargs): self.actual = connect(*args, **kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): self.actual.close()
        def __getattr__(self, name): return getattr(self.actual, name)
        def from_parquet(self, paths, **kwargs):
            file_reads.append(tuple(paths))
            return self.actual.from_parquet(paths, **kwargs)
    monkeypatch.setattr(module.duckdb, 'connect', Connection)
    with store.query_session():
        store.query('etf_holdings', symbols=['AAA'])
        store.query('etf_holdings', symbols=['BBB'])
        assert [len(paths) for paths in file_reads] == [2, 1, 1]
        store.ingest('etf_holdings', [[dict(snapshot_row('etf_holdings', 'AAA', 2, 20), new_field=1)]], source='fixture')
        latest = store.query('etf_holdings', symbols=['AAA'])
        assert [len(paths) for paths in file_reads] == [2, 1, 1, 3, 1]
        assert latest['rows'][0]['new_field'] == 1
        # A later empty complete snapshot must clear holdings, even though the
        # old Parquet schema is already bound in this same serial batch.
        store.ingest('etf_holdings', [], source='fixture', observed_at=stamp(3),
                     details={'snapshot_scopes': [{'symbol': 'AAA', 'snapshot_at': stamp(3).isoformat()}]})
        assert store.query('etf_holdings', symbols=['AAA'])['rows'] == []
