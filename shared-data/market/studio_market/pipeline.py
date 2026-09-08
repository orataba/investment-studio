"""Small scheduled entrypoints; acquisition has one configured Studio owner."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func,select,text
from .config import MarketSettings
from .numeric.collect import Collector,collect,closed_us_date,symbol_market,failure_summary
from .numeric.delivery import publish_pending,pull_numeric_directory,pull_text_directory,import_text_directory,_atomic_json,file_lock
from .numeric.schema import batches,files
from .numeric.store import NumericStore,serializable


def registered_instruments(settings):
    """Read identities only; private positions and NAV never enter shared bundles."""
    store=NumericStore(settings)
    sql="""SELECT DISTINCT i.instrument_type, substring(k.identifier_value from 5) AS symbol
      FROM instrument_data.instrument i JOIN instrument_data.instrument_identifier k USING(instrument_id)
      WHERE k.identifier_type='provider_symbol' AND k.identifier_value LIKE 'fmp:%'
      AND i.instrument_type IN ('equity','etf','public_fund','index')
      ORDER BY symbol"""
    try:
        with store.engine.connect() as conn:return [dict(row) for row in conn.execute(text(sql)).mappings()]
    finally:store.close()


def next_eod_date(settings,end):
    store=NumericStore(settings)
    query=select(func.max(files.c.max_date)).join(batches,batches.c.id==files.c.batch_id).where(batches.c.dataset=='us_eod_daily',batches.c.status=='ready',(batches.c.source=='imported_market_archive') | (batches.c.details['endpoint'].as_string()=='eod-bulk'))
    try:
        with store.engine.connect() as conn:value=conn.execute(query).scalar_one()
    finally:store.close()
    return date.fromisoformat(value)+timedelta(days=1) if value else end-timedelta(days=7)


def run(settings,action: str,*,now: datetime|None=None,market: str='all')->dict:
    settings.data_root.mkdir(parents=True,exist_ok=True)
    # Long bulk acquisition must not block a market's close or text delivery.
    lock_name='collection' if action in {'daily','weekly'} else action+('-'+market if action=='registered-prices' else '')
    try:
        with file_lock(settings.data_root/('pipeline-'+lock_name+'.lock'),blocking=False):
            return _run(settings,action,now=now,market=market)
    except BlockingIOError:return {'action':action,'status':'already_running'}


def _run(settings,action: str,*,now: datetime|None=None,market: str='all')->dict:
    role=os.environ.get('INVESTMENT_STUDIO_MARKET_ROLE','')
    if role not in {'collector','replica'}:raise ValueError('Configure INVESTMENT_STUDIO_MARKET_ROLE as collector or replica')
    if action in {'daily','weekly','publish','registered-prices'} and role!='collector':raise ValueError('This Studio installation is a replica; public acquisition and publication run on the collector')
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Pipeline now must include a timezone')
    end=now.astimezone(ZoneInfo('Asia/Shanghai')).date();results=[]
    def stage(name,function):
        print(json.dumps({'stage':name,'status':'running'},ensure_ascii=False),flush=True)
        try:
            result=function()
            failure=isinstance(result,dict) and result.get('status') in {'failed','partial'}
            item={'stage':name,'status':'failed' if failure else 'ready','result':result}
        except Exception as exc:
            item={'stage':name,'status':'failed',**failure_summary(exc)}
        results.append(item)
        print(json.dumps(serializable(item),ensure_ascii=False),flush=True)
        return item
    if action in {'daily','weekly'}:
        if action=='daily':
            prices=Collector(settings)
            try:
                stage('corporate_actions',lambda:prices.actions(end-timedelta(days=7),end,None))
                begin=next_eod_date(settings,closed_us_date(now))
                if begin<=closed_us_date(now):
                    stage('us_eod_incremental',lambda:prices.eod(begin,closed_us_date(now),None))
                else:
                    results.append({'stage':'us_eod_incremental','status':'current','last_complete_session':(begin-timedelta(days=1)).isoformat()})
                stage('corporate_action_price_revisions',lambda:prices.reconcile_price_revisions(closed_us_date(now)))
            finally:
                prices.store.close()
                if prices._client:prices._client.close()
            for group in ['analyst','financials','macro','market_series','etf','events']:
                stage(group,lambda group=group:collect(settings,groups=[group],start=end-timedelta(days=7),end=end))
            registry=stage('registered_identities',lambda:registered_instruments(settings))
            if registry['status']=='ready':
                instruments=registry['result']
                from .numeric.registered import refresh_registered
                reference=stage('registered_reference',lambda:refresh_registered(settings,instruments=instruments))
                # Raw captures consult durable revision receipts, including an
                # earlier failed full rebuild or an action becoming effective.
                symbols=[r['symbol'] for r in instruments]
                if symbols:stage('registered_raw_price_revisions',lambda:collect(settings,groups=['raw_price_revisions'],symbols=symbols,start=end-timedelta(days=7),end=end))
        else:
            for group in ['reference','profiles','indexes']:
                stage(group,lambda group=group:collect(settings,groups=[group],start=end-timedelta(days=7),end=end))
            stage('etf_disclosures',lambda:collect(settings,groups=['etf_history'],start=end-timedelta(days=400),end=end))
            stage('official_etf_disclosures',lambda:collect(settings,groups=['official_etf'],start=end-timedelta(days=400),end=end))
            stage('cn_futures_daily_facts',lambda:collect(settings,groups=['cn_futures'],start=end-timedelta(days=7),end=end))
        # Ready batches from successful sources remain useful when another source fails.
        stage('publish_numeric_delta',lambda:publish_pending(settings))
    elif action=='publish':stage('publish_numeric_delta',lambda:publish_pending(settings))
    elif action=='registered-prices':
        registry=stage('registered_identities',lambda:registered_instruments(settings))
        if registry['status']=='ready':
            symbols=[row['symbol'] for row in registry['result'] if market=='all' or symbol_market(row['symbol'])==market]
            if symbols:stage('registered_raw_prices',lambda:collect(settings,groups=['raw_eod'],symbols=symbols,start=end-timedelta(days=7),end=end))
        stage('publish_numeric_delta',lambda:publish_pending(settings))
    elif action=='sync':
        def catchup(prefix,pull):
            host=os.environ.get('INVESTMENT_STUDIO_MARKET_'+prefix+'_HOST')
            directory=os.environ.get('INVESTMENT_STUDIO_MARKET_'+prefix+'_REMOTE_DIR')
            if not host or not directory:return {'status':'failed','error_type':'ConfigurationError','required_configuration':['INVESTMENT_STUDIO_MARKET_'+prefix+'_HOST','INVESTMENT_STUDIO_MARKET_'+prefix+'_REMOTE_DIR']}
            return pull(settings,host=host,remote_dir=directory)
        if role=='replica':stage('numeric_catchup',lambda:catchup('NUMERIC',pull_numeric_directory))
        inbox=os.environ.get('INVESTMENT_STUDIO_MARKET_MI_INBOX_DIR')
        if inbox:
            if os.environ.get('INVESTMENT_STUDIO_MARKET_MI_HOST'):
                raise ValueError('Choose one MI transport: INBOX_DIR or HOST/REMOTE_DIR')
            stage('mi_text_catchup',lambda:import_text_directory(settings,directory=inbox))
        else:stage('mi_text_catchup',lambda:catchup('MI',pull_text_directory))
    else:raise ValueError('Unknown pipeline action')
    result={'action':action,'market':market if action=='registered-prices' else None,'role':role,'started_at':now.isoformat(),'finished_at':datetime.now(timezone.utc).isoformat(),'status':'failed' if any(r['status']=='failed' for r in results) else 'ready','stages':results}
    path=settings.data_root/'pipeline-status.json'
    with file_lock(settings.data_root/'pipeline-status.lock'):
        history=json.loads(path.read_text()) if path.exists() else {}
        history[action+('-'+market if action=='registered-prices' else '')]=result;_atomic_json(path,history)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(prog='studio-market pipeline')
    parser.add_argument('action',choices=['daily','weekly','registered-prices','publish','sync','status'])
    parser.add_argument('--market',choices=['cn','hk','us','eu','all'],default='all')
    args=parser.parse_args(argv);settings=MarketSettings.from_environment()
    if args.action=='status':
        path=settings.data_root/'pipeline-status.json';result=json.loads(path.read_text()) if path.exists() else {'status':'not_run'}
    else:result=run(settings,args.action,market=args.market)
    print(json.dumps(serializable(result),ensure_ascii=False,indent=2))
    return 1 if result.get('status')=='failed' else 0


if __name__=='__main__':raise SystemExit(main())
