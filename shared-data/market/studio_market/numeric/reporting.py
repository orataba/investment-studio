"""Deterministic public-market tables and immutable numerical source references."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from math import isfinite

from studio_market.calendars import closed_market_sessions

from .store import NumericStore,cutoff_instant

PRICE_WINDOW_METHOD_VERSION = "closed_sessions/v1"

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
# These calendars belong to the declared benchmark identities. Unspecified
# OTC/futures sessions remain unverified rather than borrowing an equity clock.
MARKET_CALENDARS = {**{symbol: "XNYS" for family, _, symbol, _ in MARKETS if family == "us_eod_daily"},
                    "000985.CSI": "XSHG", "HSCI.HI": "XHKG", "BTCUSD": "24/7"}


def select_report_price_window(rows, *, report_kind, period_start, period_end, cutoff, calendar_name):
    """Select confirmed closed sessions, never the last two arbitrary quotes."""
    if report_kind not in {"daily", "weekly"}:
        raise ValueError("report_kind must be daily or weekly")
    if not rows:
        return None, None, {"status": "insufficient_history", "latest_date": None}
    if not calendar_name:
        return None, None, {"status": "observation_schedule_unverified", "latest_date": rows[0]["date"]}
    end = min(period_end, cutoff.date())
    sessions = closed_market_sessions(calendar_name, period_start-timedelta(days=35), end, cutoff)
    if sessions is None:
        return None, None, {"status": "observation_schedule_unverified", "calendar": calendar_name}
    if report_kind == "daily":
        required = sessions[-2:]
    else:
        current = [day for day in sessions if day >= period_start]
        previous = [day for day in sessions if day < period_start]
        required = [previous[-1], *current] if previous and current else []
    if len(required) < 2:
        return None, None, {"status": "insufficient_history", "latest_date": rows[0]["date"]}
    by_date = {str(row["date"]): row for row in rows}
    missing = [day.isoformat() for day in required if day.isoformat() not in by_date]
    if missing:
        return None, None, {"status": "missing_expected_observations", "missing_dates": missing,
                            "calendar": calendar_name, "latest_date": rows[0]["date"]}
    base, final = by_date[required[0].isoformat()], by_date[required[-1].isoformat()]
    if any(isinstance(row.get("close"), bool) or not isinstance(row.get("close"), (int, float))
           or not isfinite(row["close"]) or row["close"] <= 0 for row in (base, final)):
        return None, None, {"status": "missing_comparable_prices"}
    return base, final, {"status": "available", "calendar": calendar_name,
                         "effective_date": final["date"], "observed_at": final["observed_at"]}


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
        base,final,state=select_report_price_window(rows,report_kind=report_kind,period_start=period_start,
            period_end=period_end,cutoff=cutoff,calendar_name=MARKET_CALENDARS.get(symbol))
        coverage.append({"symbol":symbol,"label":label,**state})
        if base is None or final is None:
            continue
        field="close"
        first=base[field];last=final[field]
        ids=[base["source_id"],final["source_id"]]
        market_rows.append({"symbol":symbol,"label":label,"start_date":base["date"],"end_date":final["date"],"start_close":first,"end_close":last,"return_pct":(last/first-1)*100,"price_field":field,"return_basis":"split_adjusted_price" if dataset=="us_eod_daily" else "unadjusted_price","source_ids":ids})
        sources.extend([base,final])
    rows=store.query("macro_series",symbols=[r[0] for r in MACRO],start=start.isoformat(),end=end.isoformat(),as_of=cutoff,limit=100000)["rows"]
    for symbol,label in MACRO:
        row=next((r for r in rows if r["symbol"]==symbol),None)
        if not row:coverage.append({"symbol":symbol,"label":label,"status":"unavailable"});continue
        macro_rows.append({"symbol":symbol,"label":label,"date":row["date"],"value":row["value"],"unit":row.get("unit"),"source_ids":[row["source_id"]]});sources.append(row)
    return {"price_window_method_version":PRICE_WINDOW_METHOD_VERSION,"status":"ready" if market_rows else "insufficient_data","cutoff":cutoff.isoformat(),"period_start":period_start.isoformat(),"period_end":period_end.isoformat(),"market_rows":market_rows,"macro_rows":macro_rows,"sources":list({r["source_id"]:r for r in sources}.values()),"coverage":coverage}


def read_numeric_source(settings,source_id: str)->dict:
    store=NumericStore(settings)
    try:return store.read_source(source_id)
    finally:store.close()
