from __future__ import annotations

import json
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks

from platform_app.core.settings import get_settings


logger = logging.getLogger(__name__)


def _normalized_instrument_ids(instrument_ids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(instrument_id).strip() for instrument_id in (instrument_ids or []) if str(instrument_id).strip()))


def _post_json(url: str, payload: dict[str, object] | None = None, *, timeout: float = 2.0) -> None:
    request = Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        logger.debug("Downstream refresh notification failed: %s", url, exc_info=True)


def _request_portfolio_daily_snapshot_refresh(
    *,
    instrument_ids: list[str],
    dirty_from: date | None,
    refresh_all: bool,
) -> None:
    settings = get_settings()
    portfolio_api_url = settings.portfolio_api_url.strip().rstrip("/")
    if not portfolio_api_url:
        return
    if not refresh_all and not instrument_ids:
        return

    _post_json(
        f"{portfolio_api_url}/api/portfolios/snapshots/daily/refresh",
        {
            "instrument_ids": instrument_ids,
            "dirty_from": dirty_from.isoformat() if dirty_from is not None else None,
            "refresh_all": refresh_all,
        },
    )


def _request_watchlist_instrument_recalc(*, instrument_ids: list[str]) -> None:
    settings = get_settings()
    watchlist_api_url = settings.watchlist_api_url.strip().rstrip("/")
    if not watchlist_api_url or not instrument_ids:
        return

    for instrument_id in instrument_ids:
        _post_json(f"{watchlist_api_url}/api/recalc/instruments/{instrument_id}/all")


def notify_market_data_downstream_refresh(
    *,
    instrument_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all_portfolios: bool = False,
    refresh_watchlist: bool = True,
) -> None:
    normalized_instrument_ids = _normalized_instrument_ids(instrument_ids)
    _request_portfolio_daily_snapshot_refresh(
        instrument_ids=normalized_instrument_ids,
        dirty_from=dirty_from,
        refresh_all=refresh_all_portfolios,
    )

    watchlist_instrument_ids = [
        instrument_id for instrument_id in normalized_instrument_ids if not instrument_id.strip().lower().startswith("fx-")
    ]
    if refresh_watchlist and watchlist_instrument_ids:
        _request_watchlist_instrument_recalc(instrument_ids=watchlist_instrument_ids)


def queue_market_data_downstream_refresh(
    background_tasks: BackgroundTasks,
    *,
    instrument_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all_portfolios: bool = False,
    refresh_watchlist: bool = True,
) -> None:
    background_tasks.add_task(
        notify_market_data_downstream_refresh,
        instrument_ids=instrument_ids,
        dirty_from=dirty_from,
        refresh_all_portfolios=refresh_all_portfolios,
        refresh_watchlist=refresh_watchlist,
    )
