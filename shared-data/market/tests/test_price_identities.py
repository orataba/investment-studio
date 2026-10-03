"""Ticker lifecycle decisions change reads, never immutable price evidence."""
from datetime import date, datetime, timedelta, timezone
import json
import zipfile

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric.collect import Collector
from studio_market.numeric.price_identities import (
    PriceIdentityUnavailable, guard_reference_changes, price_capture_bases,
    price_identities, record_price_identities, validate_identity,
)
from studio_market.numeric.price_revisions import pending_revisions
from studio_market.numeric.providers.fmp import FmpResponse
from studio_market.numeric.providers.us_market import PriceHistoryUnavailable
from studio_market.numeric.replication import export_bundle, import_bundle
from studio_market.numeric.raw import archive_response
from studio_market.numeric.schema import batches
from studio_market.numeric.store import NumericStore

UTC = timezone.utc
OLD = datetime(2026, 9, 1, tzinfo=UTC)
REVIEW = OLD + timedelta(days=1)
CAPTURE = OLD + timedelta(days=2)
END = date(2026, 9, 8)


@pytest.fixture
def store(tmp_path):
    value = NumericStore(MarketSettings(f'sqlite:///{tmp_path / "market.db"}', tmp_path / 'facts'))
    value.create_schema_for_testing()
    yield value
    value.close()


def decision(symbol='REUSED', **changes):
    return dict(symbol=symbol, security_id='isin:NEW-SECURITY', provider_symbol=symbol,
                history_start='2024-01-01', history_end=None, symbol_start='2024-01-01',
                symbol_end=None, status='verified', reason='Issuer and exchange identify a new listing.',
                source_refs=['https://exchange.example/listing/new'], **{}) | changes


def approve(store, **changes):
    record_price_identities(store, [decision(**changes)], apply=True, observed_at=REVIEW)
    return price_identities(store)


def seed(store, symbol='REUSED', day='2001-01-02', close=10):
    return store.ingest('us_eod_daily', [[dict(symbol=symbol, date=day, close=close,
                       adjusted_close=close, observed_at=OLD)]], source='imported_market_archive', observed_at=OLD)


class Prices:
    def __init__(self, *, adjusted=50, before=None, days=()):
        self.adjusted, self.before, self.calls, self.days = adjusted, before, [], days

    def get_json(self, endpoint, params):
        self.calls.append((endpoint, params.copy()))
        if self.before:
            callback, self.before = self.before, None
            callback()
        value = self.adjusted if endpoint.endswith('dividend-adjusted') else 50
        row = dict(symbol=params['symbol'], date=params['to'], open=50, high=50, low=50,
                   close=50, adjOpen=value, adjHigh=value, adjLow=value, adjClose=value, volume=1)
        payload = [{**row, 'date': day} for day in self.days if params['from'] <= day <= params['to']] + [row]
        return FmpResponse(endpoint, params, CAPTURE, json.dumps(payload).encode(), payload)


def captures(store):
    with store.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(batches)).mappings()]


def test_review_preview_is_read_only_and_reapplication_preserves_fact_identity(store):
    planned = record_price_identities(store, [decision()])
    assert not planned['applied'] and planned['identities'][0]['symbol'] == 'REUSED'
    assert not price_identities(store)
    approve(store)
    original = price_identities(store)['REUSED']['source_id']
    assert record_price_identities(store, [decision()], apply=True)['unchanged'] == 1
    assert price_identities(store)['REUSED']['source_id'] == original
    assert not price_identities(store, as_of=OLD)


@pytest.mark.parametrize('change', [
    {'history_start':None}, {'security_id':None}, {'provider_symbol':'lower'},
    {'history_end':'2023-01-01'}, {'source_refs':[]},
    {'source_refs':['https://provider.example/api?apikey=secret']},
    {'source_refs':['https://user:password@provider.example/api']},
    {'unrecognized':True},
])
def test_review_requires_explicit_scope_and_safe_evidence(change):
    with pytest.raises(ValueError):
        validate_identity(decision(**change))


def test_reused_ticker_full_capture_excludes_other_security_but_retains_original_sources(store):
    seed(store)
    old = store.query('us_eod_daily')['rows'][0]
    approve(store)
    client = Prices()
    result = Collector(store.settings, store=store, client=client).reconcile_price_revisions(END, symbols=['REUSED'])
    assert result['status'] == 'ready'
    assert {params['from'] for _, params in client.calls} == {'2024-01-01'}
    rows = store.query('us_eod_daily', symbols=['REUSED'])['rows']
    assert [(r['date'], r['close'], r['security_id']) for r in rows] == [('2026-09-08', 50, 'isin:NEW-SECURITY')]
    assert store.read_source(old['source_id'])['close'] == 10
    assert len(store.query('us_eod_daily', versions=True)['rows']) == 2
    assert store.query('us_eod_daily', as_of=OLD)['rows'][0]['source_id'] == old['source_id']
    assert not pending_revisions(store, dataset='us_eod_daily', symbols=['REUSED'], end=END)


