from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


from studio_data.core.settings import get_settings
from studio_identity import IdentityError, service_principal, principal_headers


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownstreamRequestFailure:
    url: str
    message: str


@dataclass(frozen=True)
class DownstreamRefreshResult:
    request_count: int = 0
    failures: tuple[DownstreamRequestFailure, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.failures


class DownstreamRefreshError(RuntimeError):
    def __init__(self, result: DownstreamRefreshResult) -> None:
        self.result = result
        failed_urls = ", ".join(item.url for item in result.failures)
        super().__init__(
            f"{len(result.failures)} of {result.request_count} downstream refresh requests failed: "
            f"{failed_urls}"
        )


def _normalized_instrument_ids(instrument_ids: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(
            str(instrument_id).strip()
            for instrument_id in (instrument_ids or [])
            if str(instrument_id).strip()
        )
    )


def _is_fx_instrument_id(instrument_id: str) -> bool:
    return instrument_id.strip().lower().startswith("fx-")


def _post_json(
    url: str,
    payload: dict[str, object] | None = None,
    *,
    audience: str,
    timeout: float = 2.0,
    validate_response: Callable[[object], str | None] | None = None,
) -> DownstreamRequestFailure | None:
    try:
        headers = principal_headers(service_principal(audience))
    except IdentityError as error:
        return DownstreamRequestFailure(url=url, message=error.detail)
    request = Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        # Settings accept only absolute HTTP(S) downstream API URLs.
        with urlopen(request, timeout=timeout) as response:  # nosec B310
            response_body = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        return DownstreamRequestFailure(url=url, message=str(error))
    if validate_response is not None:
        try:
            decoded: object = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return DownstreamRequestFailure(
                url=url,
                message=f"Downstream returned an invalid JSON acknowledgement: {error}",
            )
        validation_error = validate_response(decoded)
        if validation_error is not None:
            return DownstreamRequestFailure(url=url, message=validation_error)
    return None


def _request_portfolio_daily_snapshot_refresh(
    *,
    instrument_ids: list[str],
    dirty_from: date | None,
    refresh_all: bool,
    request_timeout_seconds: float,
) -> DownstreamRefreshResult:
    settings = get_settings()
    portfolio_api_url = settings.portfolio_api_url.strip().rstrip("/")
    if not portfolio_api_url:
        return DownstreamRefreshResult()
    if not refresh_all and not instrument_ids:
        return DownstreamRefreshResult()

    def validate_recalculation_acknowledgement(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return "Portfolio recalculation acknowledgement must be a JSON object."
        portfolio_ids = payload.get("portfolio_ids")
        accepted = payload.get("accepted")
        if not isinstance(portfolio_ids, list) or not isinstance(accepted, list):
            return (
                "Portfolio recalculation acknowledgement requires portfolio_ids "
                "and accepted arrays."
            )
        normalized_ids = [str(item).strip() for item in portfolio_ids]
        if any(not item for item in normalized_ids) or len(set(normalized_ids)) != len(
            normalized_ids
        ):
            return "Portfolio recalculation acknowledgement contains invalid portfolio ids."
        accepted_ids: list[str] = []
        for item in accepted:
            if not isinstance(item, dict) or item.get("status") != "accepted":
                return "Portfolio recalculation acknowledgement contains an invalid item."
            portfolio_id = str(item.get("portfolio_id") or "").strip()
            request_id = str(item.get("refresh_request_id") or "").strip()
            if not portfolio_id or not request_id:
                return (
                    "Portfolio recalculation acknowledgement is missing a portfolio "
                    "or request id."
                )
            accepted_ids.append(portfolio_id)
        if accepted_ids != normalized_ids:
            return (
                "Portfolio recalculation acknowledgement portfolio_ids do not match "
                "the accepted items."
            )
        return None

    url = f"{portfolio_api_url}/api/portfolios/snapshots/daily/recalculations"
    failure = _post_json(
        url,
        {
            # The Portfolio contract requires exactly one target selector.
            # A refresh-all request therefore cannot also carry instrument ids;
            # Watchlist still receives the normalized ids independently below.
            "instrument_ids": [] if refresh_all else instrument_ids,
            "dirty_from": dirty_from.isoformat() if dirty_from is not None else None,
            "refresh_all": refresh_all,
        },
        timeout=request_timeout_seconds,
        validate_response=validate_recalculation_acknowledgement,
        audience="portfolio",
    )
    return DownstreamRefreshResult(
        request_count=1,
        failures=(failure,) if failure is not None else (),
    )


def _request_watchlist_instrument_recalc(
    *,
    instrument_ids: list[str],
    request_timeout_seconds: float,
) -> DownstreamRefreshResult:
    settings = get_settings()
    watchlist_api_url = settings.watchlist_api_url.strip().rstrip("/")
    if not watchlist_api_url or not instrument_ids:
        return DownstreamRefreshResult()

    def validate_bulk_acknowledgement(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return "Watchlist bulk recalc acknowledgement must be a JSON object."
        accepted_count = payload.get("accepted_count")
        missing_ids = payload.get("missing_instrument_ids")
        if accepted_count != len(instrument_ids) or missing_ids not in ([], None):
            return (
                "Watchlist accepted only "
                f"{accepted_count!r} of {len(instrument_ids)} requested recalc jobs; "
                f"missing={missing_ids!r}."
            )
        return None

    url = f"{watchlist_api_url}/api/recalc/bulk"
    failure = _post_json(
        url,
        {
            "instrument_ids": instrument_ids,
            "job_type": "all",
            "trigger_type": "market_data_refresh",
            "trigger_ref_type": "shared_market_data",
        },
        timeout=request_timeout_seconds,
        validate_response=validate_bulk_acknowledgement,
        audience="watchlist",
    )
    return DownstreamRefreshResult(
        request_count=1,
        failures=(failure,) if failure is not None else (),
    )


def notify_market_data_downstream_refresh(
    *,
    instrument_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all_portfolios: bool = False,
    refresh_watchlist: bool = True,
    request_timeout_seconds: float = 2.0,
    watchlist_request_timeout_seconds: float | None = None,
    raise_on_error: bool = False,
) -> DownstreamRefreshResult:
    """Notify materialized-data consumers after a shared market-data write.

    The default remains best-effort for request/background-task callers. Scheduled
    jobs can opt into a longer timeout and ``raise_on_error=True``; all applicable
    requests are still attempted before one aggregate error is raised.
    """

    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive.")
    effective_watchlist_timeout_seconds = (
        request_timeout_seconds
        if watchlist_request_timeout_seconds is None
        else watchlist_request_timeout_seconds
    )
    if effective_watchlist_timeout_seconds <= 0:
        raise ValueError("watchlist_request_timeout_seconds must be positive.")

    normalized_instrument_ids = _normalized_instrument_ids(instrument_ids)
    effective_refresh_all_portfolios = refresh_all_portfolios or any(
        _is_fx_instrument_id(instrument_id)
        for instrument_id in normalized_instrument_ids
    )
    portfolio_result = _request_portfolio_daily_snapshot_refresh(
        instrument_ids=normalized_instrument_ids,
        dirty_from=dirty_from,
        refresh_all=effective_refresh_all_portfolios,
        request_timeout_seconds=request_timeout_seconds,
    )

    watchlist_result = DownstreamRefreshResult()
    watchlist_instrument_ids = [
        instrument_id
        for instrument_id in normalized_instrument_ids
        if not _is_fx_instrument_id(instrument_id)
    ]
    if refresh_watchlist and watchlist_instrument_ids:
        watchlist_result = _request_watchlist_instrument_recalc(
            instrument_ids=watchlist_instrument_ids,
            request_timeout_seconds=effective_watchlist_timeout_seconds,
        )

    result = DownstreamRefreshResult(
        request_count=portfolio_result.request_count + watchlist_result.request_count,
        failures=portfolio_result.failures + watchlist_result.failures,
    )
    for failure in result.failures:
        logger.warning(
            "Downstream refresh notification failed: %s (%s)",
            failure.url,
            failure.message,
        )
    if result.failures and raise_on_error:
        raise DownstreamRefreshError(result)
    return result
