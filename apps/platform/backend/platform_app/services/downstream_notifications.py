from __future__ import annotations

import json
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks

from platform_app.core.settings import get_settings


logger = logging.getLogger(__name__)


def _normalized_asset_ids(asset_ids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(asset_id).strip() for asset_id in (asset_ids or []) if str(asset_id).strip()))


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
    asset_ids: list[str],
    dirty_from: date | None,
    refresh_all: bool,
) -> None:
    settings = get_settings()
    portfolio_api_url = settings.portfolio_api_url.strip().rstrip("/")
    if not portfolio_api_url:
        return
    if not refresh_all and not asset_ids:
        return

    _post_json(
        f"{portfolio_api_url}/api/portfolios/snapshots/daily/refresh",
        {
            "asset_ids": asset_ids,
            "dirty_from": dirty_from.isoformat() if dirty_from is not None else None,
            "refresh_all": refresh_all,
        },
    )


def _request_watchlist_asset_recalc(*, asset_ids: list[str]) -> None:
    settings = get_settings()
    watchlist_api_url = settings.watchlist_api_url.strip().rstrip("/")
    if not watchlist_api_url or not asset_ids:
        return

    for asset_id in asset_ids:
        _post_json(f"{watchlist_api_url}/api/recalc/assets/{asset_id}/all")


def notify_market_data_downstream_refresh(
    *,
    asset_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all_portfolios: bool = False,
    refresh_watchlist: bool = True,
) -> None:
    normalized_asset_ids = _normalized_asset_ids(asset_ids)
    _request_portfolio_daily_snapshot_refresh(
        asset_ids=normalized_asset_ids,
        dirty_from=dirty_from,
        refresh_all=refresh_all_portfolios,
    )

    watchlist_asset_ids = [
        asset_id for asset_id in normalized_asset_ids if not asset_id.strip().lower().startswith("fx-")
    ]
    if refresh_watchlist and watchlist_asset_ids:
        _request_watchlist_asset_recalc(asset_ids=watchlist_asset_ids)


def queue_market_data_downstream_refresh(
    background_tasks: BackgroundTasks,
    *,
    asset_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all_portfolios: bool = False,
    refresh_watchlist: bool = True,
) -> None:
    background_tasks.add_task(
        notify_market_data_downstream_refresh,
        asset_ids=asset_ids,
        dirty_from=dirty_from,
        refresh_all_portfolios=refresh_all_portfolios,
        refresh_watchlist=refresh_watchlist,
    )
