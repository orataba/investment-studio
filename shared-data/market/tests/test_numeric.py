from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import json
import gzip

import duckdb
import pytest
from sqlalchemy import event, select, update

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.schema import batches,files,current,datasets
from studio_market.numeric.migrate import migration_plan,migrate_source
from studio_market.numeric.replication import export_bundle,import_bundle
from studio_market.numeric.raw import archive_response
from studio_market.numeric.reporting import build_report_snapshot

UTC=timezone.utc

def stamp(day):return datetime(2026,9,day,12,tzinfo=UTC)

@pytest.fixture
def store(tmp_path):
    settings=MarketSettings(f"sqlite:///{tmp_path/'market.db'}",tmp_path/'data')
    result=NumericStore(settings);result.create_schema_for_testing();return result


def estimate(value,day,target=2027,period="annual"):
    return dict(symbol="AAPL",estimate_period=period,target_period_end=date(target,12,31),eps_avg=value,collected_at=stamp(day))


def test_a_b_a_and_complete_frequency_snapshot(store):
    for day,value in [(1,10),(2,20),(3,10)]:
        rows=[estimate(value,day)]
        if day==1:rows.append(estimate(30,day,2028))
        store.ingest("analyst_estimates",[rows],source="fixture")
    latest=store.latest("analyst_estimates")
    assert latest['total']==1 and latest['rows'][0]['eps_avg']==10
    assert store.query("analyst_estimates",as_of=stamp(2))['rows'][0]['eps_avg']==20
    assert len(store.query("analyst_estimates",versions=True)['rows'])==4
    captures=store.observations("analyst_estimates")['observations']
    assert len(captures)==2 and captures[0]['estimate_period']=='annual'
    assert store.query("analyst_estimates",observed_at=captures[1]['observed_at'])['rows'][0]['eps_avg']==20


def test_frequency_clocks_and_empty_snapshot(store):
    store.ingest('analyst_estimates',[[estimate(3,1),estimate(4,1,period='quarter')]],source='fixture')
    store.ingest('analyst_estimates',[[estimate(5,2)]],source='fixture')
    rows=store.latest('analyst_estimates')['rows']
    assert {(r['estimate_period'],r['eps_avg']) for r in rows}=={('annual',5),('quarter',4)}
    store.ingest('analyst_estimates',[],source='fixture',observed_at=stamp(3),details={'snapshot_scopes':[{'symbol':'AAPL','frequency':'annual','snapshot_at':stamp(3).isoformat()}]})
    assert [r['estimate_period'] for r in store.latest('analyst_estimates')['rows']]==['quarter']


def test_complete_snapshot_projection_and_clock_survive_reverse_import(store,tmp_path):
    first=store.ingest('analyst_estimates',[[estimate(3,1),estimate(4,1,2028)]],source='fixture')
    second=store.ingest('analyst_estimates',[[estimate(5,2)]],source='fixture')
    target=NumericStore(MarketSettings(f"sqlite:///{tmp_path/'reverse.db'}",tmp_path/'reverse'))
    target.create_schema_for_testing()
    try:
        for item in (second,first):
            path=tmp_path/(item['batch_id']+'.zip')
            export_bundle(store.settings,path,batch_ids=[item['batch_id']])
            import_bundle(target.settings,path)
        with target.engine.connect() as conn:
            projected=conn.execute(select(current.c.payload).where(current.c.dataset=='analyst_estimates')).scalars().all()
            updated=conn.execute(select(datasets.c.updated_at).where(datasets.c.name=='analyst_estimates')).scalar_one()
            newest=conn.execute(select(batches.c.published_at).where(batches.c.id==second['batch_id'])).scalar_one()
        assert len(projected)==1 and projected[0]['eps_avg']==5
        assert updated==newest
        assert target.latest('analyst_estimates')['rows'][0]['source_id']==projected[0]['source_id']
        empty=store.ingest('analyst_estimates',[],source='fixture',observed_at=stamp(3),
            details={'snapshot_scopes':[{'symbol':'AAPL','frequency':'annual','snapshot_at':stamp(3)}]})
        path=tmp_path/'empty.zip';export_bundle(store.settings,path,batch_ids=[empty['batch_id']])
        import_bundle(target.settings,path)
        with target.engine.connect() as conn:
            assert conn.execute(select(current).where(current.c.dataset=='analyst_estimates')).first() is None
    finally:target.close()


