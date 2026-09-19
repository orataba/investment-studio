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

from curl_cffi import requests as curl_requests


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
        prefer_standard_https: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("FMP API key is required")
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.prefer_standard_https = prefer_standard_https
        self._session = curl_requests.Session()
        self._last_bulk_request_at: float | None = None

    def close(self) -> None:
        self._session.close()

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

        if self.prefer_standard_https:
            return self._get_bytes_standard_https_with_retry(
                endpoint,
                url,
                safe_params,
                accept,
            )

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._session.get(
                    url,
                    params=safe_params,
                    headers={
                        "apikey": self._api_key,
                        "Accept": accept,
                        "User-Agent": "investment-studio-market/0.1 personal-research",
                    },
                    impersonate="chrome",
                    timeout=self.timeout_seconds,
                )
            except (curl_requests.exceptions.RequestException, TimeoutError) as exc:
                if attempt == self.max_attempts:
                    return self._get_bytes_standard_https_with_retry(
                        endpoint,
                        url,
                        safe_params,
                        accept,
                    )
                self._wait(attempt)
                continue

            status = response.status_code
            try:
                if status == 429 or 500 <= status <= 599:
                    if attempt == self.max_attempts:
                        raise FmpHttpError(status)
                    self._wait(attempt)
                    continue
                if status < 200 or status >= 300:
                    raise FmpHttpError(status)
                body = response.content
            finally:
                response.close()

            if not body:
                raise FmpResponseError("FMP returned an empty response")
            if len(body) > MAX_RESPONSE_BYTES:
                raise FmpResponseError("FMP response exceeded the 256 MB safety bound")
            return FmpResponse(
                endpoint=endpoint,
                params=safe_params,
                received_at=datetime.now(timezone.utc),
                body=body,
                payload=None,
            )

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

    def _get_bytes_standard_https_with_retry(
        self,
        endpoint: str,
        url: str,
        params: Mapping[str, str],
        accept: str,
    ) -> FmpResponse:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._get_bytes_standard_https(endpoint, url, params, accept)
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
