"""Portfolio uses Watchlist's instrument-risk records, rather than a second ledger."""
import json
from studio_identity import principal_headers
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query
from portfolio_app.core.settings import get_settings

router = APIRouter()


def watchlist_risk(path: str, method: str = "GET", payload: dict | None = None):
    base = get_settings().watchlist_api_url.rstrip("/")
    request = Request(base + "/risk" + path, method=method, headers={"Content-Type": "application/json", **principal_headers()}, data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("detail", "风险事项服务请求失败")
        except (ValueError, AttributeError):
            detail = "风险事项服务请求失败"
        raise HTTPException(error.code, detail) from error
    except (URLError, TimeoutError, ValueError) as error:
        raise HTTPException(503, "暂时无法读取标的风险事项，请稍后重试") from error


@router.get("")
def workspace(instrument_ids: str = Query(...)):
    # Explicit empty scope returns no instruments; never falls back to all Watchlist assets.
    return watchlist_risk("?" + urlencode({"instrument_ids": instrument_ids}))


@router.get("/review")
def risk_review(portfolio_id: str = Query(...)):
    return watchlist_risk("/review?" + urlencode({"portfolio_id": portfolio_id}))


@router.post("/review/runs", status_code=202)
def start_risk_review(payload: dict):
    return watchlist_risk("/review/runs", "POST", payload)


@router.post("/cases", status_code=201)
def add_case(payload: dict):
    return watchlist_risk("/cases", "POST", payload)


@router.put("/cases/{case_id}")
def follow_up(case_id: str, payload: dict):
    return watchlist_risk("/cases/" + quote(case_id, safe=""), "PUT", payload)


@router.put("/rules/{instrument_id}")
def set_rule(instrument_id: str, payload: dict):
    return watchlist_risk("/rules/" + quote(instrument_id, safe=""), "PUT", payload)
