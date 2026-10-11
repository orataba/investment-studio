"""Production refresh entry to canonical eligibility; all stores are disposable."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from studio_market.config import MarketSettings
from studio_market.numeric.collect import Collector
from studio_market.numeric.store import NumericStore
from studio_data.services.equities import service as equity
from studio_data.services.fmp import eod, eod_capture, price_projection
from studio_data.services.fmp.client import FmpApiError

CLOCK = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.fixture
def projection(tmp_path, monkeypatch):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    instrument = core.create_instrument(factory, instrument_name='Identity fixture', instrument_type='equity',
        currency='USD', exchange_code='XNAS', identifiers=[{'identifier_type':'provider_symbol',
        'identifier_value':'fmp:REUSED', 'is_primary':True}])
    iid = instrument['instrument_id']
    settings = MarketSettings(f'sqlite:///{tmp_path / "market.db"}', tmp_path / 'data')
    market = NumericStore(settings)
    market.create_schema_for_testing()
    load = lambda: core.get_instrument(factory, iid)
    for module in (equity, eod):
        monkeypatch.setattr(module, 'get_instrument', lambda key: core.get_instrument(factory, key))
    for module in (eod, eod_capture):
        monkeypatch.setattr(module, 'update_refresh_status', lambda **kw:core.update_refresh_status(factory, **kw))
    monkeypatch.setattr(price_projection, 'get_session_factory', lambda: factory)
    monkeypatch.setattr(MarketSettings, 'from_environment', lambda: settings)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return CLOCK.astimezone(tz) if tz else CLOCK.replace(tzinfo=None)
    monkeypatch.setattr(eod, 'datetime', FrozenDateTime)
    class NoNetwork:
        def get_json(self, *args, **kwargs):
            pytest.fail('Unexpected provider request in offline regression')
        get_bulk_bytes = get_json
        def close(self):
            pass
    monkeypatch.setattr(eod_capture, 'Collector',
        lambda settings, store: Collector(settings, store=store, client=NoNetwork(), now=CLOCK))
    monkeypatch.setattr('socket.create_connection', lambda *a, **kw: pytest.fail('Network forbidden'))
    counter = 0
    def ingest(dataset, rows, details=None):
        nonlocal counter
        counter += 1
        observed = datetime(2026, 10, 10, 1, counter, tzinfo=UTC)
        return market.ingest(dataset, [rows], source='fixture', observed_at=observed, details=details)
    def price(rows, proof=None):
        return ingest('raw_eod_daily', [dict(symbol='REUSED', date=day, open=value, high=value,
            low=value, close=value, adjusted_close=value, volume=1) for day,value in rows],
            {'price_series_capture': {'REUSED':proof}} if proof else None)
    def identity(status='blocked', security_id=None, start=None):
        ingest('price_series_identities', [dict(symbol='REUSED', status=status, security_id=security_id,
            provider_symbol='NEW' if security_id else None, history_start=start, history_end=None,
            reason='security_identifier_changed', source_refs=['https://example.org/identity'])])
        row = market.latest('price_series_identities', symbols=['REUSED'])['rows'][0]
        return dict(security_id=security_id, provider_symbol='NEW', history_start=start,
                    history_end='2026-10-09', identity_source_id=row['source_id'])
    yield SimpleNamespace(factory=factory, iid=iid, load=load, market=market, price=price,
                          identity=identity, refresh=lambda:equity.refresh_equity_eod(iid))
    market.close()
    engine.dispose()


def active_prices(detail):
    return [(row['as_of_date'], float(row['value'])) for row in detail['market_data']
            if row['quote_basis']=='adjusted_close' and row['status']=='complete']


def test_normal_entry_quarantine_invalidates_and_new_generation_does_not_stitch(projection, monkeypatch):
    p = projection
    p.price([('2026-10-08',100), ('2026-10-09',105)])
    p.refresh()
    before = p.load()
    p.identity()
    with pytest.raises(FmpApiError, match='price series unavailable'):
        p.refresh()
    blocked = p.load()
    assert blocked['refresh_status']['status']=='failed'
    assert not active_prices(blocked)
    assert blocked['market_data_updated_at'] != before['market_data_updated_at']
    assert core.get_price_bars(p.factory, instrument_id=p.iid) == []
    assert_consumers_unavailable(blocked, monkeypatch)
    # First reviewed identity can replace a previously unbound projection.
    proof = p.identity('verified', 'new-security', '2026-10-09')
    p.price([('2026-10-09',10)], proof)
    p.refresh()
    repaired = p.load()
    assert active_prices(repaired)==[('2026-10-09',10)]
    assert repaired['source_settings']['source_provider_security_id']=='new-security'
    watermark = repaired['market_data_updated_at']
    p.refresh()
    assert p.load()['market_data_updated_at']==watermark
    # Once bound, another security requires a reviewed registry migration.
    proof = p.identity('verified', 'third-security', '2026-10-09')
    p.price([('2026-10-09',99)], proof)
    with pytest.raises(FmpApiError, match='registered binding'):
        p.refresh()
    assert not active_prices(p.load())
    assert p.load()['source_settings']['source_provider_security_id']=='new-security'
    assert p.load()['refresh_status']['status']=='failed'
    assert_consumers_unavailable(p.load(), monkeypatch)


def assert_consumers_unavailable(detail, monkeypatch):
    # Explicit cross-application contract regression, independent of live DBs.
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for app in ('watchlist', 'portfolio'):
        monkeypatch.syspath_prepend(str(root / 'apps' / app / 'backend'))
    from watchlist_app.services.canonical_recalc import (
        CanonicalRecalcService, _shared_quote_points_by_basis, _select_quote_series,
    )
    from watchlist_app.services.calculation_frequency import build_calculation_frequency_context
    from portfolio_app.services.valuation_quotes import _valuation_quote_projection
    by = _shared_quote_points_by_basis(detail['market_data'], expected_currency='USD')
    selected = _select_quote_series(by, shared_instrument=detail, role='total_return',
                                   preference='auto', instrument_type='equity')
    valuation = _select_quote_series(by, shared_instrument=detail, role='valuation',
                                    preference='auto', instrument_type='equity')
    context = build_calculation_frequency_context(selected['points'], expected_frequency='daily', market_calendar='XNYS')
    summary = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(instrument_id=detail['instrument_id'], instrument_name='Review',
            instrument_type='equity', primary_identifier_value='REUSED', metadata_json={}),
        nav_selection=selected, valuation_selection=valuation, performance_snapshot=None,
        risk_snapshot=None, exposure_snapshot=None, attributes={}, taxonomy_context={},
        source_cutoff_at=CLOCK, now=CLOCK, calculation_frequency_profile=context['profile'],
        source_settings={'expected_frequency':'daily', 'market_calendar':'XNYS', 'release_lag_days':0})
    assert summary['freshness']['data_freshness_status']=='unavailable'
    assert not selected['points'] and not valuation['points']
    quote = _valuation_quote_projection(detail, instrument_id=detail['instrument_id'], as_of_date=CLOCK.date())
    assert quote['point'] is None
    assert quote['unavailable_reason']=='quote_series_unavailable'


def test_verified_identity_without_full_capture_withdraws_old_quotes(projection):
    p = projection
    p.price([('2026-10-09',105)])
    p.refresh()
    p.identity('verified', 'new-security', '2026-10-09')
    # An arbitrary newer row is not a complete reviewed identity capture.
    p.price([('2026-10-09',10)])
    # Prevent the real collector from contacting a provider; an unavailable
    # complete generation is still inspected after this ordinary failure.
    from unittest.mock import patch
    with patch.object(eod, 'ensure_fmp_security_history', side_effect=FmpApiError('provider unavailable')):
        with pytest.raises(FmpApiError, match='price series unavailable'):
            p.refresh()
    assert not active_prices(p.load())


def test_off_session_history_is_retired_on_replay_without_synthetic_return(projection):
    p = projection
    # Retained pre-fix Saturday quotes are real prior database state.
    rows = [dict(metric_family='price', quote_basis=basis, as_of_date=day, value=value,
        currency='USD', provider='fmp:fixture', status='complete')
        for day,value in [('2026-10-02',100), ('2026-10-03',50), ('2026-10-05',100), ('2026-10-09',100)]
        for basis in ['close','adjusted_close']]
    core.upsert_price_history(p.factory, instrument_id=p.iid, market_data_rows=rows, price_bar_rows=[])
    p.price([('2026-10-02',100), ('2026-10-03',50), ('2026-10-05',100), ('2026-10-09',100)])
    p.refresh()
    assert active_prices(p.load())==[('2026-10-02',100), ('2026-10-05',100), ('2026-10-09',100)]
    assert 'Excluded 1 off-session' in p.load()['refresh_status']['message']


@pytest.mark.parametrize('controlled', [False, True])
def test_transient_collection_failure_does_not_withdraw_qualified_history(projection, monkeypatch, controlled):
    p = projection
    proof = p.identity('verified', 'same-security', '2026-10-09') if controlled else None
    p.price([('2026-10-09',105)], proof)
    p.refresh()
    before = p.load()['market_data_updated_at']
    def unavailable(*args, **kwargs):
        raise FmpApiError('temporary provider failure')
    monkeypatch.setattr(eod, 'ensure_fmp_security_history', unavailable)
    with pytest.raises(FmpApiError, match='temporary'):
        p.refresh()
    assert active_prices(p.load())==[('2026-10-09',105)]
    assert p.load()['market_data_updated_at']==before


def test_reconciliation_is_atomic_on_invalid_bars_and_fences_stale_input(projection):
    p = projection
    p.price([('2026-10-08',100), ('2026-10-09',105)])
    p.refresh()
    before = p.load()
    before_bars = core.get_price_bars(p.factory, instrument_id=p.iid)
    point = dict(metric_family='price', quote_basis='close', as_of_date='2026-10-09', value=50,
                 currency='USD', provider='fmp:fixture', status='complete')
    invalid_bar = dict(as_of_date='2026-10-09', open=50, high=1, low=40, close=50,
                       currency='USD', provider='fmp:fixture', status='complete')
    with pytest.raises(ValueError, match='OHLC high/low ordering'):
        price_projection.publish_price_history(before, market_data_rows=[point], price_bar_rows=[invalid_bar])
    assert p.load()['market_data']==before['market_data']
    assert p.load()['market_data_updated_at']==before['market_data_updated_at']
    assert core.get_price_bars(p.factory, instrument_id=p.iid)==before_bars
    core.upsert_market_data_points(p.factory, instrument_id=p.iid, rows=[point])
    with pytest.raises(FmpApiError, match='inputs changed'):
        price_projection.publish_price_history(before, market_data_rows=[], price_bar_rows=[],
                                                unavailable_reason='identity unavailable')
    assert any(row['status']=='complete' for row in p.load()['market_data'])


def test_quarantine_notification_uses_existing_watermarks_and_replay_is_idempotent(projection, monkeypatch):
    from contextlib import nullcontext
    from studio_data.services import market_data_ops
    p = projection
    p.price([('2026-10-09',105)])
    p.refresh()
    p.identity()
    monkeypatch.setattr(market_data_ops, 'get_instrument', lambda _:p.load())
    monkeypatch.setattr(market_data_ops, 'update_refresh_status', lambda **kw:core.update_refresh_status(p.factory, **kw))
    monkeypatch.setattr(market_data_ops, 'market_data_item_timeout', lambda _:nullcontext())
    monkeypatch.setattr(market_data_ops, 'refresh_market_data', lambda **kw:p.refresh())
    first = market_data_ops.refresh_market_data_with_timeout(instrument_id=p.iid, updated_by=None)
    assert first['refresh_status']['status']=='failed'
    assert first['canonical_changed'] is True
    second = market_data_ops.refresh_market_data_with_timeout(instrument_id=p.iid, updated_by=None)
    assert second['canonical_changed'] is False


def test_fmp_projection_does_not_replace_other_source_history(projection):
    p = projection
    row = dict(metric_family='price', quote_basis='close', as_of_date='2026-10-09', value=123,
               currency='USD', provider='manual:verified', status='complete')
    core.upsert_market_data_points(p.factory, instrument_id=p.iid, rows=[row])
    p.price([('2026-10-09',105)])
    before = p.load()
    with pytest.raises(FmpApiError, match='reviewed source migration'):
        p.refresh()
    assert p.load()['market_data']==before['market_data']
    assert p.load()['market_data_updated_at']==before['market_data_updated_at']


def test_bound_identity_cannot_fall_back_to_an_uncontrolled_empty_read(projection, monkeypatch):
    p = projection
    proof = p.identity('verified', 'new-security', '2026-10-09')
    p.price([('2026-10-09',10)], proof)
    p.refresh()
    # A restored/inconsistent public store with no identity facts cannot undo
    # the canonical security binding, even when its empty result is unpaged.
    from unittest.mock import Mock
    source = Mock()
    source.query.return_value = {'rows':[], 'total':0, 'provenance':{}}
    with pytest.raises(FmpApiError, match='missing its reviewed identity generation'):
        eod.refresh_fmp_eod(p.iid, instrument_type='equity', store=source, now=CLOCK)
    detail = p.load()
    assert not active_prices(detail)
    assert detail['source_settings']['source_provider_security_id']=='new-security'
    assert detail['source_settings']['fmp_price_series']['projection_status']=='unavailable'


def test_new_generation_with_failed_tail_acquisition_still_invalidates_old_security(projection, monkeypatch):
    p = projection
    p.price([('2026-10-08',100), ('2026-10-09',105)])
    p.refresh()
    proof = p.identity('verified', 'new-security', '2026-10-08')
    p.price([('2026-10-08',10)], proof)
    # Exercise the real ensure path: source has a coherent new base but its
    # last session is stale; the attempted collection returns a transport error.
    calls = []
    def failed_tail(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status':'failed', 'symbols':[{'status':'failed', 'error':'temporary provider failure'}]}
    monkeypatch.setattr(eod_capture, 'Collector', lambda *args, **kwargs:
        SimpleNamespace(raw_eod=failed_tail, _client=None))
    with pytest.raises(FmpApiError, match='generation is not yet projected'):
        p.refresh()
    assert len(calls)==1
    assert not active_prices(p.load())
    assert p.load()['refresh_status']['status']=='failed'
    assert_consumers_unavailable(p.load(), monkeypatch)
