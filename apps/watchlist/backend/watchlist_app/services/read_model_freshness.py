from __future__ import annotations

from datetime import UTC, date, datetime
import logging
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
    list_shared_instruments,
)


logger = logging.getLogger(__name__)
instrument_repository = SQLAlchemyInstrumentRepository()
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


def _parse_iso_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
        market_data = shared_instrument.get("latest_market_data")
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


def _shared_market_data_updated_at(
    shared_instrument: dict[str, object] | None,
) -> datetime | None:
    if not isinstance(shared_instrument, dict):
        return None
    return _parse_iso_datetime(shared_instrument.get("market_data_updated_at"))


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


def _local_instrument_metadata_drift(
    *,
    instrument_id: str,
    shared_instrument: dict[str, object] | None,
) -> bool:
    if not isinstance(shared_instrument, dict):
        return False
    session_factory = get_session_factory()
    with session_factory() as session:
        instrument = instrument_repository.get(session, instrument_id)
        if instrument is None:
            return False
        shared_name = str(shared_instrument.get("instrument_name") or instrument_id).strip() or instrument_id
        shared_identifier = _primary_shared_identifier(shared_instrument)
        return (
            instrument.instrument_name != shared_name
            or instrument.primary_identifier_value != shared_identifier
            or instrument.instrument_type != str(shared_instrument.get("instrument_type") or instrument.instrument_type)
        )


def _instrument_metadata_drift(
    *,
    instrument: InstrumentDetail | None,
    shared_instrument: dict[str, object] | None,
) -> bool:
    if instrument is None or not isinstance(shared_instrument, dict):
        return False
    shared_name = str(
        shared_instrument.get("instrument_name") or instrument.instrument_id
    ).strip() or instrument.instrument_id
    shared_identifier = _primary_shared_identifier(shared_instrument)
    shared_type = str(
        shared_instrument.get("instrument_type") or instrument.instrument_type
    )
    return (
        instrument.instrument_name != shared_name
        or instrument.primary_identifier_value != shared_identifier
        or instrument.instrument_type != shared_type
    )


def schedule_instrument_refreshes_if_stale(
    *,
    targets: Sequence[Mapping[str, object]],
    trigger_ref_type: str,
    trigger_ref_id: str | None = None,
) -> int:
    """Batch stale-read repair without turning a screener read into an N+1 query.

    Shared registry metadata is loaded once, local metadata/open jobs are loaded in
    set-based queries, and all newly queued repairs are committed together. This is
    intended for a post-response background task; foreground detail reads continue
    to use the single-instrument helper below.
    """

    normalized_targets: dict[str, Mapping[str, object]] = {}
    for target in targets:
        instrument_id = str(target.get("instrument_id") or "").strip()
        if instrument_id:
            normalized_targets[instrument_id] = target
    if not normalized_targets:
        return 0

    try:
        shared_by_id = {
            str(item.get("instrument_id")): item
            for item in list_shared_instruments(limit=None)
            if str(item.get("instrument_id") or "").strip() in normalized_targets
        }
    except SharedInstrumentRegistryError:
        return 0

    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            instrument_ids = list(normalized_targets)
            local_by_id = {
                item.instrument_id: item
                for item in session.scalars(
                    select(InstrumentDetail).where(
                        InstrumentDetail.instrument_id.in_(instrument_ids)
                    )
                ).all()
            }
            settings = get_settings()
            recalc_repository.requeue_stale_running_jobs(
                session,
                timeout_seconds=settings.recalc_worker_running_job_timeout_seconds,
            )
            open_instrument_ids = set(
                session.scalars(
                    select(RecalcJob.instrument_id).where(
                        RecalcJob.instrument_id.in_(instrument_ids),
                        RecalcJob.job_type == "all",
                        RecalcJob.job_status.in_(("queued", "running")),
                    )
                ).all()
            )

            scheduled_count = 0
            for instrument_id, target in normalized_targets.items():
                shared_instrument = shared_by_id.get(instrument_id)
                local_instrument = local_by_id.get(instrument_id)
                if shared_instrument is None or local_instrument is None:
                    continue
                shared_latest_date = _latest_shared_market_data_date(shared_instrument)
                shared_updated_at = _shared_market_data_updated_at(shared_instrument)
                local_latest_date = _parse_iso_date(target.get("local_latest_date"))
                local_cutoff_at = _parse_iso_datetime(
                    target.get("local_source_cutoff_at")
                )
                metadata_drift = _instrument_metadata_drift(
                    instrument=local_instrument,
                    shared_instrument=shared_instrument,
                )
                if (
                    shared_latest_date is None
                    and shared_updated_at is None
                    and not metadata_drift
                ):
                    continue
                if not metadata_drift:
                    if shared_updated_at is not None:
                        if (
                            local_cutoff_at is not None
                            and shared_updated_at <= local_cutoff_at
                        ):
                            continue
                    elif (
                        shared_latest_date is not None
                        and local_latest_date is not None
                        and shared_latest_date <= local_latest_date
                    ):
                        continue
                if instrument_id in open_instrument_ids:
                    scheduled_count += 1
                    continue
                try:
                    with session.begin_nested():
                        recalc_repository.create(
                            session,
                            recalc_job_id=make_recalc_job_id(),
                            job_type="all",
                            instrument_id=instrument_id,
                            trigger_type="stale_read_repair",
                            trigger_ref_type=trigger_ref_type,
                            trigger_ref_id=(
                                trigger_ref_id
                                or (
                                    shared_latest_date.isoformat()
                                    if shared_latest_date is not None
                                    else None
                                )
                            ),
                            job_status="queued",
                            priority=95,
                            dedupe_key=make_recalc_dedupe_key(
                                job_type="all",
                                instrument_id=instrument_id,
                            ),
                            payload_json={"requested_by": "stale_read_repair"},
                        )
                    open_instrument_ids.add(instrument_id)
                    scheduled_count += 1
                except IntegrityError:
                    # A concurrent scheduler won the partial unique-index race.
                    open_instrument_ids.add(instrument_id)
                    scheduled_count += 1
            session.commit()
            return scheduled_count
    except Exception:
        logger.exception("Batch stale read repair failed.")
        return 0


