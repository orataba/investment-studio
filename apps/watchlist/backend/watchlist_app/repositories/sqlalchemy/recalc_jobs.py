from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, func, or_, select, text, update
from sqlalchemy.orm import Session, aliased

from watchlist_app.db.models.recalc import RecalcInvalidationState, RecalcJob
from watchlist_app.repositories.sqlalchemy.recalc_invalidations import (
    SQLAlchemyRecalcInvalidationRepository,
)


DEFAULT_RECALC_MAX_ATTEMPTS = 3
recalc_invalidation_repository = SQLAlchemyRecalcInvalidationRepository()


def _database_now(session: Session) -> datetime:
    expression = (
        func.clock_timestamp()
        if session.get_bind().dialect.name == "postgresql"
        else func.current_timestamp()
    )
    value = session.scalar(select(expression))
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a valid current timestamp.")
    return value


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
        now = _database_now(session)
        cutoff = now - timedelta(seconds=timeout_seconds)
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
            exhausted = record.attempt_count >= record.max_attempts
            record.job_status = "failed" if exhausted else "queued"
            record.started_at = None
            record.heartbeat_at = None
            record.lease_token = None
            record.finished_at = now if exhausted else None
            record.available_at = now
            record.error_message = (
                "Running job lease expired after final attempt."
                if exhausted
                else "Recovered stale running job after worker interruption."
            )
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
        attempt_count: int = 0,
        max_attempts: int = DEFAULT_RECALC_MAX_ATTEMPTS,
        available_at: datetime | None = None,
    ) -> RecalcJob:
        now = _database_now(session)
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
            enqueued_at=now,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            available_at=available_at or now,
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
            RecalcJob.available_at <= _database_now(session),
            RecalcJob.attempt_count < RecalcJob.max_attempts,
            ~exists(
                select(1).where(
                    running_job.instrument_id == RecalcJob.instrument_id,
                    running_job.job_status == "running",
                )
            ),
        )
        ordering = (
            RecalcJob.available_at.asc(),
            RecalcJob.priority.desc(),
            RecalcJob.enqueued_at.asc(),
        )
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
        if record.attempt_count >= record.max_attempts:
            raise ValueError(f"Recalc attempt budget exhausted: {record.recalc_job_id}")
        now = _database_now(session)
        record.claimed_generation = (
            recalc_invalidation_repository.capture_claim_generation(
                session,
                instrument_id=record.instrument_id,
                job_type=record.job_type,
            )
        )
        record.job_status = "running"
        record.attempt_count += 1
        record.started_at = now
        record.heartbeat_at = record.started_at
        record.lease_token = uuid4().hex
        record.finished_at = None
        record.error_message = None
        session.flush()
        return record

    def touch_heartbeat(self, session: Session, job_id: str, lease_token: str) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == job_id,
                RecalcJob.job_status == "running",
                RecalcJob.lease_token == lease_token,
            )
            .values(heartbeat_at=now)
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
        now = _database_now(session)
        values: dict[str, object] = {
            "job_status": "completed",
            "finished_at": now,
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

    def complete_claimed_generation(
        self,
        session: Session,
        record: RecalcJob,
    ) -> int | None:
        return recalc_invalidation_repository.complete_claimed_generation(
            session,
            instrument_id=record.instrument_id,
            job_type=record.job_type,
            claimed_generation=record.claimed_generation,
        )

    def mark_failed(
        self,
        session: Session,
        record: RecalcJob,
        *,
        lease_token: str,
        error_message: str,
    ) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == record.recalc_job_id,
                RecalcJob.job_status == "running",
                RecalcJob.lease_token == lease_token,
            )
            .values(
                job_status="failed",
                finished_at=now,
                error_message=error_message,
            )
        )
        session.flush()
        return int(result.rowcount or 0) == 1

    def reschedule_after_failure(
        self,
        session: Session,
        record: RecalcJob,
        *,
        lease_token: str,
        error_message: str,
        retry_delay_seconds: int,
    ) -> str | None:
        if retry_delay_seconds < 1:
            raise ValueError("retry_delay_seconds must be positive.")
        now = _database_now(session)
        exhausted = record.attempt_count >= record.max_attempts
        next_status = "failed" if exhausted else "queued"
        values: dict[str, object] = {
            "job_status": next_status,
            "started_at": None,
            "heartbeat_at": None,
            "lease_token": None,
            "finished_at": now if exhausted else None,
            "error_message": error_message[:4000],
        }
        if not exhausted:
            values["available_at"] = now + timedelta(seconds=retry_delay_seconds)
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
        if int(result.rowcount or 0) != 1:
            return None
        return next_status

    def requeue_failed_job(
        self,
        session: Session,
        *,
        job_id: str,
        additional_attempts: int,
    ) -> RecalcJob | None:
        if not 1 <= additional_attempts <= 10:
            raise ValueError("additional_attempts must be between 1 and 10.")
        now = _database_now(session)
        result = session.execute(
            update(RecalcJob)
            .where(
                RecalcJob.recalc_job_id == job_id,
                RecalcJob.job_status == "failed",
            )
            .values(
                job_status="queued",
                max_attempts=RecalcJob.max_attempts + additional_attempts,
                available_at=now,
                started_at=None,
                heartbeat_at=None,
                lease_token=None,
                finished_at=None,
            )
        )
        if int(result.rowcount or 0) != 1:
            return None
        session.flush()
        return session.get(RecalcJob, job_id)

    def has_terminal_source_event_failure(self, session: Session) -> bool:
        unresolved_generation = exists().where(
            RecalcInvalidationState.instrument_id == RecalcJob.instrument_id,
            RecalcInvalidationState.job_type == RecalcJob.job_type,
            RecalcInvalidationState.completed_generation
            < RecalcJob.claimed_generation,
        )
        return bool(
            session.scalar(
                select(
                    exists().where(
                        RecalcJob.job_status == "failed",
                        RecalcJob.trigger_ref_type.is_not(None),
                        func.trim(RecalcJob.trigger_ref_type) != "",
                        RecalcJob.trigger_ref_id.is_not(None),
                        func.trim(RecalcJob.trigger_ref_id) != "",
                        or_(
                            RecalcJob.claimed_generation.is_(None),
                            unresolved_generation,
                        ),
                    )
                )
            )
        )
