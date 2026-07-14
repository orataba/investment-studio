from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core.db_models import (
    Instrument as CanonicalInstrument,
    InstrumentIdentifier as CanonicalInstrumentIdentifier,
)

from watchlist_app.api.contracts import (
    RecalcBulkRequest,
    RecalcExecuteRequest,
    RecalcJobRetryRequest,
)
from watchlist_app.api.presenters import present_recalc_job
from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.recalc import RecalcSourceEventInbox
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.repositories.sqlalchemy.recalc_invalidations import (
    SQLAlchemyRecalcInvalidationRepository,
)
from watchlist_app.services.canonical_recalc import (
    CanonicalRecalcService,
    RecalcJobAlreadyRunningError,
)
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()
recalc_invalidation_repository = SQLAlchemyRecalcInvalidationRepository()
canonical_recalc_service = CanonicalRecalcService()
SUPPORTED_RECALC_INSTRUMENT_TYPES = frozenset({"fund", "etf", "index"})


def _registry_lifecycle_state(record: CanonicalInstrument) -> tuple[str, str | None]:
    lifecycle = record.lifecycle_state_json or {}
    status = str(lifecycle.get("status") or "active").strip().lower() or "active"
    canonical_id = str(lifecycle.get("canonical_instrument_id") or "").strip() or None
    return status, canonical_id


def _resolve_registry_instrument(
    session: Session,
    instrument_id: str,
) -> CanonicalInstrument | None:
    visited: set[str] = set()
    candidate_id = instrument_id
    while candidate_id and candidate_id not in visited:
        visited.add(candidate_id)
        record = session.get(CanonicalInstrument, candidate_id)
        if record is None:
            return None
        status, canonical_id = _registry_lifecycle_state(record)
        if status != "archived" or canonical_id is None:
            return record
        candidate_id = canonical_id
    return None


def _primary_registry_identifier(
    session: Session,
    instrument_id: str,
) -> CanonicalInstrumentIdentifier | None:
    return session.scalar(
        select(CanonicalInstrumentIdentifier)
        .where(CanonicalInstrumentIdentifier.instrument_id == instrument_id)
        .order_by(
            CanonicalInstrumentIdentifier.is_primary.desc(),
            CanonicalInstrumentIdentifier.instrument_identifier_id.asc(),
        )
        .limit(1)
    )


def _materialize_local_instrument(
    session: Session,
    registry_record: CanonicalInstrument,
) -> None:
    if instrument_repository.get(session, registry_record.instrument_id) is not None:
        return
    instrument_type = registry_record.instrument_type.strip().lower()
    if instrument_type not in SUPPORTED_RECALC_INSTRUMENT_TYPES:
        raise ValueError(f"Unsupported recalc instrument type: {instrument_type}")
    identifier = _primary_registry_identifier(session, registry_record.instrument_id)
    try:
        with session.begin_nested():
            instrument_repository.upsert_minimal(
                session,
                instrument_id=registry_record.instrument_id,
                instrument_type=instrument_type,
                detail_view_type="index" if instrument_type == "index" else "fund",
                instrument_name=registry_record.instrument_name,
                primary_identifier_type=(
                    identifier.identifier_type if identifier is not None else None
                ),
                primary_identifier_value=(
                    identifier.identifier_value if identifier is not None else None
                ),
                metadata_json={},
            )
    except IntegrityError:
        if instrument_repository.get(session, registry_record.instrument_id) is None:
            raise


def _existing_source_event(
    session: Session,
    *,
    instrument_id: str,
    job_type: str,
    trigger_ref_type: str | None,
    trigger_ref_id: str | None,
) -> RecalcSourceEventInbox | None:
    if trigger_ref_type is None or trigger_ref_id is None:
        return None
    return recalc_invalidation_repository.get_source_event(
        session,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id,
        instrument_id=instrument_id,
        job_type=job_type,
    )


