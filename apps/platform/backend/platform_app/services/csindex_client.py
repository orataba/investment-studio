"""Small, fail-closed client for the public CSI index-performance endpoint."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_CSINDEX_API_URL = "https://www.csindex.com.cn/csindex-home"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class CsindexClientError(RuntimeError):
    """Raised when CSI data cannot be retrieved or validated safely."""


def _normalized_api_url(api_url: str) -> str:
    normalized = str(api_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CsindexClientError(
            "CSI API URL must be an absolute HTTP(S) URL. Set "
            "PORTFOLIO_OPS_PLATFORM_CSINDEX_API_URL first."
        )
    return normalized


def _format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def fetch_index_performance(
    *,
    index_code: str,
    start_date: date,
    end_date: date,
    api_url: str = DEFAULT_CSINDEX_API_URL,
    timeout_seconds: int = 30,
    opener: Callable[..., Any] | None = None,
) -> list[dict[str, object]]:
    """Fetch official daily index closes from CSI's public performance API."""

    normalized_code = str(index_code or "").strip().upper()
    if not normalized_code:
        raise CsindexClientError("CSI index code is required.")
    if start_date > end_date:
        raise CsindexClientError("CSI start_date must not be after end_date.")

    query = urlencode(
        {
            "indexCode": normalized_code,
            "startDate": _format_date(start_date),
            "endDate": _format_date(end_date),
        }
    )
    request = Request(
        f"{_normalized_api_url(api_url)}/perf/index-perf?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "PortfolioOperationsWorkbench/0.1",
        },
        method="GET",
    )
    open_request = opener or urlopen
    try:
        with open_request(
            request,
            timeout=max(1, int(timeout_seconds)),
        ) as response:
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise CsindexClientError(f"CSI API request failed: {error}") from error

    if len(response_body) > MAX_RESPONSE_BYTES:
        raise CsindexClientError("CSI API response exceeded the 5 MiB safety limit.")
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CsindexClientError(
            f"CSI API returned invalid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise CsindexClientError("CSI API response must be a JSON object.")
    if str(payload.get("code") or "") != "200" or payload.get("success") is not True:
        message = str(payload.get("msg") or "unknown provider error").strip()
        raise CsindexClientError(f"CSI API rejected the request: {message}")
    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list):
        raise CsindexClientError("CSI API response data must be an array.")
    return [dict(row) for row in raw_rows if isinstance(row, dict)]


__all__ = [
    "DEFAULT_CSINDEX_API_URL",
    "CsindexClientError",
    "fetch_index_performance",
]
