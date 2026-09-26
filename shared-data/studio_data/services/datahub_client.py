"""HTTP client for DataHub's Tushare OpenAPI surface."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from datetime import datetime, timedelta
import time
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_DATAHUB_TUSHARE_API_URL = (
    "http://datahubco.com/app-api/openapi/v1/tushare"
)
DATAHUB_PAGE_SIZE = 5000
DATAHUB_ENDPOINT_PAGE_SIZES = {"fund_portfolio": 1000}
DATAHUB_TRANSIENT_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)
DATAHUB_TRANSIENT_PROVIDER_CODES = {40203, 40204}
# Verified request contracts: these endpoints accept 366 inclusive days;
# multi-year requests are rejected before offset pagination can begin.
DATAHUB_DATE_WINDOW_DAYS = {"index_daily": 366, "fund_portfolio": 366}
TUSHARE_ENDPOINT_PATHS = {
    "fund_basic": "fund-basic",
    "fund_portfolio": "fund-portfolio",
    "fund_daily": "fund-daily",
    "fund_adj": "fund-adj",
    "fund_nav": "fund-nav",
    "index_basic": "index-basic",
    "index_daily": "index-daily",
}


class DataHubClientError(RuntimeError):
    """Raised when DataHub rejects a request or violates its response contract."""


def _normalized_api_url(api_url: str) -> str:
    normalized = str(api_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DataHubClientError(
            "DataHub API URL must be an absolute HTTP or HTTPS URL."
        )
    return normalized


def _normalized_api_key(api_key: str | None) -> str:
    normalized = str(api_key or "").strip()
    if not normalized:
        raise DataHubClientError(
            "DataHub API key is not configured. Set "
            "INVESTMENT_STUDIO_DATA_DATAHUB_API_KEY first."
        )
    return normalized


def _error_detail(payload: Mapping[str, Any]) -> str:
    for key in ("msg", "detail", "message"):
        value = str(payload.get(key) or "").strip()
        if value and value != "...":
            return value
    return "DataHub returned no error detail."


def _decode_rows(payload: object) -> tuple[list[dict[str, object]], bool]:
    if not isinstance(payload, dict):
        raise DataHubClientError("DataHub response must be a JSON object.")
    code = payload.get("code")
    if code != 0:
        raise DataHubClientError(
            f"DataHub returned code {code!r}: {_error_detail(payload)}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataHubClientError("DataHub response is missing the data object.")
    raw_fields = data.get("fields")
    raw_items = data.get("items")
    if not isinstance(raw_fields, list) or not all(
        isinstance(field, str) and field.strip() for field in raw_fields
    ):
        raise DataHubClientError("DataHub response has invalid fields metadata.")
    if not isinstance(raw_items, list):
        raise DataHubClientError("DataHub response has invalid items data.")

    fields = [field.strip() for field in raw_fields]
    rows: list[dict[str, object]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            rows.append({str(key): value for key, value in raw_item.items()})
            continue
        if not isinstance(raw_item, list) or len(raw_item) != len(fields):
            raise DataHubClientError(
                "DataHub item width does not match the fields metadata."
            )
        rows.append(dict(zip(fields, raw_item, strict=True)))
    return rows, bool(data.get("has_more"))


def _date_queries(api_name: str, query: dict[str, object]) -> list[dict[str, object]]:
    window_days = DATAHUB_DATE_WINDOW_DAYS.get(api_name)
    if window_days is None or not query.get("start_date") or not query.get("end_date"):
        return [query]
    try:
        start = datetime.strptime(str(query["start_date"]), "%Y%m%d").date()
        end = datetime.strptime(str(query["end_date"]), "%Y%m%d").date()
    except ValueError as error:
        raise DataHubClientError(f"{api_name} dates must use YYYYMMDD.") from error
    if start > end:
        raise DataHubClientError(f"{api_name} start_date must not follow end_date.")
    queries = []
    while start <= end:
        stop = min(start + timedelta(days=window_days - 1), end)
        queries.append({**query, "start_date":start.strftime("%Y%m%d"), "end_date":stop.strftime("%Y%m%d")})
        start = stop + timedelta(days=1)
    return queries


def fetch_tushare_rows(
    *,
    api_key: str | None,
    api_url: str = DEFAULT_DATAHUB_TUSHARE_API_URL,
    api_name: str,
    params: Mapping[str, object] | None = None,
    fields: str | None = None,
    timeout_seconds: int = 30,
    session: requests.Session | None = None,
) -> list[dict[str, object]]:
    """Fetch and decode every page for one supported Tushare endpoint."""

    api_name = str(api_name or "").strip()
    endpoint_path = TUSHARE_ENDPOINT_PATHS.get(api_name)
    if endpoint_path is None:
        raise DataHubClientError(
            f"Unsupported DataHub Tushare endpoint: {api_name!r}."
        )
    normalized_key = _normalized_api_key(api_key)
    normalized_url = _normalized_api_url(api_url)
    query = {
        str(key): value
        for key, value in dict(params or {}).items()
        if value is not None
    }
    if fields:
        query["fields"] = fields
    query["limit"] = DATAHUB_ENDPOINT_PAGE_SIZES.get(api_name, DATAHUB_PAGE_SIZE)

    owned_session = session is None
    active_session = session or requests.Session()
    if owned_session:
        active_session.trust_env = False

    rows: list[dict[str, object]] = []
    try:
        pending = deque(_date_queries(api_name, query))
        while pending:
            date_query = pending.popleft()
            offset = 0
            window_rows: list[dict[str, object]] = []
            while True:
                page_query = {**date_query, "offset": offset}
                for attempt in range(len(DATAHUB_TRANSIENT_RETRY_DELAYS) + 1):
                    try:
                        response = active_session.get(
                            f"{normalized_url}/{endpoint_path}",
                            headers={"X-API-Key": normalized_key},
                            params=page_query,
                            timeout=max(1, int(timeout_seconds)),
                        )
                        response.raise_for_status()
                        payload = response.json()
                    except (requests.RequestException, ValueError) as error:
                        safe_message = redact_datahub_error_message(
                            error,
                            api_key=normalized_key,
                        )
                        raise DataHubClientError(
                            f"DataHub request failed: {safe_message}"
                        ) from error

                    provider_code = (
                        payload.get("code") if isinstance(payload, dict) else None
                    )
                    if (
                        provider_code not in DATAHUB_TRANSIENT_PROVIDER_CODES
                        or attempt == len(DATAHUB_TRANSIENT_RETRY_DELAYS)
                    ):
                        break
                    time.sleep(DATAHUB_TRANSIENT_RETRY_DELAYS[attempt])

                page_rows, has_more = _decode_rows(payload)
                if api_name == "fund_portfolio" and not has_more and len(page_rows) >= int(query["limit"]):
                    # This REST endpoint caps its upstream result before local
                    # offset pagination, so a full final page is not complete.
                    # Its date parameters filter ann_date, not report end_date.
                    start_text, end_text = str(date_query.get("start_date", "")), str(date_query.get("end_date", ""))
                    if not start_text or not end_text or start_text == end_text:
                        raise DataHubClientError(
                            "fund_portfolio reached the provider row limit for a single announcement date "
                            "or an unbounded request; complete holdings cannot be confirmed."
                        )
                    if any(not start_text <= str(row.get("ann_date") or "") <= end_text for row in page_rows):
                        raise DataHubClientError("fund_portfolio returned an invalid announcement-date window.")
                    start = datetime.strptime(start_text, "%Y%m%d").date()
                    end = datetime.strptime(end_text, "%Y%m%d").date()
                    midpoint = start + (end - start) // 2
                    pending.appendleft({**date_query, "start_date": (midpoint + timedelta(days=1)).strftime("%Y%m%d")})
                    pending.appendleft({**date_query, "end_date": midpoint.strftime("%Y%m%d")})
                    # Discard the saturated parent window, including any pages
                    # already read. Only complete, disjoint children contribute.
                    break
                window_rows.extend(page_rows)
                if not has_more:
                    rows.extend(window_rows)
                    break
                if not page_rows:
                    raise DataHubClientError(
                        "DataHub returned has_more=true with an empty page."
                    )
                offset += len(page_rows)
        return rows
    finally:
        if owned_session:
            active_session.close()


def redact_datahub_error_message(
    error: BaseException,
    *,
    api_key: str | None,
) -> str:
    message = str(error)
    normalized_key = str(api_key or "").strip()
    if normalized_key:
        message = message.replace(normalized_key, "[REDACTED_API_KEY]")
    return message


__all__ = [
    "DEFAULT_DATAHUB_TUSHARE_API_URL",
    "DataHubClientError",
    "fetch_tushare_rows",
    "redact_datahub_error_message",
]
