from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, func, select, text, update
from sqlalchemy.orm import Session, aliased

from watchlist_app.db.models.recalc import RecalcJob


class SQLAlchemyRecalcJobRepository:
    def acquire_instrument_lock(
        self,
        session: Session,
        *,
        instrument_id: str,
        wait: bool,
    ) -> bool:
        """Serialize recalc state transitions for an instrument on PostgreSQL."""
        if session.get_bind().dialect.name != "postgresql":
            return True
        lock_function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
        acquired = session.scalar(
            text(
                f"SELECT {lock_function}(hashtextextended(:instrument_id, 20260711))"
            ),
            {"instrument_id": instrument_id},
        )
        return True if wait else bool(acquired)

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
        running_timeout_seconds: float | None = None,
        for_update: bool = False,
    ) -> RecalcJob | None:
        self.requeue_stale_running_jobs(
            session,
            timeout_seconds=running_timeout_seconds,
        )
        stmt = select(RecalcJob).where(
            RecalcJob.instrument_id == instrument_id,
            RecalcJob.job_type == job_type,
            RecalcJob.job_status.in_(("queued", "running")),
        )
        stmt = stmt.order_by(
            case((RecalcJob.job_status == "running", 0), else_=1),
            RecalcJob.enqueued_at.desc(),
        ).limit(1)
        if for_update and session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        return session.scalar(stmt)

    def find_running_job(
        self,
        session: Session,
        *,
        instrument_id: str,
        for_update: bool = False,
    ) -> RecalcJob | None:
        stmt = (
            select(RecalcJob)
            .where(
                RecalcJob.instrument_id == instrument_id,
                RecalcJob.job_status == "running",
            )
            .order_by(RecalcJob.started_at.asc())
            .limit(1)
        )
        if for_update and session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
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
            func.coalesce(RecalcJob.heartbeat_at, RecalcJob.started_at) < cutoff,
        )
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        records = session.scalars(stmt).all()
        if not records:
            return 0
        for record in records:
            record.job_status = "queued"
            record.started_at = None
            record.heartbeat_at = None
            record.lease_token = None
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
            heartbeat_at=None,
            lease_token=None,
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
        running_job = aliased(RecalcJob)
        available = (
            RecalcJob.job_status == "queued",
            ~exists(
                select(1).where(
                    running_job.instrument_id == RecalcJob.instrument_id,
                    running_job.job_status == "running",
                )
            ),
        )
        ordering = (RecalcJob.priority.desc(), RecalcJob.enqueued_at.asc())
        if bind.dialect.name == "postgresql":
            candidates = session.execute(
                select(RecalcJob.recalc_job_id, RecalcJob.instrument_id)
                .where(*available)
                .order_by(*ordering)
                .limit(50)
            ).all()
            for job_id, instrument_id in candidates:
                if not self.acquire_instrument_lock(
                    session,
                    instrument_id=instrument_id,
                    wait=False,
                ):
                    continue
                record = session.scalar(
                    select(RecalcJob)
                    .where(
                        RecalcJob.recalc_job_id == job_id,
                        *available,
                    )
                    .with_for_update(skip_locked=True)
                )
                if record is not None:
                    return self.mark_running(session, record)
            return None

        stmt = (
            select(RecalcJob)
            .where(*available)
            .order_by(*ordering)
            .limit(1)
        )
        record = session.scalar(stmt)
        if record is None:
            return None
        return self.mark_running(session, record)

    def mark_running(self, session: Session, record: RecalcJob) -> RecalcJob:
        record.job_status = "running"
        record.started_at = datetime.now(UTC).replace(microsecond=0)
        record.heartbeat_at = record.started_at
        record.lease_token = uuid4().hex
        record.finished_at = None
        record.error_message = None
        session.flush()
        return record

    def touch_heartbeat(self, session: Session, job_id: str, lease_token: str) -> bool:
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == job_id,
                RecalcJob.job_status == "running",
                RecalcJob.lease_token == lease_token,
            )
            .values(heartbeat_at=datetime.now(UTC).replace(microsecond=0))
        )
        return int(result.rowcount or 0) == 1

    def mark_completed(
        self,
        session: Session,
        record: RecalcJob,
        *,
        lease_token: str,
        payload_json: dict[str, object] | None = None,
    ) -> bool:
        values: dict[str, object] = {
            "job_status": "completed",
            "finished_at": datetime.now(UTC).replace(microsecond=0),
            "error_message": None,
        }
        if payload_json is not None:
            values["payload_json"] = payload_json
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == record.recalc_job_id,
                RecalcJob.job_status == "running",
                RecalcJob.lease_token == lease_token,
            )
            .values(**values)
        )
        session.flush()
        return int(result.rowcount or 0) == 1

    def mark_failed(
        self,
        session: Session,
        record: RecalcJob,
        *,
        lease_token: str,
        error_message: str,
    ) -> bool:
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == record.recalc_job_id,
                RecalcJob.job_status == "running",
                RecalcJob.lease_token == lease_token,
            )
            .values(
                job_status="failed",
                finished_at=datetime.now(UTC).replace(microsecond=0),
                error_message=error_message,
            )
        )
        session.flush()
        return int(result.rowcount or 0) == 1