def test_mixed_capture_clocks_do_not_clear_another_symbols_newer_projection(store):
    msft=lambda value,day:dict(estimate(value,day),symbol='MSFT')
    store.ingest('analyst_estimates',[[estimate(10,3),msft(30,3)]],source='fixture')
    store.ingest('analyst_estimates',[[estimate(11,4),msft(20,2)]],source='fixture')
    with store.engine.connect() as conn:
        projected=conn.execute(select(current.c.payload).where(current.c.dataset=='analyst_estimates')).scalars().all()
    assert {r['symbol']:r['eps_avg'] for r in projected}=={'AAPL':11,'MSFT':30}


def test_current_and_history_use_the_same_equal_timestamp_tiebreak(store,monkeypatch):
    import studio_market.numeric.store as module
    ids=iter(['00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001'])
    monkeypatch.setattr(module,'uuid4',lambda:next(ids))
    for value in (100,200):
        store.ingest('analyst_price_targets',[[dict(symbol='AAPL',target=value,collected_at=stamp(1))]],source='fixture')
    assert store.latest('analyst_price_targets')['rows'][0]['source_id']==store.latest('analyst_price_targets',as_of=stamp(2))['rows'][0]['source_id']


def test_financial_revisions_do_not_leak_back(store):
    row=dict(symbol='AAPL',statement_type='income',period_end=date(2026,6,30),fiscal_year=2026,fiscal_period='Q2',filing_date=date(2026,7,31),line_item='revenue')
    store.ingest('financial_facts',[[dict(row,value=100,collected_at=stamp(1))]],source='fixture')
    store.ingest('financial_facts',[[dict(row,value=90,collected_at=stamp(3))]],source='fixture')
    assert store.query('financial_facts',as_of=stamp(2))['rows'][0]['value']==100
    assert store.query('financial_facts')['rows'][0]['value']==90
    assert store.query('financial_facts',as_of='2026-08-01T00:00:00+00:00')['rows']==[]


def test_etf_latest_is_whole_snapshot(store):
    def holding(key,day):return dict(etf_symbol='SPY',holding_key=key,holding_symbol=key,snapshot_date=date(2026,9,day),collected_at=stamp(day),weight_percent=10)
    store.ingest('etf_holdings',[[holding('AAPL',1),holding('MSFT',1)]],source='fixture')
    store.ingest('etf_holdings',[[holding('AAPL',2)]],source='fixture')
    assert [r['holding_symbol'] for r in store.latest('etf_holdings')['rows']]==['AAPL']
    assert store.query('etf_holdings',as_of=stamp(1))['total']==2


def test_collector_empty_etf_snapshot_removes_current_holdings(store):
    from types import SimpleNamespace
    from studio_market.numeric.collect import Collector
    store.ingest('etf_holdings',[[dict(etf_symbol='SPY',holding_key='AAPL',holding_symbol='AAPL',snapshot_date=date(2026,9,1),collected_at=stamp(1),weight_percent=10)]],source='fixture')
    class Client:
        def get_json(self,endpoint,params):
            payload=[{'symbol':'SPY','name':'SPDR S&P 500 ETF'}] if endpoint=='etf/info' else []
            return SimpleNamespace(endpoint=endpoint,params=params,payload=payload,body=json.dumps(payload).encode(),received_at=stamp(2))
    Collector(store.settings,store=store,client=Client()).etf(date(2026,9,1),date(2026,9,2),['SPY'])
    assert store.latest('etf_holdings',symbols=['SPY'])['rows']==[]
    assert store.query('etf_holdings',as_of=stamp(1))['total']==1


