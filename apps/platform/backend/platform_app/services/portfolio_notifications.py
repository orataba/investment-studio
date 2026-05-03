from __future__ import annotations

import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks

from platform_app.core.settings import get_settings


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

    payload = {
        "asset_ids": asset_ids,
        "dirty_from": dirty_from.isoformat() if dirty_from is not None else None,
        "refresh_all": refresh_all,
    }
    request = Request(
        f"{portfolio_api_url}/api/portfolios/snapshots/daily/refresh",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        return


def queue_portfolio_daily_snapshot_refresh(
    background_tasks: BackgroundTasks,
    *,
    asset_ids: list[str] | None = None,
    dirty_from: date | None = None,
    refresh_all: bool = False,
) -> None:
    normalized_asset_ids = list(
        dict.fromkeys(str(asset_id).strip() for asset_id in (asset_ids or []) if str(asset_id).strip())
    )
    background_tasks.add_task(
        _request_portfolio_daily_snapshot_refresh,
        asset_ids=normalized_asset_ids,
        dirty_from=dirty_from,
        refresh_all=refresh_all,
    )
