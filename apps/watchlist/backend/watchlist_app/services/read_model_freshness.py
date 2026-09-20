from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import logging
from typing import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.read_models import (
    InstrumentChartReadModel,
    WatchlistRowReadModel,
)
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id
from watchlist_app.services.calculation_frequency import (
    source_calendar_date,
    assess_latest_observation_freshness,
)
from watchlist_app.services.materialization_policy import (
    WATCHLIST_MATERIALIZATION_VERSION,
)
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
    get_shared_instrument_summaries,
)


logger = logging.getLogger(__name__)
instrument_repository = SQLAlchemyInstrumentRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()


@dataclass(frozen=True, slots=True)
class ReconciliationBatchResult:
    scanned_count: int
    scheduled_count: int
    next_cursor: str | None
    cycle_completed: bool


def _load_reconciliation_targets(
    *,
    limit: int,
    after_instrument_id: str | None,
) -> tuple[list[dict[str, object]], str | None, bool]:
    """Load one keyset-paginated local batch and its materialization cutoffs."""

    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(InstrumentDetail.instrument_id).where(
            InstrumentDetail.is_active.is_(True)
        )
        normalized_cursor = str(after_instrument_id or "").strip()
        if normalized_cursor:
            statement = statement.where(
                InstrumentDetail.instrument_id > normalized_cursor
            )
        statement = statement.order_by(InstrumentDetail.instrument_id).limit(limit + 1)
        candidate_ids = list(session.scalars(statement).all())
        has_more = len(candidate_ids) > limit
        instrument_ids = candidate_ids[:limit]
        if not instrument_ids:
            return [], None, True

        chart_state_by_id = {
            instrument_id: {
                "source_cutoff_at": source_cutoff_at,
                "materialization_version": materialization_version if screener_ready else None,
                "data_freshness_status": data_freshness_status,
            }
            for (
                instrument_id,
                source_cutoff_at,
                materialization_version,
                data_freshness_status,
                screener_ready,
            ) in session.execute(
                select(
                    InstrumentChartReadModel.instrument_id,
                    InstrumentChartReadModel.source_cutoff_at,
                    InstrumentChartReadModel.materialization_version,
                    InstrumentChartReadModel.data_freshness_status,
                    InstrumentChartReadModel.screener_payload_json.is_not(None),
                ).where(InstrumentChartReadModel.instrument_id.in_(instrument_ids))
            ).all()
        }
        row_stats_by_id = {
            instrument_id: {
                "latest_nav_date": latest_nav_date,
                "oldest_source_cutoff_at": oldest_source_cutoff_at,
                "row_count": int(row_count),
                "source_cutoff_count": int(source_cutoff_count),
                "min_materialization_version": min_materialization_version,
                "max_materialization_version": max_materialization_version,
            }
            for (
                instrument_id,
                latest_nav_date,
                oldest_source_cutoff_at,
                row_count,
                source_cutoff_count,
                min_materialization_version,
                max_materialization_version,
            ) in session.execute(
                select(
                    WatchlistRowReadModel.instrument_id,
                    func.max(WatchlistRowReadModel.last_nav_date),
                    func.min(WatchlistRowReadModel.last_fact_update_at),
                    func.count(),
                    func.count(WatchlistRowReadModel.last_fact_update_at),
                    func.min(WatchlistRowReadModel.materialization_version),
                    func.max(WatchlistRowReadModel.materialization_version),
                )
                .where(WatchlistRowReadModel.instrument_id.in_(instrument_ids))
                .group_by(WatchlistRowReadModel.instrument_id)
            ).all()
        }

        targets: list[dict[str, object]] = []
        for instrument_id in instrument_ids:
            row_stats = row_stats_by_id.get(instrument_id)
            row_source_cutoff: object = None
            if row_stats is not None and (
                row_stats["row_count"] == row_stats["source_cutoff_count"]
            ):
                row_source_cutoff = row_stats["oldest_source_cutoff_at"]
            local_cutoffs: tuple[object, ...] = (
                (chart_state_by_id.get(instrument_id) or {}).get("source_cutoff_at"),
            )
            if row_stats is not None:
                local_cutoffs = (*local_cutoffs, row_source_cutoff)
            local_versions = [
                str(
                    (chart_state_by_id.get(instrument_id) or {}).get(
                        "materialization_version"
                    )
                    or ""
                ).strip()
            ]
            if row_stats is not None:
                row_version = (
                    str(row_stats["min_materialization_version"] or "").strip()
                    if row_stats["min_materialization_version"]
                    == row_stats["max_materialization_version"]
                    else ""
                )
                local_versions.append(row_version)
            targets.append(
                {
                    "instrument_id": instrument_id,
                    "local_latest_date": (
                        row_stats["latest_nav_date"]
                        if row_stats is not None
                        else None
                    ),
                    "local_source_cutoff_at": local_materialization_source_cutoff(
                        *local_cutoffs
                    ),
                    "local_materialization_version": (
                        local_versions[0]
                        if local_versions
                        and local_versions[0]
                        and all(version == local_versions[0] for version in local_versions)
                        else None
                    ),
                    "local_data_freshness_status": (
                        chart_state_by_id.get(instrument_id) or {}
                    ).get("data_freshness_status"),
                }
            )

    return (
        targets,
        instrument_ids[-1] if has_more else None,
        not has_more,
    )


