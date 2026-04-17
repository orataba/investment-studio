from __future__ import annotations

import json
import logging
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.settings import get_settings


logger = logging.getLogger(__name__)


def schedule_watchlist_recalc(
    *,
    asset_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None = None,
) -> None:
    normalized_asset_id = asset_id.strip()
    if not normalized_asset_id:
        return
    Thread(
        target=_notify_watchlist_recalc,
        kwargs={
            "asset_id": normalized_asset_id,
            "trigger_ref_type": trigger_ref_type,
            "trigger_ref_id": trigger_ref_id,
        },
        daemon=True,
    ).start()


def _notify_watchlist_recalc(
    *,
    asset_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> None:
    settings = get_settings()
    url = f"{settings.watchlist_api_url.rstrip('/')}/api/recalc/assets/{quote(asset_id)}/execute"
    payload = json.dumps(
        {
            "job_type": "all",
            "trigger_type": "shared_asset_write",
            "trigger_ref_type": trigger_ref_type,
            "trigger_ref_id": trigger_ref_id,
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=5):
            return
    except HTTPError as error:
        if error.code == 404:
            logger.debug(
                "Skipping watchlist recalc because asset is not materialized locally.",
                extra={"asset_id": asset_id},
            )
            return
        logger.warning(
            "Watchlist recalc callback returned HTTP %s for %s.",
            error.code,
            asset_id,
        )
    except (URLError, TimeoutError) as error:
        logger.warning(
            "Watchlist recalc callback failed for %s: %s",
            asset_id,
            error,
        )
