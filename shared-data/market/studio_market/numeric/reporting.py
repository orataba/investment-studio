"""Deterministic public-market tables and immutable numerical source references."""
from __future__ import annotations
from datetime import date, datetime, timedelta

from .store import NumericStore,cutoff_instant

# Dataset key, provider series, display symbol, label.
MARKETS = [
    ("us_eod_daily", "SPY", "SPY", "标普500 ETF（SPY）"),
    ("us_eod_daily", "QQQ", "QQQ", "纳斯达克100 ETF（QQQ）"),
    ("us_eod_daily", "DIA", "DIA", "道琼斯 ETF（DIA）"),
    ("us_eod_daily", "IWM", "IWM", "美国小盘 ETF（IWM）"),
    ("us_eod_daily", "GLD", "GLD", "黄金 ETF（GLD）"),
    ("us_eod_daily", "TLT", "TLT", "美国长期国债 ETF（TLT）"),
    ("regime_market_daily", "datahub:index_daily:000985.CSI", "000985.CSI", "中证全指"),
    ("regime_market_daily", "hsil:HSCI.HI", "HSCI.HI", "恒生综合指数"),
    ("regime_market_daily", "fmp:raw:XAUUSD", "XAUUSD", "现货黄金"),
    ("regime_market_daily", "fmp:raw:BTCUSD", "BTCUSD", "比特币"),
    ("regime_market_daily", "fmp:raw:DX-Y.NYB", "DX-Y.NYB", "美元指数"),
]
MACRO=[("CBOE_VIX","VIX"),("US_TREASURY_2Y","美国2年国债收益率"),("US_TREASURY_10Y","美国10年国债收益率"),("US_TREASURY_REAL_10Y","美国10年实际收益率"),("US_HY_OAS","美国高收益债利差"),("US_IG_OAS","美国投资级债利差"),("US_SOFR","SOFR")]


def build_report_snapshot(settings,*,report_kind: str,period_start: date,period_end: date,cutoff: datetime)->dict:
    store=NumericStore(settings)
    try:
        return _build_report_snapshot(store,report_kind=report_kind,period_start=period_start,period_end=period_end,cutoff=cutoff)
    finally:
        store.close()


def _build_report_snapshot(store,*,report_kind,period_start,period_end,cutoff):
    cutoff=cutoff_instant(cutoff)
    if report_kind not in ("daily","weekly"):raise ValueError("report_kind must be daily or weekly")
    if period_start>period_end:raise ValueError("Invalid report period")
    market_rows=[];macro_rows=[];sources=[];coverage=[]
    end=min(period_end,cutoff.date())
    # This is a bounded report window, not a fabricated calendar or forward fill.
    start=period_start-timedelta(days=35)
    by_dataset={name:[symbol for family,symbol,_,_ in MARKETS if family==name] for name in {r[0] for r in MARKETS}}
    records={}
    for name,symbols in by_dataset.items():
        rows=store.query(name,symbols=symbols,start=start.isoformat(),end=end.isoformat(),as_of=cutoff,limit=100000)["rows"]
        for symbol in symbols:records[(name,symbol)]=[r for r in rows if r["symbol"]==symbol]
    for dataset,series,symbol,label in MARKETS:
        rows=records[(dataset,series)]
        if report_kind=="daily":
            pair=rows[:2]
            final=pair[0] if pair else None;base=pair[1] if len(pair)>1 else None
        else:
            final=next((r for r in rows if r["date"]>=period_start.isoformat()),None)
            base=next((r for r in rows if r["date"]<period_start.isoformat()),None)
        if not final or not base:
            coverage.append({"symbol":symbol,"label":label,"status":"insufficient_history","latest_date":rows[0]["date"] if rows else None});continue
        field="close"
        first=base.get(field);last=final.get(field)
        if first is None or last is None or first<=0:
            coverage.append({"symbol":symbol,"label":label,"status":"missing_comparable_prices"});continue
        ids=[base["source_id"],final["source_id"]]
        market_rows.append({"symbol":symbol,"label":label,"start_date":base["date"],"end_date":final["date"],"start_close":first,"end_close":last,"return_pct":(last/first-1)*100,"price_field":field,"return_basis":"split_adjusted_price" if dataset=="us_eod_daily" else "unadjusted_price","source_ids":ids})
        sources.extend([base,final]);coverage.append({"symbol":symbol,"label":label,"status":"available","effective_date":final["date"],"observed_at":final["observed_at"]})
    rows=store.query("macro_series",symbols=[r[0] for r in MACRO],start=start.isoformat(),end=end.isoformat(),as_of=cutoff,limit=100000)["rows"]
    for symbol,label in MACRO:
        row=next((r for r in rows if r["symbol"]==symbol),None)
        if not row:coverage.append({"symbol":symbol,"label":label,"status":"unavailable"});continue
        macro_rows.append({"symbol":symbol,"label":label,"date":row["date"],"value":row["value"],"unit":row.get("unit"),"source_ids":[row["source_id"]]});sources.append(row)
    return {"status":"ready" if market_rows else "insufficient_data","cutoff":cutoff.isoformat(),"period_start":period_start.isoformat(),"period_end":period_end.isoformat(),"market_rows":market_rows,"macro_rows":macro_rows,"sources":list({r["source_id"]:r for r in sources}.values()),"coverage":coverage}


def read_numeric_source(settings,source_id: str)->dict:
    store=NumericStore(settings)
    try:return store.read_source(source_id)
    finally:store.close()
