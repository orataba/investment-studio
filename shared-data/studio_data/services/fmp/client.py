from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

import requests

from studio_data.core.settings import Settings, get_settings


class FmpApiError(RuntimeError):
    """A direct FMP request failed or returned an invalid contract."""


FMP_EOD_RESPONSE_LIMIT = 5000


class FmpClient:
    def __init__(
        self,
        settings: Settings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self._base_url = resolved_settings.fmp_api_url.rstrip("/")
        self._api_key = resolved_settings.resolved_fmp_api_key()
        self._timeout = resolved_settings.fmp_timeout_seconds
        self._session = session or requests.Session()

    def _get_list(
        self,
        path: str,
        *,
        params: Mapping[str, object],
    ) -> list[dict[str, object]]:
        request_params = {**params, "apikey": self._api_key}
        try:
            response = self._session.get(
                f"{self._base_url}/{path.lstrip('/')}",
                params=request_params,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise FmpApiError(f"FMP {path} request failed.") from error
        if isinstance(payload, dict) and payload.get("Error Message"):
            raise FmpApiError(f"FMP {path} rejected the request.")
        if not isinstance(payload, list):
            raise FmpApiError(f"FMP {path} returned a non-list payload.")
        return [dict(item) for item in payload if isinstance(item, dict)]

    def active_equities(self, exchange: str) -> list[dict[str, object]]:
        return self._active_listings(exchange, is_etf=False)

    def active_etfs(self, exchange: str) -> list[dict[str, object]]:
        return self._active_listings(exchange, is_etf=True)

    def profile(self, symbol: str) -> dict[str, object]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("FMP profile symbol must not be blank.")
        rows = self._get_list("profile", params={"symbol": normalized_symbol})
        matches = [
            row
            for row in rows
            if str(row.get("symbol") or "").strip().upper() == normalized_symbol
        ]
        if len(matches) != 1:
            raise FmpApiError(
                f"FMP profile returned {len(matches)} exact rows for {normalized_symbol}."
            )
        return matches[0]

    def _active_listings(
        self,
        exchange: str,
        *,
        is_etf: bool,
    ) -> list[dict[str, object]]:
        return self._get_list(
            "company-screener",
            params={
                "exchange": exchange,
                "isEtf": "true" if is_etf else "false",
                "isFund": "false",
                "isActivelyTrading": "true",
                "limit": 10000,
            },
        )

    def historical_fx(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        return self._historical_eod_endpoint(
            "historical-price-eod/full",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

    def _historical_eod_endpoint(
        self,
        endpoint: str,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        requested_start = date.fromisoformat(start_date)
        page_end = date.fromisoformat(end_date)
        rows_by_date: dict[str, dict[str, object]] = {}
        while page_end >= requested_start:
            rows = self._get_list(
                endpoint,
                params={
                    "symbol": symbol,
                    "from": requested_start.isoformat(),
                    "to": page_end.isoformat(),
                },
            )
            dated_rows = {
                str(row.get("date") or "").strip(): row
                for row in rows
                if str(row.get("date") or "").strip()
            }
            rows_by_date.update(dated_rows)
            if len(rows) < FMP_EOD_RESPONSE_LIMIT or not dated_rows:
                break
            try:
                earliest = min(date.fromisoformat(value) for value in dated_rows)
            except ValueError as error:
                raise FmpApiError(f"FMP {endpoint} returned an invalid date.") from error
            if earliest <= requested_start:
                break
            next_end = earliest - timedelta(days=1)
            if next_end >= page_end:
                raise FmpApiError(f"FMP {endpoint} history did not paginate backward.")
            page_end = next_end
        return [rows_by_date[value] for value in sorted(rows_by_date)]
