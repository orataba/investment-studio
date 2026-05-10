from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import RecalcExecuteRequest
from watchlist_app.api.presenters import present_recalc_job
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()
canonical_recalc_service = CanonicalRecalcService()


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
        trigger_type="manual_api",
        trigger_ref_type="api_request",
        trigger_ref_id=None,
    )
    if existing is not None:
        return present_recalc_job(existing)

    dedupe_key = make_recalc_dedupe_key(
        job_type=job_type,
        instrument_id=instrument_id,
        trigger_type="manual_api",
        trigger_ref_type="api_request",
        trigger_ref_id=None,
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
            trigger_type="manual_api",
            trigger_ref_type="api_request",
            trigger_ref_id=None,
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


@router.post("/instruments/{instrument_id}/ratings")
def enqueue_ratings_recalc(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, instrument_id=instrument_id, job_type="ratings")


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