def test_renamed_security_uses_one_provider_generation_and_preserves_alias_lineage(store):
    seed(store, symbol='OLD', day='2024-06-03', close=10)
    approve(store, symbol='OLD', provider_symbol='NEW', symbol_end='2025-03-31')
    client = Prices(days=['2024-06-03'])
    Collector(store.settings, store=store, client=client).reconcile_price_revisions(END, symbols=['OLD'])
    assert {p['symbol'] for _, p in client.calls} == {'NEW'}
    row = store.query('us_eod_daily', symbols=['OLD'])['rows'][0]
    assert (row['symbol'], row['provider_symbol'], row['close']) == ('OLD', 'NEW', 50)
    base = price_capture_bases(store, 'us_eod_daily', price_identities(store))['OLD']
    assert base['details']['price_series_capture']['OLD']['provider_symbol'] == 'NEW'
    # A current-symbol action maps to the same identity; an expired old-symbol
    # action must not produce a new obligation or be marked completed.
    store.ingest('dividends', [], source='fixture', observed_at=CAPTURE + timedelta(days=1), details={
        'price_revision_requests':[{'symbol':'OLD','effective_date':'2026-09-04'}]})
    assert not pending_revisions(store, dataset='us_eod_daily', symbols=['OLD'], end=END)
    new = store.ingest('dividends', [], source='fixture', observed_at=CAPTURE + timedelta(days=2), details={
        'price_revision_requests':[{'symbol':'NEW','effective_date':'2026-09-05'}]})
    assert pending_revisions(store, dataset='us_eod_daily', symbols=['OLD'], end=END)['OLD'].startswith(new['batch_id'])


def test_first_capture_protects_retained_dates_only_inside_reviewed_symbol_lifecycle(store):
    seed(store, symbol='OLD', day='2001-01-02')
    seed(store, symbol='OLD', day='2024-06-03')
    seed(store, symbol='OLD', day='2025-04-01')
    approve(store, symbol='OLD', provider_symbol='NEW', symbol_end='2025-03-31')
    collector = Collector(store.settings, store=store, client=Prices())
    result = collector.reconcile_price_revisions(END, symbols=['OLD'])
    assert result['status'] == 'failed'
    assert result['price_revisions'][0]['missing_dates'] == ['2024-06-03']
    assert not price_capture_bases(store, 'us_eod_daily', price_identities(store))
    assert not store.query('us_eod_daily', symbols=['OLD'])['rows']
    collector = Collector(store.settings, store=store, client=Prices(days=['2024-06-03']))
    assert collector.reconcile_price_revisions(END, symbols=['OLD'])['status'] == 'ready'
    assert store.query('us_eod_daily', versions=True)['total'] == 5


def test_identity_raw_evidence_is_included_in_replication_bundle(store, tmp_path):
    _, ref = archive_response(store.settings, 'price_diagnostics', b'{"invalid_adjusted_price":0}')
    approve(store, source_refs=[ref, 'https://exchange.example/listing/new'])
    bundle = tmp_path / 'identity-evidence.zip'
    export_bundle(store.settings, bundle)
    with zipfile.ZipFile(bundle) as package:
        assert ref in package.namelist()
    batch = next(row for row in captures(store) if row['dataset'] == 'price_series_identities')
    assert batch['details']['source_parts'] == [{'raw_ref': ref}]


def test_terminated_security_bounds_history_and_does_not_acknowledge_impossible_action(store):
    approve(store, history_start='2020-01-01', history_end='2022-11-09',
            symbol_start='2020-01-01', symbol_end='2022-11-09')
    store.ingest('dividends', [], source='fixture', observed_at=REVIEW, details={
        'price_revision_requests':[{'symbol':'REUSED','effective_date':'2025-10-30'}]})
    client = Prices()
    Collector(store.settings, store=store, client=client).reconcile_price_revisions(END, symbols=['REUSED'])
    assert {p['to'] for _, p in client.calls} == {'2022-11-09'}
    completions = [r['details'].get('price_revision_completed', {}) for r in captures(store)]
    assert all('2025-10-30' not in value for item in completions for value in item.values())
    assert not pending_revisions(store, dataset='us_eod_daily', symbols=['REUSED'], end=END)


@pytest.mark.parametrize('value,reason', [(0,'adjusted_price_nonpositive'),(-1,'adjusted_price_nonpositive'),
                                        (float('nan'),'adjusted_price_nonfinite'),(None,'adjusted_price_missing')])
