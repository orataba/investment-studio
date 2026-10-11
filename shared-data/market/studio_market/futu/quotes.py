"""Restricted Futu REST market-data client with original response evidence.

Only Hong Kong / US stock and ETF daily bars are supported. Prices are raw
(`autype=0`); they must never be presented as dividend-adjusted total returns.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import requests


API_ORIGIN = "https://webapi.futunn.com"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# 2026-10-08: stock-basicinfo returned HTTP 200 / -11 rate_limited with
# Retry-After: 43 and the same 43s body delay. Permit a bounded minute; the
# default three attempts still allow at most two waits (120s combined).
MAX_RETRY_WAIT_SECONDS = 60.0


@dataclass(frozen=True)
class FutuCapture:
    endpoint: str
    params: dict[str, object]
    received_at: datetime
    body: bytes
    payload: dict[str, object]


@dataclass(frozen=True)
class FutuBasicInfoResult:
    info: dict[str, object]
    captures: tuple[FutuCapture, ...]


@dataclass(frozen=True)
class FutuHistoryResult:
    rows: tuple[dict[str, object], ...]
    captures: tuple[FutuCapture, ...]


class FutuQuoteError(RuntimeError):
    def __init__(self, message: str, *, captures: tuple[FutuCapture, ...] = (), business_code: int | None = None):
        super().__init__(message)
        self.captures = captures
        self.business_code = business_code


class FutuQuoteCoverageError(FutuQuoteError):
    """The requested security is unknown or unsupported by this provider."""


def validate_symbol(symbol: str) -> str:
    """Require canonical provider symbols; never accept URLs or path fragments."""
    if not isinstance(symbol, str) or not (
        re.fullmatch(r"HK\.[0-9]{5}", symbol)
        or re.fullmatch(r"US\.[A-Z][A-Z0-9.\-]{0,31}", symbol)
    ):
        raise FutuQuoteError("Futu symbol must be a canonical HK.00000 or US.TICKER code")
    if symbol == "HK.00000":
        raise FutuQuoteError("Futu Hong Kong security code must be positive")
    return symbol


def _zone(symbol: str) -> ZoneInfo:
    return ZoneInfo("Asia/Hong_Kong" if symbol.startswith("HK.") else "America/New_York")


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FutuQuoteError(f"Futu returned an invalid {field}")
    return value


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise FutuQuoteError(f"Futu returned an invalid {field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise FutuQuoteError(f"Futu returned an invalid {field}") from None
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise FutuQuoteError(f"Futu returned an invalid {field}")
    return result


def _timestamp_date(value: object, symbol: str, field: str) -> date:
    timestamp = _integer(value, field, minimum=1)
    try:
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).astimezone(_zone(symbol)).date()
    except (OverflowError, OSError, ValueError):
        raise FutuQuoteError(f"Futu returned an invalid {field}") from None


def _identity(row: dict[str, object], symbol: str) -> None:
    # History commonly omits identity fields; reject a contradiction if supplied.
    for key in ("code", "symbol"):
        if key in row and row[key] != symbol:
            raise FutuQuoteError("Futu returned a different security identity")


def _daily_row(row: object, symbol: str, start: date, end: date, precision: int) -> dict[str, object]:
    if not isinstance(row, dict):
        raise FutuQuoteError("Futu returned an invalid daily bar")
    _identity(row, symbol)
    day = _timestamp_date(row.get("time_key"), symbol, "time_key")
    if "date" in row:
        explicit = _integer(row["date"], "date", minimum=1)
        if str(explicit) != day.strftime("%Y%m%d"):
            raise FutuQuoteError("Futu daily bar date conflicts with its market-local timestamp")
    if not start <= day <= end:
        raise FutuQuoteError("Futu returned a daily bar outside the requested date range")
    result: dict[str, object] = {"symbol": symbol, "date": day.isoformat()}
    for field in ("open", "high", "low", "close"):
        result[field] = _number(row.get(field), field, positive=True)
    if result["low"] > result["high"]:
        raise FutuQuoteError("Futu returned a daily low above its high")
    if not result["low"] <= result["close"] <= result["high"]:
        raise FutuQuoteError("Futu daily close conflicts with its high-low range")
    # An inconsistent open cannot form a canonical candle. The independently
    # range-consistent close is retained; a contradictory close is rejected above.
    result["ohlc_quality"] = (
        "valid" if result["low"] <= min(result["open"], result["close"])
        <= max(result["open"], result["close"]) <= result["high"] else "inconsistent"
    )
    volume = _integer(row.get("volume"), "volume")
    try:
        shares = Decimal(f"{volume}e-{precision}")
        as_float = float(shares)
        if not math.isfinite(as_float) or (volume and as_float == 0):
            raise FutuQuoteError("Futu volume cannot be represented in the numeric store")
        normalized_volume = int(shares) if shares == shares.to_integral_value() else as_float
    except (InvalidOperation, OverflowError, ValueError):
        raise FutuQuoteError("Futu returned an invalid volume precision") from None
    result.update(volume=normalized_volume, volume_unit="shares", turnover=_number(row.get("turnover"), "turnover"))
    return result


class FutuQuoteClient:
    """Restricted quote-only endpoints; the OAuth provider retains credential ownership.

    ``token_provider`` is ``FutuOAuth.access_token(force_refresh=False)``. A 401
    permits one forced refresh. Transient failures have a separate bounded retry
    budget; redirects and permission/business validation failures are not retried.
    """

    def __init__(
        self,
        token_provider: Callable[..., str],
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not callable(token_provider):
            raise ValueError("Futu token provider must be callable")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Futu request timeout must be positive")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("Futu request attempts must be a positive integer")
        self._token_provider = token_provider
        self._session = session if session is not None else requests.Session()
        # Do not forward bearer credentials through ambient proxy configuration or
        # allow requests to replace bearer auth with credentials from .netrc.
        self._session.trust_env = False
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._sleep = sleep

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> FutuQuoteClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def basic_info(self, symbol: str) -> FutuBasicInfoResult:
        result = self.basic_info_many([symbol])
        if not result.rows:
            raise FutuQuoteCoverageError(
                "Futu has no stock/ETF basic information for the requested security",
                captures=result.captures,
            )
        return FutuBasicInfoResult(result.rows[0], result.captures)

    def basic_info_many(self, symbols: Sequence[str]) -> FutuHistoryResult:
        """Verify a public exchange directory against Futu in batches of 400.

        The endpoint deliberately omits unknown securities. Missing symbols and
        non-stock/ETF results are absent from ``rows``; callers must compare the
        returned symbols to their requested directory instead of assuming full
        coverage. Provider-specific extra metadata is retained, never inferred.
        """
        if isinstance(symbols, (str, bytes)):
            raise FutuQuoteError("Futu basic information requires a sequence of symbols")
        requested = [validate_symbol(symbol) for symbol in symbols]
        if len(set(requested)) != len(requested):
            raise FutuQuoteError("Futu basic information request contains duplicate symbols")
        captures: list[FutuCapture] = []
        rows: list[dict[str, object]] = []
        try:
            for offset in range(0, len(requested), 400):
                batch = requested[offset:offset + 400]
                try:
                    capture = self._request("POST", "/api/v1.0/quote/stock-basicinfo", {"code_list": batch})
                except FutuQuoteCoverageError as exc:
                    # The documented all-unknown result is -7, while a partial
                    # match returns success with unknown codes omitted.
                    if exc.business_code != -7:
                        raise
                    captures.extend(exc.captures)
                    continue
                captures.append(capture)
                data = capture.payload["data"]
                items = data.get("basic_list")
                if not isinstance(items, list) or len(items) > len(batch):
                    raise FutuQuoteError("Futu returned an invalid basic-info response")
                seen: set[str] = set()
                for info in items:
                    if not isinstance(info, dict) or not isinstance(info.get("code"), str):
                        raise FutuQuoteError("Futu returned an invalid basic-info security")
                    symbol = info["code"]
                    if symbol not in batch:
                        raise FutuQuoteError("Futu returned a different security identity")
                    if symbol in seen:
                        raise FutuQuoteError("Futu returned duplicate basic-info security identities")
                    seen.add(symbol)
                    _identity(info, symbol)
                    stock_type = info.get("stock_type")
                    if not isinstance(stock_type, str) or not stock_type:
                        raise FutuQuoteError("Futu returned an invalid stock_type")
                    if stock_type not in ("STOCK", "ETF"):
                        continue
                    stock_id = _integer(info.get("stock_id"), "stock_id", minimum=1)
                    listing_timestamp = _integer(info.get("listing_date"), "listing_date")
                    listing_date = _timestamp_date(listing_timestamp, symbol, "listing_date").isoformat() if listing_timestamp else None
                    rows.append(dict(info, symbol=symbol, stock_id=stock_id, listing_date=listing_date))
        except FutuQuoteError as exc:
            exc.captures = tuple(captures) + exc.captures
            raise
        return FutuHistoryResult(tuple(rows), tuple(captures))

    def history(self, symbol: str, start: date, end: date) -> FutuHistoryResult:
        symbol = validate_symbol(symbol)
        if type(start) is not date or type(end) is not date or start > end:
            raise FutuQuoteError("Futu daily history requires an ordered pair of calendar dates")
        captures: list[FutuCapture] = []
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        window_start = start
        try:
            while window_start <= end:
                # At most 365 distinct daily rows, below the server's 370 row cap.
                # This avoids the documented date/ms ambiguity of the end cursor.
                window_end = window_start + timedelta(days=min(364, (end - window_start).days))
                capture = self._request("GET", f"/api/v1.0/quote/{symbol}/history-kline", {
                    "start": window_start.isoformat(), "end": window_end.isoformat(),
                    "ktype": 2, "autype": 0, "extended_time": 0, "num": 370,
                })
                captures.append(capture)
                data = capture.payload["data"]
                _identity(data, symbol)
                items = data.get("kline_list")
                if not isinstance(items, list) or len(items) > 370:
                    raise FutuQuoteError("Futu returned an invalid daily history response")
                precision = _integer(data.get("volume_precision", 0), "volume_precision")
                for item in items:
                    row = _daily_row(item, symbol, window_start, window_end, precision)
                    if row["date"] in seen:
                        raise FutuQuoteError("Futu returned duplicate daily bar dates")
                    seen.add(row["date"])
                    rows.append(row)
                cursor = data.get("next_time", 0)
                if cursor is not None:
                    cursor = _integer(cursor, "next_time")
                    if cursor and window_start <= _timestamp_date(cursor, symbol, "next_time") <= window_end:
                        raise FutuQuoteError("Futu daily history returned an unresolved pagination cursor")
                if window_end == end:
                    break
                window_start = window_end + timedelta(days=1)
        except FutuQuoteError as exc:
            # Include earlier successful windows and the current failure evidence.
            exc.captures = tuple(captures) + exc.captures
            raise
        return FutuHistoryResult(tuple(sorted(rows, key=lambda row: row["date"])), tuple(captures))

    def corporate_actions(self, symbol: str) -> FutuHistoryResult:
        """Capture the provider-reported complete since-listing action history.

        Empty is a valid response, not proof of coverage for an ETF. Financial
        interpretation is separate: preserve unsupported events as evidence.
        """
        symbol = validate_symbol(symbol)
        capture = self._request("GET", f"/api/v1.0/quote/{symbol}/corporate-actions/rehab",
                                {"divi_mode": "include_divi"})
        try:
            data = capture.payload["data"]
            _identity(data, symbol)
            items = data.get("rehabs")
            if not isinstance(items, list):
                raise FutuQuoteError("Futu corporate-action response lacks an explicit event list")
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    raise FutuQuoteError("Futu returned an invalid corporate action")
                _identity(item, symbol)
                try:
                    day = date.fromisoformat(item["ex_div_date"])
                except (KeyError, TypeError, ValueError):
                    raise FutuQuoteError("Futu corporate action has an invalid ex-dividend date") from None
                types = item.get("action_types")
                if not isinstance(types, list) or not types or any(not isinstance(value, str) or not value for value in types):
                    raise FutuQuoteError("Futu corporate action lacks explicit action types")
                rows.append(dict(item, ex_div_date=day.isoformat()))
            return FutuHistoryResult(tuple(sorted(rows, key=lambda row: row["ex_div_date"])), (capture,))
        except FutuQuoteError as exc:
            exc.captures = (capture,) + exc.captures
            raise

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        delay = min(MAX_RETRY_WAIT_SECONDS, float(2 ** (attempt - 1)))
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except (ValueError, OverflowError):
                try:
                    resume_at = parsedate_to_datetime(retry_after)
                    if resume_at.tzinfo is None:
                        resume_at = resume_at.replace(tzinfo=timezone.utc)
                    delay = (resume_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    raise FutuQuoteError("Futu returned an invalid Retry-After header") from None
            if not math.isfinite(delay):
                raise FutuQuoteError("Futu returned a non-finite retry delay")
            if delay > MAX_RETRY_WAIT_SECONDS:
                raise FutuQuoteError(
                    f"Futu requested a retry delay of {delay:g}s, exceeding the "
                    f"{MAX_RETRY_WAIT_SECONDS:g}s per-wait budget; retry later"
                )
            delay = max(0.0, delay)
        self._sleep(delay)

    def _request(self, method: str, path: str, params: dict[str, object]) -> FutuCapture:
        # Keep even the private transport restricted, so it cannot become an
        # accidental trade/account API bridge when reused by another caller.
        if not ((method == "POST" and path == "/api/v1.0/quote/stock-basicinfo") or (
            method == "GET" and re.fullmatch(r"/api/v1\.0/quote/(?:HK\.[0-9]{5}|US\.[A-Z][A-Z0-9.\-]{0,31})/(?:history-kline|corporate-actions/rehab)", path)
        )):
            raise FutuQuoteError("Futu request is outside the permitted quote endpoints")
        endpoint = API_ORIGIN + path
        refreshed = False
        token = self._token_provider()
        attempt = 0
        while attempt < self._max_attempts:
            attempt += 1
            try:
                response = self._session.request(
                    method, endpoint, params=params if method == "GET" else None,
                    json=params if method == "POST" else None,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=self._timeout, allow_redirects=False, stream=True,
                )
                try:
                    status = response.status_code
                    retry_after = response.headers.get("Retry-After")
                    chunks: list[bytes] = []
                    size = 0
                    if 200 <= status < 300:
                        for chunk in response.iter_content(chunk_size=65536):
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise FutuQuoteError("Futu response exceeds the 8 MB safety bound")
                            chunks.append(chunk)
                    body = b"".join(chunks)
                finally:
                    response.close()
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ContentDecodingError, TimeoutError) as exc:
                if attempt >= self._max_attempts:
                    raise FutuQuoteError(f"Futu quote transport failed: {type(exc).__name__}") from None
                self._wait(attempt, None)
                continue
            except requests.RequestException as exc:
                raise FutuQuoteError(f"Futu quote request failed: {type(exc).__name__}") from None
            if status == 401 and not refreshed:
                token = self._token_provider(force_refresh=True)
                refreshed = True
                attempt -= 1
                continue
            if status == 429 or 500 <= status <= 599:
                if attempt < self._max_attempts:
                    self._wait(attempt, retry_after)
                    continue
            if not 200 <= status < 300:
                raise FutuQuoteError(f"Futu quote request failed with HTTP status {status}")
            received_at = datetime.now(timezone.utc)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                capture = FutuCapture(endpoint, dict(params), received_at, body, {})
                raise FutuQuoteError("Futu returned an invalid JSON response", captures=(capture,)) from None
            if not isinstance(payload, dict):
                capture = FutuCapture(endpoint, dict(params), received_at, body, {})
                raise FutuQuoteError("Futu returned an invalid response envelope", captures=(capture,))
            capture = FutuCapture(endpoint, dict(params), received_at, body, payload)
            code = payload.get("ret_code")
            if isinstance(code, bool) or not isinstance(code, int):
                raise FutuQuoteError("Futu response is missing its business status", captures=(capture,))
            error = payload.get("error")
            if (code == -11 and isinstance(error, dict) and error.get("code") == "rate_limited"
                    and attempt < self._max_attempts):
                # Futu also returns rate limits inside a successful HTTP envelope.
                # This observed contract names a precise delay in error.message.
                # Only this explicit rate-limit code is eligible for retry.
                message = error.get("message")
                delay = re.search(r"\bretry after ([0-9]+(?:\.[0-9]+)?)s\b", message, re.IGNORECASE) if isinstance(message, str) else None
                try:
                    self._wait(attempt, retry_after if retry_after is not None else delay.group(1) if delay else None)
                except FutuQuoteError as exc:
                    exc.captures = (capture,) + exc.captures
                    exc.business_code = code
                    raise
                continue
            if code != 0:
                error_type = FutuQuoteCoverageError if code in (-7, -8) else FutuQuoteError
                raise error_type(f"Futu quote request failed with business code {code}", captures=(capture,), business_code=code)
            if not isinstance(payload.get("data"), dict):
                raise FutuQuoteError("Futu response is missing its data object", captures=(capture,))
            return capture
        raise AssertionError("unreachable")
