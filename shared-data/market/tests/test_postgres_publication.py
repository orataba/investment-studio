"""Publication races exercise the real PostgreSQL transaction contract."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
