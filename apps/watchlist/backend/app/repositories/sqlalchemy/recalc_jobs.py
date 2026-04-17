from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.recalc import RecalcJob


class SQLAlchemyRecalcJobRepository:
    def list_recent(self, session: Session, limit: int = 100) -> Sequence[RecalcJob]:
        stmt = (
            select(RecalcJob)
            .order_by(RecalcJob.enqueued_at.desc())
            .limit(limit)
        )
        return session.scalars(stmt).all()

    def get(self, session: Session, job_id: str) -> RecalcJob | None:
        return session.get(RecalcJob, job_id)

    def create(
        self,
        session: Session,
        *,
        recalc_job_id: str,
        job_type: str,
        asset_id: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        job_status: str,
        priority: int,
        dedupe_key: str,
        payload_json: dict[str, object],
    ) -> RecalcJob:
        record = RecalcJob(
            recalc_job_id=recalc_job_id,
            job_type=job_type,
            asset_id=asset_id,
            trigger_type=trigger_type,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            job_status=job_status,
            priority=priority,
            dedupe_key=dedupe_key,
            payload_json=payload_json,
            enqueued_at=datetime.now(UTC).replace(microsecond=0),
            started_at=None,
            finished_at=None,
            error_message=None,
        )
        session.add(record)
        session.flush()
        return record

    def mark_running(self, session: Session, record: RecalcJob) -> RecalcJob:
        record.job_status = "running"
        record.started_at = datetime.now(UTC).replace(microsecond=0)
        record.error_message = None
        session.flush()
        return record

    def mark_completed(
        self,
        session: Session,
        record: RecalcJob,
        *,
        payload_json: dict[str, object] | None = None,
    ) -> RecalcJob:
        record.job_status = "completed"
        record.finished_at = datetime.now(UTC).replace(microsecond=0)
        if payload_json is not None:
            record.payload_json = payload_json
        record.error_message = None
        session.flush()
        return record

    def mark_failed(
        self,
        session: Session,
        record: RecalcJob,
        *,
        error_message: str,
    ) -> RecalcJob:
        record.job_status = "failed"
        record.finished_at = datetime.now(UTC).replace(microsecond=0)
        record.error_message = error_message
        session.flush()
        return record