def test_invalid_adjusted_price_retains_raw_evidence_and_never_exposes_legacy_history(store,tmp_path,value,reason):
    seed(store)
    approve(store)
    collector = Collector(store.settings, store=store, client=Prices(adjusted=value))
    result = collector.reconcile_price_revisions(END, symbols=['REUSED'])
    assert result['status'] == 'failed'
    assert result['price_revisions'][0]['reason'] == reason
    assert store.query('us_eod_daily')['rows'] == []
    assert store.query('us_eod_daily', versions=True)['total'] == 1
    failed = [r for r in captures(store) if r['details'].get('price_capture_failure')]
    assert len(failed) == 1 and failed[0]['row_count'] == 0
    assert 'price_series_capture' not in failed[0]['details']
    assert 'price_revision_completed' not in failed[0]['details']
    bundle = tmp_path / 'evidence.zip'
    export_bundle(store.settings, bundle)
    with zipfile.ZipFile(bundle) as package:
        for part in failed[0]['details']['source_parts']:
            assert part['raw_ref'] in package.namelist()
            assert part['adjusted_raw_ref'] in package.namelist()
    replica = NumericStore(MarketSettings(f'sqlite:///{tmp_path / "replica.db"}', tmp_path / 'replica'))
    replica.create_schema_for_testing()
    try:
        import_bundle(replica.settings, bundle)
        assert replica.query('us_eod_daily')['rows'] == []
        assert replica.query('us_eod_daily', versions=True)['total'] == 1
    finally:
        replica.close()


def test_concurrent_identity_review_prevents_capture_from_acknowledging_new_identity(store):
    approve(store)
    def revise():
        record_price_identities(store,[decision(security_id='isin:OTHER',reason='Corrected security')],
                                apply=True,observed_at=REVIEW + timedelta(hours=1))
    result = Collector(store.settings, store=store, client=Prices(before=revise)).reconcile_price_revisions(END,symbols=['REUSED'])
    assert result['price_revisions'][0]['reason'] == 'identity_changed_during_capture'
    assert not store.query('us_eod_daily')['rows']
    assert all('price_series_capture' not in r['details'] for r in captures(store))


def test_new_symbol_change_blocks_both_aliases_but_repeated_source_does_not_undo_review(store):
    row = dict(old_symbol='OLD',new_symbol='NEW',event_date='2026-09-03',
               raw_ref='numeric/raw/fmp/aa/'+'a'*64+'.gz')
    guard_reference_changes(store,'symbol_changes',[row],observed_at=OLD)
    assert {r['status'] for r in price_identities(store).values()} == {'blocked'}
    store.ingest('symbol_changes',[[row]],source='fixture',observed_at=OLD)
    approve(store,symbol='OLD',provider_symbol='NEW')
    source_id = price_identities(store)['OLD']['source_id']
    assert guard_reference_changes(store,'symbol_changes',[row],observed_at=CAPTURE) is None
    assert price_identities(store)['OLD']['source_id'] == source_id


def test_stable_security_identifier_change_blocks_before_new_reference_is_published(store):
    store.ingest('company_profiles',[[dict(symbol='REUSED',cusip='OLD',company_name='Same issuer')]],source='fixture',observed_at=OLD)
    approve(store)
    incoming = dict(symbol='REUSED',cusip='NEW',company_name='Same issuer',raw_ref='numeric/raw/fmp/bb/'+'b'*64+'.gz')
    guard_reference_changes(store,'company_profiles',[incoming],observed_at=CAPTURE)
    assert price_identities(store)['REUSED']['reason'] == 'security_identifier_changed'
    assert store.latest('company_profiles')['rows'][0]['cusip'] == 'OLD'


def test_blocked_identity_does_not_contact_provider(store):
    approve(store,status='blocked',reason='Source returns invalid adjusted observations')
    client = Prices()
    result = Collector(store.settings,store=store,client=client).reconcile_price_revisions(END,symbols=['REUSED'])
    assert result['status'] == 'ready' and not client.calls
    assert result['coverage'] == 'quarantined' and result['price_revisions'] == []
    assert result['quarantined'][0]['reason'] == 'Source returns invalid adjusted observations'
    assert pending_revisions(store,dataset='us_eod_daily',symbols=['REUSED'],end=END) == {}
    with pytest.raises(PriceIdentityUnavailable):
        Collector(store.settings,store=store,client=client).symbol_prices(['REUSED'],date(2024,1,1),END)
    assert not client.calls


