from __future__ import annotations

import json
from http.client import IncompleteRead
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

BASE_URL = "https://financialmodelingprep.com/stable"
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
FMP_BULK_MIN_INTERVAL_SECONDS = 10.1


class FmpError(RuntimeError):
    """Base error for a safe FMP request."""


class FmpHttpError(FmpError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"FMP request failed with HTTP status {status}")


class FmpTransportError(FmpError):
    """A transient network failure while contacting FMP."""


class FmpResponseError(FmpError):
    pass


@dataclass(frozen=True)
class FmpResponse:
    endpoint: str
    params: dict[str, str]
    received_at: datetime
    body: bytes
    payload: Any


class FmpClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("FMP API key is required")
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._last_bulk_request_at: float | None = None

    def close(self) -> None:
        # Each standard HTTPS response is closed by its request context.
        pass

    def get_json(
        self,
        endpoint: str,
        params: Mapping[str, object] | None = None,
    ) -> FmpResponse:
        response = self.get_bytes(endpoint, params, accept="application/json")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FmpResponseError("FMP returned invalid JSON") from None
        return FmpResponse(
            endpoint=response.endpoint,
            params=response.params,
            received_at=response.received_at,
            body=response.body,
            payload=payload,
        )

    def get_bytes(
        self,
        endpoint: str,
        params: Mapping[str, object] | None = None,
        *,
        accept: str = "application/octet-stream,text/csv;q=0.9,*/*;q=0.1",
    ) -> FmpResponse:
        endpoint = endpoint.strip("/")
        safe_params = {
            str(key): str(value)
            for key, value in (params or {}).items()
            if value is not None
        }
        url = f"{BASE_URL}/{endpoint}"

        # FMP is an authenticated API; browser TLS impersonation is unnecessary.
        # One transport also keeps the retry budget independent of fallback paths.
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._get_bytes_standard_https(endpoint, url, safe_params, accept)
            except FmpTransportError:
                if attempt == self.max_attempts:
                    raise
                self._wait(attempt)
            except FmpHttpError as exc:
                if exc.status != 429 and not 500 <= exc.status <= 599:
                    raise
                if attempt == self.max_attempts:
                    raise
                self._wait(attempt)
        raise AssertionError("unreachable")

    def get_bulk_bytes(
        self,
        endpoint: str,
        params: Mapping[str, object] | None = None,
        *,
        min_interval_seconds: float = FMP_BULK_MIN_INTERVAL_SECONDS,
    ) -> FmpResponse:
        if min_interval_seconds < FMP_BULK_MIN_INTERVAL_SECONDS:
            raise ValueError("bulk interval is below FMP's documented minimum")
        if self._last_bulk_request_at is not None:
            remaining = min_interval_seconds - (
                time.monotonic() - self._last_bulk_request_at
            )
            if remaining > 0:
                time.sleep(remaining)
        try:
            try:
                return self.get_bytes(
                    endpoint,
                    params,
                    accept="text/csv,application/octet-stream;q=0.9,*/*;q=0.1",
                )
            except FmpHttpError as exc:
                if exc.status != 429:
                    raise
                time.sleep(min_interval_seconds)
                return self.get_bytes(
                    endpoint,
                    params,
                    accept="text/csv,application/octet-stream;q=0.9,*/*;q=0.1",
                )
        finally:
            self._last_bulk_request_at = time.monotonic()

    def _get_bytes_standard_https(
        self,
        endpoint: str,
        url: str,
        params: Mapping[str, str],
        accept: str,
    ) -> FmpResponse:
        query = urllib.parse.urlencode(params)
        request_url = f"{url}?{query}" if query else url
        request = urllib.request.Request(
            request_url,
            headers={
                "apikey": self._api_key,
                "Accept": accept,
                "User-Agent": "investment-studio-market/0.1 personal-research",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = int(response.status)
                body = response.read(MAX_RESPONSE_BYTES + 1)
                # Bounded HTTPResponse.read(size) can return a short body without
                # raising when Content-Length remains. Never publish that CSV.
                remaining = getattr(response, "length", None)
                if remaining and len(body) <= MAX_RESPONSE_BYTES:
                    raise IncompleteRead(body, remaining)
        except urllib.error.HTTPError as exc:
            raise FmpHttpError(int(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, IncompleteRead) as exc:
            reason = getattr(exc, "reason", exc)
            raise FmpTransportError(
                "FMP standard HTTPS transport failed: "
                f"{type(exc).__name__} ({type(reason).__name__})"
            ) from None
        if status < 200 or status >= 300:
            raise FmpHttpError(status)
        if not body:
            raise FmpResponseError("FMP returned an empty response")
        if len(body) > MAX_RESPONSE_BYTES:
            raise FmpResponseError("FMP response exceeded the 256 MB safety bound")
        return FmpResponse(
            endpoint=endpoint,
            params=dict(params),
            received_at=datetime.now(timezone.utc),
            body=body,
            payload=None,
        )

    @staticmethod
    def _wait(attempt: int) -> None:
        delay = min(8.0, float(2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
        time.sleep(delay)
