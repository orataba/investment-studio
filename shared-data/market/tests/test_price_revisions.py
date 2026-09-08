from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.collect import Collector
from studio_market.numeric.price_revisions import pending_revisions
from studio_market.numeric.replication import export_bundle, import_bundle

UTC = timezone.utc


@pytest.fixture
def store(tmp_path):
    value = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / 'source'))
    value.create_schema_for_testing()
    yield value
    value.close()


def request(store, symbol='SPY', *, day=1, effective='2026-09-01'):
    return store.ingest('stock_splits', [], source='fixture', observed_at=datetime(2026,9,day,tzinfo=UTC),
        details={'observed_at':datetime(2026,9,day,tzinfo=UTC),
                 'price_revision_requests':[{'symbol':symbol, 'effective_date':effective}]})


def pending(store, symbols=('SPY',), dataset='us_eod_daily', end=date(2026,9,8)):
    return pending_revisions(store, dataset=dataset, symbols=symbols, end=end)


def complete(store, symbol, identity, dataset='us_eod_daily'):
    store.ingest(dataset, [], source='fixture', details={'price_revision_completed':{symbol:identity}})


def test_failed_symbol_remains_due_after_restart_and_does_not_block_another(store, monkeypatch):
    request(store, 'AAPL'); request(store, 'SPY')
    attempts=[]
    fail=True
    def prices(self, symbols, start, end, **kwargs):
        symbol=symbols[0];attempts.append(symbol)
        if symbol=='AAPL' and fail:raise ValueError('incomplete provider history')
        complete(self.store, symbol, kwargs['revision_requests'][symbol])
    monkeypatch.setattr(Collector, 'symbol_prices', prices)
    collector=Collector(store.settings, store=store)
    result=collector.reconcile_price_revisions(date(2026,9,8), symbols=['AAPL','SPY'])
    assert result['status']=='failed'
    assert set(pending(store, ['AAPL','SPY']))=={'AAPL'}
    # A fresh worker has no in-memory action changes, but reads the same obligation.
    fail=False
    fresh=Collector(store.settings, store=store)
    assert fresh.reconcile_price_revisions(date(2026,9,8),symbols=['AAPL','SPY'])['status']=='ready'
    assert attempts==['AAPL','SPY','AAPL']
    assert pending(store,['AAPL','SPY'])=={}


def test_newer_action_cannot_be_cleared_by_an_older_run_and_raw_completes_separately(store):
    request(store)
    original=pending(store)['SPY']
    request(store,day=2)
    newer=pending(store)['SPY']
    complete(store,'SPY',original)
    assert pending(store)['SPY']==newer
    complete(store,'SPY',newer)
    assert pending(store)=={}
    assert pending(store,dataset='raw_eod_daily')['SPY']==newer


def test_announced_action_becomes_due_only_on_effective_day(store):
    request(store,effective='2026-09-08')
    assert pending(store,end=date(2026,9,7))=={}
    assert 'SPY' in pending(store,end=date(2026,9,8))


def test_same_capture_future_and_current_actions_have_distinct_receipts(store):
    store.ingest('stock_splits',[],source='fixture',observed_at=datetime(2026,9,1,tzinfo=UTC),details={
        'price_revision_requests':[{'symbol':'SPY','effective_date':'2026-09-01'},
                                   {'symbol':'SPY','effective_date':'2026-09-08'}]})
    complete(store,'SPY',pending(store,end=date(2026,9,7))['SPY'])
    assert pending(store,end=date(2026,9,7))=={}
    assert 'SPY' in pending(store,end=date(2026,9,8))


