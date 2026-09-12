"""Publication races exercise the real PostgreSQL transaction contract."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.replication import export_bundle, import_bundle
from studio_market.numeric.schema import batches, datasets

pytestmark = pytest.mark.postgresql_integration


@pytest.fixture
def publication_settings(tmp_path, monkeypatch):
    base = os.environ.get('INVESTMENT_STUDIO_TEST_POSTGRES_URL')
    if not base:
        pytest.skip('INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured')
    name = 'studio_publication_' + uuid4().hex[:10]
    admin = create_engine(make_url(base).set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_DATABASE_URL', url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    try:
        command.upgrade(config, 'head')
        yield MarketSettings(url, tmp_path / 'market')
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_simultaneous_first_writers_register_once_and_publish_both(publication_settings):
    store = NumericStore(publication_settings)
    barrier = Barrier(2)
    def before_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('INSERT INTO market_data.datasets'):
            barrier.wait(timeout=10)
    event.listen(store.engine, 'before_cursor_execute', before_insert)
    def publish(value):
        return store.ingest('analyst_price_targets', [[{'symbol': 'SPY', 'target': value}]],
                            source='fixture', observed_at=datetime(2026, 9, value, tzinfo=timezone.utc))
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            result = list(workers.map(publish, (1, 2)))
        assert all(item['status'] == 'ready' for item in result)
        assert store.latest('analyst_price_targets')['rows'][0]['target'] == 2
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(datasets)) == 1
    finally:
        store.close()


@pytest.mark.parametrize('future_clock', ['observed_at', 'available_at'])
def test_latest_visibility_uses_full_postgres_scope_and_exact_cutoff(publication_settings, monkeypatch, future_clock):
    store = NumericStore(publication_settings)
    cutoff = datetime(2026, 9, 2, 8, 0, 0, 123456, tzinfo=timezone.utc)
    try:
        store.ingest('macro_series', [[
            dict(series_id='A', date=date(2026, 9, 2), value=10),
            dict(series_id='Z', date=date(2026, 9, 1), value=20),
        ]], source='fixture', observed_at=cutoff - timedelta(days=1))
        revision = dict(series_id='Z', date=date(2026, 9, 1), value=99,
                        observed_at=cutoff, available_at=cutoff)
        revision[future_clock] += timedelta(microseconds=1)
        store.ingest('macro_series', [[revision]], source='fixture')
        original = store.query
        calls = []

        def history(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)

        monkeypatch.setattr(store, 'query', history)
        page = store.latest('macro_series', as_of=cutoff, limit=1)
        assert page['total'] == 2 and page['rows'][0]['symbol'] == 'A'
        assert len(calls) == 1  # The future winner is outside the returned page.
        assert store.latest('macro_series', symbols=['Z'], as_of=cutoff)['rows'][0]['value'] == 20
        known = cutoff + timedelta(microseconds=1)
        expected = original('macro_series', as_of=known, _latest=True)
        monkeypatch.setattr(store, 'query', lambda *a, **k: pytest.fail('visible latest scanned history'))
        actual = store.latest('macro_series', as_of=known)
        assert actual['total'] == expected['total']
        assert actual['provenance'] == expected['provenance']
        assert [(r['source_id'], r['value']) for r in actual['rows']] == [
            (r['source_id'], r['value']) for r in expected['rows']]
    finally:
        store.close()


def test_concurrent_bundle_imports_publish_once_without_shared_partial_files(publication_settings, tmp_path, monkeypatch):
    source = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / 'source'))
    source.create_schema_for_testing()
    try:
        source.ingest('analyst_price_targets', [[{'symbol': 'SPY', 'target': 100}]], source='fixture')
        bundle = tmp_path / 'capture.zip'
        export_bundle(source.settings, bundle)
    finally:
        source.close()
    from studio_market.numeric import replication
    original_link = replication.os.link
    barrier = Barrier(2)
    def link_together(source_path, target_path):
        barrier.wait(timeout=10)
        return original_link(source_path, target_path)
    monkeypatch.setattr(replication.os, 'link', link_together)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: import_bundle(publication_settings, bundle), range(2)))
    assert {r['batches'][0]['status'] for r in results} == {'ready', 'already_imported'}
    store = NumericStore(publication_settings)
    try:
        assert store.latest('analyst_price_targets')['rows'][0]['target'] == 100
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(batches)) == 1
        assert not list(publication_settings.data_root.rglob('.receiving-*'))
    finally:
        store.close()


def test_durable_price_revision_requests_use_postgres_json_and_exact_receipts(publication_settings):
    from datetime import date
    from studio_market.numeric.price_revisions import pending_revisions
    store = NumericStore(publication_settings)
    try:
        requests = []
        for day in (1, 2):
            batch = store.ingest('stock_splits', [], source='fixture', details={
                'observed_at':datetime(2026,9,day,tzinfo=timezone.utc),
                'price_revision_requests':[{'symbol':'SPY','effective_date':'2026-09-01'}]})
            requests.append(batch['batch_id']+':2026-09-01')
        store.ingest('us_eod_daily', [], source='fixture', details={'price_revision_completed':{'SPY':requests[0]}})
        kwargs = dict(dataset='us_eod_daily',symbols=['SPY'],end=date(2026,9,8))
        assert pending_revisions(store,**kwargs)=={'SPY':requests[1]}
        store.ingest('us_eod_daily', [], source='fixture', details={'price_revision_completed':{'SPY':requests[1]}})
        assert pending_revisions(store,**kwargs)=={}
        assert pending_revisions(store,**{**kwargs,'dataset':'raw_eod_daily'})=={'SPY':requests[1]}
    finally:
        store.close()


def test_regime_replica_scopes_delivered_partial_evidence_and_keeps_original_receipt(publication_settings, tmp_path):
    import gzip
    import json
    from types import SimpleNamespace
    from studio_market.numeric.regime_sources import RegimeSources

    source_settings = MarketSettings(f"sqlite:///{tmp_path / 'collector.db'}", tmp_path / 'collector')
    source_store = NumericStore(source_settings)
    source_store.create_schema_for_testing()

    class Client:
        observed = datetime(2026, 9, 8, tzinfo=timezone.utc)
        payload = []

        def get_json(self, endpoint, params):
            return SimpleNamespace(payload=self.payload, body=json.dumps(self.payload).encode(),
                                   endpoint=endpoint, params=params, received_at=self.observed)

        def close(self):
            pass

    client = Client()
    collector = RegimeSources(source_settings, store=source_store, role='collector', fmp_client=client)
    replica = RegimeSources(publication_settings, role='replica')
    endpoint = 'historical-price-eod/full'
    params = {'symbol': 'USDHKD', 'from': '2026-09-06', 'to': '2026-09-07'}
    bundle = tmp_path / 'regime-capture.zip'

    def deliver(rows, *, day, symbol='USDHKD', end='2026-09-07'):
        client.payload = rows
        client.observed = datetime(2026, 9, day, tzinfo=timezone.utc)
        collector.fmp_payload(endpoint, {**params, 'symbol': symbol, 'to': end})
        capture = collector.provenance[-1]
        exported = export_bundle(source_settings, bundle, batch_ids=[capture['batch_id']])
        assert exported['batch_count'] == 1
        imported = import_bundle(publication_settings, bundle)
        assert imported['batches'][0]['status'] == 'ready'
        return capture

    def read(**overrides):
        replica.provenance.clear()
        return replica.fmp_payload(endpoint, {**params, **overrides})

    good = {'symbol': 'USDHKD', 'date': '2026-09-07', 'close': 7.8}
    bad = {**good, 'date': '2026-09-06', 'close': 0}
    try:
        partial = deliver([bad, good], day=8)
        original = replica.store.query('regime_market_daily', as_of='2026-09-08T23:59:00Z')['rows']
        assert [row['date'] for row in read(**{'from': '2026-09-07'})] == ['2026-09-07']
        assert all('validation' not in capture for capture in replica.provenance)
        assert read(to='2026-09-06') == []
        assert replica.provenance[0]['validation']['rejected_rows'][0]['date'] == '2026-09-06'
        copied_raw = publication_settings.data_root / partial['raw_ref']
        assert json.loads(gzip.decompress(copied_raw.read_bytes())) == [bad, good]

        # PostgreSQL's nested JSONB scope must exclude a different symbol's
        # later complete empty receipt, even though its requested dates match.
        deliver([], day=9, symbol='USDCNH')
        assert len(read()) == 1
        assert any(capture.get('validation', {}).get('status') == 'partial' for capture in replica.provenance)

        # Correct only the rejected date: the other selected row still belongs
        # to the original partial capture but must no longer inherit its warning.
        deliver([{**bad, 'close': 7.7}], day=10, end='2026-09-06')
        assert {row['date'] for row in read()} == {'2026-09-06', '2026-09-07'}
        assert len(replica.provenance) == 2
        assert all('validation' not in capture for capture in replica.provenance)
        with replica.store.engine.connect() as connection:
            retained = connection.execute(select(batches.c.details).where(batches.c.id == partial['batch_id'])).scalar_one()
        assert retained['validation'] == partial['validation']
        assert replica.store.query('regime_market_daily', as_of='2026-09-08T23:59:00Z')['rows'] == original
    finally:
        collector.close()
        replica.close()
