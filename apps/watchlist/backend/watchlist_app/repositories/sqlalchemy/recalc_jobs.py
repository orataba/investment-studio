from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.recalc import RecalcJob


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

    def find_open_job(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        running_timeout_seconds: float | None = None,
    ) -> RecalcJob | None:
        self.requeue_stale_running_jobs(
            session,
            timeout_seconds=running_timeout_seconds,
        )
        stmt = select(RecalcJob).where(
            RecalcJob.instrument_id == instrument_id,
            RecalcJob.job_type == job_type,
            RecalcJob.trigger_type == trigger_type,
            RecalcJob.job_status.in_(("queued", "running")),
        )
        if trigger_ref_type is None:
            stmt = stmt.where(RecalcJob.trigger_ref_type.is_(None))
        else:
            stmt = stmt.where(RecalcJob.trigger_ref_type == trigger_ref_type)
        if trigger_ref_id is None:
            stmt = stmt.where(RecalcJob.trigger_ref_id.is_(None))
        else:
            stmt = stmt.where(RecalcJob.trigger_ref_id == trigger_ref_id)
        stmt = stmt.order_by(RecalcJob.enqueued_at.desc()).limit(1)
        return session.scalar(stmt)

    def requeue_stale_running_jobs(
        self,
        session: Session,
        *,
        timeout_seconds: float | None,
    ) -> int:
        if timeout_seconds is None or timeout_seconds <= 0:
            return 0
        cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=timeout_seconds)
        bind = session.get_bind()
        stmt = select(RecalcJob).where(
            RecalcJob.job_status == "running",
            RecalcJob.started_at.is_not(None),
            RecalcJob.started_at < cutoff,
        )
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        records = session.scalars(stmt).all()
        if not records:
            return 0
        for record in records:
            record.job_status = "queued"
            record.started_at = None
            record.finished_at = None
            record.error_message = "Recovered stale running job after worker interruption."
        session.flush()
        return len(records)

    def create(
        self,
        session: Session,
        *,
        recalc_job_id: str,
        job_type: str,
        instrument_id: str,
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
            instrument_id=instrument_id,
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

    def claim_next_queued(
        self,
        session: Session,
        *,
        running_timeout_seconds: float | None = None,
    ) -> RecalcJob | None:
        self.requeue_stale_running_jobs(
            session,
            timeout_seconds=running_timeout_seconds,
        )
        bind = session.get_bind()
        stmt = (
            select(RecalcJob)
            .where(RecalcJob.job_status == "queued")
            .order_by(RecalcJob.priority.desc(), RecalcJob.enqueued_at.asc())
            .limit(1)
        )
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        record = session.scalar(stmt)
        if record is None:
            return None
        return self.mark_running(session, record)

    def mark_running(self, session: Session, record: RecalcJob) -> RecalcJob:
        record.job_status = "running"
        record.started_at = datetime.now(UTC).replace(microsecond=0)
        record.finished_at = None
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