def test_failed_publication_never_changes_readers(store):
    store.ingest('analyst_price_targets',[[dict(symbol='AAPL',last_month_avg_price_target=100,collected_at=stamp(1))]],source='fixture')
    def partial():
        yield [dict(symbol='AAPL',last_month_avg_price_target=999,collected_at=stamp(2))]
        raise RuntimeError('source stopped')
    with pytest.raises(RuntimeError):store.ingest('analyst_price_targets',partial(),source='fixture')
    assert store.latest('analyst_price_targets')['rows'][0]['last_month_avg_price_target']==100
    assert store.query('analyst_price_targets')['total']==1
    with store.engine.connect() as conn:
        bad=conn.execute(select(batches.c.id).where(batches.c.status=='failed')).scalar_one()
        assert conn.execute(select(files).where(files.c.batch_id==bad)).first() is None


def test_unwritable_batch_directory_is_reported_as_failed(store, monkeypatch):
    original = Path.mkdir
    def refuse_batch(path, *args, **kwargs):
        if path.parent.name == 'analyst_price_targets':
            raise PermissionError('read-only storage')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', refuse_batch)
    with pytest.raises(PermissionError):
        store.ingest('analyst_price_targets', [], source='fixture')
    with store.engine.connect() as conn:
        assert conn.execute(select(batches.c.status)).scalar_one() == 'failed'
    assert store.status()['recent_failures'][0]['error'].startswith('PermissionError:')


def test_threads_capturing_identical_raw_response_use_distinct_staging_files(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from studio_market.numeric import raw
    barrier = Barrier(2)
    compress = raw.gzip.compress
    def together(*args, **kwargs):
        barrier.wait(timeout=5)
        return compress(*args, **kwargs)
    monkeypatch.setattr(raw.gzip, 'compress', together)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: archive_response(store.settings, 'fixture', b'original'), range(2)))
    assert results[0] == results[1]
    assert gzip.decompress((store.settings.data_root / results[0][1]).read_bytes()) == b'original'


def test_latest_asof_and_pagination_prices(store):
    for day in (1,2,3):
        store.ingest('raw_eod_daily',[[dict(symbol='SPY',date=date(2026,9,day),open=100,high=101,low=99,close=100,adjusted_close=90,volume=5,collected_at=stamp(day))]],source='fixture')
    result=store.latest('raw_eod_daily',as_of=stamp(2))
    assert result['total']==1 and result['rows'][0]['date']=='2026-09-02'
    assert result['rows'][0]['adjustment_factor']==.9
    assert result['rows'][0]['ohlc_adjustment']=='unadjusted'
    page=store.query('raw_eod_daily',limit=1,offset=1)
    assert page['total']==3 and page['rows'][0]['date']=='2026-09-02'


