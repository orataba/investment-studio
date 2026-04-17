from __future__ import annotations

from datetime import date, datetime
import logging
from threading import Lock, Thread
from typing import Sequence

from app.db.session import get_session_factory
from app.services.canonical_recalc import CanonicalRecalcService
from app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
)


logger = logging.getLogger(__name__)
_refresh_lock = Lock()
_refresh_inflight: set[str] = set()


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def latest_local_market_data_date(
    *,
    chart_payload: object | None,
    fallback_values: Sequence[object] = (),
) -> date | None:
    if isinstance(chart_payload, dict):
        series = chart_payload.get("series")
        if isinstance(series, list):
            for candidate in series:
                if not isinstance(candidate, dict):
                    continue
                points = candidate.get("points")
                if isinstance(points, list) and points:
                    latest_point = points[-1]
                    if isinstance(latest_point, dict):
                        parsed = _parse_iso_date(latest_point.get("date"))
                        if parsed is not None:
                            return parsed
        date_range = chart_payload.get("date_range")
        if isinstance(date_range, dict):
            parsed = _parse_iso_date(date_range.get("end"))
            if parsed is not None:
                return parsed
    for value in fallback_values:
        parsed = _parse_iso_date(value)
        if parsed is not None:
            return parsed
    return None


def _latest_shared_market_data_date(shared_instrument: dict[str, object] | None) -> date | None:
    if not isinstance(shared_instrument, dict):
        return None
    market_data = shared_instrument.get("market_data")
    if not isinstance(market_data, list):
        return None
    candidates = [
        parsed
        for parsed in (
            _parse_iso_date(item.get("as_of_date"))
            for item in market_data
            if isinstance(item, dict)
        )
        if parsed is not None
    ]
    return max(candidates) if candidates else None


def schedule_asset_refresh_if_stale(
    *,
    asset_id: str,
    local_latest_date: date | None,
    trigger_ref_type: str,
    trigger_ref_id: str | None = None,
) -> bool:
    normalized_asset_id = asset_id.strip()
    if not normalized_asset_id:
        return False
    try:
        shared_latest_date = _latest_shared_market_data_date(
            get_shared_instrument(normalized_asset_id)
        )
    except SharedInstrumentRegistryError:
        return False
    if shared_latest_date is None:
        return False
    if local_latest_date is not None and shared_latest_date <= local_latest_date:
        return False
    return _start_async_recalc(
        asset_id=normalized_asset_id,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id or shared_latest_date.isoformat(),
    )


def _start_async_recalc(
    *,
    asset_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> bool:
    with _refresh_lock:
        if asset_id in _refresh_inflight:
            return False
        _refresh_inflight.add(asset_id)
    Thread(
        target=_execute_async_recalc,
        kwargs={
            "asset_id": asset_id,
            "trigger_ref_type": trigger_ref_type,
            "trigger_ref_id": trigger_ref_id,
        },
        daemon=True,
    ).start()
    return True


def _execute_async_recalc(
    *,
    asset_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> None:
    session_factory = get_session_factory()
    service = CanonicalRecalcService()
    try:
        with session_factory() as session:
            service.execute_recalc(
                session,
                asset_id=asset_id,
                job_type="all",
                trigger_type="stale_read_repair",
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                commit=True,
            )
    except ValueError as error:
        if str(error).startswith("Asset not found:"):
            logger.debug("Skipping stale read repair for unknown asset %s.", asset_id)
        else:
            logger.exception("Stale read repair failed for %s.", asset_id)
    except Exception:
        logger.exception("Stale read repair failed for %s.", asset_id)
    finally:
        with _refresh_lock:
            _refresh_inflight.discard(asset_id)
