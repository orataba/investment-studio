from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone

from .raw import archive_response


def collect_tushare(settings,store,start,end,symbols=None):
    token=settings.read_secret("tushare_token")
    results=[]
    def request(api,params):
        body=json.dumps({"api_name":api,"token":token,"params":params,"fields":""}).encode()
        req=urllib.request.Request("https://api.tushare.pro",data=body,headers={"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req,timeout=60) as response:raw=response.read()
        observed=datetime.now(timezone.utc)
        sha,ref=archive_response(settings,"tushare",raw)
        parsed=json.loads(raw)
        if parsed.get("code")!=0:raise ValueError(f"Tushare {api} rejected request")
        data=parsed["data"]
        return [dict(zip(data["fields"],row)) for row in data["items"]],observed,sha,ref
    for status in ("L","D","P"):
        rows,observed,sha,ref=request("stock_basic",{"exchange":"","list_status":status})
        normalized=[{**r,"symbol":r["ts_code"],"collected_at":observed,"raw_sha256":sha,"raw_ref":ref} for r in rows]
        results.append(store.ingest("cn_security_directory",[normalized],source="tushare",observed_at=observed,details={"api":"stock_basic","list_status":status}))
    calendar,_,_,_=request("trade_cal",{"exchange":"SSE","start_date":start.strftime("%Y%m%d"),"end_date":end.strftime("%Y%m%d"),"is_open":"1"})
    for entry in calendar:
        day=entry["cal_date"]
        for api,name in (("daily","cn_equity_daily"),("daily_basic","cn_equity_daily_basic")):
            seen=set()
            for offset in range(0,600000,6000):
                rows,observed,sha,ref=request(api,{"trade_date":day,"limit":6000,"offset":offset})
                if sha in seen:raise ValueError("Tushare pagination repeated a page")
                seen.add(sha)
                normalized=[]
                for row in rows:
                    if symbols and row["ts_code"] not in symbols:continue
                    normalized.append({**row,"symbol":row["ts_code"],"date":datetime.strptime(row["trade_date"],"%Y%m%d").date(),"ohlc_adjustment":"unadjusted","volume_unit":"100_shares","amount_unit":"thousand_CNY","collected_at":observed,"raw_sha256":sha,"raw_ref":ref})
                results.append(store.ingest(name,[normalized],source="tushare",observed_at=observed,details={"api":api,"trade_date":day}))
                if len(rows)<6000:break
            else:raise ValueError("Tushare pagination incomplete")
    return results
