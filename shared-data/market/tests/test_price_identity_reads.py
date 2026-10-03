from datetime import date, datetime, timezone

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric.store import NumericStore


def stamp(day):
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path/'data'))
    result.create_schema_for_testing()
    yield result
    result.close()


def price(store, day, *, symbol='REUSED', dates=('2026-09-01',), value=10, details=None):
    return store.ingest('us_eod_daily', [[dict(symbol=symbol, date=date.fromisoformat(at),
        close=value, adjusted_close=value) for at in dates]], source='fixture',
        observed_at=stamp(day), details={**(details or {}), 'observed_at': stamp(day)})


def identity(store, day, *, status='verified', security='new-security'):
    store.ingest('price_series_identities', [[dict(symbol='REUSED', status=status,
        security_id=security, provider_symbol='NEW', history_start='2026-09-02',
        history_end=None, reason='Ticker reused', source_refs=['https://example.org/filing'])]],
        source='verified_identity', observed_at=stamp(day))
    return store.latest('price_series_identities')['rows'][0]


def capture(control):
    return {'price_series_capture': {'REUSED': dict(identity_source_id=control['source_id'],
        security_id=control['security_id'], provider_symbol='NEW',
        history_start='2026-09-02', history_end='2026-09-08')}}


def test_quarantine_is_visible_in_current_reads_but_does_not_rewrite_pit_or_sources(store):
    old = price(store, 1)
    old_source = store.query('us_eod_daily')['rows'][0]['source_id']
    identity(store, 2, status='blocked')
    for result in (store.query('us_eod_daily'), store.latest('us_eod_daily')):
        assert result['rows'] == [] and result['total'] == 0
        assert result['provenance']['price_series_unavailable'][0]['symbol'] == 'REUSED'
    assert store.query('us_eod_daily', as_of=stamp(1))['rows'][0]['source_id'] == old_source
    assert store.latest('us_eod_daily', as_of=stamp(1))['rows'][0]['source_id'] == old_source
    assert store.query('us_eod_daily', versions=True)['total'] == 1
    assert store.read_source(old_source)['batch_id'] == old['batch_id']


def test_verified_identity_without_complete_generation_stays_unavailable(store):
    price(store, 1)
    identity(store, 2)
    # A daily/bulk capture cannot certify a repaired history.
    price(store, 3, dates=('2026-09-03',))
    result = store.latest('us_eod_daily', symbols=['REUSED'])
    assert result['rows'] == []
    assert result['provenance']['price_series_unavailable'][0]['reason'] == 'verified_identity_history_not_captured'


def test_rebuilds_replace_generation_and_pagination_counts_filter_before_ranking(store):
    price(store, 1, dates=('2026-09-01', '2026-09-08'))
    control = identity(store, 2)
    first = price(store, 3, dates=('2026-09-01', '2026-09-02'), value=20, details=capture(control))
    price(store, 4, dates=('2026-09-04',), value=21, details={'price_series_updates': {
        'REUSED': {'identity_source_id': control['source_id'], 'base_capture_id': first['batch_id']}}})
    price(store, 4, symbol='NORMAL', dates=('2026-09-04',), value=99)
    before = store.query('us_eod_daily', symbols=['REUSED'], as_of=stamp(4))
    assert {row['date'] for row in before['rows']} == {'2026-09-02', '2026-09-04'}
    assert store.latest('us_eod_daily')['total'] == 2
    second = price(store, 5, dates=('2026-09-02', '2026-09-03'), value=30, details=capture(control))
    # Unbound newer rows and updates from the superseded base cannot leak back.
    price(store, 6, dates=('2026-09-08',), value=777)
    price(store, 6, dates=('2026-09-04',), value=888, details={'price_series_updates': {
        'REUSED': {'identity_source_id': control['source_id'], 'base_capture_id': first['batch_id']}}})
    result = store.query('us_eod_daily', symbols=['REUSED'], limit=1, offset=1)
    assert result['total'] == 2 and result['rows'][0]['date'] == '2026-09-02'
    assert result['rows'][0]['batch_id'] == second['batch_id']
    assert store.query('us_eod_daily', symbols=['REUSED'], batch_id=first['batch_id'])['rows'] == []
    latest = store.latest('us_eod_daily')
    assert latest['total'] == 2
    assert {row['symbol']: row['close'] for row in latest['rows']} == {'NORMAL': 99, 'REUSED': 30}
    assert store.latest('us_eod_daily', as_of=stamp(4), symbols=['REUSED'])['rows'][0]['close'] == 21
    assert store.query('us_eod_daily', versions=True, symbols=['REUSED'])['total'] == 9


def test_new_identity_observation_invalidates_previous_verified_generation(store):
    control = identity(store, 2)
    price(store, 3, dates=('2026-09-02',), details=capture(control))
    identity(store, 4, security='different-security')
    assert store.query('us_eod_daily')['rows'] == []
    assert store.latest('us_eod_daily', as_of=stamp(3))['total'] == 1


def test_quarantined_and_ordinary_symbols_share_correct_query_counts(store):
    price(store, 1)
    price(store, 1, symbol='NORMAL')
    identity(store, 2, status='blocked')
    result = store.query('us_eod_daily', limit=1)
    assert result['total'] == 1 and result['rows'][0]['symbol'] == 'NORMAL'
    assert store.latest('us_eod_daily', limit=1)['rows'][0]['symbol'] == 'NORMAL'
    assert store.latest('us_eod_daily', as_of=stamp(2), limit=1)['rows'][0]['symbol'] == 'NORMAL'


def test_identity_and_generation_survive_bundle_transfer_with_old_current_winner(store, tmp_path):
    from studio_market.numeric.replication import export_bundle, import_bundle
    price(store, 1, dates=('2026-09-08',))
    control = identity(store, 2)
    price(store, 3, dates=('2026-09-02',), value=25, details=capture(control))
    target = NumericStore(MarketSettings(f"sqlite:///{tmp_path/'replica.db'}", tmp_path/'replica'))
    target.create_schema_for_testing()
    try:
        archive = tmp_path/'replica.zip'
        export_bundle(store.settings, archive)
        import_bundle(target.settings, archive)
        assert target.latest('us_eod_daily')['rows'][0]['close'] == 25
        assert target.query('us_eod_daily', versions=True)['total'] == 2
        assert target.latest('us_eod_daily', as_of=stamp(1))['rows'][0]['date'] == '2026-09-08'
    finally:
        target.close()


def test_mixed_latest_pit_page_keeps_public_query_order(store):
    price(store, 1, symbol='NORMAL', dates=('2026-09-02',))
    control = identity(store, 2)
    price(store, 3, dates=('2026-09-02',), details=capture(control))
    result = store.latest('us_eod_daily', as_of=stamp(3), limit=1)
    expected = store.query('us_eod_daily', as_of=stamp(3), limit=1, _latest=True)
    assert result['total'] == expected['total'] == 2
    assert result['rows'][0]['source_id'] == expected['rows'][0]['source_id']
