from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.contracts import RecalcExecuteRequest
from app.db.session import get_db_session
from app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from app.services.canonical_recalc import CanonicalRecalcService
from app.services.recalc_job_ids import make_recalc_job_id


router = APIRouter()
asset_repository = SQLAlchemyAssetRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()
canonical_recalc_service = CanonicalRecalcService()


def _ensure_asset_exists(session: Session, asset_id: str) -> None:
    if asset_repository.get(session, asset_id) is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")


def _enqueue_recalc(
    session: Session,
    *,
    asset_id: str,
    job_type: str,
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = recalc_repository.create(
        session,
        recalc_job_id=make_recalc_job_id(),
        job_type=job_type,
        asset_id=asset_id,
        trigger_type="manual_api",
        trigger_ref_type="api_request",
        trigger_ref_id=None,
        job_status="queued",
        priority=100 if job_type == "all" else 85,
        dedupe_key=f"{job_type}:{asset_id}:{make_recalc_job_id()}",
        payload_json={"requested_by": "api"},
    )
    session.commit()
    return {
        "recalc_job_id": record.recalc_job_id,
        "job_type": record.job_type,
        "asset_id": record.asset_id,
        "trigger_type": record.trigger_type,
        "trigger_ref_type": record.trigger_ref_type,
        "trigger_ref_id": record.trigger_ref_id,
        "job_status": record.job_status,
        "priority": record.priority,
        "dedupe_key": record.dedupe_key,
        "payload_json": record.payload_json,
        "enqueued_at": record.enqueued_at.isoformat(),
        "started_at": None,
        "finished_at": None,
        "error_message": None,
    }


@router.post("/assets/{asset_id}/performance")
def enqueue_performance_recalc(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, asset_id=asset_id, job_type="performance")


@router.post("/assets/{asset_id}/exposure")
def enqueue_exposure_recalc(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, asset_id=asset_id, job_type="exposure")


@router.post("/assets/{asset_id}/ratings")
def enqueue_ratings_recalc(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, asset_id=asset_id, job_type="ratings")


@router.post("/assets/{asset_id}/all")
def enqueue_full_recalc(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _enqueue_recalc(session, asset_id=asset_id, job_type="all")


@router.post("/assets/{asset_id}/execute")
def execute_recalc_now(
    asset_id: str,
    payload: RecalcExecuteRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    try:
        return canonical_recalc_service.execute_recalc(
            session,
            asset_id=asset_id,
            job_type=payload.job_type,
            trigger_type=payload.trigger_type,
            trigger_ref_type=payload.trigger_ref_type,
            trigger_ref_id=payload.trigger_ref_id,
            commit=True,
        )
    except ValueError as error:
        if str(error).startswith("Asset not found:"):
            raise HTTPException(status_code=404, detail=str(error)) from error
        raise


@router.get("/jobs")
def get_recalc_jobs(session: Session = Depends(get_db_session)) -> list[dict[str, object]]:
    return [
        {
            "recalc_job_id": item.recalc_job_id,
            "job_type": item.job_type,
            "asset_id": item.asset_id,
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
        "asset_id": record.asset_id,
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
