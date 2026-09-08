from datetime import date,datetime,timezone
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric.store import NumericStore
from studio_market.numeric import delivery
from studio_market import pipeline
from studio_market.numeric.collect import Collector,closed_symbol_date,failure_summary
from studio_market.numeric.providers.http_client import PublicResponse


def make_store(tmp_path,name):
    store=NumericStore(MarketSettings(f"sqlite:///{tmp_path/(name+'.db')}",tmp_path/name))
    store.create_schema_for_testing()
    return store


def test_public_macro_response_is_archived_and_published_without_json_payload(tmp_path):
    store=make_store(tmp_path,'macro')
    collector=Collector(store.settings,store=store)
    response=PublicResponse('https://fred.stlouisfed.org/graph/fredgraph.csv',{},
                            datetime(2026,9,8,tzinfo=timezone.utc),b'date,value\n2026-09-04,3.1\n')
    _,ref=collector.archive(response,'fred')
    collector.publish('macro_series',[dict(series_id='fixture',date=date(2026,9,4),value=3.1)],
                      response,ref,provider='fred')
    row=store.latest('macro_series')['rows'][0]
    assert row['value']==3.1 and row['raw_ref']==ref
    assert (store.settings.data_root/ref).is_file()
    store.close()


def test_default_us_universe_retains_us_delistings_without_global_corporate_actions(tmp_path):
    from types import SimpleNamespace
    rows = {
        'security_directory': [{'symbol':'AAPL', 'is_etf':False}],
        'delisted_securities': [{'symbol':'US-OLD','exchange':'NASDAQ'}, {'symbol':'US-OTC','exchange':'OTC'},
                                {'symbol':'MI-UN.TO','exchange':'TSX'}, {'symbol':'LTIM.NS','exchange':'NSE'}],
        'etf_info': [{'symbol':'SPY'}],
    }
    collector = Collector(MarketSettings('sqlite://', tmp_path),
                          store=SimpleNamespace(latest=lambda name, **kwargs: {'rows': rows[name]}))
    assert collector.universe() == {'AAPL', 'US-OLD', 'US-OTC', 'SPY'}
    assert collector.universe(companies=True) == {'AAPL'}


def test_bad_market_series_does_not_block_other_series(tmp_path,monkeypatch):
    from studio_market.numeric.collect import collect
    store=make_store(tmp_path,'series')
    called=[]
    def acquire(self,start,end,symbols):
        called.extend(symbols)
        if symbols==['DXY']:raise ValueError('DXY close is not positive')
        self.store.ingest('market_series_daily',[[dict(series_id=symbols[0],date=end,close=100)]],source='fixture')
    monkeypatch.setattr(Collector,'market_series',acquire)
    result=collect(store.settings,groups=['market_series'],symbols=['DXY','HSCIEN.HI'],
                   start=date(2026,9,1),end=date(2026,9,7))
    assert called==['DXY','HSCIEN.HI']
    assert result['status']=='failed'
    assert [(s['series_id'],s['status']) for s in result['stages']]==[('DXY','failed'),('HSCIEN.HI','ready')]
    assert store.latest('market_series_daily',symbols=['HSCIEN.HI'])['rows'][0]['date']=='2026-09-07'
    assert store.latest('market_series_daily',symbols=['DXY'])['rows']==[]
    store.close()


def test_one_macro_provider_failure_does_not_block_independent_series(tmp_path, monkeypatch):
    from studio_market.numeric.collect import collect
    from studio_market.numeric.providers.http_client import PublicHttpError
    store = make_store(tmp_path, 'independent-macro')
    def acquire(self, start, end, symbols):
        if symbols == ['CBOE_VIX']:
            raise PublicHttpError('public download failed with HTTP status 403')
        self.store.ingest('macro_series', [[dict(series_id=symbols[0],date=end,value=3.0)]], source='fixture')
    monkeypatch.setattr(Collector, 'macro', acquire)
    result = collect(store.settings, groups=['macro'], symbols=['CBOE_VIX', 'US_SOFR'],
                     start=date(2026,9,1), end=date(2026,9,7))
    assert result['status'] == 'failed'
    assert [(s['series_id'],s['status']) for s in result['stages']] == [('CBOE_VIX','failed'),('US_SOFR','ready')]
    assert 'acquire' in result['stages'][0]['error_location']
    assert store.latest('macro_series', symbols=['US_SOFR'])['rows'][0]['value'] == 3.0
    store.close()