def test_full_history_receipt_is_absent_until_every_price_part_succeeds(store):
    request(store)
    identity=pending(store)['SPY']
    class Client:
        fail=True
        def get_json(self, endpoint, params):
            if params['from']>'2000-01-03' and self.fail:raise ValueError('later history part failed')
            row={'symbol':'SPY','date':params['to'],'volume':1,'open':100,'high':101,'low':99,'close':100,
                 'adjOpen':90,'adjHigh':91,'adjLow':89,'adjClose':90}
            return SimpleNamespace(endpoint=endpoint,params=params,payload=[row],body=str(params).encode(),received_at=datetime(2026,9,8,tzinfo=UTC))
    client=Client();collector=Collector(store.settings,store=store,client=client)
    with pytest.raises(ValueError):
        collector.symbol_prices(['SPY'],date(2000,1,3),date(2005,1,1),revision_requests={'SPY':identity})
    assert pending(store)['SPY']==identity
    client.fail=False
    collector.symbol_prices(['SPY'],date(2000,1,3),date(2026,9,8),revision_requests={'SPY':identity})
    assert pending(store)=={}


def test_pending_requests_and_completion_survive_bundle_replication(store,tmp_path):
    request(store)
    archive=tmp_path/'pending.zip';export_bundle(store.settings,archive)
    replica=NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'replica.db'}",tmp_path/'replica'))
    replica.create_schema_for_testing()
    try:
        import_bundle(replica.settings,archive)
        assert pending(replica)==pending(store)
        complete(store,'SPY',pending(store)['SPY'])
        archive=tmp_path/'completed.zip';export_bundle(store.settings,archive)
        import_bundle(replica.settings,archive)
        assert pending(replica)=={}
    finally:replica.close()


def test_legacy_recovery_preserves_pit_and_distinguishes_full_from_partial_rebuild(store):
    from studio_market.numeric.price_revisions import recover_legacy_revisions
    def action(day, value):
        return store.ingest('stock_splits', [[{'symbol':'SPY','event_date':'2026-09-01','numerator':value,'denominator':1}]],
                            source='fmp',observed_at=datetime(2026,9,day,tzinfo=UTC),
                            details={'observed_at':datetime(2026,9,day,tzinfo=UTC)})
    action(1,2); changed=action(2,3); action(3,3)
    def prices(start, stop, day=4):
        return store.ingest('us_eod_daily', [[{'symbol':'SPY','date':stop,'close':100,'adjusted_close':90}]],
            source='fmp',observed_at=datetime(2026,9,day,tzinfo=UTC),details={
                'observed_at':datetime(2026,9,day,tzinfo=UTC),'parameters':{'symbol':'SPY','from':start,'to':stop}})
    before=store.query('stock_splits',versions=True)
    first=prices('2000-01-03','2010-12-31')
    args={'since':'2026-09-02T00:00:00+00:00','end':date(2026,9,8),'symbols':['SPY']}
    preview=recover_legacy_revisions(store,**args)
    assert len(preview['requests'])==1
    assert preview['requests'][0]['request_id'].startswith('numeric:'+changed['batch_id'])
    assert preview['requests'][0]['completion_evidence']=={}
    assert pending(store)=={}
    recover_legacy_revisions(store,**args,apply=True)
    identity=pending(store)['SPY']
    assert pending(store,dataset='raw_eod_daily')['SPY']==identity
    assert recover_legacy_revisions(store,**args,apply=True)['receipt_batches']==[]
    last=prices('2011-01-01','2026-09-08')
    recovered=recover_legacy_revisions(store,**args,apply=True)
    assert recovered['requests'][0]['completion_evidence']['us_eod_daily']==[first['batch_id'],last['batch_id']]
    assert pending(store)=={}
    assert pending(store,dataset='raw_eod_daily')['SPY']==identity
    assert store.query('stock_splits',versions=True)==before
    # Recovery receipt creation is not a new observation of the original fact.
    assert store.query('stock_splits',as_of='2026-09-01T23:59:00+00:00')['rows'][0]['numerator']==2
    assert recover_legacy_revisions(store,**args,apply=True)['receipt_batches']==[]


