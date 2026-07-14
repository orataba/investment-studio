from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from platform_app.core.settings import get_settings


@dataclass(frozen=True)
class WatchlistRecalcAcknowledgement:
    event_id: str
    instrument_id: str


class WatchlistRecalcDeliveryError(RuntimeError):
    pass


def _acknowledged_instrument_ids(
    acknowledgement: dict[str, object],
    *,
    field_name: str,
) -> list[str]:
    raw_ids = acknowledgement.get(field_name)
    if not isinstance(raw_ids, list):
        raise WatchlistRecalcDeliveryError(
            f"Watchlist acknowledgement field {field_name!r} must be a JSON array"
        )
    instrument_ids: list[str] = []
    for raw_id in raw_ids:
        if not isinstance(raw_id, str) or not raw_id or raw_id != raw_id.strip():
            raise WatchlistRecalcDeliveryError(
                f"Watchlist acknowledgement field {field_name!r} contains "
                "an invalid instrument id"
            )
        instrument_ids.append(raw_id)
    if len(instrument_ids) != len(set(instrument_ids)):
        raise WatchlistRecalcDeliveryError(
            f"Watchlist acknowledgement field {field_name!r} contains duplicates"
        )
    return instrument_ids


def send_watchlist_market_data_recalc(
    *,
    event_id: str,
    instrument_id: str,
    timeout_seconds: float = 5.0,
) -> WatchlistRecalcAcknowledgement:
    """Send one durable outbox event to Watchlist.

    Database state and retry policy deliberately live outside this HTTP seam.
    """

    normalized_event_id = event_id.strip()
    normalized_instrument_id = instrument_id.strip()
    if not normalized_event_id:
        raise ValueError("event_id must not be blank")
    if not normalized_instrument_id:
        raise ValueError("instrument_id must not be blank")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    watchlist_api_url = get_settings().watchlist_api_url.strip().rstrip("/")
    if not watchlist_api_url:
        raise WatchlistRecalcDeliveryError("Watchlist API URL is not configured")

    payload = {
        "instrument_ids": [normalized_instrument_id],
        "job_type": "all",
        "trigger_type": "market_data_refresh",
        "trigger_ref_type": "instrument_registry_outbox",
        "trigger_ref_id": normalized_event_id,
    }
    request = Request(
        f"{watchlist_api_url}/api/recalc/bulk",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise WatchlistRecalcDeliveryError(str(error)) from error

    try:
        acknowledgement: object = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WatchlistRecalcDeliveryError(
            f"Watchlist returned invalid JSON: {error}"
        ) from error
    if not isinstance(acknowledgement, dict):
        raise WatchlistRecalcDeliveryError(
            "Watchlist bulk acknowledgement must be a JSON object"
        )

    requested_count = acknowledgement.get("requested_count")
    accepted_count = acknowledgement.get("accepted_count")
    if type(requested_count) is not int or requested_count != 1:
        raise WatchlistRecalcDeliveryError(
            "Watchlist acknowledgement requested_count must be exactly 1"
        )
    if type(accepted_count) is not int or accepted_count != 1:
        raise WatchlistRecalcDeliveryError(
            "Watchlist acknowledgement accepted_count must be exactly 1"
        )

    enqueued_ids = _acknowledged_instrument_ids(
        acknowledgement,
        field_name="enqueued_instrument_ids",
    )
    coalesced_ids = _acknowledged_instrument_ids(
        acknowledgement,
        field_name="coalesced_instrument_ids",
    )
    existing_ids = _acknowledged_instrument_ids(
        acknowledgement,
        field_name="existing_instrument_ids",
    )
    ignored_ids = _acknowledged_instrument_ids(
        acknowledgement,
        field_name="ignored_instrument_ids",
    )
    missing_ids = _acknowledged_instrument_ids(
        acknowledgement,
        field_name="missing_instrument_ids",
    )
    deferred_ids = (
        _acknowledged_instrument_ids(
            acknowledgement,
            field_name="deferred_instrument_ids",
        )
        if "deferred_instrument_ids" in acknowledgement
        else []
    )
    if deferred_ids or missing_ids:
        raise WatchlistRecalcDeliveryError(
            "Watchlist acknowledgement deferred or could not resolve the outbox instrument"
        )

    accepted_lists = (enqueued_ids, coalesced_ids, existing_ids, ignored_ids)
    accepted_ids = [
        acknowledged_id
        for acknowledged_ids in accepted_lists
        for acknowledged_id in acknowledged_ids
    ]
    if len(accepted_ids) != len(set(accepted_ids)):
        raise WatchlistRecalcDeliveryError(
            "Watchlist acknowledgement overlaps accepted instrument dispositions"
        )
    if accepted_ids != [normalized_instrument_id]:
        raise WatchlistRecalcDeliveryError(
            "Watchlist acknowledgement does not identify the requested outbox instrument"
        )
    return WatchlistRecalcAcknowledgement(
        event_id=normalized_event_id,
        instrument_id=normalized_instrument_id,
    )