def schedule_instrument_refresh_if_stale(
    *,
    instrument_id: str,
    local_latest_date: date | None,
    local_source_cutoff_at: datetime | None = None,
    trigger_ref_type: str,
    trigger_ref_id: str | None = None,
) -> bool:
    normalized_instrument_id = instrument_id.strip()
    if not normalized_instrument_id:
        return False
    try:
        shared_instrument = get_shared_instrument(normalized_instrument_id)
    except SharedInstrumentRegistryError:
        return False
    shared_latest_date = _latest_shared_market_data_date(shared_instrument)
    shared_updated_at = _shared_market_data_updated_at(shared_instrument)
    local_cutoff_at = _parse_iso_datetime(local_source_cutoff_at)
    metadata_drift = _local_instrument_metadata_drift(
        instrument_id=normalized_instrument_id,
        shared_instrument=shared_instrument,
    )
    if shared_latest_date is None and shared_updated_at is None and not metadata_drift:
        return False
    if not metadata_drift:
        if shared_updated_at is not None:
            if local_cutoff_at is not None and shared_updated_at <= local_cutoff_at:
                return False
        elif (
            shared_latest_date is not None
            and local_latest_date is not None
            and shared_latest_date <= local_latest_date
        ):
            return False
    return _enqueue_stale_recalc_job(
        instrument_id=normalized_instrument_id,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id
        or (shared_latest_date.isoformat() if shared_latest_date is not None else None),
    )


def _enqueue_stale_recalc_job(
    *,
    instrument_id: str,
    trigger_ref_type: str,
    trigger_ref_id: str | None,
) -> bool:
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            if instrument_repository.get(session, instrument_id) is None:
                logger.debug("Skipping stale read repair enqueue for unknown instrument %s.", instrument_id)
                return False
            settings = get_settings()
            existing = recalc_repository.find_open_job(
                session,
                instrument_id=instrument_id,
                job_type="all",
                running_timeout_seconds=settings.recalc_worker_running_job_timeout_seconds,
            )
            if existing is not None:
                session.commit()
                return True
            recalc_repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type="all",
                instrument_id=instrument_id,
                trigger_type="stale_read_repair",
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
                job_status="queued",
                priority=95,
                dedupe_key=make_recalc_dedupe_key(
                    job_type="all",
                    instrument_id=instrument_id,
                ),
                payload_json={"requested_by": "stale_read_repair"},
            )
            session.commit()
            return True
    except Exception:
        logger.exception("Stale read repair enqueue failed for %s.", instrument_id)
        return False