def reconcile_stale_instrument_read_models(
    *,
    limit: int,
    after_instrument_id: str | None = None,
) -> ReconciliationBatchResult:
    """Repair missed notifications by comparing local read models with Registry.

    The notification endpoints are a low-latency hint, not a correctness
    boundary.  This bounded worker scan guarantees eventual repair after a
    process restart or a lost HTTP acknowledgement while reusing the same
    durable, per-instrument deduplicated queue as foreground stale-read repair.
    """

    if limit < 1:
        raise ValueError("Reconciliation limit must be positive.")

    targets, next_cursor, cycle_completed = _load_reconciliation_targets(
        limit=limit,
        after_instrument_id=after_instrument_id,
    )
    scheduled_count = schedule_instrument_refreshes_if_stale(
        targets=targets,
        trigger_ref_type="worker_reconcile",
        raise_on_error=True,
    )
    return ReconciliationBatchResult(
        scanned_count=len(targets),
        scheduled_count=scheduled_count,
        next_cursor=next_cursor,
        cycle_completed=cycle_completed,
    )


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


def local_materialization_source_cutoff(*values: object) -> datetime | None:
    """Return the oldest complete source generation across local consumers.

    A missing generation is deliberately stale: using a recalculation wall clock
    or the newest consumer would hide a partially materialized source revision.
    """

    if not values:
        return None
    parsed = [_parse_iso_datetime(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return min(value for value in parsed if value is not None)


def local_materialization_version(*values: object) -> str | None:
    """Return one version only when every local consumer agrees on it."""

    if not values:
        return None
    normalized = [str(value or "").strip() for value in values]
    if not normalized[0] or any(value != normalized[0] for value in normalized):
        return None
    return normalized[0]


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


def _shared_calculation_inputs_updated_at(
    shared_instrument: dict[str, object] | None,
) -> datetime | None:
    if not isinstance(shared_instrument, dict):
        return None
    candidates = [
        parsed
        for parsed in (
            _parse_iso_datetime(shared_instrument.get("market_data_updated_at")),
            _parse_iso_datetime(
                shared_instrument.get("calculation_inputs_updated_at")
            ),
        )
        if parsed is not None
    ]
    return max(candidates) if candidates else None


def _source_generation_ref(
    *,
    shared_updated_at: datetime | None,
    shared_latest_date: date | None,
) -> str | None:
    if shared_updated_at is not None:
        return shared_updated_at.isoformat().replace("+00:00", "Z")
    return shared_latest_date.isoformat() if shared_latest_date is not None else None


def _source_data_is_materialized(
    *,
    shared_updated_at: datetime | None,
    shared_latest_date: date | None,
    local_source_cutoff_at: datetime | None,
    local_latest_date: date | None,
) -> bool:
    if shared_updated_at is not None:
        return (
            local_source_cutoff_at is not None
            and shared_updated_at <= local_source_cutoff_at
        )
    if shared_latest_date is None:
        return True
    if local_latest_date is not None and shared_latest_date <= local_latest_date:
        return True
    # Registry instruments without market or calculation-input updates have no
    # source watermark. For an unavailable selected series, a completed local
    # materialization after the latest source observation proves that generation
    # was considered; a later source date still requeues.
    return (
        local_source_cutoff_at is not None
        and shared_latest_date <= local_source_cutoff_at.date()
    )


def _source_today(market_calendar: object) -> date:
    return source_calendar_date(datetime.now(UTC), market_calendar)


def _freshness_needs_refresh(
    *,
    shared_instrument: dict[str, object] | None,
    local_latest_date: date | None,
    local_data_freshness_status: object,
) -> bool:
    """Re-evaluate freshness when the clock or source publication rule changes.

    Source watermarks do not advance on days when a provider publishes nothing,
    but freshness is still a function of today's completed market sessions.
    A prior stale result may become fresh after correcting its release schedule;
    it must be re-materialized even when its data watermark is unchanged.
    """

    if (
        str(local_data_freshness_status or "").strip().lower() not in {"fresh", "stale"}
        or local_latest_date is None
    ):
        return False
    source_settings = (
        dict(shared_instrument.get("source_settings") or {})
        if isinstance(shared_instrument, dict)
        else {}
    )
    assessment = assess_latest_observation_freshness(
        latest_observation_date=local_latest_date,
        current_date=_source_today(source_settings.get("market_calendar")),
        resolved_frequency="daily",
        expected_frequency=source_settings.get("expected_frequency"),
        market_calendar=source_settings.get("market_calendar"),
        release_lag_days=source_settings.get("release_lag_days"),
        source_mode=source_settings.get("source_mode"),
        instrument_type=shared_instrument.get("instrument_type") if shared_instrument else None,
    )
    return assessment["status"] != str(local_data_freshness_status).strip().lower()


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
    raise_on_error: bool = False,
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
        shared_by_id = get_shared_instrument_summaries(list(normalized_targets))
    except SharedInstrumentRegistryError:
        if raise_on_error:
            raise
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
                if (shared_instrument.get("lifecycle_state") or {}).get("status") == "archived":
                    # Registry owns lifecycle. Keep historical facts/read models,
                    # but stop treating an archived instrument as a live target.
                    local_instrument.is_active = False
                    continue
                shared_latest_date = _latest_shared_market_data_date(shared_instrument)
                shared_updated_at = _shared_calculation_inputs_updated_at(shared_instrument)
                local_latest_date = _parse_iso_date(target.get("local_latest_date"))
                local_cutoff_at = _parse_iso_datetime(
                    target.get("local_source_cutoff_at")
                )
                freshness_aged = _freshness_needs_refresh(
                    shared_instrument=shared_instrument,
                    local_latest_date=local_latest_date,
                    local_data_freshness_status=target.get(
                        "local_data_freshness_status"
                    ),
                )
                metadata_drift = _instrument_metadata_drift(
                    instrument=local_instrument,
                    shared_instrument=shared_instrument,
                )
                materialization_drift = (
                    str(target.get("local_materialization_version") or "").strip()
                    != WATCHLIST_MATERIALIZATION_VERSION
                )
                if (
                    shared_latest_date is None
                    and shared_updated_at is None
                    and not metadata_drift
                    and not materialization_drift
                    and not freshness_aged
                ):
                    continue
                if not metadata_drift and not materialization_drift:
                    if _source_data_is_materialized(
                        shared_updated_at=shared_updated_at,
                        shared_latest_date=shared_latest_date,
                        local_source_cutoff_at=local_cutoff_at,
                        local_latest_date=local_latest_date,
                    ) and not freshness_aged:
                        continue
                if instrument_id in open_instrument_ids:
                    scheduled_count += 1
                    continue
                source_generation_ref = _source_generation_ref(
                    shared_updated_at=shared_updated_at,
                    shared_latest_date=shared_latest_date,
                )
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
                                or source_generation_ref
                            ),
                            job_status="queued",
                            priority=95,
                            dedupe_key=make_recalc_dedupe_key(
                                job_type="all",
                                instrument_id=instrument_id,
                            ),
                            payload_json={
                                "requested_by": "stale_read_repair",
                                "target_source_generation": source_generation_ref,
                                "target_materialization_version": (
                                    WATCHLIST_MATERIALIZATION_VERSION
                                ),
                            },
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
        if raise_on_error:
            raise
        logger.exception("Batch stale read repair failed.")
        return 0


def schedule_instrument_refresh_if_stale(
    *,
    instrument_id: str,
    local_latest_date: date | None,
    local_source_cutoff_at: datetime | None = None,
    local_materialization_version: str | None = WATCHLIST_MATERIALIZATION_VERSION,
    local_data_freshness_status: str | None = None,
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
    shared_updated_at = _shared_calculation_inputs_updated_at(shared_instrument)
    local_cutoff_at = _parse_iso_datetime(local_source_cutoff_at)
    metadata_drift = _local_instrument_metadata_drift(
        instrument_id=normalized_instrument_id,
        shared_instrument=shared_instrument,
    )
    materialization_drift = (
        str(local_materialization_version or "").strip()
        != WATCHLIST_MATERIALIZATION_VERSION
    )
    freshness_aged = _freshness_needs_refresh(
        shared_instrument=shared_instrument,
        local_latest_date=local_latest_date,
        local_data_freshness_status=local_data_freshness_status,
    )
    if (
        shared_latest_date is None
        and shared_updated_at is None
        and not metadata_drift
        and not materialization_drift
        and not freshness_aged
    ):
        return False
    if not metadata_drift and not materialization_drift:
        if _source_data_is_materialized(
            shared_updated_at=shared_updated_at,
            shared_latest_date=shared_latest_date,
            local_source_cutoff_at=local_cutoff_at,
            local_latest_date=local_latest_date,
        ) and not freshness_aged:
            return False
    return _enqueue_stale_recalc_job(
        instrument_id=normalized_instrument_id,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id
        or _source_generation_ref(
            shared_updated_at=shared_updated_at,
            shared_latest_date=shared_latest_date,
        ),
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