def test_legacy_raw_mismatch_is_recovered_without_affecting_adjusted_dataset(store):
    from studio_market.numeric.price_revisions import recover_legacy_revisions
    for day,value in [(1,90),(2,80),(3,80)]:
        store.ingest('raw_eod_daily', [[{'symbol':'SPY','date':'2026-09-01','close':100,'adjusted_close':value}]],
            source='fmp',observed_at=datetime(2026,9,day,tzinfo=UTC),details={
                'observed_at':datetime(2026,9,day,tzinfo=UTC),
                'parameters':{'symbol':'SPY','from':'2026-09-01','to':'2026-09-01'}})
    result=recover_legacy_revisions(store,since='2026-09-02T00:00:00+00:00',end=date(2026,9,8),symbols=['SPY'],apply=True)
    assert len(result['requests'])==1
    assert pending(store)=={}
    assert 'SPY' in pending(store,dataset='raw_eod_daily')


def test_raw_revision_refreshes_retained_history_before_2000(store,monkeypatch):
    request(store)
    store.ingest('raw_eod_daily',[[{'symbol':'SPY','date':'1998-12-22','close':100,'adjusted_close':90}]],source='fixture')
    starts=[]
    monkeypatch.setattr(Collector,'symbol_prices',lambda self,symbols,start,end,**kwargs: starts.append(start))
    Collector(store.settings,store=store).reconcile_price_revisions(date(2026,9,7),symbols=['SPY'],raw=True)
    assert starts==[date(1998,12,22)]


def test_unchanged_future_action_capture_keeps_original_due_request(store):
    import json
    from studio_market.numeric.providers.fmp import FmpResponse
    class Client:
        day=1
        def get_json(self,endpoint,params):
            rows=[{'symbol':'SPY','date':'2026-09-08','numerator':2,'denominator':1}] if endpoint=='splits-calendar' and params['from']<='2026-09-08'<=params['to'] else []
            return FmpResponse(endpoint,params,datetime(2026,9,self.day,tzinfo=UTC),json.dumps(rows).encode(),rows)
    client=Client(); collector=Collector(store.settings,store=store,client=client)
    collector.actions(date(2026,9,1),date(2026,9,7),['SPY'])
    identity=pending(store)['SPY']
    assert pending(store,end=date(2026,9,7))=={}
    client.day=2
    collector.actions(date(2026,9,1),date(2026,9,7),['SPY'])
    assert pending(store)['SPY']==identity


def test_empty_history_segment_cannot_complete_over_retained_prices(store):
    from studio_market.numeric.providers.fmp import FmpResponse
    request(store)
    identity=pending(store)['SPY']
    store.ingest('us_eod_daily',[[{'symbol':'SPY','date':'2001-01-02','close':100,'adjusted_close':90}]],source='fixture')
    class Client:
        def get_json(self,endpoint,params):
            return FmpResponse(endpoint,params,datetime(2026,9,8,tzinfo=UTC),b'[]',[])
    collector=Collector(store.settings,store=store,client=Client())
    with pytest.raises(ValueError,match='omits retained'):
        collector.symbol_prices(['SPY'],date(2000,1,3),date(2026,9,8),revision_requests={'SPY':identity})
    assert pending(store)['SPY']==identity
    assert store.latest('us_eod_daily')['rows'][0]['adjusted_close']==90


def test_legacy_range_metadata_without_retained_dates_is_not_a_completed_rebuild(store):
    from studio_market.numeric.price_revisions import recover_legacy_revisions
    store.ingest('stock_splits',[[{'symbol':'SPY','event_date':'2026-09-01','numerator':2,'denominator':1}]],
                 source='fmp',observed_at=datetime(2026,9,1,tzinfo=UTC),details={'observed_at':'2026-09-01T00:00:00+00:00'})
    for day, observation, params in [('2001-01-02',1,{}),('2026-09-08',2,{'symbol':'SPY','from':'2000-01-03','to':'2026-09-08'})]:
        store.ingest('us_eod_daily',[[{'symbol':'SPY','date':day,'close':100,'adjusted_close':90}]],
            source='fmp',observed_at=datetime(2026,9,observation,tzinfo=UTC),
            details={'observed_at':datetime(2026,9,observation,tzinfo=UTC),'parameters':params})
    result=recover_legacy_revisions(store,since='2026-09-01T00:00:00+00:00',end=date(2026,9,8),symbols=['SPY'],apply=True)
    assert result['requests'][0]['completion_evidence']=={}
    assert 'SPY' in pending(store)
