from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Sequence

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
)


logger = logging.getLogger(__name__)
asset_repository = SQLAlchemyAssetRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()


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


def _primary_shared_identifier(shared_instrument: dict[str, object] | None) -> str | None:
    if not isinstance(shared_instrument, dict):
        return None
    identifiers = shared_instrument.get("identifiers")
    if not isinstance(identifiers, list):
        return None
    primary = next(
        (
            item
            for item in identifiers
            if isinstance(item, dict) and bool(item.get("is_primary"))
        ),
        None,
    )
    fallback = next((item for item in identifiers if isinstance(item, dict)), None)
    candidate = primary or fallback
    if not isinstance(candidate, dict):
        return None
    normalized = str(candidate.get("identifier_value") or "").strip()
    return normalized or None


def _local_asset_metadata_drift(
    *,
    asset_id: str,
    shared_instrument: dict[str, object] | None,
) -> bool:
    if not isinstance(shared_instrument, dict):
        return False
    session_factory = get_session_factory()
    with session_factory() as session:
        asset = asset_repository.get(session, asset_id)
        if asset is None:
            return False
        shared_name = str(shared_instrument.get("asset_name") or asset_id).strip() or asset_id
        shared_identifier = _primary_shared_identifier(shared_instrument)
        return (
            asset.asset_name != shared_name
            or asset.primary_identifier_value != shared_identifier
            or asset.asset_type != str(shared_instrument.get("asset_type") or asset.asset_type)
        )


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
        shared_instrument = get_shared_instrument(normalized_asset_id)
    except SharedInstrumentRegistryError:
        return False
    shared_latest_date = _latest_shared_market_data_date(shared_instrument)
    metadata_drift = _local_asset_metadata_drift(
        asset_id=normalized_asset_id,
        shared_instrument=shared_instrument,
    )
    if shared_latest_date is None and not metadata_drift:
        return False
    if (
        shared_latest_date is not None
        and local_latest_date is not None
        and shared_latest_date <= local_latest_date
        and not metadata_drift
    ):
        return False
    return _enqueue_stale_recalc_job(
        asset_id=normalized_asset_id,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id
        or (shared_latest_date.isoformat() if shared_latest_date is not None else None),
    )


def _enqueue_stale_recalc_job(
    *,
    asset_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> bool:
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            if asset_repository.get(session, asset_id) is None:
                logger.debug("Skipping stale read repair enqueue for unknown asset %s.", asset_id)
                return False
            settings = get_settings()
            existing = recalc_repository.find_open_job(
                session,
                asset_id=asset_id,
                job_type="all",
                trigger_type="stale_read_repair",
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                running_timeout_seconds=settings.recalc_worker_running_job_timeout_seconds,
            )
            if existing is not None:
                session.commit()
                return True
            recalc_repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type="all",
                asset_id=asset_id,
                trigger_type="stale_read_repair",
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                job_status="queued",
                priority=95,
                dedupe_key=make_recalc_dedupe_key(
                    job_type="all",
                    asset_id=asset_id,
                    trigger_type="stale_read_repair",
                    trigger_ref_type=trigger_ref_type,
                    trigger_ref_id=trigger_ref_id,
                ),
                payload_json={"requested_by": "stale_read_repair"},
            )
            session.commit()
            return True
    except Exception:
        logger.exception("Stale read repair enqueue failed for %s.", asset_id)
        return False