def test_public_http_error_does_not_call_urllib_read_on_curl_response(monkeypatch):
    from types import SimpleNamespace
    from studio_market.numeric.providers import http_client
    closed=[]
    response=SimpleNamespace(status_code=403, content=b'forbidden', close=lambda:closed.append(True))
    monkeypatch.setattr(http_client.curl_requests, 'get', lambda *args, **kwargs: response)
    with pytest.raises(http_client.PublicHttpError, match='HTTP status 403'):
        http_client.get_public_bytes('https://public.example/series', max_attempts=1)
    assert closed == [True]


def test_directory_catches_up_all_missed_bundles_and_resumes_after_failure(tmp_path,monkeypatch):
    source=make_store(tmp_path,'source');target=make_store(tmp_path,'target')
    for day,value in [(1,10),(2,20)]:
        source.ingest('analyst_price_targets',[[dict(symbol='AAPL',last_month_avg_price_target=value,collected_at=datetime(2026,9,day,tzinfo=timezone.utc))]],source='fixture')
        delivery.publish_pending(source.settings)
    assert delivery.publish_pending(source.settings)['status']=='no_new_batches'
    remote=source.settings.data_root/'numeric/outbox'
    index=json.loads((remote/'index.json').read_text())
    assert len(index['bundles'])==2
    copied=[]
    fail_name=index['bundles'][1]['name']
    def copy(host,path,destination,timeout):
        name=Path(path).name;copied.append(name)
        if name==fail_name:raise TimeoutError('transfer interrupted')
        shutil.copyfile(remote/name,destination)
    monkeypatch.setattr(delivery,'_copy',copy)
    with pytest.raises(TimeoutError):delivery.pull_numeric_directory(target.settings,host='trusted-host',remote_dir='/outbox')
    assert target.latest('analyst_price_targets')['rows'][0]['last_month_avg_price_target']==10
    fail_name=None;copied.clear()
    result=delivery.pull_numeric_directory(target.settings,host='trusted-host',remote_dir='/outbox')
    assert result['new_bundles']==1 and index['bundles'][0]['name'] not in copied
    assert target.latest('analyst_price_targets')['rows'][0]['last_month_avg_price_target']==20
    assert delivery.pull_numeric_directory(target.settings,host='trusted-host',remote_dir='/outbox')['new_bundles']==0
    (target.settings.data_root/'delivery-status.json').unlink()
    copied.clear()
    result=delivery.pull_numeric_directory(target.settings,host='trusted-host',remote_dir='/outbox')
    assert result['new_bundles']==0 and len(result['already_present'])==2
    assert copied==['index.json']
    source.close();target.close()


def test_incremental_cursor_uses_whole_market_completed_batches(tmp_path):
    store=make_store(tmp_path,'store')
    row=dict(symbol='SPY',date=date(2026,9,4),open=100,high=100,low=100,close=100,adjusted_close=100,volume=1)
    store.ingest('us_eod_daily',[[row]],source='fmp',details={'endpoint':'eod-bulk'})
    store.ingest('us_eod_daily',[[dict(row,date=date(2026,9,7))]],source='fmp',details={'endpoint':'historical-price-eod/full'})
    assert pipeline.next_eod_date(store.settings,date(2026,9,7))==date(2026,9,5)
    store.close()