@router.post("/bulk")
def enqueue_bulk_recalc(
    payload: RecalcBulkRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    instrument_ids = list(
        dict.fromkeys(
            instrument_id.strip()
            for instrument_id in payload.instrument_ids
            if instrument_id.strip()
        )
    )
    trigger_ref_type = (payload.trigger_ref_type or "").strip() or None
    trigger_ref_id = (payload.trigger_ref_id or "").strip() or None
    is_source_event = trigger_ref_id is not None
    if is_source_event and trigger_ref_type is None:
        raise HTTPException(
            status_code=422,
            detail="trigger_ref_type must be non-empty for a source event",
        )
    enqueued_ids: list[str] = []
    coalesced_ids: list[str] = []
    existing_ids: list[str] = []
    ignored_ids: list[str] = []
    missing_ids: list[str] = []
    settings = get_settings()
    for requested_instrument_id in instrument_ids:
        registry_record = _resolve_registry_instrument(
            session,
            requested_instrument_id,
        )
        if registry_record is None:
            missing_ids.append(requested_instrument_id)
            continue
        instrument_id = registry_record.instrument_id
        instrument_type = registry_record.instrument_type.strip().lower()
        lifecycle_status, _canonical_id = _registry_lifecycle_state(registry_record)
        if (
            instrument_type not in SUPPORTED_RECALC_INSTRUMENT_TYPES
            or lifecycle_status == "archived"
        ):
            if is_source_event:
                existing_event = _existing_source_event(
                    session,
                    instrument_id=instrument_id,
                    job_type=payload.job_type,
                    trigger_ref_type=trigger_ref_type,
                    trigger_ref_id=trigger_ref_id,
                )
                if existing_event is None:
                    try:
                        with session.begin_nested():
                            recalc_invalidation_repository.record_ignored_source_event(
                                session,
                                trigger_ref_type=str(trigger_ref_type),
                                trigger_ref_id=str(trigger_ref_id),
                                instrument_id=instrument_id,
                                job_type=payload.job_type,
                            )
                    except IntegrityError:
                        existing_event = _existing_source_event(
                            session,
                            instrument_id=instrument_id,
                            job_type=payload.job_type,
                            trigger_ref_type=trigger_ref_type,
                            trigger_ref_id=trigger_ref_id,
                        )
                        if existing_event is None:
                            raise
            ignored_ids.append(requested_instrument_id)
            continue

        _materialize_local_instrument(session, registry_record)
        existing_event = _existing_source_event(
            session,
            instrument_id=instrument_id,
            job_type=payload.job_type,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
        )
        if existing_event is not None:
            existing_ids.append(requested_instrument_id)
            continue

        accepted_generation: int | None = None
        if is_source_event:
            try:
                with session.begin_nested():
                    inbox_record = (
                        recalc_invalidation_repository.record_supported_source_event(
                            session,
                            trigger_ref_type=str(trigger_ref_type),
                            trigger_ref_id=str(trigger_ref_id),
                            instrument_id=instrument_id,
                            job_type=payload.job_type,
                        )
                    )
                    accepted_generation = inbox_record.generation
            except IntegrityError:
                existing_event = _existing_source_event(
                    session,
                    instrument_id=instrument_id,
                    job_type=payload.job_type,
                    trigger_ref_type=trigger_ref_type,
                    trigger_ref_id=trigger_ref_id,
                )
                if existing_event is None:
                    raise
                existing_ids.append(requested_instrument_id)
                continue

        open_job = recalc_repository.find_open_job(
            session,
            instrument_id=instrument_id,
            job_type=payload.job_type,
            running_timeout_seconds=(
                settings.recalc_worker_running_job_timeout_seconds
            ),
        )
        if open_job is not None:
            (coalesced_ids if is_source_event else existing_ids).append(
                requested_instrument_id
            )
            continue
        if is_source_event and recalc_invalidation_repository.has_unresolved_failed_job(
            session,
            instrument_id=instrument_id,
            job_type=payload.job_type,
        ):
            coalesced_ids.append(requested_instrument_id)
            continue
        try:
            with session.begin_nested():
                recalc_repository.create(
                    session,
                    recalc_job_id=make_recalc_job_id(),
                    job_type=payload.job_type,
                    instrument_id=instrument_id,
                    trigger_type=payload.trigger_type,
                    trigger_ref_type=trigger_ref_type,
                    trigger_ref_id=trigger_ref_id,
                    job_status="queued",
                    priority=100 if payload.job_type == "all" else 85,
                    dedupe_key=make_recalc_dedupe_key(
                        job_type=payload.job_type,
                        instrument_id=instrument_id,
                    ),
                    payload_json={
                        "requested_by": payload.trigger_type,
                        "requested_generation": accepted_generation,
                    },
                )
            enqueued_ids.append(requested_instrument_id)
        except IntegrityError:
            open_job = recalc_repository.find_open_job(
                session,
                instrument_id=instrument_id,
                job_type=payload.job_type,
            )
            if open_job is None:
                raise
            (coalesced_ids if is_source_event else existing_ids).append(
                requested_instrument_id
            )

    classified_ids = [
        *enqueued_ids,
        *coalesced_ids,
        *existing_ids,
        *ignored_ids,
        *missing_ids,
    ]
    if len(classified_ids) != len(instrument_ids) or set(classified_ids) != set(
        instrument_ids
    ):
        raise RuntimeError("Bulk recalc response classification is not a partition.")
    session.commit()
    return {
        "requested_count": len(instrument_ids),
        "accepted_count": len(instrument_ids) - len(missing_ids),
        "enqueued_instrument_ids": enqueued_ids,
        "coalesced_instrument_ids": coalesced_ids,
        "existing_instrument_ids": existing_ids,
        "ignored_instrument_ids": ignored_ids,
        "missing_instrument_ids": missing_ids,
    }


def _ensure_instrument_exists(session: Session, instrument_id: str) -> None:
    if instrument_repository.get(session, instrument_id) is None:
        raise HTTPException(status_code=404, detail=f"Instrument not found: {instrument_id}")


def _enqueue_recalc(
    session: Session,
    *,
    instrument_id: str,
    job_type: str,
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    existing = recalc_repository.find_open_job(
        session,
        instrument_id=instrument_id,
        job_type=job_type,
    )
    if existing is not None:
        return present_recalc_job(existing)

    dedupe_key = make_recalc_dedupe_key(
        job_type=job_type,
        instrument_id=instrument_id,
    )
    try:
        record = recalc_repository.create(
            session,
            recalc_job_id=make_recalc_job_id(),
            job_type=job_type,
            instrument_id=instrument_id,
            trigger_type="manual_api",
            trigger_ref_type="api_request",
            trigger_ref_id=None,
            job_status="queued",
            priority=100 if job_type == "all" else 85,
            dedupe_key=dedupe_key,
            payload_json={"requested_by": "api"},
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        existing = recalc_repository.find_open_job(
            session,
            instrument_id=instrument_id,
            job_type=job_type,
        )
        if existing is not None:
            return present_recalc_job(existing)
        raise HTTPException(status_code=409, detail="Open recalc job already exists.") from error
    return present_recalc_job(record)


@router.post("/instruments/{instrument_id}/performance")
def enqueue_performance_recalc(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, instrument_id=instrument_id, job_type="performance")


@router.post("/instruments/{instrument_id}/all")
def enqueue_full_recalc(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, instrument_id=instrument_id, job_type="all")


@router.post("/instruments/{instrument_id}/execute")
def execute_recalc_now(
    instrument_id: str,
    payload: RecalcExecuteRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    try:
        return canonical_recalc_service.execute_recalc(
            session,
            instrument_id=instrument_id,
            job_type=payload.job_type,
            trigger_type=payload.trigger_type,
            trigger_ref_type=payload.trigger_ref_type,
            trigger_ref_id=payload.trigger_ref_id,
            commit=True,
        )
    except ValueError as error:
        if str(error).startswith("Instrument not found:"):
            raise HTTPException(status_code=404, detail=str(error)) from error
        raise
    except RecalcJobAlreadyRunningError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/jobs")
def get_recalc_jobs(session: Session = Depends(get_db_session)) -> list[dict[str, object]]:
    return [
        {
            "recalc_job_id": item.recalc_job_id,
            "job_type": item.job_type,
            "instrument_id": item.instrument_id,
            "trigger_type": item.trigger_type,
            "trigger_ref_type": item.trigger_ref_type,
            "trigger_ref_id": item.trigger_ref_id,
            "job_status": item.job_status,
            "priority": item.priority,
            "dedupe_key": item.dedupe_key,
            "payload_json": item.payload_json,
            "enqueued_at": item.enqueued_at.isoformat(),
            "attempt_count": item.attempt_count,
            "max_attempts": item.max_attempts,
            "available_at": item.available_at.isoformat(),
            "claimed_generation": item.claimed_generation,
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "finished_at": item.finished_at.isoformat() if item.finished_at else None,
            "error_message": item.error_message,
        }
        for item in recalc_repository.list_recent(session)
    ]


@router.post("/jobs/{job_id}/retry")
def retry_failed_recalc_job(
    job_id: str,
    payload: RecalcJobRetryRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    record = recalc_repository.get(session, job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Recalc job not found")
    if record.job_status != "failed":
        raise HTTPException(status_code=409, detail="Only failed recalc jobs can be retried.")
    try:
        with session.begin_nested():
            retried = recalc_repository.requeue_failed_job(
                session,
                job_id=job_id,
                additional_attempts=payload.additional_attempts,
            )
    except IntegrityError as error:
        open_job = recalc_repository.find_open_job(
            session,
            instrument_id=record.instrument_id,
            job_type=record.job_type,
        )
        if open_job is not None and open_job.recalc_job_id != job_id:
            raise HTTPException(
                status_code=409,
                detail="Another open recalc job already exists for this instrument.",
            ) from error
        raise
    if retried is None:
        session.rollback()
        raise HTTPException(status_code=409, detail="Recalc job is no longer failed.")
    session.commit()
    session.refresh(retried)
    return present_recalc_job(retried)


@router.get("/jobs/{job_id}")
def get_recalc_job_detail(
    job_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    record = recalc_repository.get(session, job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Recalc job not found")
    return {
        "recalc_job_id": record.recalc_job_id,
        "job_type": record.job_type,
        "instrument_id": record.instrument_id,
        "trigger_type": record.trigger_type,
        "trigger_ref_type": record.trigger_ref_type,
        "trigger_ref_id": record.trigger_ref_id,
        "job_status": record.job_status,
        "priority": record.priority,
        "dedupe_key": record.dedupe_key,
        "payload_json": record.payload_json,
        "enqueued_at": record.enqueued_at.isoformat(),
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "available_at": record.available_at.isoformat(),
        "claimed_generation": record.claimed_generation,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "error_message": record.error_message,
    }
