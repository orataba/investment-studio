"""Independent, source-specific daily/low-frequency acquisition for Studio."""
from __future__ import annotations

import csv
import io
import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

from studio_market.config import MarketSettings
from .raw import archive_response
from .store import NumericStore
from .price_revisions import history_start, pending_revisions
from .providers import analyst, directory, etf, events, financials, indexes, macro, market_series, profiles, ratings, treasury, us_market
from .providers.fmp import FmpClient, FmpHttpError, FmpResponseError, FmpTransportError
from .providers.cn_futures import CnFuturesError
from .providers.http_client import get_public_bytes,PublicHttpError

UTC=timezone.utc
DEFAULT_GROUPS=("reference","actions","eod","analyst","financials","profiles","etf","events","indexes","macro","market_series")
MACRO_SERIES = ("CBOE_VIX", *(item["series_id"] for item in macro.FRED_CONTEXT_SERIES),
                "US_TREASURY_NOMINAL", "US_TREASURY_REAL")


def failure_summary(exc):
    """Exception URLs and response bodies may contain credentials; never log them."""
    kind=type(exc).__name__
    status=getattr(exc,'status',None) or getattr(exc,'code',None) or getattr(getattr(exc,'response',None),'status_code',None)
    result={'error_type':kind,'error':kind}
    if isinstance(status,int):result.update(http_status=status,error=f'{kind}: HTTP {status}')
    if isinstance(exc,(FmpResponseError,FmpTransportError,PublicHttpError,CnFuturesError)):result['error']=str(exc)
    frames = traceback.extract_tb(exc.__traceback__)
    if frames:
        frame = frames[-1]
        result['error_location'] = f'{Path(frame.filename).name}:{frame.lineno} in {frame.name}'
    return result


def symbol_market(symbol):
    name=symbol.upper()
    if name.endswith('.HK') or name in {'^HSI','^HSCE','^HSTECH'}:return 'hk'
    if name.endswith(('.SS','.SZ','.SH','.BJ')):return 'cn'
    if name.endswith(('.L','.DE','.PA','.AS','.MI','.SW')):return 'eu'
    if '.' not in name or name.endswith(('.A','.B')):return 'us'
    raise ValueError('No configured closing-time contract for this FMP symbol')


def closed_symbol_date(symbol,now):
    market=symbol_market(symbol)
    if market=='us':return closed_us_date(now)
    zone,hour,minute={'cn':('Asia/Shanghai',15,30),'hk':('Asia/Hong_Kong',17,0),'eu':('Europe/Paris',18,0)}[market]
    if symbol.upper().endswith('.L'):zone,hour,minute='Europe/London',17,0
    local=now.astimezone(ZoneInfo(zone))
    day=local.date() if local.time()>=time(hour,minute) else local.date()-timedelta(days=1)
    while day.weekday()>4:day-=timedelta(days=1)
    return day


def closed_us_date(now: datetime) -> date:
    eastern=now.astimezone(ZoneInfo("America/New_York"))
    day=eastern.date() if eastern.time()>=time(17) else eastern.date()-timedelta(days=1)
    while day.weekday()>4: day-=timedelta(days=1)
    return day


