"""A price-adjustment generation is visible as a whole, including after replication."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event
import zipfile

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector
from studio_market.numeric.price_revisions import pending_revisions
from studio_market.numeric.providers.fmp import FmpResponse
from studio_market.numeric.replication import export_bundle, import_bundle
from studio_market.numeric.schema import batches

UTC = timezone.utc
START, END = date(2000, 1, 3), date(2026, 9, 8)
OLD = datetime(2026, 9, 1, tzinfo=UTC)
CAPTURE = datetime(2026, 9, 3, tzinfo=UTC)
WINDOWS = []
cursor = START
while cursor <= END:
    stop = min(cursor + timedelta(days=1459), END)
    WINDOWS.append((cursor, stop))
    cursor = stop + timedelta(days=1)


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / 'data'))
    result.create_schema_for_testing()
    yield result
    result.close()


def seed(store, dataset, *, points=None):
    days = points or [begin for begin, _ in WINDOWS]
    store.ingest(dataset, [[dict(symbol='SPY', date=day, close=100, adjusted_close=100,
                                observed_at=OLD) for day in days]], source='fixture')
    store.ingest('stock_splits', [], source='fixture', observed_at=OLD + timedelta(days=1),
                 details={'observed_at': OLD + timedelta(days=1), 'price_revision_requests': [
                     {'symbol': 'SPY', 'effective_date': '2026-09-02'}]})
    return pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END)


def prices_and_sources(rows):
    return [{key: row[key] for key in ('date', 'close', 'adjusted_close', 'source_id', 'observed_at', 'available_at')}
            for row in rows]


class Prices:
    def __init__(self, *, raw=False, failure=None, before_request=None, empty_first=False):
        self.raw, self.failure, self.before_request = raw, failure, before_request
        self.empty_first = empty_first

    def get_json(self, endpoint, params):
        part = next(index for index, (begin, _) in enumerate(WINDOWS) if params['from'] == begin.isoformat())
        adjusted = endpoint.endswith('/dividend-adjusted')
        if not adjusted and self.before_request:
            self.before_request(part)
        if part == self.failure:
            raise ValueError('source segment unavailable')
        value = 100 if self.raw and not adjusted else 50
        row = dict(symbol='SPY', date=params['from'], open=value, high=value, low=value, close=value,
                   adjOpen=value, adjHigh=value, adjLow=value, adjClose=value, volume=1)
        rows = [] if self.empty_first and part == 0 else [row]
        clock = CAPTURE + timedelta(hours=part, minutes=int(adjusted))
        body = json.dumps({'endpoint': endpoint, 'params': params, 'rows': rows}).encode()
        return FmpResponse(endpoint, params, clock, body, rows)


@pytest.mark.parametrize('raw', [False, True])
@pytest.mark.parametrize('failure', [0, len(WINDOWS) // 2, len(WINDOWS) - 1])
def test_first_middle_or_last_failure_never_publishes_partial_adjustment(store, raw, failure):
    dataset = 'raw_eod_daily' if raw else 'us_eod_daily'
    obligations = seed(store, dataset)
    original = store.query(dataset)['rows']
    collector = Collector(store.settings, store=store, client=Prices(raw=raw, failure=failure))
    with pytest.raises(ValueError, match='source segment unavailable'):
        collector.symbol_prices(['SPY'], START, END, raw=raw, revision_requests=obligations)
    assert store.query(dataset)['rows'] == original
    assert store.query(dataset, versions=True)['rows'] == original
    assert pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END) == obligations


@pytest.mark.parametrize('raw', [False, True])
def test_concurrent_reader_sees_old_then_whole_completed_history(store, raw):
    dataset = 'raw_eod_daily' if raw else 'us_eod_daily'
    obligations = seed(store, dataset)
    original = store.query(dataset)['rows']
    paused, release = Event(), Event()

    def pause(part):
        if part == 1:
            paused.set()
            assert release.wait(10)

    collector = Collector(store.settings, store=store, client=Prices(raw=raw, before_request=pause))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(collector.symbol_prices, ['SPY'], START, END,
                             raw=raw, revision_requests=obligations)
        try:
            assert paused.wait(10)
            assert store.query(dataset)['rows'] == original
            assert pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END) == obligations
        finally:
            release.set()
        future.result(timeout=10)
    rows = store.query(dataset)['rows']
    assert {row['adjusted_close'] for row in rows} == {50}
    assert {row['close'] for row in rows} == ({100} if raw else {50})
    assert len({row['batch_id'] for row in rows}) == 1
    assert pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END) == {}


@pytest.mark.parametrize('raw', [False, True])
def test_capture_clock_and_original_sources_survive_pit_and_replication(store, tmp_path, raw):
    dataset = 'raw_eod_daily' if raw else 'us_eod_daily'
    obligations = seed(store, dataset, points=[begin for begin, _ in WINDOWS[1:]])
    original = store.query(dataset)['rows']
    collector = Collector(store.settings, store=store, client=Prices(raw=raw, empty_first=True))
    collector.symbol_prices(['SPY'], START, END, raw=raw, revision_requests=obligations)
    completed = CAPTURE + timedelta(hours=len(WINDOWS) - 1, minutes=1)
    rows = store.query(dataset)['rows']
    assert {row['adjusted_close'] for row in rows} == {50}
    assert prices_and_sources(store.query(dataset, as_of=completed - timedelta(microseconds=1))['rows']) == prices_and_sources(original)
    assert store.query(dataset, as_of=completed)['rows'] == rows
    assert {datetime.fromisoformat(row['observed_at']) for row in rows} == {completed}
    assert {datetime.fromisoformat(row['available_at']) for row in rows} == {completed}
    assert min(datetime.fromisoformat(row['collected_at']) for row in rows) < completed
    for row in original:
        assert store.read_source(row['source_id'])['adjusted_close'] == 100

    archive = tmp_path / 'prices.zip'
    export_bundle(store.settings, archive)
    with zipfile.ZipFile(archive) as package:
        manifest = json.loads(package.read('manifest.json'))
        capture = next(batch for batch in manifest['batches'] if batch['id'] == rows[0]['batch_id'])
        parts = capture['details']['source_parts']
        assert len(parts) == len(WINDOWS)
        assert datetime.fromisoformat(parts[0]['full_observed_at']) == CAPTURE
        assert datetime.fromisoformat(parts[0]['adjusted_observed_at']) == CAPTURE + timedelta(minutes=1)
        assert all(part[key] in package.namelist() for part in parts for key in ('raw_ref', 'adjusted_raw_ref'))
    replica = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'replica.db'}", tmp_path / 'replica'))
    replica.create_schema_for_testing()
    try:
        import_bundle(replica.settings, archive)
        assert replica.query(dataset)['rows'] == rows
        assert prices_and_sources(replica.query(dataset, as_of=completed - timedelta(microseconds=1))['rows']) == prices_and_sources(original)
        assert pending_revisions(replica, dataset=dataset, symbols=['SPY'], end=END) == {}
    finally:
        replica.close()


@pytest.mark.parametrize('raw', [False, True])
def test_completed_old_capture_cannot_acknowledge_action_arriving_during_acquisition(store, raw):
    dataset = 'raw_eod_daily' if raw else 'us_eod_daily'
    original = seed(store, dataset)
    newer = {}

    def new_action(part):
        if part == 1:
            store.ingest('stock_splits', [], source='fixture', observed_at=CAPTURE,
                         details={'observed_at': CAPTURE, 'price_revision_requests': [
                             {'symbol': 'SPY', 'effective_date': '2026-09-02'}]})
            newer.update(pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END))

    collector = Collector(store.settings, store=store, client=Prices(raw=raw, before_request=new_action))
    collector.symbol_prices(['SPY'], START, END, raw=raw, revision_requests=original)
    assert newer and newer != original
    assert pending_revisions(store, dataset=dataset, symbols=['SPY'], end=END) == newer


def test_raw_overlap_detects_change_without_publishing_it_before_failed_rebuild(store):
    days = ['2025-01-02', '2026-09-01']
    store.ingest('raw_eod_daily', [[dict(symbol='SPY', date=day, close=100, adjusted_close=100,
                                       observed_at=OLD) for day in days]], source='fixture')
    original = store.query('raw_eod_daily')['rows']

    class Client:
        def get_json(self, endpoint, params):
            if params['from'] == '2000-01-03':
                raise ValueError('full history unavailable')
            adjusted = endpoint.endswith('/dividend-adjusted')
            value = 90 if adjusted else 100
            rows = [dict(symbol='SPY', date='2026-09-01', adjOpen=value, adjHigh=value,
                         adjLow=value, adjClose=value, volume=1)]
            return FmpResponse(endpoint, params, CAPTURE, json.dumps(rows).encode(), rows)

    result = Collector(store.settings, store=store, client=Client()).raw_eod(
        date(2026, 9, 1), date(2026, 9, 1), ['SPY'])
    assert result['status'] == 'failed'
    assert store.query('raw_eod_daily')['rows'] == original
    assert 'SPY' in pending_revisions(store, dataset='raw_eod_daily', symbols=['SPY'], end=END)


def test_bulk_prices_defer_pending_symbol_while_publishing_other_symbols(store):
    seed(store, 'us_eod_daily', points=[date(2026, 9, 7)])
    from studio_market.numeric import collect
    coverage = json.loads((Path(collect.__file__).parent / 'providers/us_etf_coverage.json').read_text())
    store.ingest('security_directory', [[dict(symbol=item['symbol']) for item in coverage['symbols']]], source='fixture')

    class Client:
        def get_json(self, endpoint, params):
            rows = [dict(symbol='SPY', date='2026-09-08')]
            return FmpResponse(endpoint, params, CAPTURE, json.dumps(rows).encode(), rows)

        def get_bulk_bytes(self, endpoint, params):
            body = b'symbol,date,open,high,low,close,adjClose,volume\nSPY,2026-09-08,50,50,50,50,50,1\nIWM,2026-09-08,100,100,100,100,100,1\n'
            return FmpResponse(endpoint, params, CAPTURE, body, None)

    collector = Collector(store.settings, store=store, client=Client())
    collector.eod(date(2026, 9, 8), date(2026, 9, 8), None)
    assert store.latest('us_eod_daily', symbols=['SPY'])['rows'][0]['close'] == 100
    assert store.latest('us_eod_daily', symbols=['IWM'])['rows'][0]['close'] == 100
    with store.engine.connect() as connection:
        details = connection.execute(select(batches.c.details).where(
            batches.c.details['endpoint'].as_string() == 'eod-bulk')).scalar_one()
    assert details['deferred_price_revision_symbols'] == ['SPY']


@pytest.mark.parametrize('reason', ['adjusted_price_missing', 'retained_price_dates_missing', 'price_history_empty'])
def test_source_coverage_failures_keep_old_facts_and_actionable_receipts(store, reason):
    seed(store, 'us_eod_daily', points=[date(2001, 1, 2)])
    if reason == 'price_history_empty':
        # A second symbol has a due action but no previously observed price.
        symbol = 'EMPTY'
        store.ingest('stock_splits', [], source='fixture', observed_at=OLD,
                     details={'price_revision_requests': [{'symbol': symbol, 'effective_date': '2026-09-02'}]})
    else:
        symbol = 'SPY'
    original = store.query('us_eod_daily')['rows']

    class Client:
        def get_json(self, endpoint, params):
            rows = ([dict(symbol=symbol, date='2001-01-02', open=50, high=50, low=50, close=50, volume=1)]
                    if reason == 'adjusted_price_missing' and endpoint.endswith('/full') else [])
            return FmpResponse(endpoint, params, CAPTURE, json.dumps(rows).encode(), rows)

    result = Collector(store.settings, store=store, client=Client()).reconcile_price_revisions(END, symbols=[symbol])
    assert result['status'] == 'failed'
    failure = result['price_revisions'][0]
    assert failure['reason'] == reason and failure['symbol'] == symbol
    assert failure['missing_date_count'] == (0 if reason == 'price_history_empty' else 1)
    assert store.query('us_eod_daily')['rows'] == original
    assert symbol in pending_revisions(store, dataset='us_eod_daily', symbols=[symbol], end=END)