def test_incremental_alias_capture_links_to_base_and_changed_scale_waits_for_full_rebuild(store):
    approve(store, symbol='OLD', provider_symbol='NEW', symbol_end='2025-03-31')
    collector = Collector(store.settings,store=store,client=Prices())
    collector.reconcile_price_revisions(END,symbols=['OLD'])
    original = store.query('us_eod_daily')['rows']
    base = price_capture_bases(store,'us_eod_daily',price_identities(store))['OLD']['id']

    class OverlapPrices(Prices):
        def get_json(self, endpoint, params):
            response = super().get_json(endpoint,params)
            rows = [dict(response.payload[0],date=day) for day in (params['from'],params['to'])]
            return FmpResponse(endpoint,params,CAPTURE+timedelta(days=1),json.dumps(rows).encode(),rows)

    collector._client = OverlapPrices()
    collector.symbol_prices(['OLD'],END,END+timedelta(days=1))
    updates = [r for r in captures(store) if r['details'].get('price_series_updates')]
    assert updates[-1]['details']['price_series_updates']['OLD']['base_capture_id'] == base
    assert {r['date'] for r in store.query('us_eod_daily')['rows']} == {'2026-09-08','2026-09-09'}
    collector._client = OverlapPrices(adjusted=5)
    prior = store.query('us_eod_daily')['rows']
    collector.symbol_prices(['OLD'],END,END+timedelta(days=2))
    assert store.query('us_eod_daily')['rows'] == prior
    assert 'OLD' in pending_revisions(store,dataset='us_eod_daily',symbols=['OLD'],end=END+timedelta(days=2))
    assert original[0]['close'] == 50


def test_bulk_update_cannot_replace_an_expired_alias_with_stale_symbol_prices(store,monkeypatch):
    from pathlib import Path
    from studio_market.numeric import collect
    approve(store,symbol='OLD',provider_symbol='NEW',symbol_end='2025-03-31')
    collector=Collector(store.settings,store=store,client=Prices())
    collector.reconcile_price_revisions(END,symbols=['OLD'])
    covered=json.loads((Path(collect.__file__).parent/'providers/us_etf_coverage.json').read_text())
    store.ingest('security_directory',[[{'symbol':r['symbol']} for r in covered['symbols']]],source='fixture')
    monkeypatch.setattr(collector,'universe',lambda:{'OLD'})

    class Daily(Prices):
        def get_json(self,endpoint,params):
            if params['symbol']=='SPY':
                payload=[{'date':'2026-09-09'}]
                return FmpResponse(endpoint,params,CAPTURE+timedelta(days=1),json.dumps(payload).encode(),payload)
            return super().get_json(endpoint,params)
        def get_bulk_bytes(self,endpoint,params):
            body=b'symbol,date,open,high,low,close,adjClose,volume\nOLD,2026-09-09,999,999,999,999,999,1\n'
            return FmpResponse(endpoint,params,CAPTURE+timedelta(days=1),body,None)

    collector._client=Daily()
    collector.eod(END+timedelta(days=1),END+timedelta(days=1),None)
    rows=store.query('us_eod_daily',symbols=['OLD'])['rows']
    assert {r['close'] for r in rows}=={50}
    assert rows[0]['provider_symbol']=='NEW'
    assert all(p['symbol']=='NEW' for _,p in collector._client.calls)


def test_reviewed_raw_evidence_prevents_repeated_unpublished_event_from_blocking_again(store):
    ref='numeric/raw/fmp/aa/'+'a'*64+'.gz'
    approve(store,symbol='OLD',provider_symbol='NEW',source_refs=[ref])
    row=dict(old_symbol='OLD',new_symbol='NEW',event_date='2026-09-03',raw_ref=ref)
    guard_reference_changes(store,'symbol_changes',[row],observed_at=CAPTURE)
    assert price_identities(store)['OLD']['status']=='verified'
    assert price_identities(store)['NEW']['status']=='blocked'


def test_pending_revisions_observe_request_completion_and_identity_information_clock(store):
    kwargs=dict(dataset='us_eod_daily',symbols=['REUSED'],end=END)
    first=store.ingest('stock_splits',[],source='fixture',observed_at=OLD,details={
        'observed_at':OLD,'price_revision_requests':[{'symbol':'REUSED','effective_date':'2026-09-01'}]})
    assert pending_revisions(store,**kwargs,as_of=OLD-timedelta(seconds=1))=={}
    request_id=pending_revisions(store,**kwargs,as_of=OLD)['REUSED']
    store.ingest('us_eod_daily',[],source='fixture',observed_at=REVIEW,details={
        'observed_at':REVIEW,'price_revision_completed':{'REUSED':request_id}})
    assert pending_revisions(store,**kwargs,as_of=OLD)['REUSED']==request_id
    assert pending_revisions(store,**kwargs,as_of=REVIEW)=={}
    record_price_identities(store,[decision(status='blocked',reason='Later discovered identity conflict')],
                            apply=True,observed_at=CAPTURE)
    assert pending_revisions(store,**kwargs,as_of=REVIEW)=={}
    assert pending_revisions(store,**kwargs,as_of=CAPTURE)=={}
    assert price_identities(store,as_of=CAPTURE)['REUSED']['status']=='blocked'