def test_visible_current_matches_history_and_reads_one_snapshot(store, monkeypatch):
    # Include same-fact A -> B -> A, an older-date later revision, and ties.
    for observed, value in ((1, 10), (2, 20), (3, 10)):
        store.ingest('macro_series', [[dict(series_id='A', date=date(2026, 9, 1),
            value=value, observed_at=stamp(observed))]], source='fixture')
    store.ingest('macro_series', [[
        dict(series_id='B', date=date(2026, 9, 2), value=30, observed_at=stamp(2)),
        dict(series_id='C', date=date(2026, 9, 2), value=40, observed_at=stamp(2)),
        dict(series_id='C', date=date(2026, 9, 2), value=41, observed_at=stamp(2)),
        dict(series_id='B', date=date(2026, 8, 31), value=99, observed_at=stamp(3)),
    ]], source='fixture')
    expected = store.query('macro_series', as_of=stamp(3), limit=2, _latest=True)
    statements = []
    event.listen(store.engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
    monkeypatch.setattr(store, 'query', lambda *a, **k: pytest.fail('visible latest scanned history'))
    result = store.latest('macro_series', as_of=stamp(3), limit=2)
    assert {key: value for key, value in result.items() if key != 'rows'} == {
        key: value for key, value in expected.items() if key != 'rows'}
    clocks = {'observed_at', 'available_at', 'snapshot_at'}
    for actual, historical in zip(result['rows'], expected['rows']):
        assert {key: value for key, value in actual.items() if key not in clocks} == {
            key: value for key, value in historical.items() if key not in clocks}
        assert all(datetime.fromisoformat(actual[key]) == datetime.fromisoformat(historical[key]) for key in clocks)
    assert result['total'] == 3
    assert [row['symbol'] for row in result['rows']] == ['B', 'C']
    assert result['rows'][1]['value'] == 41
    assert len(statements) == 1
    assert store.latest('macro_series', symbols=['A'], as_of=stamp(3))['rows'][0]['value'] == 10
    assert store.latest('macro_series', symbols=[], as_of=stamp(3))['total'] == 0
    assert store.latest('macro_series', symbols=['UNKNOWN'], as_of=stamp(3))['rows'] == []
    for limit in (0, 100001):
        with pytest.raises(ValueError):
            store.latest('macro_series', as_of=stamp(3), limit=limit)


@pytest.mark.parametrize('future_clock', ['observed_at', 'available_at'])
def test_latest_cutoff_checks_winners_beyond_limit(store, monkeypatch, future_clock):
    store.ingest('macro_series', [[
        dict(series_id='A', date=date(2026, 9, 2), value=10, observed_at=stamp(1)),
        dict(series_id='Z', date=date(2026, 9, 1), value=20, observed_at=stamp(1)),
    ]], source='fixture')
    future = dict(series_id='Z', date=date(2026, 9, 1), value=99,
                  observed_at=stamp(2), available_at=stamp(2))
    future[future_clock] = stamp(3)
    store.ingest('macro_series', [[future]], source='fixture')
    original = store.query
    calls = []
    def history(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'query', history)
    result = store.latest('macro_series', as_of=stamp(2), limit=1)
    assert result == original('macro_series', as_of=stamp(2), limit=1, _latest=True)
    assert result['total'] == 2 and len(calls) == 1
    result = store.latest('macro_series', symbols=['Z'], as_of=stamp(2))
    assert result['rows'][0]['value'] == 20
    assert len(calls) == 2


def test_latest_cutoff_preserves_exact_microsecond_availability(store, monkeypatch):
    cutoff = stamp(2)
    store.ingest('analyst_price_targets', [[dict(symbol='A', target=10,
        observed_at=stamp(1))]], source='fixture')
    store.ingest('analyst_price_targets', [[dict(symbol='A', target=20,
        observed_at=cutoff, available_at=cutoff + timedelta(microseconds=1))]], source='fixture')
    assert store.latest('analyst_price_targets', as_of=cutoff)['rows'][0]['target'] == 10
    monkeypatch.setattr(store, 'query', lambda *a, **k: pytest.fail('visible clock scanned history'))
    assert store.latest('analyst_price_targets',
        as_of=cutoff + timedelta(microseconds=1))['rows'][0]['target'] == 20


def test_latest_missing_availability_does_not_prove_visibility(store, monkeypatch):
    store.ingest('analyst_price_targets', [[dict(symbol='A', target=10,
        observed_at=stamp(1))]], source='fixture')
    with store.engine.begin() as conn:
        payload = conn.execute(select(current.c.payload)).scalar_one()
        payload['available_at'] = None
        conn.execute(update(current).values(payload=payload))
    expected = store.query('analyst_price_targets', as_of=stamp(2), _latest=True)
    calls = []
    monkeypatch.setattr(store, 'query', lambda *a, **k: calls.append(k) or expected)
    assert store.latest('analyst_price_targets', as_of=stamp(2)) == expected
    assert len(calls) == 1


def test_mutable_fact_date_stays_on_historical_query(store, monkeypatch):
    for observed, day in ((1, 5), (2, 4)):
        store.ingest('delisted_securities', [[dict(symbol='A',
            delisted_date=date(2026, 9, day), observed_at=stamp(observed))]], source='fixture')
    monkeypatch.setattr(store, '_visible_current', lambda *a, **k: pytest.fail('mutable date used projection'))
    assert store.latest('delisted_securities', as_of=stamp(2))['rows'][0]['date'] == '2026-09-04'


def test_numeric_bundle_keeps_source_ids_and_is_idempotent(store,tmp_path):
    _,ref=archive_response(store.settings,'fixture',b'{"data":10}')
    store.ingest('analyst_estimates',[[dict(estimate(10,1),raw_ref=ref)]],source='fixture')
    source_id=store.latest('analyst_estimates')['rows'][0]['source_id']
    bundle=tmp_path/'numeric.zip';export_bundle(store.settings,bundle)
    target=NumericStore(MarketSettings(f"sqlite:///{tmp_path/'target.db'}",tmp_path/'target'));target.create_schema_for_testing()
    assert import_bundle(target.settings,bundle)['batches'][0]['status']=='ready'
    assert target.read_source(source_id)['eps_avg']==10
    assert target.latest('analyst_estimates')['rows'][0]['source_id']==source_id
    assert import_bundle(target.settings,bundle)['batches'][0]['status']=='already_imported'


def test_bundle_projection_matches_date_clock_and_row_index_rank_across_parts(store,tmp_path):
    def value(day,observed,number):
        return dict(series_id='rate',date=date(2026,9,day),value=number,collected_at=stamp(observed),available_at=stamp(observed))
    store.ingest('macro_series',[[value(4,5,10),value(5,6,20)],[value(5,7,30),value(5,7,31)],[value(4,7,99)]],source='fixture')
    for day,number in [(1,10),(2,20),(3,10)]:
        store.ingest('analyst_estimates',[[estimate(number,day),estimate(99,day,target=2028)] if day==1 else [estimate(number,day)]],source='fixture')
    store.ingest('analyst_estimates',[],source='fixture',observed_at=stamp(4),details={'snapshot_scopes':[{'symbol':'AAPL','frequency':'annual','snapshot_at':stamp(4)}]})
    fact=dict(symbol='AAPL',statement_type='income',period_end=date(2026,6,30),fiscal_year=2026,fiscal_period='Q2',line_item='revenue',value=100,collected_at=stamp(1))
    store.ingest('financial_facts',[[fact]],source='fixture')
    archive=tmp_path/'rank.zip';export_bundle(store.settings,archive)
    target=NumericStore(MarketSettings(f"sqlite:///{tmp_path/'rank.db'}",tmp_path/'rank'));target.create_schema_for_testing()
    try:
        import_bundle(target.settings,archive)
        original=store.latest('macro_series')['rows'][0]
        copied=target.latest('macro_series')['rows'][0]
        assert copied['value']==31 and copied['row_index']==3
        assert copied==original
        assert target.latest('analyst_estimates')['rows']==[]
        assert target.query('analyst_estimates',as_of=stamp(2))['rows']==store.query('analyst_estimates',as_of=stamp(2))['rows']
        assert target.query('analyst_estimates',versions=True)['rows']==store.query('analyst_estimates',versions=True)['rows']
        assert target.query('financial_facts')['rows']==store.query('financial_facts')['rows']
    finally:target.close()


def test_migration_excludes_minutes_preserves_versions_and_skips_completed(store,tmp_path):
    source=tmp_path/'source';source.mkdir()
    with duckdb.connect(str(source/'market_research.duckdb')) as db:
        db.execute('CREATE TABLE analyst_price_target_observations(symbol VARCHAR,last_month_avg_price_target DOUBLE,collected_at TIMESTAMPTZ)')
        db.execute("INSERT INTO analyst_price_target_observations VALUES ('AAPL',10,'2026-09-01T12:00:00Z'),('AAPL',20,'2026-09-02T12:00:00Z')")
        db.execute('CREATE TABLE cn_futures_contract_minute (symbol VARCHAR)')
    plan=migration_plan(source)
    assert [r['dataset'] for r in plan['datasets']]==['analyst_price_targets']
    receipt=migrate_source(store.settings,source,batch_rows=1)
    assert receipt['batches'][0]['row_count']==2
    assert receipt['batches'][0]['source_counts']['source_rows']==2
    assert store.latest('analyst_price_targets')['rows'][0]['last_month_avg_price_target']==20
    assert migrate_source(store.settings,source)['batches'][0]['status']=='already_imported'


def test_daily_report_uses_last_two_us_closes_in_china_morning(store):
    for day,close in [(3,100),(4,110)]:
        store.ingest('us_eod_daily',[[dict(symbol='SPY',date=date(2026,9,day),open=close,high=close,low=close,close=close,adjusted_close=close,volume=1,collected_at=datetime(2026,9,day,22,tzinfo=UTC))]],source='fixture')
    result=build_report_snapshot(store.settings,report_kind='daily',period_start=date(2026,9,5),period_end=date(2026,9,5),cutoff=datetime(2026,9,5,0,tzinfo=UTC))
    row=result['market_rows'][0]
    assert row['start_date']=='2026-09-03' and row['end_date']=='2026-09-04'
    assert row['return_pct']==pytest.approx(10)


def test_migration_exclusion_receipt_retains_raw_evidence(store,tmp_path):
    import hashlib
    source=tmp_path/'financial_source';source.mkdir()
    bodies=[b'{"period":"valid"}',b'{"period":"future"}']
    shas=[hashlib.sha256(b).hexdigest() for b in bodies]
    with duckdb.connect(str(source/'market_research.duckdb')) as db:
        db.execute('CREATE TABLE financial_statement_observations(symbol VARCHAR,statement_type VARCHAR,period_end DATE,fiscal_year INTEGER,fiscal_period VARCHAR,collected_at TIMESTAMPTZ,raw_sha256 VARCHAR)')
        db.execute('CREATE TABLE raw_objects(raw_sha256 VARCHAR,relative_path VARCHAR,retrieved_at TIMESTAMPTZ)')
        for i,(body,sha) in enumerate(zip(bodies,shas)):
            path=f'raw/{sha}.gz';(source/'raw').mkdir(exist_ok=True);(source/path).write_bytes(gzip.compress(body))
            db.execute("INSERT INTO raw_objects VALUES (?,?,'2026-09-01T00:00:00Z')",[sha,path])
            db.execute("INSERT INTO financial_statement_observations VALUES ('AAPL','income',?,2026,'Q2','2026-09-01T00:00:00Z',?)",['2026-06-30' if i==0 else '2026-12-31',sha])
    result=migrate_source(store.settings,source,names=['financial_statements'])
    counts=result['batches'][0]['source_counts']
    assert counts['source_rows']==2 and counts['excluded_rows']==1
    assert result['batches'][0]['row_count']==1
    assert all((store.settings.data_root/f'numeric/raw/imported/{sha[:2]}/{sha}.gz').is_file() for sha in shas)


def test_raw_fmp_wire_names_and_second_response_clock(store):
    from studio_market.numeric.collect import Collector
    from studio_market.numeric.providers.fmp import FmpResponse
    class Client:
        def get_json(self,endpoint,params):
            adjusted=endpoint.endswith('/dividend-adjusted')
            row=dict(symbol='SPY',date='2026-09-01',adjOpen=95 if adjusted else 100,adjHigh=100 if adjusted else 105,adjLow=90 if adjusted else 95,adjClose=98 if adjusted else 103,volume=500)
            return FmpResponse(endpoint,params,stamp(2 if adjusted else 1),json.dumps([row]).encode(),[row])
    collector=Collector(store.settings,store=store,client=Client())
    collector.raw_eod(date(2026,9,1),date(2026,9,1),['SPY'])
    row=store.latest('raw_eod_daily')['rows'][0]
    assert row['open']==100 and row['close']==103
    assert row['adjusted_open']==95 and row['adjusted_close']==98
    assert datetime.fromisoformat(row['observed_at'])==stamp(2)
    assert row['raw_ref']!=row['adjusted_raw_ref']


def test_raw_adjustment_change_refreshes_history_before_overlap(store):
    from studio_market.numeric.collect import Collector
    from studio_market.numeric.providers.fmp import FmpResponse
    for day in ('2025-01-02','2026-09-01'):
        store.ingest('raw_eod_daily',[[dict(symbol='SPY',date=day,open=100,high=100,low=100,close=100,adjusted_close=100,volume=1,collected_at=stamp(1))]],source='fixture')
    class Client:
        def get_json(self,endpoint,params):
            adjusted=endpoint.endswith('/dividend-adjusted')
            payload=[dict(symbol='SPY',date=day,adjOpen=90 if adjusted else 100,adjHigh=90 if adjusted else 100,adjLow=90 if adjusted else 100,adjClose=90 if adjusted else 100,volume=1) for day in ('2025-01-02','2026-09-01') if params['from']<=day<=params['to']]
            return FmpResponse(endpoint,params,stamp(2),json.dumps(payload).encode(),payload)
    collector=Collector(store.settings,store=store,client=Client())
    collector.raw_eod(date(2026,9,1),date(2026,9,1),['SPY'])
    older=store.query('raw_eod_daily',end='2025-01-02')['rows'][0]
    assert older['adjusted_close']==90 and older['adjustment_factor']==.9


def test_report_uses_owned_cn_hk_and_special_source_closes_with_capture_cutoff(store):
    capture=datetime(2026,9,7,10,tzinfo=UTC)
    for series,days in [("datahub:index_daily:000985.CSI",[(4,100),(7,102)]),("hsil:HSCI.HI",[(3,200),(4,198)]),("fmp:raw:BTCUSD",[(5,1000),(6,1100)]),("fmp:raw:XAUUSD",[(3,300),(4,303)]),("fmp:raw:DX-Y.NYB",[(3,100),(4,99)])]:
        store.ingest("regime_market_daily",[[{"series_id":series,"date":date(2026,9,day),"close":close} for day,close in days]],source="fixture",observed_at=capture)
    before=build_report_snapshot(store.settings,report_kind="daily",period_start=date(2026,9,7),period_end=date(2026,9,7),cutoff=datetime(2026,9,7,9,tzinfo=UTC))
    assert before["market_rows"]==[]
    result=build_report_snapshot(store.settings,report_kind="daily",period_start=date(2026,9,7),period_end=date(2026,9,7),cutoff=datetime(2026,9,7,11,tzinfo=UTC))
    rows={row["symbol"]:row for row in result["market_rows"]}
    assert rows["000985.CSI"]["end_date"]=="2026-09-07"
    assert rows["000985.CSI"]["return_pct"]==pytest.approx(2)
    assert rows["HSCI.HI"]["end_date"]=="2026-09-04"
    assert rows["HSCI.HI"]["return_pct"]==pytest.approx(-1)
    assert rows["BTCUSD"]["end_date"]=="2026-09-06"
    assert rows["DX-Y.NYB"]["end_close"]==99
    for row in rows.values():
        assert row["return_basis"]=="unadjusted_price"
        assert row["price_field"]=="close" and len(row["source_ids"])==2
        assert all(store.read_source(source_id)["source"]=="fixture" for source_id in row["source_ids"])