class Collector:
    def __init__(self,settings: MarketSettings,*,store=None,client=None):
        self.settings=settings
        self.store=store or NumericStore(settings)
        self._client=client
        self.results=[]

    @property
    def fmp(self):
        if self._client is None:self._client=FmpClient(self.settings.read_secret("fmp_api_key"),timeout_seconds=90)
        return self._client

    def archive(self,response,provider="fmp"):
        sha,ref=archive_response(self.settings,provider,response.body)
        return dict(raw_sha256=sha,collected_at=response.received_at),ref

    def publish(self,name,rows,response,ref,**details):
        for row in rows: row["raw_ref"]=ref
        result=self.store.ingest(name,(rows[i:i+65536] for i in range(0,len(rows),65536)),source=details.pop("provider","fmp"),observed_at=response.received_at,details={"endpoint":response.endpoint,"parameters":response.params,"raw_ref":ref,"observed_at":response.received_at,"response_empty":getattr(response,"payload",None)==[],**details})
        self.results.append(result)
        return result

    def universe(self,companies=False):
        rows=self.store.latest("security_directory",limit=100000)["rows"]
        if companies: rows=[r for r in rows if not r.get("is_etf") and not r.get("is_fund")]
        symbols={r["symbol"] for r in rows}
        if not companies:
            # FMP's delisted endpoint is global, unlike the US exchange screener.
            # Keep US delisted listings for survivorship coverage without routing
            # overseas corporate actions through the US EOD collector.
            us_exchanges = {"NASDAQ", "NYSE", "AMEX", "CBOE", "OTC", "PNK"}
            symbols.update(r["symbol"] for r in self.store.latest("delisted_securities",limit=100000)["rows"]
                           if str(r.get("exchange") or "").upper() in us_exchanges)
            symbols.update(r["symbol"] for r in self.store.latest("etf_info",limit=100000)["rows"])
        if not symbols:raise ValueError("Populate the shared security directory first")
        return symbols

    def reference(self,start,end,symbols):
        for exchange in ("NASDAQ","NYSE","AMEX","CBOE"):
            seen=set()
            for page in range(100):
                response=self.fmp.get_json("company-screener",{"exchange":exchange,"includeAllShareClasses":"true","limit":10000,"page":page})
                clock,ref=self.archive(response)
                if clock["raw_sha256"] in seen:raise ValueError("Security directory repeated a page")
                seen.add(clock["raw_sha256"])
                rows=directory.normalize_security_directory(response.payload,**clock)
                self.publish("security_directory",rows,response,ref)
                if len(response.payload)<10000:break
            else:raise ValueError("Security directory exceeded provider pagination")
        response=self.fmp.get_json("symbol-change");clock,ref=self.archive(response)
        self.publish("symbol_changes",directory.normalize_symbol_changes(response.payload,**clock),response,ref)
        seen=set()
        for page in range(1000):
            response=self.fmp.get_json("delisted-companies",{"page":page,"limit":100});clock,ref=self.archive(response)
            if clock["raw_sha256"] in seen:raise ValueError("Delisted directory repeated a page")
            seen.add(clock["raw_sha256"])
            rows=directory.normalize_delisted_securities(response.payload,**clock)
            self.publish("delisted_securities",rows,response,ref)
            if len(response.payload)<100:break
        else:raise ValueError("Delisted directory exceeded provider pagination")

    def eod(self,start,end,symbols):
        end=min(end,closed_us_date(datetime.now(UTC)))
        if symbols:
            self.symbol_prices(symbols,start,end)
            return
        allowed=self.universe()
        # Provider SPY daily history supplies actual sessions, including holidays.
        response=self.fmp.get_json("historical-price-eod/full",{"symbol":"SPY","from":start.isoformat(),"to":end.isoformat()})
        _,calendar_ref=self.archive(response)
        if not isinstance(response.payload,list):raise ValueError("Trading-session response is not a list")
        days=sorted({date.fromisoformat(str(row["date"])[:10]) for row in response.payload if row.get("date")})
        for day in days:
            if not start<=day<=end:continue
            response=self.fmp.get_bulk_bytes("eod-bulk",{"date":day.isoformat()})
            clock,ref=self.archive(response)
            rows=us_market.normalize_us_eod_rows(response.body,allowed_symbols=allowed,expected_date=day,**clock)
            if not rows:raise ValueError(f"EOD bulk returned no usable rows for observed session {day}")
            self.publish("us_eod_daily",rows,response,ref,trading_sessions_raw_ref=calendar_ref)
        coverage=json.loads((Path(__file__).parent/"providers/us_etf_coverage.json").read_text())
        direct=[item["symbol"] for item in coverage["symbols"] if item["symbol"] not in {r["symbol"] for r in self.store.latest("security_directory",limit=100000)["rows"]}]
        if direct:self.symbol_prices(direct,start,end)
    def reconcile_price_revisions(self, end, *, symbols=None, raw=False):
        targets = symbols if symbols is not None else sorted(self.universe())
        dataset = 'raw_eod_daily' if raw else 'us_eod_daily'
        requests = pending_revisions(self.store, dataset=dataset, symbols=targets, end=end)
        results = []
        for symbol, request_id in sorted(requests.items()):
            try:
                stop = min(end, closed_symbol_date(symbol, datetime.now(UTC))) if raw else end
                due = pending_revisions(self.store, dataset=dataset, symbols=[symbol], end=stop) if stop < end else {symbol:request_id}
                if not due:continue
                self.symbol_prices([symbol], history_start(self.store, dataset, symbol), stop, raw=raw, revision_requests=due)
                results.append({'symbol':symbol,'status':'ready'})
            except Exception as exc:
                results.append({'symbol':symbol,'status':'failed',**failure_summary(exc)})
        return {'status':'failed' if any(r['status']=='failed' for r in results) else 'ready','price_revisions':results}

    def price_revisions(self, start, end, symbols):
        return self.reconcile_price_revisions(min(end, closed_us_date(datetime.now(UTC))), symbols=symbols)

    def raw_price_revisions(self, start, end, symbols):
        if not symbols:raise ValueError('Raw price revisions require explicit registered symbols')
        return self.reconcile_price_revisions(end, symbols=symbols, raw=True)

    def raw_eod(self,start,end,symbols):
        if not symbols: raise ValueError("raw_eod requires explicit registered symbols")
        results = []
        for symbol in symbols:
            try:
                stop=min(end,closed_symbol_date(symbol,datetime.now(UTC)))
                if stop < start:continue
                latest = self.store.latest('raw_eod_daily',symbols=[symbol],limit=1)['rows']
                # Retain an observed overlap and fill every missed close after
                # downtime; a rolling seven-day window alone can leave a gap.
                begin = min(start,date.fromisoformat(latest[0]['date'])) if latest else start
                self.symbol_prices([symbol],begin,stop,raw=True)
                requests = pending_revisions(self.store, dataset='raw_eod_daily', symbols=[symbol], end=stop)
                if requests:
                    self.symbol_prices([symbol],history_start(self.store, "raw_eod_daily", symbol),stop,raw=True,revision_requests=requests)
                results.append({'symbol':symbol,'status':'ready'})
            except Exception as exc:
                results.append({'symbol':symbol,'status':'failed',**failure_summary(exc)})
        return {'status':'failed' if any(r['status']=='failed' for r in results) else 'ready','symbols':results}

    def symbol_prices(self,symbols,start,end,raw=False,revision_requests=None):
        for symbol in symbols:
            cursor=start
            total_rows = 0
            while cursor<=end:
                stop=min(cursor+timedelta(days=1459),end)
                params={"symbol":symbol,"from":cursor.isoformat(),"to":stop.isoformat()}
                full=self.fmp.get_json("historical-price-eod/non-split-adjusted" if raw else "historical-price-eod/full",params)
                adjusted=self.fmp.get_json("historical-price-eod/dividend-adjusted",params)
                clock,ref=self.archive(full);_,adjusted_ref=self.archive(adjusted)
                clock["collected_at"]=max(full.received_at,adjusted.received_at)
                # FMP's non-split-adjusted endpoint uses adj* wire names for RAW prices.
                payload=full.payload
                if raw:
                    payload=[{**row,**{field:row.get("adj"+field.title()) for field in ("open","high","low","close")}} for row in full.payload]
                rows=us_market.normalize_us_eod_symbol_rows(payload,adjusted.payload,expected_symbol=symbol,start_date=cursor,end_date=stop,**clock)
                total_rows += len(rows)
                publication = {}
                if raw or revision_requests:
                    prior={row['date']:row for row in self.store.query('raw_eod_daily' if raw else 'us_eod_daily',symbols=[symbol],start=cursor.isoformat(),end=stop.isoformat(),limit=100000)['rows']}
                    if revision_requests and set(prior) - {row['date'].isoformat() for row in rows}:
                        raise ValueError('Full price revision response omits retained observation dates')
                if raw:
                    if any(row['date'].isoformat() in prior and prior[row['date'].isoformat()].get('adjusted_close') is not None and row.get('adjusted_close')!=prior[row['date'].isoformat()]['adjusted_close'] for row in rows):
                        if start > date(2000,1,3):
                            publication['price_revision_requests'] = [{'symbol':symbol, 'effective_date':max(row['date'] for row in rows).isoformat()}]
                adjusted_by_date={str(r.get("date"))[:10]:r for r in adjusted.payload}
                for row in rows:
                    row["adjusted_raw_ref"]=adjusted_ref
                    original=adjusted_by_date[row["date"].isoformat()]
                    for field in ("open","high","low"):
                        value=original.get("adj"+field.title())
                        row["adjusted_"+field]=float(value) if value is not None else None
                if revision_requests and symbol in revision_requests and stop == end:
                    if not total_rows:
                        raise ValueError('Full price revision capture contained no history')
                    publication['price_revision_completed'] = {symbol:revision_requests[symbol]}
                self.publish("raw_eod_daily" if raw else "us_eod_daily",rows,full,ref,adjusted_raw_ref=adjusted_ref,observed_at=clock["collected_at"],**publication)
                cursor=stop+timedelta(days=1)

    def actions(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe()
        begin=min(start,end-timedelta(days=365));stop=end+timedelta(days=365)
        for name,endpoint,normalizer,datefield in [("dividends","dividends-calendar",us_market.normalize_dividend_rows,"ex_date"),("stock_splits","splits-calendar",us_market.normalize_split_rows,"event_date")]:
            previous=self.store.query(name,start=begin.isoformat(),end=stop.isoformat(),limit=100000)["rows"]
            fields=("dividend","adjusted_dividend") if name=="dividends" else ("numerator","denominator")
            prior={(r["symbol"],r[datefield]):tuple(r.get(k) for k in fields) for r in previous}
            for year in range(begin.year,stop.year+1):
                lo=max(begin,date(year,1,1));hi=min(stop,date(year,12,31))
                for page in range(250):
                    response=self.fmp.get_json(endpoint,{"from":lo.isoformat(),"to":hi.isoformat(),"page":page,"limit":1000})
                    clock,ref=self.archive(response)
                    rows=normalizer(response.payload,allowed_symbols=allowed,start_date=lo,end_date=hi,**clock)
                    changes=[]
                    for r in rows:
                        if prior.get((r["symbol"],r[datefield].isoformat()))!=tuple(r.get(k) for k in fields):
                            changes.append({'symbol':r['symbol'], 'effective_date':r[datefield].isoformat()})
                    self.publish(name,rows,response,ref,price_revision_requests=changes)
                    if len(response.payload)<1000:break
                else:raise ValueError("Corporate action pagination incomplete")

    def analyst(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe(companies=True)
        for period,years in [("annual",range(end.year,end.year+5)),("quarter",range(end.year,end.year+3))]:
            captures=[]
            for year in years:
                response=self.fmp.get_bulk_bytes("analyst-estimates-bulk",{"year":year,"period":period},min_interval_seconds=60.1)
                clock,ref=self.archive(response)
                rows=analyst.normalize_analyst_estimates(response.body,allowed_symbols=allowed,estimate_period=period,**clock)
                for row in rows:row["raw_ref"]=ref
                captures.append(rows)
            completed=response.received_at
            for rows in captures:
                for row in rows:row["snapshot_at"]=completed
            scopes=[{"symbol":symbol,"frequency":period,"snapshot_at":completed.isoformat()} for symbol in sorted(allowed)]
            self.results.append(self.store.ingest("analyst_estimates",captures,source="fmp",observed_at=completed,details={"endpoint":"analyst-estimates-bulk","snapshot_complete":True,"snapshot_scopes":scopes,"frequency":period}))
        for name,endpoint,normalizer in [("analyst_price_targets","price-target-summary-bulk",analyst.normalize_price_targets),("analyst_rating_consensus","upgrades-downgrades-consensus-bulk",analyst.normalize_rating_consensus),("fmp_quant_ratings","rating-bulk",ratings.normalize_quant_ratings_csv)]:
            response=self.fmp.get_bulk_bytes(endpoint);clock,ref=self.archive(response)
            self.publish(name,normalizer(response.body,allowed_symbols=allowed,**clock),response,ref)

    def profiles(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe()
        if symbols:
            for symbol in symbols:
                response=self.fmp.get_json("profile",{"symbol":symbol});clock,ref=self.archive(response)
                if not isinstance(response.payload,list):raise ValueError("Company profile response is not a list")
                # The same pure profile normalizer is used for bulk and direct symbols.
                keys=sorted({k for r in response.payload for k in r}|{"symbol","companyName","exchange","cik","isin","cusip"})
                output=io.StringIO();writer=csv.DictWriter(output,fieldnames=keys);writer.writeheader();writer.writerows(response.payload)
                rows,_=profiles.normalize_company_profiles(output.getvalue().encode(),allowed_symbols=allowed,**clock)
                self.publish("company_profiles",rows,response,ref)
            return
        for part in range(20):
            try:response=self.fmp.get_bulk_bytes("profile-bulk",{"part":part},min_interval_seconds=60.1)
            except FmpHttpError as exc:
                if exc.status==400 and part>0:break
                raise
            clock,ref=self.archive(response)
            rows,total=profiles.normalize_company_profiles(response.body,allowed_symbols=allowed,**clock)
            self.publish("company_profiles",rows,response,ref)
            if total==0:break
        else:raise ValueError("Company profiles exceeded provider pagination")

    def _financial_response(self,response,kind,allowed,bulk=False,**extra):
        clock,ref=self.archive(response)
        if bulk:
            statements,facts=financials.normalize_financial_csv(response.body,statement_type=kind,allowed_symbols=allowed,source_dataset=f"fmp_{response.endpoint}",**clock,**extra)
        else:
            statements,facts=financials.normalize_financial_payload(response.payload,statement_type=kind,allowed_symbols=allowed,source_dataset=f"fmp_{response.endpoint}",**clock)
        statements=[s for s in statements if s["period_end"]<=response.received_at.date() and abs(s["period_end"].year-s["fiscal_year"])<=1]
        versions={s["statement_content_sha256"]:s for s in statements}
        full_facts=[{**versions[f["statement_content_sha256"]],**f} for f in facts if f["statement_content_sha256"] in versions]
        self.publish("financial_statements",statements,response,ref)
        self.publish("financial_facts",full_facts,response,ref)

    def financials(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe()
        targets=set(symbols or [])
        if not targets:
            seen=set()
            for page in range(1000):
                response=self.fmp.get_json("latest-financial-statements",{"page":page,"limit":250});clock,ref=self.archive(response)
                if not isinstance(response.payload,list):raise ValueError("Latest financial statements response is not a list")
                if clock["raw_sha256"] in seen:raise ValueError("Financial discovery repeated a page")
                seen.add(clock["raw_sha256"])
                dates=[]
                for row in response.payload:
                    stamp=row.get("dateAdded") or row.get("filingDate") or row.get("acceptedDate")
                    if stamp:dates.append(date.fromisoformat(str(stamp)[:10]))
                    if row.get("symbol") in allowed and (not stamp or date.fromisoformat(str(stamp)[:10])>=start):targets.add(row["symbol"])
                if len(response.payload)<250 or (dates and max(dates)<start):break
            else:raise ValueError("Financial discovery pagination incomplete")
        for symbol in sorted(targets):
            for kind,(_,endpoint) in financials.STATEMENT_ENDPOINTS.items():
                for period,limit in [("annual",3),("quarter",8)]:
                    response=self.fmp.get_json(endpoint,{"symbol":symbol,"period":period,"limit":limit})
                    self._financial_response(response,kind,{symbol})

    def financial_history(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe()
        for year in range(start.year,end.year+1):
            for period in financials.FINANCIAL_PERIODS:
                for kind,(endpoint,_) in financials.STATEMENT_ENDPOINTS.items():
                    response=self.fmp.get_bulk_bytes(endpoint,{"year":year,"period":period})
                    self._financial_response(response,kind,allowed,bulk=True,expected_year=year,expected_period=period)

    def financial_details(self,start,end,symbols):
        if not symbols:raise ValueError("financial_details requires explicit symbols")
        for symbol in symbols:
            for period in ('annual','quarter'):
                response=self.fmp.get_json('financial-statement-full-as-reported',{'symbol':symbol,'period':period,'limit':200})
                clock,ref=self.archive(response)
                statements,facts=financials.normalize_as_reported_payload(response.payload,expected_symbol=symbol,start_date=start,end_date=end,**clock)
                versions={row['statement_content_sha256']:row for row in statements}
                full_facts=[{**versions[row['statement_content_sha256']],**row} for row in facts]
                self.publish('as_reported_statements',statements,response,ref)
                self.publish('as_reported_facts',full_facts,response,ref)
            seen=set()
            for page in range(1000):
                response=self.fmp.get_json('sec-filings-search/symbol',{'symbol':symbol,'from':start.isoformat(),'to':end.isoformat(),'page':page,'limit':100})
                clock,ref=self.archive(response)
                if clock['raw_sha256'] in seen:raise ValueError('SEC filings repeated a page')
                seen.add(clock['raw_sha256'])
                rows=financials.normalize_sec_filing_payload(response.payload,expected_symbol=symbol,start_date=start,end_date=end,**clock)
                self.publish('sec_filings',rows,response,ref)
                if len(response.payload)<100:break
            else:raise ValueError('SEC filings exceeded provider pagination')

    def rating_history(self,start,end,symbols):
        if not symbols:raise ValueError('rating_history requires explicit symbols')
        for symbol in symbols:
            for name,endpoint,normalizer in [('analyst_grade_events','grades',ratings.normalize_grade_events),('analyst_grade_snapshots','grades-historical',ratings.normalize_grade_snapshots)]:
                response=self.fmp.get_json(endpoint,{'symbol':symbol});clock,ref=self.archive(response)
                self.publish(name,normalizer(response.payload,requested_symbol=symbol,**clock),response,ref)

    def etf(self,start,end,symbols):
        selected=symbols or [r["symbol"] for r in json.loads((Path(__file__).parent/"providers/us_etf_coverage.json").read_text())["symbols"]]
        for symbol in selected:
            for name,endpoint,normalizer in [("etf_info","etf/info",etf.normalize_etf_info),("etf_holdings","etf/holdings",etf.normalize_current_holdings)]:
                response=self.fmp.get_json(endpoint,{"symbol":symbol});clock,ref=self.archive(response)
                scopes=[{"symbol":symbol,"frequency":"","snapshot_at":response.received_at.isoformat()}] if name=="etf_holdings" else []
                self.publish(name,normalizer(response.payload,requested_symbol=symbol,**clock),response,ref,snapshot_complete=True,snapshot_scopes=scopes)

    def etf_history(self,start,end,symbols):
        selected=symbols or [r["symbol"] for r in json.loads((Path(__file__).parent/"providers/us_etf_coverage.json").read_text())["symbols"]]
        for symbol in selected:
            response=self.fmp.get_json("funds/disclosure-dates",{"symbol":symbol});self.archive(response)
            if not isinstance(response.payload,list):raise ValueError("ETF disclosure dates response is not a list")
            for item in response.payload:
                year=int(item["year"]);quarter=int(item["quarter"])
                if not start.year<=year<=end.year:continue
                response=self.fmp.get_json("funds/disclosure",{"symbol":symbol,"year":year,"quarter":quarter});clock,ref=self.archive(response)
                rows=etf.normalize_etf_disclosure(response.payload,requested_symbol=symbol,**clock)
                # FMP has returned another fund's pre-inception history for IBIT.
                if symbol=="IBIT":rows=[r for r in rows if r["report_date"]>=date(2024,1,11)]
                self.publish("etf_disclosures",rows,response,ref)

    def events(self,start,end,symbols):
        allowed=set(symbols) if symbols else self.universe(companies=True)
        for year in range(start.year,end.year+1):
            response=self.fmp.get_bulk_bytes("earnings-surprises-bulk",{"year":year});clock,ref=self.archive(response)
            self.publish("earnings_surprises",events.normalize_earnings_surprises(response.body,allowed_symbols=allowed,**clock),response,ref)
        for name,endpoint,normalizer in [("insider_trades","insider-trading/latest",events.normalize_insider_trades),("institutional_filings","institutional-ownership/latest",events.normalize_institutional_filings)]:
            seen=set()
            for page in range(100):
                response=self.fmp.get_json(endpoint,{"page":page,"limit":100});clock,ref=self.archive(response)
                if clock["raw_sha256"] in seen:raise ValueError("Event pagination repeated a page")
                seen.add(clock["raw_sha256"])
                options={"allowed_symbols":allowed} if name=="insider_trades" else {}
                rows=normalizer(response.payload,**clock,**options)
                self.publish(name,rows,response,ref)
                if len(response.payload)<100:break
                dates=[r.get("filing_date") for r in rows if r.get("filing_date")]
                if dates and max(dates)<start:break
            else:raise ValueError("Event pagination incomplete")

    def indexes(self,start,end,symbols):
        for spec in indexes.INDEX_SPECS:
            response=self.fmp.get_json(spec.current_endpoint);clock,ref=self.archive(response)
            members=indexes.normalize_current_membership(response.payload,endpoint=spec.current_endpoint)
            rows=[dict(index_id=spec.index_id,symbol=symbol,name=name,snapshot_date=response.received_at.date(),**clock) for symbol,name in members.items()]
            self.publish("index_membership_snapshots",rows,response,ref)
            response=self.fmp.get_json(spec.history_endpoint);clock,ref=self.archive(response)
            rows=indexes.normalize_index_events(response.payload,index_id=spec.index_id,source_dataset="fmp_historical_index_constituents",**clock)
            self.publish("index_constituent_events",rows,response,ref)

    def macro(self,start,end,symbols):
        if not symbols or 'CBOE_VIX' in symbols:
            response=get_public_bytes(macro.CBOE_VIX_URL);clock,ref=self.archive(response,"cboe")
            self.publish("macro_series",macro.normalize_cboe_vix(response.body,start_date=start,end_date=end,**clock),response,ref,provider="cboe")
        for spec in macro.FRED_CONTEXT_SERIES:
            if symbols and spec['series_id'] not in symbols:continue
            response=get_public_bytes(macro.FRED_CSV_URL,{"id":spec["source_series_id"],"cosd":start.isoformat(),"coed":end.isoformat()});clock,ref=self.archive(response,"fred")
            rows=macro.normalize_fred_series(response.body,source_series_id=spec["source_series_id"],series_id=spec["series_id"],unit=spec["unit"],start_date=start,end_date=end,source_dataset="fred_market_context",minimum=spec["minimum"],**clock)
            self.publish("macro_series",rows,response,ref,provider="fred",release_clock="capture_only")
        for selector,field_map,key in [("US_TREASURY_NOMINAL",treasury.NOMINAL_FIELDS,"daily_treasury_yield_curve"),("US_TREASURY_REAL",treasury.REAL_FIELDS,"daily_treasury_real_yield_curve")]:
            if symbols and selector not in symbols:continue
            for year in range(start.year,end.year+1):
                response=get_public_bytes(treasury.TREASURY_XML_URL,{"data":key,"field_tdr_date_value":year},timeout_seconds=60);clock,ref=self.archive(response,"us_treasury")
                self.publish("macro_series",treasury.normalize_treasury_xml(response.body,field_map=field_map,start_date=start,end_date=end,source_dataset=key,**clock),response,ref,provider="us_treasury")

    def market_series(self,start,end,symbols):
        source_issues=[]
        for series_id,provider_symbol in market_series.FMP_PROVIDER_SYMBOLS.items():
            if symbols and series_id not in symbols:continue
            stop=min(end,datetime.now(UTC).date()-timedelta(days=1) if series_id=="BTCUSD" else closed_us_date(datetime.now(UTC)))
            cursor=start
            while cursor<=stop:
                hi=min(stop,cursor+timedelta(days=1459))
                response=self.fmp.get_json("historical-price-eod/full",{"symbol":provider_symbol,"from":cursor.isoformat(),"to":hi.isoformat()});clock,ref=self.archive(response)
                rows,rejected=market_series.normalize_fmp_market_series(response.payload,series_id=series_id,start_date=cursor,end_date=hi,**clock)
                details={"validation":{"status":"partial","accepted_row_count":len(rows),"rejected_rows":rejected}} if rejected else {}
                batch=self.publish("market_series_daily",rows,response,ref,**details)
                if rejected:
                    source_issues.append({"series_id":series_id,"batch_id":batch["batch_id"],"raw_ref":ref,"rejected_rows":rejected})
                cursor=hi+timedelta(days=1)
        for series_id,code in market_series.HK_SECTOR_CODES.items():
            if symbols and series_id not in symbols:continue
            response=get_public_bytes(market_series.HSIL_CHART_URL.format(code=code));clock,ref=self.archive(response,"hang_seng_indexes")
            rows=market_series.normalize_hsil_close_series(response.body,series_id=series_id,code=code,start_date=start,end_date=end,**clock)
            self.publish("market_series_daily",rows,response,ref,provider="hang_seng_indexes")
        if source_issues:return {"status":"failed","source_issues":source_issues}

    def cn_futures(self,start,end,symbols):
        from .providers.cn_futures import GTJAFuturesClient,ENDPOINTS,load_cn_futures_coverage,_requests_for_endpoint,_extract_records,normalize_gtja_records,_dataset_catalog_row
        client=GTJAFuturesClient("https://vip.gtjaqh.com",self.settings.read_secret("gtja_access_key_id"),self.settings.read_secret("gtja_access_key_secret"))
        products,exchanges=load_cn_futures_coverage(Path(__file__).parent/"providers/cn_futures_coverage.json")
        if symbols:products=[p for p in products if p.product_id in symbols]
        specs=[s for s in ENDPOINTS if s.enabled and not any(word in s.dataset for word in ("viewpoint","notice","reminder"))]
        self.results.append(self.store.ingest("cn_futures_products",[[dict(product_id=p.product_id,exchange_id=p.exchange_id,product_name=p.name,active=p.active,catalog_source="studio_coverage") for p in products]],source="studio_coverage"))
        self.results.append(self.store.ingest("cn_futures_dataset_catalog",[[_dataset_catalog_row(s) for s in specs]],source="gtja"))
        day=start
        while day<=end:
            if day.weekday()<5:
                for spec in specs:
                    for params,context in _requests_for_endpoint(spec,products,exchanges,day):
                        response=client.post_json(spec.path,params);clock,ref=self.archive(response,"gtja_futures")
                        if not response.succeeded:raise ValueError(f"GTJA request failed: {spec.name}")
                        rows=normalize_gtja_records(spec,_extract_records(response.payload,spec.records_path),context=context,**clock)
                        self.publish("cn_futures_observations",rows,response,ref,provider="gtja_futures")
            day+=timedelta(days=1)


def collect(settings: MarketSettings, *, groups: list[str] | None=None, start: date | None=None,end: date | None=None,symbols: list[str] | None=None,progress=None) -> dict:
    end=end or datetime.now(UTC).date()
    start=start or end-timedelta(days=7)
    if start>end:raise ValueError("start must not follow end")
    collector=Collector(settings)
    started_at=datetime.now(UTC).isoformat()
    selected=groups or list(DEFAULT_GROUPS)
    allowed=set(DEFAULT_GROUPS)|{"financial_history","financial_details","rating_history","etf_history","cn_futures","tushare","raw_eod","official_etf","price_revisions","raw_price_revisions"}
    if set(selected)-allowed:raise ValueError("Unknown collection group")
    stages=[]
    try:
        jobs=[]
        for group in selected:
            if group in {"market_series", "macro"}:
                series=symbols or ([*market_series.FMP_PROVIDER_SYMBOLS,*market_series.HK_SECTOR_CODES]
                                  if group=="market_series" else MACRO_SERIES)
                jobs.extend((group,[symbol]) for symbol in series)
            else:jobs.append((group,symbols))
        if 'eod' in selected and 'price_revisions' not in selected and not symbols:
            jobs.append(('price_revisions', None))
        for group,job_symbols in jobs:
            identity={"group":group}
            if group in {"market_series", "macro"}:identity["series_id"]=job_symbols[0]
            if progress:progress({**identity,"status":"collecting"})
            try:
                outcome = None
                if group=="official_etf":
                    from .official_etf import collect_official_etf
                    outcome=collect_official_etf(collector,start,end,job_symbols)
                elif group=="tushare":
                    from .tushare import collect_tushare
                    collector.results.extend(collect_tushare(settings,collector.store,start,end,job_symbols))
                else:
                    outcome=getattr(collector,group)(start,end,job_symbols)
                if isinstance(outcome,dict) and outcome.get('status')=='failed':
                    stages.append({**identity,**outcome})
                    if progress:progress(stages[-1])
                    continue
                stage={**identity,"status":"ready"}
            except Exception as exc:
                stage={**identity,"status":"failed",**failure_summary(exc)}
            stages.append(stage)
            if progress:progress(stage)
    finally:
        if collector._client:collector._client.close()
        collector.store.close()
    result={"status":"failed" if any(s["status"]=="failed" for s in stages) else "ready","started_at":started_at,"finished_at":datetime.now(UTC).isoformat(),"stages":stages,"batches":collector.results}
    settings.data_root.mkdir(parents=True,exist_ok=True)
    target=settings.data_root/"collection-status.json"
    temporary=target.with_name(f".collection-status.{os.getpid()}.tmp")
    with temporary.open("w") as output:
        json.dump(result,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
    os.replace(temporary,target)
    return result