def test_replica_has_no_collection_and_failed_delivery_stays_visible(tmp_path,monkeypatch):
    settings=MarketSettings(f"sqlite:///{tmp_path/'unused.db'}",tmp_path/'data')
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_ROLE','replica')
    with pytest.raises(ValueError,match='replica'):pipeline.run(settings,'daily')
    with pytest.raises(ValueError,match='replica'):pipeline.run(settings,'crypto')
    for name in ['NUMERIC','MI']:
        monkeypatch.delenv('INVESTMENT_STUDIO_MARKET_'+name+'_HOST',raising=False)
        monkeypatch.delenv('INVESTMENT_STUDIO_MARKET_'+name+'_REMOTE_DIR',raising=False)
    result=pipeline.run(settings,'sync')
    assert result['status']=='failed'
    assert [r['stage'] for r in result['stages']]==['numeric_catchup','mi_text_catchup']
    assert json.loads((settings.data_root/'pipeline-status.json').read_text())['sync']['status']=='failed'


def test_scheduled_definitions_only_collect_on_cloud_and_do_not_embed_secrets(tmp_path):
    path=Path(__file__).resolve().parents[3]/'infra/scripts/install_market_pipeline.py'
    spec=importlib.util.spec_from_file_location('market_pipeline_installer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    units=module.definitions('systemd','collector',tmp_path/'project',tmp_path/'external',tmp_path/'python',tmp_path/'logs')
    assert len(units)==18
    assert b'Asia/Shanghai' in units['investment-studio-market-daily.timer']
    assert b'Persistent=true' in units['investment-studio-market-sync.timer']
    assert b'15:30 Asia/Shanghai' in units['investment-studio-market-registered-prices-cn.timer']
    assert b'registered-prices --market hk' in units['investment-studio-market-registered-prices-hk.service']
    assert b'*-*-* 08:15 Asia/Shanghai' in units['investment-studio-market-crypto.timer']
    assert b'run_market_pipeline.sh" crypto' in units['investment-studio-market-crypto.service']
    plists=module.definitions('launchd','replica',tmp_path/'project',tmp_path/'external',tmp_path/'python',tmp_path/'logs')
    assert list(plists)==['com.orataba.investment-studio.market-sync.plist']
    item=plistlib.loads(next(iter(plists.values())))
    assert item['ProgramArguments'][-1]=='sync' and item['RunAtLoad']
    assert item['EnvironmentVariables']['INVESTMENT_STUDIO_MARKET_ROLE']=='replica'
    assert item['StartInterval']==3600 and item['StartCalendarInterval']=={'Hour':8,'Minute':20}


@pytest.mark.parametrize('clock, cutoff', [('2026-09-08T08:15:00+08:00', date(2026,9,7)),
                                        ('2026-09-06T08:15:00+08:00', date(2026,9,5))])
def test_crypto_pipeline_captures_completed_utc_days_and_publishes_without_listed_data(tmp_path,monkeypatch,clock,cutoff):
    settings=MarketSettings('sqlite://',tmp_path/'data')
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_ROLE','collector')
    captured=[]
    monkeypatch.setattr(pipeline,'collect',lambda _settings,**kwargs:captured.append(kwargs) or {'status':'ready'})
    monkeypatch.setattr(pipeline,'registered_instruments',lambda _settings:pytest.fail('crypto must not use the listed registry capture'))
    monkeypatch.setattr(pipeline,'publish_pending',lambda _settings:{'status':'no_new_batches'})
    result=pipeline.run(settings,'crypto',now=datetime.fromisoformat(clock))
    assert result['status']=='ready'
    assert len(captured)==1 and captured[0]['groups']==['market_series'] and captured[0]['symbols']==['BTCUSD']
    assert captured[0]['end']==cutoff and (cutoff-captured[0]['start']).days==7
    assert [r['stage'] for r in result['stages']]==['market_series','publish_numeric_delta']


def test_weekly_pipeline_maintains_migrated_index_membership_and_events(tmp_path,monkeypatch):
    settings=MarketSettings('sqlite://',tmp_path/'data')
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_ROLE','collector')
    collected=[]
    monkeypatch.setattr(pipeline,'collect',lambda _settings,**kwargs: collected.extend(kwargs['groups']) or {'status':'ready'})
    monkeypatch.setattr(pipeline,'publish_pending',lambda _settings:{'status':'no_new_batches'})
    result=pipeline.run(settings,'weekly',now=datetime(2026,9,12,3,tzinfo=timezone.utc))
    assert result['status']=='ready'
    assert collected.count('indexes')==1
    assert next(r for r in result['stages'] if r['stage']=='indexes')['status']=='ready'


def test_market_specific_closing_dates_and_safe_http_error():
    now=datetime.fromisoformat('2026-09-07T17:00:00+08:00')
    assert closed_symbol_date('600519.SS',now)==date(2026,9,7)
    assert closed_symbol_date('0700.HK',now)==date(2026,9,7)
    assert closed_symbol_date('SPY',now)==date(2026,9,4)
    before=datetime.fromisoformat('2026-09-07T15:20:00+08:00')
    assert closed_symbol_date('600519.SS',before)==date(2026,9,4)
    class HTTPStatusError(Exception):
        response=type('Response',(),{'status_code':403})()
    result=failure_summary(HTTPStatusError('https://provider.example?apikey=SECRET&token=SECRET'))
    assert result=={'error_type':'HTTPStatusError','error':'HTTPStatusError: HTTP 403','http_status':403}


def test_authenticated_sftp_inbox_ignores_partial_and_records_only_success(tmp_path,monkeypatch):
    from studio_market.text.store import TextStore
    settings=MarketSettings('sqlite://',tmp_path/'data')
    inbox=tmp_path/'inbox';inbox.mkdir()
    checksum='a'*64;name='mi-text-'+checksum+'.zip'
    (inbox/(name+'.partial')).write_bytes(b'in flight')
    calls=[]
    monkeypatch.setattr(TextStore,'import_bundle',lambda self,path,expected_sha256: calls.append((path.name,expected_sha256)) or {'imported':1})
    assert delivery.import_text_directory(settings,directory=inbox)['new_bundles']==0
    (inbox/(name+'.sha256')).write_text(checksum+'  '+name+'\n')
    (inbox/name).write_bytes(b'complete')
    assert delivery.import_text_directory(settings,directory=inbox)['new_bundles']==1
    assert calls==[(name,checksum)]
    assert delivery.import_text_directory(settings,directory=inbox)['new_bundles']==0


def test_daily_price_revisions_continue_when_incremental_prices_fail(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from studio_market.numeric import registered
    settings=MarketSettings('sqlite://',tmp_path/'data')
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_ROLE','collector')
    steps=[]
    def broken(*args):raise ValueError('bad EOD payload')
    collector=SimpleNamespace(actions=lambda *args:None,eod=broken,
        reconcile_price_revisions=lambda end: steps.append('revisions') or {'status':'ready'},
        store=SimpleNamespace(close=lambda:None),_client=None)
    monkeypatch.setattr(pipeline,'Collector',lambda settings:collector)
    monkeypatch.setattr(pipeline,'next_eod_date',lambda settings,end:date(2026,9,1))
    monkeypatch.setattr(pipeline,'registered_instruments',lambda settings:[{'symbol':'SPY','instrument_type':'etf'}])
    monkeypatch.setattr(registered,'refresh_registered',lambda *args,**kwargs:{'status':'ready'})
    monkeypatch.setattr(pipeline,'collect',lambda settings,**kwargs:steps.extend(kwargs['groups']) or {'status':'ready'})
    monkeypatch.setattr(pipeline,'publish_pending',lambda settings:steps.append('publish') or {'status':'ready'})
    result=pipeline.run(settings,'daily',now=datetime(2026,9,8,tzinfo=timezone.utc))
    assert result['status']=='failed'
    assert steps[0]=='revisions' and 'raw_price_revisions' in steps and steps[-1]=='publish'
