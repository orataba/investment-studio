from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import RecalcBulkRequest, RecalcExecuteRequest
from watchlist_app.api.presenters import present_recalc_job
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.canonical_recalc import (
    CanonicalRecalcService,
    RecalcJobAlreadyRunningError,
)
from watchlist_app.services.instrument_resolution import resolve_watchlist_instrument
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
)
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()
canonical_recalc_service = CanonicalRecalcService()


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
    available_ids = set(
        session.scalars(
            select(InstrumentDetail.instrument_id).where(
                InstrumentDetail.instrument_id.in_(instrument_ids)
            )
        ).all()
    )
    provisioned_ids: list[str] = []
    for instrument_id in instrument_ids:
        if instrument_id in available_ids:
            continue
        try:
            resolution = resolve_watchlist_instrument(
                session,
                instrument_id=instrument_id,
            )
        except SharedInstrumentRegistryError as error:
            raise HTTPException(
                status_code=503,
                detail="Shared instrument registry is unavailable.",
            ) from error
        if (
            resolution is not None
            and bool(resolution.get("detail_supported"))
            and str(resolution.get("canonical_instrument_id") or "").strip()
            == instrument_id
            and session.get(InstrumentDetail, instrument_id) is not None
        ):
            available_ids.add(instrument_id)
            provisioned_ids.append(instrument_id)
    missing_ids = [
        instrument_id for instrument_id in instrument_ids if instrument_id not in available_ids
    ]
    recalc_repository.requeue_stale_running_jobs(session, timeout_seconds=300)
    open_ids = set(
        session.scalars(
            select(RecalcJob.instrument_id).where(
                RecalcJob.instrument_id.in_(available_ids),
                RecalcJob.job_type == payload.job_type,
                RecalcJob.job_status.in_(("queued", "running")),
            )
        ).all()
    )
    enqueued_ids: list[str] = []
    existing_ids: list[str] = []
    for instrument_id in instrument_ids:
        if instrument_id not in available_ids:
            continue
        if instrument_id in open_ids:
            existing_ids.append(instrument_id)
            continue
        try:
            with session.begin_nested():
                recalc_repository.create(
                    session,
                    recalc_job_id=make_recalc_job_id(),
                    job_type=payload.job_type,
                    instrument_id=instrument_id,
                    trigger_type=payload.trigger_type,
                    trigger_ref_type=payload.trigger_ref_type,
                    trigger_ref_id=payload.trigger_ref_id,
                    job_status="queued",
                    priority=100 if payload.job_type == "all" else 85,
                    dedupe_key=make_recalc_dedupe_key(
                        job_type=payload.job_type,
                        instrument_id=instrument_id,
                    ),
                    payload_json={"requested_by": payload.trigger_type},
                )
            open_ids.add(instrument_id)
            enqueued_ids.append(instrument_id)
        except IntegrityError:
            open_ids.add(instrument_id)
            existing_ids.append(instrument_id)
    session.commit()
    return {
        "requested_count": len(instrument_ids),
        "accepted_count": len(enqueued_ids) + len(existing_ids),
        "provisioned_instrument_ids": provisioned_ids,
        "enqueued_instrument_ids": enqueued_ids,
        "existing_instrument_ids": existing_ids,
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


@router.post("/instruments/{instrument_id}/exposure")
def enqueue_exposure_recalc(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, instrument_id=instrument_id, job_type="exposure")


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
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "finished_at": item.finished_at.isoformat() if item.finished_at else None,
            "error_message": item.error_message,
        }
        for item in recalc_repository.list_recent(session)
    ]


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
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "error_message": record.error_message,
    }
